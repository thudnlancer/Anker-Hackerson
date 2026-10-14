"""168-hour, self-use-relative offline strategy experiments; never controls HA."""
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from time import perf_counter
import argparse
import hashlib
import json
import sys

import numpy as np
import pandas as pd
from threadpoolctl import threadpool_limits

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
from energy_lab.backtest import load_csv
from energy_lab.contest_replay import (
    aggregate_sites, execute_command, power_inputs, select_guarded, window_metrics,
)
from energy_lab.model import Battery
from energy_lab.rolling import optimize_window
from energy_lab.validation import audit_trace

IDLE = pd.Series({"flow": "idle", "charge_w": 0., "discharge_w": 0.})
PUBLISHED = ["time", "buy", "sell", "shortwave_radiation_wm2"]
STAGES = {1: ("self_consumption", "persistence"),
          2: ("self_consumption", "persistence", "irradiance"),
          3: ("self_consumption", "persistence", "irradiance", "load_upper", "guarded"),
          4: ("self_consumption", "persistence", "irradiance", "hybrid")}


def pv_forecast(paths, published, arm):
    if arm == "persistence":
        return paths["persistence_pv"]
    if arm == "irradiance":
        return paths["irradiance_pv"]
    if arm == "hybrid":
        if not paths.get("calibration") or paths["calibration"].get("samples", 0) < 3:
            return paths["persistence_pv"].copy()
        hybrid = paths["irradiance_pv"].copy()
        # The current measurement is always authoritative; the next device
        # hour stays persistence because D2 did not establish a 1h benefit.
        hybrid[:min(2, len(hybrid))] = paths["persistence_pv"][:min(2, len(hybrid))]
        return hybrid
    raise ValueError("unknown PV forecast arm")


def visible_window(raw, index, start, end, terminal_mode="a5_eval_start_mean"):
    """Shrink at contest end and calculate a declared terminal-price estimate.

    ``a5_eval_start_mean`` preserves the historical A5 diagnostic.  The
    causal ``current_visible_mean`` uses only the price points currently
    published by the API at this decision tick. ``zero`` is a sensitivity
    ablation with no terminal inventory credit.
    """
    stop = min(index + 24, end)
    published = raw.iloc[index:stop][PUBLISHED].reset_index(drop=True)
    if terminal_mode == "a5_eval_start_mean":
        terminal_estimate = float(raw.iloc[start:stop].buy.mean())
    elif terminal_mode == "current_visible_mean":
        terminal_estimate = float(published.buy.mean())
    elif terminal_mode == "zero":
        terminal_estimate = 0.0
    else:
        raise ValueError("unknown terminal price mode")
    return published, terminal_estimate


def evaluate(path, output, stage, offset, wear, threshold, solver_time_limit,
             hold_mode="self_consumption", terminal_mode="a5_eval_start_mean"):
    with threadpool_limits(limits=1):
        started = perf_counter()
        raw = load_csv(path)
        values = raw[["pv", "load", "buy", "sell", "shortwave_radiation_wm2"]].to_numpy()
        if (not np.isfinite(values).all() or (raw.buy <= 0).any()
                or (raw[["pv", "load", "shortwave_radiation_wm2"]] < 0).any().any()
                or raw.time.isna().any()
                or not raw.time.diff().dropna().eq(pd.Timedelta(hours=1)).all()):
            raise ValueError("invalid or discontinuous hourly source")
        start = int(len(raw) * .8) + offset
        end = start + 168
        if start < 168 or end > len(raw):
            raise ValueError("full 168-hour evaluation plus prior 168-hour history required")
        batteries = {arm: Battery(soc=50.) for arm in STAGES[stage]}
        records = []
        for index in range(start, end):
            current = raw.iloc[index]
            published, terminal = visible_window(raw, index, start, end, terminal_mode)
            paths = power_inputs(current, raw.iloc[index-168:index], published)

            def solve(pv, load, soc):
                return optimize_window(
                    published.time, pv, load, published.buy.to_numpy(), published.sell.to_numpy(),
                    soc, solver="milp", command_contract="sop", soc_min=0., step_hours=1.,
                    terminal_price=terminal, degradation_cost_eur_kwh=wear,
                    time_limit_seconds=solver_time_limit)

            for arm, battery in batteries.items():
                pv = pv_forecast(paths, published, arm) if arm in ("persistence", "irradiance", "hybrid") else paths["irradiance_pv"]
                load = paths["interval_load" if arm == "load_upper" else "current_load"]
                gate_accepted, margin = False, None
                status, nonoptimal, fallback = "not_applicable", 0, False
                if arm == "self_consumption":
                    action = IDLE
                else:
                    plan = solve(pv, load, battery.soc)
                    nonoptimal += int(plan.status != "optimal")
                    if arm == "guarded":
                        candidate = solve(pv, paths["interval_load"], battery.soc)
                        nonoptimal += int(candidate.status != "optimal")
                        gate_accepted, margin = select_guarded(
                            plan, candidate, published, paths, battery.soc, terminal, wear, threshold, hold_mode)
                        if gate_accepted:
                            plan, load = candidate, paths["interval_load"]
                    status = plan.status
                    fallback = status != "optimal"
                    action = IDLE if fallback else plan.plan.iloc[0]
                # Baseline and failure fallback never inherit diagnostic idle semantics.
                execution_hold_mode = "self_consumption" if arm == "self_consumption" or fallback else hold_mode
                step = execute_command(battery, current, action, pv[0], load[0], hold_mode=execution_hold_mode)
                records.append({"site": path.stem, "time": current.time, "arm": arm,
                                "solver_status": status, "nonoptimal_solves": nonoptimal,
                                "fallback": fallback, "gate_accepted": gate_accepted,
                                "gate_worst_gain_eur": margin,
                                "horizon_hours": len(published), "terminal_estimate": terminal,
                                "calibration_samples": (paths["calibration"] or {}).get("samples", 0),
                                "pv_w": float(current.pv), "load_w": float(current.load),
                                "buy": float(current.buy), "sell": float(current.sell), **step})
        trace = pd.DataFrame(records)
        physics_audit = audit_trace(trace.rename(columns={"arm": "policy"}), raw, soc_min=0.)
        realized = raw.iloc[start:end]
        settlement = float(realized.buy.mean())
        k = float((realized.load / 1000 * realized.buy).sum())
        report = {"site": path.stem, "start_index": start, "hours": 168,
                  "successful_plan_idle_mode": hold_mode,
                  "accounting_arm": "zero_flow" if hold_mode == "zero_flow" else "self_use_diagnostic",
                  "start_time": str(realized.time.iloc[0]), "end_time": str(realized.time.iloc[-1]),
                  "k_eur": k, "settlement_price_eur_kwh": settlement,
                  "terminal_price_mode": terminal_mode,
                  "arms": window_metrics(trace, settlement, k, wear), "physical_audit": physics_audit,
                  "guard_accepted_steps": int(trace.gate_accepted.sum()),
                  "elapsed_seconds": perf_counter() - started}
        trace.to_csv(output / f"site_{path.stem}_trace.csv", index=False)
        (output / f"site_{path.stem}_summary.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        return report


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=Path("forecast_data"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--stage", type=int, choices=(1, 2, 3, 4), default=3)
    parser.add_argument("--max-sites", type=int, default=20)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--wear", type=float, default=.02,
                        help="internal objective/accounting cost, NOT an official score deduction")
    parser.add_argument("--gate-threshold", type=float, default=.02,
                        help="minimum worst-scenario plan improvement in EUR; exploratory, not tuned")
    parser.add_argument("--solver-time-limit", type=float, default=5.)
    parser.add_argument("--hold-mode", choices=("self_consumption", "zero_flow"), default="self_consumption",
                        help="successful-plan idle behavior only; baseline and failure fallback always self-consume")
    parser.add_argument("--terminal-price-mode", choices=("a5_eval_start_mean", "current_visible_mean", "zero"),
                        default="a5_eval_start_mean",
                        help="diagnostic terminal inventory valuation rule; current_visible_mean is causal")
    args = parser.parse_args()
    if min(args.max_sites, args.workers) < 1 or args.offset < 0:
        parser.error("positive sites/workers and nonnegative offset required")
    if (not np.isfinite([args.wear, args.gate_threshold, args.solver_time_limit]).all()
            or min(args.wear, args.gate_threshold) < 0 or args.solver_time_limit <= 0):
        parser.error("invalid wear/threshold/solver limit")
    paths = sorted(args.data.glob("*.csv"), key=lambda p: int(p.stem))[:args.max_sites]
    if len(paths) != args.max_sites:
        raise ValueError("requested sites unavailable")
    args.output.mkdir(parents=True, exist_ok=False)
    sources = [Path(__file__), *Path("src/energy_lab").glob("*.py")]
    manifest = {"parameters": vars(args), "data_sha256": {str(p): digest(p) for p in paths},
                "source_sha256": {str(p): digest(p) for p in sources},
                "no_model_training": True, "initial_soc": 50., "hours": 168,
                "weather_source": "future_observed_shortwave_proxy_not_archived_API_forecast"}
    (args.output / "manifest.json").write_text(json.dumps(manifest, default=str, indent=2), encoding="utf-8")
    reports, started = [], perf_counter()
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(evaluate, p, args.output, args.stage, args.offset, args.wear,
                               args.gate_threshold, args.solver_time_limit, args.hold_mode,
                               args.terminal_price_mode) for p in paths]
        for future in as_completed(futures):
            report = future.result()
            reports.append(report)
            print(json.dumps({"completed": len(reports), "sites": len(paths), "site": report["site"],
                              "scores": {k: v["saving_settled_percent_estimate"] for k, v in report["arms"].items()}}), flush=True)
    reports.sort(key=lambda r: int(r["site"]))
    summary = {"mode": "offline_contest_formula_estimate", "writes_performed": 0,
               "successful_plan_idle_mode": args.hold_mode,
               "terminal_price_mode": args.terminal_price_mode,
               "accounting_arm": "zero_flow" if args.hold_mode == "zero_flow" else "self_use_diagnostic",
               "official_score_claim": False,
               "sites": reports, "aggregate": aggregate_sites(reports),
               "elapsed_seconds": perf_counter()-started,
               "limits": [
                   "Not official scoring: 168-hour hourly replay, 50% initial SOC assumption, no simulator ledger reconciliation.",
                   "Aggregate is sum savings / sum K, not average of site percentages; these practice sites are not the live 3 stations.",
                   "Shortwave observations proxy issued API irradiance forecasts; real forecast errors/updates not reproduced.",
                   "Calibration uses preceding 168 observed hours only; no shared ML model or retraining.",
                   "Successful-plan idle mode is explicit; baseline/failure fallback always self-consume. Zero_flow is offline-only, not hardware-verified.",
                   "Guard checks fixed plans under three device-hour historical load quantile profiles, NOT robust receding-horizon guarantees.",
                   "No curtailment modeled; unavoidable negative-price PV export may remain when storage is full.",
                   "Wear is a separate internal cost, not subtracted from estimated official-formula percentage.",
                   "Current hourly averages proxy HA readings; no 15-minute transients, actuation latency or real readback."]}
    (args.output / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary["aggregate"], indent=2), flush=True)


if __name__ == "__main__":
    main()
