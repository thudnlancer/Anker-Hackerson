"""Frozen-model paired hourly replay: diagnostic, not official contest scoring."""
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from time import perf_counter
import argparse
import hashlib
import json
import sys
import joblib
import numpy as np
import pandas as pd
from threadpoolctl import threadpool_limits

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
from energy_lab.backtest import load_csv
from energy_lab.forecast import WEATHER_FEATURES
from energy_lab.interface_contract import map_battery_action
from energy_lab.model import Battery
from energy_lab.rolling import optimize_window
from energy_lab.strategy_backtest import _policy_step

WEATHER = ["temperature_c", "cloud_cover_percent"]
ARMS = ("persistence", "auxiliary")
ABLATION_ARMS = ("persistence", "pv_only", "load_only", "auxiliary")


def forecast_paths(frame, index, pv_model, load_model, weather_mode="observed_proxy"):
    """Future target columns are excluded, not merely ignored by the model."""
    window = frame.iloc[index:index + 24]
    current = window.iloc[0]
    history = frame.iloc[index - 167:index + 1][["time", "pv", "load", *WEATHER]]
    future = window.iloc[1:][["time", *WEATHER]].copy().reset_index(drop=True)
    if weather_mode == "persistence":
        for field in WEATHER:
            future[field] = float(current[field])
    auxiliary = tuple(np.r_[float(current[target]), model.predict(history, future).prediction.to_numpy()]
                      for target, model in (("pv", pv_model), ("load", load_model)))
    persistence = (np.full(len(window), float(current.pv)),
                   np.full(len(window), float(current.load)))
    return {"persistence": persistence, "auxiliary": auxiliary,
            "pv_only": (auxiliary[0].copy(), persistence[1].copy()),
            "load_only": (persistence[0].copy(), auxiliary[1].copy())}


def decompose_saving(baseline, candidate):
    """Positive values mean improvement; inventory is part of settlement."""
    operating = baseline["cost_eur"] - candidate["cost_eur"]
    settled = baseline["settled_cost_eur"] - candidate["settled_cost_eur"]
    degradation = candidate["degradation_cost_eur"] - baseline["degradation_cost_eur"]
    return {"operating_saving_eur": operating,
            "inventory_contribution_eur": settled - operating,
            "settled_saving_eur": settled,
            "extra_degradation_eur": degradation,
            "operating_saving_after_degradation_eur": operating - degradation,
            "settled_saving_after_degradation_eur": settled - degradation}


def execute(battery, action, current, soc_min):
    flow = str(action.flow)
    power = max(0., float(action.charge_w if flow == "charge" else
                         action.discharge_w if flow == "discharge" else 0.))
    mapping = map_battery_action(flow, power, float(current.pv), float(current.load))
    if not mapping.representable:
        raise ValueError("unrepresentable first action: " + mapping.reason)
    result = _policy_step(battery, flow, power, current, float(current.buy), float(current.sell), soc_min)
    if abs(result["power_w"] - power) > 1e-3:
        raise ValueError("unexpected physical power clipping")
    charge = result["power_w"] if result["flow"] == "charge" else 0.
    discharge = result["power_w"] if result["flow"] == "discharge" else 0.
    energy_error = ((result["soc_after"] - result["soc_before"]) * battery.capacity_kwh / 100
                    - (charge * battery.charge_efficiency - discharge) / 1000)
    grid_error = ((float(current.pv) + discharge - float(current.load) - charge) / 1000
                  + result["grid_import_kwh"] - result["grid_export_kwh"])
    if abs(energy_error) > 1e-6 or abs(grid_error) > 1e-6:
        raise ValueError("energy balance violation")
    if not soc_min - 1e-6 <= result["soc_after"] <= 100 + 1e-6:
        raise ValueError("SOC bounds violation")
    return {**result, "command_target_w": mapping.target_w, "command_reason": mapping.reason,
            "representable": mapping.representable, "energy_error_kwh": energy_error,
            "grid_error_kwh": grid_error}


def evaluate(path, model_dir, output, hours, offset, soc_min, degradation, weather_mode,
             terminal_price=None, ablation=False, solver_time_limit=5.0):
    with threadpool_limits(limits=1):
        started = perf_counter()
        frame = load_csv(path)
        if not frame.time.diff().dropna().eq(pd.Timedelta(hours=1)).all():
            raise ValueError("hourly data has gaps")
        if not np.isfinite(frame[["pv", "load", "buy", "sell", *WEATHER]].to_numpy()).all() or (frame.buy <= 0).any():
            raise ValueError("invalid observations or buy prices")
        models = [joblib.load(model_dir / f"shared_{target}.joblib") for target in ("pv", "load")]
        for model in models:
            if (set(model.features) & set(WEATHER_FEATURES)) - set(WEATHER):
                raise ValueError("model uses unavailable API weather fields")
        start = int(len(frame) * .8) + offset
        stop = start + hours
        if start < 168 or stop + 23 > len(frame):
            raise ValueError("evaluation must fit temporal holdout with complete 24-hour horizons")
        arms = ABLATION_ARMS if ablation else ARMS
        batteries = {arm: Battery(soc=50.) for arm in arms}
        rows, forecast_errors = [], []
        for index in range(start, stop):
            current = frame.iloc[index]
            window = frame.iloc[index:index + 24]
            paths = forecast_paths(frame, index, *models, weather_mode)
            for arm in arms:
                pv, load = paths[arm]
                plan = optimize_window(window.time, pv, load, window.buy.to_numpy(), window.sell.to_numpy(),
                                       batteries[arm].soc, solver="milp", command_contract="sop",
                                       soc_min=soc_min, step_hours=1., terminal_price=terminal_price,
                                       degradation_cost_eur_kwh=degradation, time_limit_seconds=solver_time_limit)
                action = (plan.plan.iloc[0] if plan.status == "optimal" else
                          pd.Series({"flow": "hold", "charge_w": 0., "discharge_w": 0.}))
                result = execute(batteries[arm], action, current, soc_min)
                for lead in (1, 6, 12, 23):
                    realized = frame.iloc[index + lead]
                    forecast_errors.append({"arm": arm, "lead_hours": lead,
                        "pv_abs_error_w": abs(float(realized.pv) - pv[lead]),
                        "load_abs_error_w": abs(float(realized.load) - load[lead]),
                        "net_abs_error_w": abs(float(realized.load-realized.pv) - (load[lead]-pv[lead]))})
                actual = frame.iloc[index + 1]  # scoring only, not decision input
                rows.append({"site": path.stem, "time": current.time, "arm": arm,
                             "solver_status": plan.status, "fallback": plan.status != "optimal",
                             "pv_w": current.pv, "load_w": current.load, "buy": current.buy, "sell": current.sell,
                             "pv_abs_error_w": abs(float(actual.pv) - pv[1]),
                             "load_abs_error_w": abs(float(actual.load) - load[1]), **result})
        trace = pd.DataFrame(rows)
        trace.to_csv(output / f"site_{path.stem}_trace.csv", index=False)
        error_report = pd.DataFrame(forecast_errors).groupby(["arm", "lead_hours"]).mean().reset_index()
        settlement = float(frame.iloc[start:stop].buy.mean())
        report = {"site": path.stem, "steps_per_arm": hours, "start_index": start,
                  "start_time": str(frame.iloc[start].time), "end_time": str(frame.iloc[stop-1].time),
                  "settlement_price_eur_kwh": settlement, "arms": {},
                  "forecast_horizon_metrics": error_report.to_dict(orient="records")}
        for arm in arms:
            part = trace[trace.arm == arm]
            cost = float(part.cost_eur.sum())
            inventory = float(part.soc_after.iloc[-1]) / 100 * 14 * settlement
            throughput = float(part.throughput_kwh.sum())
            report["arms"][arm] = {
                "cost_eur": cost, "settled_cost_eur": cost - inventory,
                "terminal_inventory_value_eur": inventory,
                "degradation_cost_eur": throughput * degradation,
                "settled_cost_with_degradation_eur": cost - inventory + throughput * degradation,
                "final_soc": float(part.soc_after.iloc[-1]),
                "min_soc": float(part.soc_after.min()), "max_soc": float(part.soc_after.max()),
                "throughput_kwh": throughput, "negative_export_loss_eur": float(part.negative_export_loss_eur.sum()),
                "nonoptimal_steps": int(part.fallback.sum()),
                "soc_bounds_ok": bool(part.soc_after.between(soc_min-1e-6, 100+1e-6).all()),
                "power_bounds_ok": bool(part.power_w.between(-1e-6, 5000+1e-6).all()),
                "unrepresentable_steps": int((~part.representable).sum()),
                "max_energy_error_kwh": float(part.energy_error_kwh.abs().max()),
                "pv_one_step_mae_w": float(part.pv_abs_error_w.mean()),
                "load_one_step_mae_w": float(part.load_abs_error_w.mean())}
        report["settled_saving_auxiliary_vs_persistence_eur"] = (
            report["arms"]["persistence"]["settled_cost_eur"] - report["arms"]["auxiliary"]["settled_cost_eur"])
        report["comparisons_vs_persistence"] = {arm: decompose_saving(report["arms"]["persistence"], report["arms"][arm])
                                                for arm in arms if arm != "persistence"}
        report["elapsed_seconds"] = perf_counter() - started
        (output / f"site_{path.stem}_summary.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        return report


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, default=Path("forecast_data"))
    parser.add_argument("--model-dir", type=Path, default=Path("models/online_api"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-sites", type=int, default=20)
    parser.add_argument("--eval-hours", type=int, default=96)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--soc-min", type=float, default=0.)
    parser.add_argument("--degradation-cost", type=float, default=.02)
    parser.add_argument("--weather-mode", choices=("observed_proxy", "persistence"), default="observed_proxy")
    parser.add_argument("--terminal-price", type=float, default=None,
                        help="optimizer horizon-end inventory value only; final accounting always uses evaluation mean buy price")
    parser.add_argument("--solver-time-limit", type=float, default=5.0,
                        help="per rolling MILP time limit in seconds")
    parser.add_argument("--ablation", action="store_true", help="also compare PV-only and load-only model inputs")
    args = parser.parse_args()
    if min(args.max_sites, args.eval_hours, args.workers) < 1 or args.offset < 0:
        parser.error("positive sites/hours/workers and nonnegative offset required")
    if not np.isfinite(args.solver_time_limit) or args.solver_time_limit <= 0:
        parser.error("solver-time-limit must be positive and finite")
    if not np.isfinite([args.soc_min, args.degradation_cost]).all() or not 0 <= args.soc_min <= 50 or args.degradation_cost < 0:
        parser.error("invalid SOC bound or degradation parameter")
    if args.terminal_price is not None and not np.isfinite(args.terminal_price):
        parser.error("terminal-price must be finite")
    if args.output.exists():
        raise FileExistsError("Use a new output directory")
    paths = sorted(args.data.glob("*.csv"), key=lambda p: int(p.stem))[:args.max_sites]
    if len(paths) != args.max_sites:
        raise ValueError("requested sites unavailable")
    args.output.mkdir(parents=True)
    manifest = {"parameters": vars(args), "code_sha256": sha(Path(__file__)),
                "data_sha256": {p.name: sha(p) for p in paths},
                "model_sha256": {target: sha(args.model_dir / f"shared_{target}.joblib") for target in ("pv", "load")}}
    (args.output / "manifest.json").write_text(json.dumps(manifest, default=str, indent=2), encoding="utf-8")
    started, reports = perf_counter(), []
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(evaluate, p, args.model_dir, args.output, args.eval_hours, args.offset,
                               args.soc_min, args.degradation_cost, args.weather_mode, args.terminal_price,
                               args.ablation, args.solver_time_limit) for p in paths]
        for future in as_completed(futures):
            report = future.result()
            reports.append(report)
            print(json.dumps({"completed": len(reports), "sites": len(paths), "site": report["site"],
                              "settled_saving_eur": report["settled_saving_auxiliary_vs_persistence_eur"]}), flush=True)
    reports.sort(key=lambda r: int(r["site"]))
    totals = {}
    arms = ABLATION_ARMS if args.ablation else ARMS
    for arm in arms:
        items = [r["arms"][arm] for r in reports]
        totals[arm] = {key: sum(item[key] for item in items) for key in (
            "cost_eur", "settled_cost_eur", "settled_cost_with_degradation_eur", "throughput_kwh",
            "negative_export_loss_eur", "nonoptimal_steps", "unrepresentable_steps",
            "terminal_inventory_value_eur", "degradation_cost_eur")}
        totals[arm].update({key: all(item[key] for item in items) for key in ("soc_bounds_ok", "power_bounds_ok")})
        totals[arm].update({key: float(np.mean([item[key] for item in items]))
                           for key in ("pv_one_step_mae_w", "load_one_step_mae_w")})
    saving = totals["persistence"]["settled_cost_eur"] - totals["auxiliary"]["settled_cost_eur"]
    summary = {"mode": "offline_frozen_model_diagnostic", "writes_performed": 0,
               "sites": reports, "aggregate": totals, "settled_saving_eur": saving,
               "auxiliary_better_sites": sum(r["settled_saving_auxiliary_vs_persistence_eur"] > 1e-6 for r in reports),
               "elapsed_seconds": perf_counter()-started,
               "limits": ["No retraining; existing model trained on first 80% of all 100 sites. Not unseen-site validation.",
                          "Legacy clip_max used full-series maxima, so this is exploratory, not a leakage-free certification.",
                          "Future historical weather is an observed proxy, not archived API-issued forecasts; persistence mode is a sensitivity arm.",
                          "Hourly mean powers and hourly replanning; no claim about real 15-minute transients.",
                          "Settlement uses evaluation-period mean buy price only for final accounting, never decisions.",
                          "Hold means zero battery flow in this simulator, not verified device mode behavior.",
                          "API irradiance-calibrated production path is not replaced by this diagnostic."]}
    summary["comparisons_vs_persistence"] = {arm: decompose_saving(totals["persistence"], totals[arm])
                                             for arm in arms if arm != "persistence"}
    summary["forecast_horizon_metrics"] = pd.DataFrame([
        row for site in reports for row in site["forecast_horizon_metrics"]
    ]).groupby(["arm", "lead_hours"]).mean().reset_index().to_dict(orient="records")
    (args.output / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps({"aggregate": totals, "settled_saving_eur": saving}, indent=2))


if __name__ == "__main__":
    main()
