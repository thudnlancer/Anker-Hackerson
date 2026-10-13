"""C1 case library: limit_reached failure snapshot feature analysis.

Read-only diagnosis of the irradiance pilot failures. Three lenses:
1. Shadow replay of each failure at multiple budgets (evaluate_snapshot path).
2. Exact live-path replay (evaluate_pair_snapshot with causal calibration pairs).
3. Deep MILP diagnostics for intrinsically hard cases (gap, nodes, LP relaxation).

No network, no HA writes. Historical snapshots only.
"""
import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.optimize import milp as scipy_milp

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
from energy_lab import rolling, shadow  # noqa: E402
from energy_lab.interface_contract import align_hourly_points  # noqa: E402
from run_irradiance_shadow import evaluate_pair_snapshot  # noqa: E402


def price_window_features(prices_payload, device_time, device_timezone):
    points = shadow.daily_points(prices_payload)
    tick = pd.Timestamp(device_time)
    if tick.tzinfo is None and device_timezone is not None:
        tick = tick.tz_localize(device_timezone)
    horizon = 24 if (tick.minute == 0 and tick.second == 0) else 25
    aligned = align_hourly_points(points, tick, horizon=horizon)
    imp = aligned["import_price"].to_numpy(dtype=float)
    exp = aligned["export_price"].to_numpy(dtype=float)
    spread = imp - exp
    return {
        "import_min": float(imp.min()), "import_max": float(imp.max()),
        "export_min": float(exp.min()), "export_max": float(exp.max()),
        "import_ptp": float(imp.max() - imp.min()),
        "export_ptp": float(exp.max() - exp.min()),
        "spread_min": float(spread.min()),
        "negative_import_hours": int((imp < 0).sum()),
        "negative_export_hours": int((exp < 0).sum()),
        "flat_import_runs_ge6h": _longest_run(imp, 6),
        "flat_export_runs_ge6h": _longest_run(exp, 6),
    }


def _longest_run(arr, min_len):
    best = run = 1
    for a, b in zip(arr, arr[1:]):
        run = run + 1 if a == b else 1
        best = max(best, run)
    return best if best >= min_len else 0


def state_features(state):
    return {
        "soc": float(state["soc"]),
        "pv_w": float(state["solar_power"]),
        "load_w": float(state["home_load"]),
        "net_w": float(state["solar_power"]) - float(state["home_load"]),
        "battery_charging_w": float(state["battery_charging_power_w"]),
        "battery_discharging_w": float(state["battery_discharging_power_w"]),
        "grid_import_w": float(state["grid_import_power_w"]),
        "grid_export_w": float(state["grid_export_power_w"]),
        "operating_mode": state.get("operating_mode"),
    }


class MilpProbe:
    """Wrap energy_lab.rolling.milp to capture the raw scipy result.

    Diagnosis only; production code untouched. Also supports integrality
    override (LP relaxation) and mip_rel_gap override as *diagnostic* probes.
    """

    def __init__(self, gap_override=None, relax=False):
        self.records = []
        self.gap_override = gap_override
        self.relax = relax

    def __enter__(self):
        original = rolling.milp

        def wrapper(c, integrality, bounds, constraints, options):
            opts = dict(options)
            if self.gap_override is not None:
                opts["mip_rel_gap"] = self.gap_override
            integ = np.zeros_like(integrality) if self.relax else integrality
            t0 = time.perf_counter()
            result = original(c, integrality=integ, bounds=bounds,
                              constraints=constraints, options=opts)
            rec = {"wall_s": round(time.perf_counter() - t0, 3),
                   "status": result.status, "success": bool(result.success),
                   "message": str(result.message),
                   "fun": float(result.fun) if result.fun is not None else None,
                   "mip_node_count": getattr(result, "mip_node_count", None),
                   "mip_gap": getattr(result, "mip_gap", None),
                   "mip_dual_bound": getattr(result, "mip_dual_bound", None),
                   "n_vars": len(c), "n_constraints": constraints.A.shape[0]}
            self.records.append(rec)
            return result

        rolling.milp = wrapper
        return self

    def __exit__(self, *exc):
        rolling.milp = scipy_milp
        return False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pilot-dir", default="outputs/irradiance_post_reboot_20261013")
    parser.add_argument("--budgets", default="1,5,30,120", help="comma-separated solver seconds")
    parser.add_argument("--live-budget", type=float, default=2.0,
                        help="budget used by the live pilot (argparse default)")
    parser.add_argument("--skip-deep", action="store_true",
                        help="skip the 300s deep MILP probes (reuse previous results)")
    parser.add_argument("--output", default=None)
    args = parser.parse_args()

    pilot = Path(args.pilot_dir)
    result = json.loads((pilot / "result.json").read_text(encoding="utf-8"))
    budgets = [float(x) for x in args.budgets.split(",")]

    failures = []
    for cycle in result["cycles"]:
        for site, r in cycle["stations"].items():
            status = r.get("solver_status") or r.get("status")
            if status != "optimal":
                failures.append({"iteration": cycle["iteration"], "site": site,
                                 "device_time": r.get("device_time"),
                                 "forecast_mode": r.get("forecast_mode")})
    print(f"{len(failures)} live failures: " +
          ", ".join(f"i{f['iteration']}/s{f['site']}" for f in failures))

    # Lens 2: exact live path replay with causal pair accumulation.
    print("\n=== live-path replay (causal calibration, budget"
          f" {args.live_budget:g}s) ===")
    pairs = []
    live_replay = {}
    for i in range(max(f["iteration"] for f in failures)):
        snap = json.loads((pilot / f"snapshot_{i+1:03d}.json").read_text(encoding="utf-8"))
        t0 = time.perf_counter()
        results, additions = evaluate_pair_snapshot(
            snap, pairs, now=snap["received_at"], solver_seconds=args.live_budget)
        wall = time.perf_counter() - t0
        pairs.extend(additions)
        for f in failures:
            if f["iteration"] == i + 1:
                r = results[f["site"]]
                live_replay[(i + 1, f["site"])] = {
                    "status": r.get("solver_status") or r.get("status"),
                    "wall_s": round(wall, 3),
                    "calibration_status": r.get("calibration_status"),
                    "forecast_mode": r.get("forecast_mode"),
                    "battery_flow": r.get("battery_flow")}
    case_rows = []
    for f in failures:
        it, site = f["iteration"], f["site"]
        snap = json.loads((pilot / f"snapshot_{it:03d}.json").read_text(encoding="utf-8"))
        st = snap["stations"][site]["state"]
        tz = shadow._infer_api_timezone(snap["prices"], snap["stations"][site].get("weather", {}))
        row = {"iteration": it, "site": site, "device_time": st["device_time"],
               "live_forecast_mode": f["forecast_mode"],
               "live_replay": live_replay.get((it, site))}
        row.update(state_features(st))
        row.update(price_window_features(snap["prices"], st["device_time"], tz))
        lr = row["live_replay"] or {}
        print(f"iter{it:>2} site{site} soc={row['soc']:>5.1f} net={row['net_w']:>7.1f}W "
              f"imp_ptp={row['import_ptp']:.4f} exp_ptp={row['export_ptp']:.4f} "
              f"| live-replay: {lr.get('status')} ({lr.get('wall_s')}s, "
              f"calib={lr.get('calibration_status')})")

        # Lens 1: shadow replay at escalating budgets.
        replay = {}
        for budget in budgets:
            t0 = time.perf_counter()
            try:
                out = shadow.evaluate_snapshot(snap, max_age=1e12,
                                               solver_seconds=budget)["stations"][site]
                replay[f"{budget:g}s"] = {"status": out.get("solver_status") or out.get("status"),
                                          "wall_s": round(time.perf_counter() - t0, 3),
                                          "forecast_mode": out.get("forecast_mode"),
                                          "battery_flow": out.get("battery_flow"),
                                          "battery_power_w": out.get("battery_power_w")}
            except (ValueError, TypeError, KeyError) as exc:
                replay[f"{budget:g}s"] = {"status": f"error:{exc}",
                                          "wall_s": round(time.perf_counter() - t0, 3)}
        row["shadow_replay"] = replay
        print("   shadow: " + " ".join(f"{b:g}s->{replay[f'{b:g}s']['status']}"
                                       f"({replay[f'{b:g}s']['wall_s']:.1f}s)" for b in budgets))
        case_rows.append(row)

    # Lens 3: deep MILP diagnostics on cases still failing at max budget.
    hard = [r for r in case_rows
            if r["shadow_replay"][f"{budgets[-1]:g}s"]["status"] == "limit_reached"]
    if args.skip_deep:
        hard = []
    for row in hard:
        it, site = row["iteration"], row["site"]
        snap = json.loads((pilot / f"snapshot_{it:03d}.json").read_text(encoding="utf-8"))
        st = snap["stations"][site]["state"]
        print(f"\n=== deep MILP diagnostics iter{it} site{site} ===")
        probes = {}
        for label, probe, budget in [
                ("gap1e-9_300s", MilpProbe(), 300.),
                ("gap1e-2_300s_diagnostic", MilpProbe(gap_override=1e-2), 300.),
                ("lp_relaxation_diagnostic", MilpProbe(relax=True), 300.)]:
            with probe:
                try:
                    shadow.evaluate_snapshot(snap, max_age=1e12, solver_seconds=budget)
                except (ValueError, TypeError, KeyError) as exc:
                    probe.records.append({"error": str(exc)})
            probes[label] = probe.records
            for rec in probe.records:
                print(f"  {label}: {json.dumps(rec)}")
        row["milp_probes"] = probes

    # Controls for contrast.
    fail_keys = {(f["iteration"], f["site"]) for f in failures}
    ctrl_rows = []
    for cycle in result["cycles"]:
        for site, r in cycle["stations"].items():
            if (cycle["iteration"], site) in fail_keys:
                continue
            it = cycle["iteration"]
            snap = json.loads((pilot / f"snapshot_{it:03d}.json").read_text(encoding="utf-8"))
            st = snap["stations"][site]["state"]
            tz = shadow._infer_api_timezone(snap["prices"], snap["stations"][site].get("weather", {}))
            entry = {"iteration": it, "site": site, "device_time": st["device_time"]}
            entry.update(state_features(st))
            entry.update(price_window_features(snap["prices"], st["device_time"], tz))
            ctrl_rows.append(entry)
        if len(ctrl_rows) >= 20:
            break

    # Lens 2b: live path with diagnostic gap 1e-2 — is a near-optimal incumbent
    # available inside the 2s control budget for every case? (R8: diagnostic
    # only; production gap policy is a separate decision.)
    print("\n=== live-path replay with diagnostic mip_rel_gap=1e-2 (2s) ===")
    pairs = []
    for i in range(max(f["iteration"] for f in failures)):
        snap = json.loads((pilot / f"snapshot_{i+1:03d}.json").read_text(encoding="utf-8"))
        probe = MilpProbe(gap_override=1e-2)
        with probe:
            results, additions = evaluate_pair_snapshot(
                snap, pairs, now=snap["received_at"], solver_seconds=args.live_budget)
        pairs.extend(additions)
        for f in failures:
            if f["iteration"] == i + 1:
                r = results[f["site"]]
                rec = probe.records[int(f["site"]) - 1] if len(probe.records) >= int(f["site"]) else {}
                f["gap1e-2_live"] = {
                    "status": r.get("solver_status") or r.get("status"),
                    "battery_flow": r.get("battery_flow"),
                    "battery_power_w": r.get("battery_power_w"),
                    "solve_wall_s": rec.get("wall_s"),
                    "incumbent_gap": rec.get("mip_gap")}
                print(f"iter{i+1:>2} site{f['site']}: {f['gap1e-2_live']['status']} "
                      f"solve={rec.get('wall_s')}s gap={rec.get('mip_gap')} "
                      f"flow={r.get('battery_flow')} {r.get('battery_power_w')}")

    for row in case_rows:
        match = next(f for f in failures
                     if f["iteration"] == row["iteration"] and f["site"] == row["site"])
        row["gap1e-2_live"] = match.get("gap1e-2_live")

    report = {"failures": case_rows, "controls_sample": ctrl_rows,
              "pilot_dir": str(pilot), "budgets_s": budgets,
              "live_budget_s": args.live_budget}
    out = args.output or str(pilot / "limit_reached_case_library.json")
    Path(out).write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nwrote {out} ({len(case_rows)} failures, {len(ctrl_rows)} controls)")


if __name__ == "__main__":
    main()
