"""Deterministic rolling stress experiments; never connects to HA."""
from __future__ import annotations
import argparse
from contextlib import nullcontext
import hashlib
import json
from pathlib import Path
import sys
from time import perf_counter
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from energy_lab.model import Battery
from energy_lab.decision import decide
from energy_lab.strategy_backtest import _policy_step


def audit_plan(plan, pv, load, buy, sell, initial_soc, dt):
    """Audit all future steps, rather than infer exclusivity from executed flow."""
    p = plan.plan
    c, d, gi, ge = (p[k].to_numpy() for k in
                    ("charge_w", "discharge_w", "grid_import_w", "grid_export_w"))
    energy = p.soc_end.to_numpy() * .14
    previous = np.r_[initial_soc * .14, energy[:-1]]
    assert np.isfinite(p[["charge_w", "discharge_w", "grid_import_w", "grid_export_w", "soc_end"]]).all().all()
    assert np.minimum(c, d).max() < 1e-5
    assert np.minimum(gi, ge).max() < 1e-5
    assert min(c.min(), d.min(), gi.min(), ge.min()) >= -1e-3, {"minimum_powers_w": [c.min(), d.min(), gi.min(), ge.min()], "soc_min": p.soc_end.min()}
    assert max(c.max(), d.max()) <= 5000 + 1e-3
    assert energy.min() >= 1.4 - 1e-7 and energy.max() <= 14 + 1e-7
    grid = float(np.max(np.abs(pv + gi + d - load - ge - c)))
    battery = float(np.max(np.abs(energy - previous - (.95*c-d)*dt/1000)))
    objective = float(np.sum((gi*buy - ge*sell + .02*(c+d))*dt/1000) - .1*energy[-1])
    assert grid < 1e-3 and battery < 1e-6, {"grid_residual_w": grid, "battery_residual_kwh": battery,
                                             "objective": plan.objective}
    assert abs(objective - plan.objective) < 1e-6
    return grid, battery


def run_case(name, pv, load, buy, sell, initial_soc=50., dt=1., fault=None):
    pv, load, buy, sell = (np.asarray(x, dtype=float) for x in (pv, load, buy, sell))
    times = pd.Series(pd.date_range("2026-01-01", periods=len(pv), freq=pd.Timedelta(hours=dt)))
    battery = Battery(soc=initial_soc)
    rows, plans = [], []
    for index in range(len(pv)):
        end = min(len(pv), index + round(24/dt))
        pp, ll = pv[index:end].copy(), load[index:end].copy()
        # A controlled fault affects only this decision. Observations remain valid.
        context = nullcontext()
        if index == 2 and fault == "timeout":
            context = patch("energy_lab.rolling.milp",
                            return_value=SimpleNamespace(success=False, status=1, message="injected time limit"))
        if index == 2 and fault == "invalid_forecast":
            pp[-1] = np.nan
        before = battery.soc
        tick = perf_counter()
        with context:
            decision = decide(times.iloc[index:end], pp, ll, buy[index:end], sell[index:end], before,
                              observed_pv=pv[index], observed_load=load[index],
                              soc_min=10, terminal_price=.1, degradation_cost_eur_kwh=.02,
                              solver="milp", step_hours=dt, time_limit_seconds=5.)
        elapsed = perf_counter()-tick
        if decision.status == "optimal":
            audit_plan(decision.plan, pp, ll, buy[index:end], sell[index:end], before, dt)
            plans.extend({"scenario": name, "decision_step": index, **p}
                         for p in decision.plan.plan.to_dict("records"))
        actual = _policy_step(battery, decision.flow, decision.power_w,
                              pd.Series({"pv": pv[index], "load": load[index]}),
                              buy[index], sell[index], 10, step_hours=dt)
        charge = actual["power_w"] if actual["flow"] == "charge" else 0.
        discharge = actual["power_w"] if actual["flow"] == "discharge" else 0.
        grid_residual = (pv[index]+discharge-load[index]-charge)*dt/1000 + actual["grid_import_kwh"]-actual["grid_export_kwh"]
        energy_residual = (battery.soc-before)*.14 - (.95*charge-discharge)*dt/1000
        assert abs(grid_residual) < 1e-7 and abs(energy_residual) < 1e-7
        assert min(actual["grid_import_kwh"], actual["grid_export_kwh"]) < 1e-8
        assert -1e-7 <= battery.soc <= 100+1e-7
        # Starting below the reserve is an intentional real infeasibility case.
        if before >= 10-1e-7:
            assert battery.soc >= 10-1e-7
        else:
            assert battery.soc >= before-1e-7
        planned_power = decision.power_w if decision.status == "optimal" else None
        if planned_power is not None:
            assert abs(actual["power_w"]-planned_power) < 1e-3
            assert abs(battery.soc-decision.plan.plan.iloc[0].soc_end) < 1e-5, {
                "scenario": name, "step": index, "flow": decision.flow,
                "command_w": decision.power_w, "actual_soc": battery.soc,
                "planned_soc": float(decision.plan.plan.iloc[0].soc_end)}
        rows.append({"scenario": name, "step": index, "time": times.iloc[index],
                     "status": decision.status, "fallback": decision.fallback,
                     "reason": decision.reason, "solve_seconds": elapsed,
                     "grid_residual_kwh": grid_residual, "energy_residual_kwh": energy_residual,
                     "planned_power_w": planned_power, **actual})
    trace = pd.DataFrame(rows)
    if fault:
        expected = "limit_reached" if fault == "timeout" else "invalid_input"
        assert trace.loc[2, "status"] == expected and trace.loc[2, "fallback"]
        assert trace.loc[1, "status"] == trace.loc[3, "status"] == "optimal"
        assert not trace.loc[3, "fallback"]
    if initial_soc < 10:
        assert "infeasible" in trace.status.tolist()
        assert trace.iloc[-1].status == "optimal" and battery.soc >= 10
    return trace, plans


def run(output):
    output.mkdir(parents=True, exist_ok=True)
    if (output / "summary.json").exists():
        raise FileExistsError("Use a new output directory")
    n = 48
    cases = [
        ("negative_export_full_battery", [5000]*n, [0]*n, [.3]*n, [-.2]*n, 100, 1., None),
        ("near_full_headroom", [0, 5000, 5000, 0]*12, [1000, 0, 0, 1000]*12,
         [.3, .1, .1, .3]*12, [-.1]*n, 99, 1., None),
        ("reserve_floor", [0]*n, [5000]*n, [.4]*n, [.1]*n, 11, 1., None),
        ("price_inversion", [0]*n, [0]*n, [.1]*n, [.4]*n, 10, 1., None),
        ("timeout_recovery", [0]*n, [1000]*n, [.3]*n, [.1]*n, 50, 1., "timeout"),
        ("invalid_forecast_recovery", [1000]*n, [2000]*n, [.3]*n, [.1]*n, 50, 1., "invalid_forecast"),
        # 14 kWh * 10% cannot be replenished at 5 kW in a 5 min step.
        ("infeasible_initial_soc_recovery", [0]*24, [1000]*24, [.3]*24, [.1]*24, 0, 1/12, None),
        # Synthetic quarter-hour energy accounting, not resampled observed data.
        ("quarter_hour", [0, 5000, 5000, 0]*24, [1000]*96, [.3]*96, [-.1]*96, 99, .25, None),
    ]
    traces, plans, summaries = [], [], []
    for case in cases:
        trace, all_plans = run_case(*case)
        traces.append(trace); plans.extend(all_plans)
        summary = {"name": case[0], "steps": len(trace), "status_counts": trace.status.value_counts().to_dict(),
                   "fallback_steps": int(trace.fallback.sum()),
                   "minimum_soc": float(trace.soc_after.min()), "final_soc": float(trace.soc_after.iloc[-1]),
                   "negative_export_loss_eur": float(trace.negative_export_loss_eur.sum()),
                   "max_energy_residual_kwh": float(trace.energy_residual_kwh.abs().max()),
                   "max_grid_residual_kwh": float(trace.grid_residual_kwh.abs().max()),
                   "solve_p95_seconds": float(trace.solve_seconds.quantile(.95))}
        summaries.append(summary)
        print(json.dumps(summary), flush=True)
    combined = pd.concat(traces, ignore_index=True)
    combined.to_csv(output / "trace.csv", index=False)
    pd.DataFrame(plans).to_csv(output / "plans.csv", index=False)
    full = combined[combined.scenario.eq("negative_export_full_battery")]
    assert full.flow.eq("idle").all()
    assert np.isclose(full.negative_export_loss_eur.sum(), 48.)
    report = {"passed": True, "scenarios": summaries, "executed_steps": len(combined),
              "plan_tolerances": {"power_w": .001, "grid_residual_w": .001,
                                  "energy_residual_kwh": 1e-6, "first_step_soc_percent": 1e-5},
              "audited_plan_rows": len(plans), "writes_performed": 0,
              "source_sha256": {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in
                [Path(__file__), *[Path("src/energy_lab")/f for f in
                                  ("decision.py", "rolling.py", "strategy_backtest.py", "model.py")]]},
              "limits": [
                  "Synthetic power/price paths are known to the optimizer; no forecast quality claim.",
                  "Timeout is injected; infeasible initial SOC is solved by the real solver.",
                  "Faults are executed chronologically with actual fallback and subsequent recovery.",
                  "Below-reserve initial SOC is reported, not clamped or counted as a valid reserve state.",
                  "No curtailment or export limit exists; full battery can incur negative export losses.",
                  "Quarter-hour and 5-minute cases only test synthetic energy/control mechanics.",
                  "This tests the offline decision path, not run_live.py or real device actuation."]}
    (output / "summary.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("outputs/stress_closed_loop_v2"))
    run(parser.parse_args().output)
