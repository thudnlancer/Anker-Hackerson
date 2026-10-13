from __future__ import annotations

from pathlib import Path
from typing import Any
import json
import hashlib
import joblib
from concurrent.futures import ProcessPoolExecutor, as_completed
from time import perf_counter
from threadpoolctl import threadpool_limits

import numpy as np
import pandas as pd
from sklearn.metrics import mean_absolute_error, mean_squared_error

from .backtest import load_csv
from .forecast import (
    WEATHER_FEATURES,
    ForecastModel,
    fit_shared_model_full,
    load_weather_csv,
)
from .model import Battery, dispatch
from .rolling import optimize_window
from .decision import decide


_WORKER_PV_MODEL: ForecastModel | None = None
_WORKER_LOAD_MODEL: ForecastModel | None = None
_WORKER_THREADS: int = 1


def _init_site_worker(pv_model: ForecastModel, load_model: ForecastModel, threads: int = 1) -> None:
    global _WORKER_PV_MODEL, _WORKER_LOAD_MODEL, _WORKER_THREADS
    _WORKER_PV_MODEL = pv_model
    _WORKER_LOAD_MODEL = load_model
    _WORKER_THREADS = threads


def _evaluate_site_task(task: tuple[Any, ...]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if _WORKER_PV_MODEL is None or _WORKER_LOAD_MODEL is None:
        raise RuntimeError("site worker models were not initialized")
    (
        path, train_fraction, initial_soc, capacity_kwh, max_power_w,
        charge_efficiency, degradation_cost_eur_kwh, soc_min_percent,
        terminal_price, horizon, weather_mode, solver,
    ) = task
    path = Path(path)
    raw = load_csv(path)
    forecast_frame = load_weather_csv(path)
    if len(raw) != len(forecast_frame) or not pd.to_datetime(raw.time).equals(pd.to_datetime(forecast_frame.time)):
        raise ValueError(f"{path}: normalized backtest and forecast timelines do not match")
    with threadpool_limits(limits=_WORKER_THREADS or None):
        return _evaluate_site(
        raw, forecast_frame, _WORKER_PV_MODEL, _WORKER_LOAD_MODEL,
        train_fraction=train_fraction, initial_soc=initial_soc,
        capacity_kwh=capacity_kwh, max_power_w=max_power_w,
        charge_efficiency=charge_efficiency,
        degradation_cost_eur_kwh=degradation_cost_eur_kwh,
        soc_min_percent=soc_min_percent, terminal_price=terminal_price,
        horizon=horizon, weather_mode=weather_mode, site=path.stem,
        solver=solver,
    )


def _policy_step(
    battery: Battery,
    flow: str,
    power_w: float,
    row: pd.Series,
    import_price: float,
    export_price: float,
    soc_min_percent: float = 0.0,
    step_hours: float = 1.0,
) -> dict[str, Any]:
    if not np.isfinite(step_hours) or step_hours <= 0:
        raise ValueError("step_hours must be positive and finite")
    # Legacy "hold" input is accepted as the pre-rename idle label (R6 read
    # compatibility); outputs always carry the new "idle" label.
    if flow not in ("charge", "discharge", "idle", "hold") or not np.isfinite(power_w):
        raise ValueError("invalid battery command")
    if flow in ("idle", "hold"):
        flow, power_w = "idle", 0.0
    power_w = float(np.clip(power_w, 0.0, battery.max_power_w))
    if flow == "charge":
        available_kwh = max(0.0, (100.0 - battery.soc) / 100.0 * battery.capacity_kwh)
        # Battery.step applies charge efficiency and caps SOC at 100%. Clip the
        # command first so the recorded power and SOC delta remain consistent.
        if battery.charge_efficiency > 1e-9:
            power_w = min(power_w, available_kwh * 1000.0 / battery.charge_efficiency / step_hours)
        if power_w <= 1e-9:
            flow = "idle"
    if flow == "discharge":
        available_kwh = max(0.0, (battery.soc - soc_min_percent) / 100.0 * battery.capacity_kwh)
        power_w = min(power_w, available_kwh * 1000.0 / step_hours)
        if power_w <= 1e-9:
            flow = "idle"
    soc_before = battery.soc
    battery.step(flow, power_w, hours=step_hours)
    charge_w = power_w if flow == "charge" else 0.0
    discharge_w = power_w if flow == "discharge" else 0.0
    grid_import_w = max(0.0, float(row.load) + charge_w - float(row.pv) - discharge_w)
    grid_export_w = max(0.0, float(row.pv) + discharge_w - float(row.load) - charge_w)
    import_kwh, export_kwh = grid_import_w / 1000.0 * step_hours, grid_export_w / 1000.0 * step_hours
    return {
        "flow": flow,
        "power_w": power_w,
        "soc_before": soc_before,
        "soc_after": battery.soc,
        "grid_import_kwh": import_kwh,
        "grid_export_kwh": export_kwh,
        "cost_eur": import_kwh * import_price - export_kwh * export_price,
        "negative_export_loss_eur": export_kwh * max(0.0, -export_price),
        "throughput_kwh": power_w / 1000.0 * step_hours,
    }


def _weather_horizon(
    frame: pd.DataFrame,
    current_index: int,
    horizon: int,
    mode: str,
) -> pd.DataFrame:
    future = frame.iloc[current_index + 1 : current_index + horizon].copy().reset_index(drop=True)
    if mode == "persistence" and not future.empty:
        current = frame.iloc[current_index]
        for col in WEATHER_FEATURES:
            if col in future.columns and col in frame.columns:
                future[col] = current[col]
    return future


def _evaluate_site(
    raw: pd.DataFrame,
    forecast_frame: pd.DataFrame,
    pv_model: ForecastModel,
    load_model: ForecastModel,
    *,
    train_fraction: float,
    initial_soc: float,
    capacity_kwh: float,
    max_power_w: float,
    charge_efficiency: float,
    degradation_cost_eur_kwh: float,
    soc_min_percent: float,
    terminal_price: float | None,
    horizon: int,
    weather_mode: str,
    site: str,
    solver: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    split = max(1, int(len(raw) * train_fraction))
    if len(raw) - split < 25:
        raise ValueError(f"site {site}: holdout needs at least 25 rows")
    optimized_policy = f"forecast_{solver}_{weather_mode}"
    policy_names = ("rule", optimized_policy)
    batteries = {
        name: Battery(capacity_kwh, initial_soc, max_power_w, charge_efficiency)
        for name in policy_names
    }
    rows: list[dict[str, Any]] = []
    forecast_pv_actual, forecast_pv_pred = [], []
    forecast_load_actual, forecast_load_pred = [], []
    horizon = max(1, int(horizon))

    for index in range(split, len(raw)):
        current = raw.iloc[index]
        end = min(len(raw), index + horizon)
        price_window = raw.iloc[index:end]
        buy_prices = price_window["buy"].to_numpy(dtype=float)
        sell_prices = price_window["sell"].to_numpy(dtype=float)
        future_import = buy_prices[1:].tolist()

        rule_battery = batteries["rule"]
        rule_flow, rule_power, _ = dispatch(
            float(current.buy), float(current.sell), float(current.pv), float(current.load),
            rule_battery.soc, future_import, capacity_kwh, max_power_w,
        )
        rule_result = _policy_step(
            rule_battery, rule_flow, rule_power, current,
            float(current.buy), float(current.sell),
            soc_min_percent,
        )
        rows.append({
            "site": site, "time": current.time, "weather_mode": weather_mode,
            "policy": "rule", **rule_result,
        })

        actual_horizon = end - index
        predicted_pv = np.empty(actual_horizon, dtype=float)
        predicted_load = np.empty(actual_horizon, dtype=float)
        predicted_pv[0], predicted_load[0] = float(current.pv), float(current.load)
        if actual_horizon > 1:
            # Recursive features only depend on the latest 168 observed/predicted
            # target values.  Passing the full expanding history here creates an
            # avoidable O(n^2) copy/conversion cost over a long holdout.
            history_start = max(0, index - 167)
            history = forecast_frame.iloc[history_start : index + 1]
            future_weather = _weather_horizon(forecast_frame, index, actual_horizon, weather_mode)
            pv_path = pv_model.predict(history, future_weather)["prediction"].to_numpy(dtype=float)
            load_path = load_model.predict(history, future_weather)["prediction"].to_numpy(dtype=float)
            predicted_pv[1:] = pv_path
            predicted_load[1:] = load_path
            forecast_pv_actual.append(float(raw.iloc[index + 1].pv))
            forecast_pv_pred.append(float(pv_path[0]))
            forecast_load_actual.append(float(raw.iloc[index + 1].load))
            forecast_load_pred.append(float(load_path[0]))

        lp_battery = batteries[optimized_policy]
        decision = decide(
            price_window["time"], predicted_pv, predicted_load, buy_prices, sell_prices,
            lp_battery.soc,
            capacity_kwh=capacity_kwh,
            max_power_w=max_power_w,
            charge_efficiency=charge_efficiency,
            degradation_cost_eur_kwh=degradation_cost_eur_kwh,
            soc_min=soc_min_percent,
            terminal_price=terminal_price,
            step_hours=1.0,
            solver=solver,
            observed_pv=float(current.pv), observed_load=float(current.load),
        )
        lp_result = _policy_step(
            lp_battery, decision.flow, decision.power_w, current,
            float(current.buy), float(current.sell),
            soc_min_percent,
        )
        rows.append({
            "site": site, "time": current.time, "weather_mode": weather_mode,
            "policy": optimized_policy, "lp_status": decision.status,
            "lp_fallback": decision.fallback, **lp_result,
        })

    forecast_metrics: dict[str, Any] = {"one_step_pairs": len(forecast_pv_actual)}
    if forecast_pv_actual:
        forecast_metrics.update({
            "pv_mae_w": float(mean_absolute_error(forecast_pv_actual, forecast_pv_pred)),
            "pv_rmse_w": float(mean_squared_error(forecast_pv_actual, forecast_pv_pred) ** 0.5),
            "load_mae_w": float(mean_absolute_error(forecast_load_actual, forecast_load_pred)),
            "load_rmse_w": float(mean_squared_error(forecast_load_actual, forecast_load_pred) ** 0.5),
        })
    return rows, forecast_metrics


def _summarize(
    trace: pd.DataFrame,
    initial_soc: float,
    capacity_kwh: float,
    max_power_w: float,
    soc_min_percent: float,
    terminal_price: float | None,
) -> dict[str, Any]:
    summaries = []
    for (weather_mode, policy), group in trace.groupby(["weather_mode", "policy"], sort=True):
        per_site = group.groupby("site").agg(
            cost_eur=("cost_eur", "sum"),
            final_soc=("soc_after", "last"),
            min_soc=("soc_after", "min"),
            max_soc=("soc_after", "max"),
            grid_import_kwh=("grid_import_kwh", "sum"),
            grid_export_kwh=("grid_export_kwh", "sum"),
            negative_export_loss_eur=("negative_export_loss_eur", "sum"),
            throughput_kwh=("throughput_kwh", "sum"),
            max_power_observed_w=("power_w", "max"),
        )
        summary = {
            "weather_mode": weather_mode,
            "policy": policy,
            "sites": int(len(per_site)),
            "decision_steps": int(len(group)),
            "total_cost_eur": float(group.cost_eur.sum()),
            "mean_cost_per_site_eur": float(per_site.cost_eur.mean()),
            "mean_final_soc_percent": float(per_site.final_soc.mean()),
            "minimum_soc_percent": float(per_site.min_soc.min()),
            "maximum_soc_percent": float(per_site.max_soc.max()),
            "total_grid_import_kwh": float(group.grid_import_kwh.sum()),
            "total_grid_export_kwh": float(group.grid_export_kwh.sum()),
            "negative_export_loss_eur": float(group.negative_export_loss_eur.sum()),
            "total_battery_throughput_kwh": float(group.throughput_kwh.sum()),
            "max_power_observed_w": float(per_site.max_power_observed_w.max()),
            "soc_bounds_ok": bool(per_site.min_soc.min() >= soc_min_percent - 1e-6 and per_site.max_soc.max() <= 100.0 + 1e-6),
            "power_bound_ok": bool(per_site.max_power_observed_w.max() <= max_power_w + 1e-6),
            "initial_soc_percent": initial_soc,
            "capacity_kwh": capacity_kwh,
            "max_power_w": max_power_w,
            "soc_min_percent": soc_min_percent,
        }
        terminal_value = (
            float(per_site.final_soc.mean() / 100.0 * capacity_kwh * terminal_price)
            if terminal_price is not None else None
        )
        summary["mean_terminal_inventory_value_eur"] = terminal_value
        summary["total_settled_cost_eur"] = (
            float(group.cost_eur.sum() - group.groupby("site").soc_after.last().sum() / 100.0 * capacity_kwh * terminal_price)
            if terminal_price is not None else None
        )
        if "lp_fallback" in group:
            summary["lp_fallback_steps"] = int(group.lp_fallback.eq(True).sum())
        summaries.append(summary)
    rule_costs = {
        mode: item["total_cost_eur"]
        for item in summaries
        if item["policy"] == "rule"
        for mode in [item["weather_mode"]]
    }
    for item in summaries:
        baseline_cost = rule_costs.get(item["weather_mode"])
        if baseline_cost is not None and item["policy"] != "rule":
            item["cost_savings_vs_rule_eur"] = float(baseline_cost - item["total_cost_eur"])
            item["cost_savings_vs_rule_percent"] = (
                float((baseline_cost - item["total_cost_eur"]) / abs(baseline_cost) * 100.0)
                if baseline_cost > 1e-9 else None
            )
    return {"policies": summaries}


def _aggregate_forecast_metrics(reports: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for mode in sorted({item["weather_mode"] for item in reports}):
        group = [item for item in reports if item["weather_mode"] == mode and item.get("one_step_pairs", 0)]
        count = sum(int(item["one_step_pairs"]) for item in group)
        if not count:
            continue
        result.append({
            "weather_mode": mode,
            "one_step_pairs": count,
            "pv_mae_w": sum(item["pv_mae_w"] * item["one_step_pairs"] for item in group) / count,
            "pv_rmse_w": float(np.sqrt(sum(item["pv_rmse_w"] ** 2 * item["one_step_pairs"] for item in group) / count)),
            "load_mae_w": sum(item["load_mae_w"] * item["one_step_pairs"] for item in group) / count,
            "load_rmse_w": float(np.sqrt(sum(item["load_rmse_w"] ** 2 * item["one_step_pairs"] for item in group) / count)),
        })
    return result


def run_strategy_comparison(
    data_dir: Path,
    output_dir: Path,
    *,
    max_sites: int | None = None,
    site_start: int = 0,
    train_all_sites: bool = False,
    train_fraction: float = 0.8,
    initial_soc: float = 50.0,
    capacity_kwh: float = 14.0,
    max_power_w: float = 5000.0,
    charge_efficiency: float = 0.95,
    degradation_cost_eur_kwh: float = 0.002,
    soc_min_percent: float = 10.0,
    terminal_price: float | None = None,
    horizon: int = 24,
    weather_modes: tuple[str, ...] = ("oracle", "persistence"),
    solver: str = "lp",
    workers: int = 1,
    worker_threads: int = 1,
    resume: bool = False,
) -> dict[str, Any]:
    started = perf_counter()
    all_csvs = sorted(
        data_dir.rglob("*.csv"),
        key=lambda path: (0, int(path.stem)) if path.stem.isdigit() else (1, path.stem),
    )
    if not all_csvs:
        raise FileNotFoundError(f"no CSV files under {data_dir}")
    if site_start < 0 or site_start >= len(all_csvs):
        raise ValueError("site_start must be within the available site range")
    if max_sites is not None and max_sites < 1:
        raise ValueError("max_sites must be positive")
    csvs = all_csvs[site_start: site_start + max_sites if max_sites is not None else None]
    if not csvs:
        raise ValueError("selected site range is empty")
    model_csvs = all_csvs if train_all_sites else csvs
    if not 0.5 <= train_fraction < 1.0:
        raise ValueError("train_fraction must be in [0.5, 1.0)")
    if not weather_modes or len(set(weather_modes)) != len(weather_modes) or any(mode not in ("oracle", "persistence") for mode in weather_modes):
        raise ValueError("weather_modes must contain only oracle and/or persistence")
    if workers < 1:
        raise ValueError("workers must be positive")
    if worker_threads < 0:
        raise ValueError("worker_threads must be nonnegative (0 means library defaults)")
    if max_sites is not None and site_start + max_sites > len(all_csvs):
        raise ValueError("selected site range exceeds available sites")
    if solver not in ("lp", "milp"):
        raise ValueError("solver must be lp or milp")
    output_dir.mkdir(parents=True, exist_ok=True)
    if (output_dir / "strategy_comparison_summary.json").exists():
        raise FileExistsError("Use a new output directory to preserve completed results")
    progress_path = output_dir / "progress.jsonl"
    checkpoint_dir = output_dir / "site_results"
    if any(checkpoint_dir.glob("*.json")) and not resume:
        raise FileExistsError("Partial site results exist: use --resume or a new output directory")
    checkpoint_dir.mkdir(exist_ok=True)

    training_frames = []
    for path in model_csvs:
        frame = load_weather_csv(path)
        split = max(1, int(len(frame) * train_fraction))
        training_frames.append(frame.iloc[:split].reset_index(drop=True))
    pv_model = fit_shared_model_full(training_frames, "pv")
    load_model = fit_shared_model_full(training_frames, "load")
    training_seconds = perf_counter() - started

    tasks = [
        (str(path), train_fraction, initial_soc, capacity_kwh, max_power_w,
         charge_efficiency, degradation_cost_eur_kwh, soc_min_percent,
         terminal_price, horizon, weather_mode, solver)
        for path in csvs for weather_mode in weather_modes
    ]
    manifest = {
        "tasks": [list(task) for task in tasks],
        "model_hash": joblib.hash((pv_model, load_model)),
        "data_sha256": {str(p.resolve()): hashlib.sha256(p.read_bytes()).hexdigest() for p in model_csvs},
        "code_sha256": {name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
                        for name in ("strategy_backtest.py", "forecast.py", "rolling.py", "model.py", "backtest.py", "decision.py")},
    }
    manifest_path = output_dir / "run_manifest.json"
    if resume:
        if not manifest_path.exists() or json.loads(manifest_path.read_text(encoding="utf-8")) != manifest:
            raise ValueError("Resume rejected: model, input, configuration or code differs from the saved run")
    else:
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    completed = [None] * len(tasks)
    if resume:
        for index, task in enumerate(tasks):
            checkpoint = checkpoint_dir / f"{index:04d}_{Path(task[0]).stem}_{task[10]}.json"
            if checkpoint.exists():
                payload = json.loads(checkpoint.read_text(encoding="utf-8"))
                if payload["site"] != Path(task[0]).stem or payload["weather_mode"] != task[10]:
                    raise ValueError("Checkpoint identity mismatch")
                for row in payload["rows"]:
                    row["time"] = pd.Timestamp(row["time"])
                completed[index] = (payload["rows"], payload["forecast_metrics"])

    def record(index, result):
        completed[index] = result
        task = tasks[index]
        site = Path(task[0]).stem
        payload = {"site": site, "weather_mode": task[10], "rows": result[0], "forecast_metrics": result[1]}
        destination = checkpoint_dir / f"{index:04d}_{site}_{task[10]}.json"
        temporary = destination.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, default=str, allow_nan=False), encoding="utf-8")
        temporary.replace(destination)
        count = sum(value is not None for value in completed)
        with progress_path.open("a", encoding="utf-8") as log:
            log.write(json.dumps({"completed": count, "tasks": len(tasks), "site": site,
                                  "elapsed_seconds": perf_counter() - started}) + "\n")
        print(f"completed task {count}/{len(tasks)}: site {site} {task[10]}", flush=True)

    if workers == 1:
        _init_site_worker(pv_model, load_model, worker_threads)
        for task_index, task in enumerate(tasks):
            if completed[task_index] is None:
                record(task_index, _evaluate_site_task(task))
    else:
        with ProcessPoolExecutor(
            max_workers=workers,
            initializer=_init_site_worker,
            initargs=(pv_model, load_model, worker_threads),
        ) as pool:
            futures = {pool.submit(_evaluate_site_task, task): i for i, task in enumerate(tasks) if completed[i] is None}
            for future in as_completed(futures):
                record(futures[future], future.result())

    all_rows: list[dict[str, Any]] = []
    forecast_reports: list[dict[str, Any]] = []
    for task, (rows, forecast_metrics) in zip(tasks, completed):
        all_rows.extend(rows)
        forecast_reports.append({"site": Path(task[0]).stem, "weather_mode": task[10], **forecast_metrics})

    output_dir.mkdir(parents=True, exist_ok=True)
    trace = pd.DataFrame(all_rows)
    trace.to_csv(output_dir / "strategy_comparison_trace.csv", index=False)
    report = _summarize(trace, initial_soc, capacity_kwh, max_power_w, soc_min_percent, terminal_price)
    report.update({
        "sites": len(csvs),
        "site_start": site_start,
        "model_training_sites": len(model_csvs),
        "train_all_sites": train_all_sites,
        "train_fraction": train_fraction,
        "horizon_hours": horizon,
        "step_hours": 1.0,
        "charge_efficiency": charge_efficiency,
        "solver": solver,
        "workers": workers,
        "worker_threads": worker_threads,
        "timing": {"training_and_loading_seconds": training_seconds,
                   "evaluation_and_output_seconds": perf_counter() - started - training_seconds,
                   "total_seconds": perf_counter() - started},
        "evaluated_site_ids": [p.stem for p in csvs],
        "training_site_ids": [p.stem for p in model_csvs],
        "holdout_semantics": f"chronological per-site holdout; decisions use current observed PV/load and execute only first {solver.upper()} action",
        "price_assumption": "the current and next horizon import/export prices are known at decision time",
        "degradation_cost_eur_per_kwh": degradation_cost_eur_kwh,
        "terminal_price_eur_per_kwh": terminal_price,
        "accounting_note": "total_cost_eur is operating import cost minus export credit; total_settled_cost_eur additionally subtracts the internal end-of-test battery inventory valuation when terminal_price is set; neither is official contest settlement",
        "weather_scenarios": {
            "oracle": "future realized weather covariates; diagnostic scenario, not a strict economic upper bound or online-valid result",
            "persistence": "future weather covariates held at latest observed values; sensitivity test, not a validated weather forecast",
        },
        "forecast_metrics": _aggregate_forecast_metrics(forecast_reports),
        "artifacts": {"trace": "strategy_comparison_trace.csv"},
    })
    (output_dir / "strategy_comparison_summary.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return report
