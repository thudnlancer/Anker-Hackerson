"""Offline archived-command replay plus real solves under the new SOP contract."""
from pathlib import Path
from datetime import datetime, timezone, timedelta
import argparse
import hashlib
import json
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
from energy_lab.backtest import load_csv
from energy_lab.interface_contract import map_battery_action, verify_readback
from energy_lab.rolling import optimize_window


def run(source, data, output):
    if (output / "summary.json").exists():
        raise FileExistsError("Choose a new output directory")
    output.mkdir(parents=True, exist_ok=True)
    path = source / "strategy_comparison_trace.csv"
    trace = pd.read_csv(path, dtype={"site": str}, parse_dates=["time"], float_precision="round_trip")
    trace = trace[trace.policy.eq("forecast_milp_persistence")]
    mappings, plans = [], []
    checks = 0
    issued = datetime(2026, 10, 10, tzinfo=timezone.utc)
    observed_at = issued+timedelta(seconds=5)
    for site, group in trace.groupby("site"):
        raw = load_csv(data / f"{site}.csv")
        indexed = raw.set_index("time")
        for step in group.itertuples(index=False):
            pv, load = indexed.loc[step.time, ["pv", "load"]]
            command = map_battery_action(step.flow, step.power_w, pv, load)
            if command.representable:
                # Independent idealized device response under SOP semantics.
                c = d = 0.
                if command.flow == "charge":
                    c = min(command.target_w, 5000, (100-step.soc_before)*.14/.95*1000)
                else:
                    d = min(max(0, command.target_w-(pv-load)), 5000, step.soc_before*.14*1000)
                exported = max(0, pv+d-load-c)
                state = dict(last_changed=observed_at.isoformat(), device_time=step.time+pd.Timedelta(hours=1),
                             operating_mode="third_party_control", grid_flow=command.flow,
                             power_control=command.target_w, battery_charging_power_w=c,
                             battery_discharging_power_w=d, grid_export_power_w=exported)
                checked = verify_readback(command, state, sent_device_time=step.time,
                                          issued_at=issued, now=observed_at, tolerance_w=.001)
                assert checked.verified, (site, step.time, checked)
                assert abs(c+d-step.power_w) < .001
                assert not verify_readback(command, state, now=observed_at).verified
                checks += 1
            mappings.append(dict(site=site, time=step.time, representable=command.representable, reason=command.reason))
        # Same real power/price windows used for both solvers; mechanism comparison,
        # not forecast performance or a rolling economic outcome.
        split = int(len(raw)*.8)
        for start in (split, (split+len(raw)-24)//2, len(raw)-24):
            window = raw.iloc[start:start+24]
            kwargs = dict(soc_min=10, terminal_price=.1, degradation_cost_eur_kwh=.02, solver="milp")
            args = (window.time, window.pv.to_numpy(), window.load.to_numpy(), window.buy.to_numpy(), window.sell.to_numpy(), 50.)
            original = optimize_window(*args, **kwargs)
            constrained = optimize_window(*args, command_contract="sop", **kwargs)
            assert original.status == constrained.status == "optimal"
            assert constrained.objective >= original.objective-1e-6
            previous = 7.
            for i, row in enumerate(constrained.plan.itertuples(index=False)):
                pv, load = window.pv.iloc[i], window.load.iloc[i]
                power = row.charge_w if row.flow == "charge" else row.discharge_w if row.flow == "discharge" else 0
                command = map_battery_action(row.flow, max(0, power), pv, load)
                assert command.representable, (site, start, i)
                assert min(row.charge_w, row.discharge_w) < .001
                assert min(row.grid_import_w, row.grid_export_w) < .001
                assert abs(pv+row.grid_import_w+row.discharge_w-load-row.grid_export_w-row.charge_w) < .001
                energy = row.soc_end*.14
                assert abs(energy-previous-(.95*row.charge_w-row.discharge_w)/1000) < 1e-6
                assert 1.4-1e-6 <= energy <= 14+1e-6
                previous = energy
                plans.append(dict(site=site, start=start, **row._asdict(), command_target_w=command.target_w))
        if int(site) % 20 == 0:
            print(f"reviewed site {site}", flush=True)
    frame = pd.DataFrame(mappings)
    frame.to_csv(output/"historical_mapping.csv", index=False)
    pd.DataFrame(plans).to_csv(output/"constrained_plans.csv", index=False)
    report = dict(passed=True, network_requests=0, writes_performed=0, archived_actions=len(frame),
                  historical_unrepresentable=int((~frame.representable).sum()),
                  simulated_readback_verified=checks, constrained_windows=300, constrained_plan_steps=len(plans),
                  constrained_unrepresentable_steps=0, source_trace_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                  source_sha256={str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in
                      [Path(__file__), Path("src/energy_lab/rolling.py"), Path("src/energy_lab/interface_contract.py")]},
                  limits=["SOP command mode uses a conservative subset; saturated device modes are not represented.",
                          "Readbacks are simulated, not actual device acceptance.",
                          "Windows use realized future power, not forecast performance or rolling realized gains.",
                          "Do not transfer previous unconstrained economic results to this model."])
    (output/"summary.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--source", type=Path, default=Path("outputs/milp_100site_parallel8"))
    p.add_argument("--data", type=Path, default=Path("forecast_data"))
    p.add_argument("--output", type=Path, default=Path("outputs/online_policy_replay"))
    a = p.parse_args()
    run(a.source, a.data, a.output)
