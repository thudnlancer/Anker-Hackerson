"""Replay an API-first strategy without training station-specific predictors.

The replay exposes future prices and weather metadata, but only the current
PV/load observation is supplied to the strategy.  Future PV/load values are
used solely by the simulator when applying the command and by the oracle
reference.  No network or HA calls are made.
"""

from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
import argparse
import json
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from energy_lab.backtest import load_csv
from energy_lab.interface_contract import map_battery_action
from energy_lab.model import Battery
from energy_lab.rolling import optimize_window
from energy_lab.system_model import calibrate_pv_scale, pv_from_irradiance


def _grid_cost(pv: float, load: float, charge: float, discharge: float,
               buy: float, sell: float, step_hours: float = 1.0) -> tuple[float, float, float]:
    grid_import = max(0.0, load + charge - pv - discharge)
    grid_export = max(0.0, pv + discharge - load - charge)
    cost = (grid_import * buy - grid_export * sell) / 1000.0 * step_hours
    return cost, grid_import / 1000.0 * step_hours, grid_export / 1000.0 * step_hours


def _self_consumption_step(battery: Battery, pv: float, load: float,
                           buy: float, sell: float) -> dict:
    surplus = pv - load
    if surplus > 0:
        charge = min(battery.max_power_w, surplus)
        battery.step("charge", charge)
        discharge = 0.0
    elif surplus < 0:
        charge = 0.0
        discharge = min(battery.max_power_w, -surplus)
        battery.step("discharge", discharge)
    else:
        charge = discharge = 0.0
    cost, grid_import, grid_export = _grid_cost(pv, load, charge, discharge, buy, sell)
    return {"cost_eur": cost, "grid_import_kwh": grid_import, "grid_export_kwh": grid_export,
            "charge_w": charge, "discharge_w": discharge, "soc_after": battery.soc}


def _apply_action(battery: Battery, row: pd.Series, action: pd.Series, *,
                  planned_pv: float | None = None, planned_load: float | None = None) -> dict:
    flow = str(action.flow)
    planned_power = float(action.charge_w if flow == "charge" else action.discharge_w if flow == "discharge" else 0.0)
    forecast_pv = float(row.pv if planned_pv is None else planned_pv)
    forecast_load = float(row.load if planned_load is None else planned_load)
    # Idle steps execute as zero battery flow in this simulator; the command
    # mapping must name the unverified zero-flow candidate explicitly.
    command = map_battery_action(flow, planned_power, forecast_pv, forecast_load,
                                 idle_mode="zero_flow" if flow in ("idle", "hold") else None)
    if not command.representable:
        raise ValueError(f"unrepresentable SOP action: {command.reason}")
    # SOP ``discharge`` is a grid-export target. Forecast error changes the
    # battery-side power needed to hit that target at execution time.
    power = (min(battery.max_power_w,
                 max(0.0, float(command.target_w) - (float(row.pv) - float(row.load))))
             if flow == "discharge" else planned_power)
    before = battery.soc
    battery.step(flow, power)
    cost, grid_import, grid_export = _grid_cost(float(row.pv), float(row.load),
                                                 power if flow == "charge" else 0.0,
                                                 power if flow == "discharge" else 0.0,
                                                 float(row.buy), float(row.sell))
    return {"flow": flow, "power_w": power, "command_target_w": float(command.target_w or 0.0),
            "command_reason": command.reason, "soc_before": before, "soc_after": battery.soc,
            "cost_eur": cost, "grid_import_kwh": grid_import, "grid_export_kwh": grid_export,
            "charge_w": power if flow == "charge" else 0.0,
            "discharge_w": power if flow == "discharge" else 0.0}


def evaluate_site(path: Path, *, eval_hours: int, horizon: int, initial_soc: float,
                  solver: str, soc_min: float, load_quantile: float,
                  calibrate_weather: bool) -> tuple[pd.DataFrame, dict]:
    raw = load_csv(path)
    if len(raw) <= 168 + eval_hours:
        raise ValueError(f"{path}: need at least 168 + {eval_hours} rows")
    start = 168
    end = min(len(raw), start + eval_hours)
    settlement_price = float(raw.iloc[start:end].buy.mean())
    api_battery = Battery(soc=initial_soc)
    baseline_battery = Battery(soc=initial_soc)
    oracle_battery = Battery(soc=initial_soc)
    unrepresentable_steps = 0
    calibration = None
    calibration_source = "disabled"
    if calibrate_weather:
        try:
            # The training CSV has shortwave radiation rather than the live
            # API's solar_irradiance field. This is an explicitly labelled
            # offline proxy, not a production field alias.
            calibration = calibrate_pv_scale(raw.iloc[:168].shortwave_radiation_wm2,
                                             raw.iloc[:168].pv)
            calibration_source = "shortwave_radiation_proxy"
        except (AttributeError, KeyError, ValueError):
            calibration_source = "unavailable_fallback_current_pv"
    rows: list[dict] = []
    for index in range(start, end):
        row = raw.iloc[index]
        window = raw.iloc[index:min(len(raw), index + horizon)]
        # API-first: future prices and weather are available. Future PV uses
        # an explicitly calibrated offline irradiance proxy when enabled;
        # future load uses a past-only upper quantile, never future labels.
        if calibration is not None:
            irradiance = window.shortwave_radiation_wm2.to_numpy(dtype=float)
            predicted_pv = np.asarray([pv_from_irradiance(value, calibration)
                                       for value in irradiance], dtype=float)
            predicted_pv[0] = float(row.pv)
        else:
            predicted_pv = np.full(len(window), float(row.pv), dtype=float)
        past_load = raw.iloc[max(0, index - 168):index].load.to_numpy(dtype=float)
        load_reference = float(np.quantile(past_load, load_quantile)) if len(past_load) else float(row.load)
        predicted_load = np.full(len(window), max(float(row.load), load_reference), dtype=float)
        plan = optimize_window(
            window.time, predicted_pv, predicted_load,
            window.buy.to_numpy(dtype=float), window.sell.to_numpy(dtype=float),
            api_battery.soc, capacity_kwh=14.0, max_power_w=5000.0,
            charge_efficiency=0.95, terminal_price=float(window.buy.mean()),
            degradation_cost_eur_kwh=0.002, soc_min=soc_min, soc_max=100.0,
            step_hours=1.0, solver=solver, command_contract="sop" if solver == "milp" else "battery",
        )
        if plan.status != "optimal":
            action = pd.Series({"flow": "idle", "charge_w": 0.0, "discharge_w": 0.0})
            status = plan.status
        else:
            action = plan.plan.iloc[0]
            status = "optimal"
        try:
            api = _apply_action(api_battery, row, action,
                                planned_pv=float(predicted_pv[0]), planned_load=float(predicted_load[0]))
        except ValueError as exc:
            # An action planned from forecasts may be impossible to express at
            # the actual grid point. Reject it and use an explicit idle; never
            # silently clamp it into a different command.
            unrepresentable_steps += 1
            api = _apply_action(api_battery, row,
                                pd.Series({"flow": "idle", "charge_w": 0.0, "discharge_w": 0.0}))
            api["rejected_action_reason"] = str(exc)
        baseline = _self_consumption_step(baseline_battery, float(row.pv), float(row.load),
                                           float(row.buy), float(row.sell))
        # Oracle is a diagnostic upper bound only; it sees realized future PV/load.
        oracle_plan = optimize_window(
            window.time, window.pv.to_numpy(dtype=float), window.load.to_numpy(dtype=float),
            window.buy.to_numpy(dtype=float), window.sell.to_numpy(dtype=float),
            oracle_battery.soc, capacity_kwh=14.0, max_power_w=5000.0,
            charge_efficiency=0.95, terminal_price=float(window.buy.mean()),
            degradation_cost_eur_kwh=0.002, soc_min=soc_min, soc_max=100.0,
            step_hours=1.0, solver=solver, command_contract="sop" if solver == "milp" else "battery",
        )
        oracle_action = oracle_plan.plan.iloc[0] if oracle_plan.status == "optimal" else pd.Series({"flow": "idle", "charge_w": 0.0, "discharge_w": 0.0})
        oracle = _apply_action(oracle_battery, row, oracle_action,
                               planned_pv=float(window.pv.iloc[0]), planned_load=float(window.load.iloc[0]))
        rows.append({"site": path.stem, "time": row.time, "solver_status": status,
                     "api_first_mode": "calibrated_weather_load_quantile" if calibration is not None else "current_pv_load_quantile",
                     "calibration_source": calibration_source, "load_reference_w": load_reference,
                     **{f"api_{k}": v for k, v in api.items()},
                     **{f"baseline_{k}": v for k, v in baseline.items()},
                     **{f"oracle_{k}": v for k, v in oracle.items()}})
    trace = pd.DataFrame(rows)
    summary = {"site": path.stem, "steps": len(trace),
               "api_first_cost_eur": float(trace.api_cost_eur.sum()),
               "baseline_cost_eur": float(trace.baseline_cost_eur.sum()),
               "oracle_cost_eur": float(trace.oracle_cost_eur.sum()),
               "api_first_saving_vs_baseline_eur": float(trace.baseline_cost_eur.sum() - trace.api_cost_eur.sum()),
               "oracle_saving_vs_baseline_eur": float(trace.baseline_cost_eur.sum() - trace.oracle_cost_eur.sum()),
               "api_first_final_soc": float(trace.api_soc_after.iloc[-1]),
               "baseline_final_soc": float(trace.baseline_soc_after.iloc[-1]),
               "oracle_final_soc": float(trace.oracle_soc_after.iloc[-1]),
               "nonoptimal_steps": int((trace.solver_status != "optimal").sum()),
               "unrepresentable_steps": unrepresentable_steps, "soc_min_percent": soc_min,
               "load_quantile": load_quantile, "calibration_source": calibration_source}
    trace_prefixes = {"api_first": "api", "baseline": "baseline", "oracle": "oracle"}
    for prefix, trace_prefix in trace_prefixes.items():
        operating = float(trace[f"{trace_prefix}_cost_eur"].sum())
        final_soc = float(trace[f"{trace_prefix}_soc_after"].iloc[-1])
        summary[f"{prefix}_settled_cost_eur"] = operating - final_soc / 100.0 * 14.0 * settlement_price
    summary["settlement_price_eur_per_kwh"] = settlement_price
    summary["api_first_settled_saving_vs_baseline_eur"] = (
        summary["baseline_settled_cost_eur"] - summary["api_first_settled_cost_eur"])
    summary["oracle_settled_saving_vs_baseline_eur"] = (
        summary["baseline_settled_cost_eur"] - summary["oracle_settled_cost_eur"])
    return trace, summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, default=Path("forecast_data"))
    parser.add_argument("--output", type=Path, default=Path("outputs/api_first_replay_pilot"))
    parser.add_argument("--max-sites", type=int, default=5)
    parser.add_argument("--eval-hours", type=int, default=168)
    parser.add_argument("--horizon", type=int, default=24)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--solver", choices=("lp", "milp"), default="milp")
    parser.add_argument("--soc-min", type=float, default=10.0)
    parser.add_argument("--load-quantile", type=float, default=0.75)
    parser.add_argument("--calibrate-weather", action="store_true")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    paths = sorted(args.data.glob("*.csv"), key=lambda p: int(p.stem))[:args.max_sites]
    jobs = [(path, args.eval_hours, args.horizon, args.solver) for path in paths]
    results = []
    with ProcessPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = {pool.submit(evaluate_site, path, eval_hours=hours, horizon=horizon,
                               initial_soc=50.0, solver=solver, soc_min=args.soc_min,
                               load_quantile=args.load_quantile,
                               calibrate_weather=args.calibrate_weather): path
                   for path, hours, horizon, solver in jobs}
        for future in as_completed(futures):
            trace, summary = future.result()
            trace.to_csv(args.output / f"site_{summary['site']}_trace.csv", index=False)
            results.append(summary)
            print(json.dumps(summary, ensure_ascii=False), flush=True)
    results.sort(key=lambda x: int(x["site"]))
    report = {"experiment": "api_first_replay", "data_role": "100-site training data proxy; not contest history",
              "strategy_inputs": ["future today/tomorrow-equivalent prices",
                                  "past-only load quantile",
                                  "current SOC",
                                  "calibrated offline irradiance proxy" if args.calibrate_weather else "current PV persistence"],
              "future_realized_pv_load_used_for_decision": False, "horizon_hours": args.horizon,
              "evaluation_hours_per_site": args.eval_hours, "solver": args.solver,
              "soc_min_percent": args.soc_min, "load_quantile": args.load_quantile,
              "calibrate_weather": args.calibrate_weather,
              "sites": results, "aggregate": {
                  "sites": len(results),
                  "api_first_cost_eur": sum(x["api_first_cost_eur"] for x in results),
                  "baseline_cost_eur": sum(x["baseline_cost_eur"] for x in results),
                  "oracle_cost_eur": sum(x["oracle_cost_eur"] for x in results),
                  "api_first_saving_vs_baseline_eur": sum(x["api_first_saving_vs_baseline_eur"] for x in results),
                  "oracle_saving_vs_baseline_eur": sum(x["oracle_saving_vs_baseline_eur"] for x in results),
                  "api_first_settled_cost_eur": sum(x["api_first_settled_cost_eur"] for x in results),
                  "baseline_settled_cost_eur": sum(x["baseline_settled_cost_eur"] for x in results),
                  "oracle_settled_cost_eur": sum(x["oracle_settled_cost_eur"] for x in results),
                  "api_first_settled_saving_vs_baseline_eur": sum(x["api_first_settled_saving_vs_baseline_eur"] for x in results),
                  "oracle_settled_saving_vs_baseline_eur": sum(x["oracle_settled_saving_vs_baseline_eur"] for x in results),
                  "unrepresentable_steps": sum(x["unrepresentable_steps"] for x in results),
              },
              "limits": ["Training CSV future PV/load are used only for simulator outcomes and oracle diagnostic.",
                         "This is not an official score and does not use contest/history.",
                         "No network requests or HA writes are performed."]}
    (args.output / "summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report["aggregate"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
