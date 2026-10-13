"""Offline mapping coverage on archived traces; inspect local snapshot schema only."""
from pathlib import Path
import hashlib
import json
import sys

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
from energy_lab.backtest import load_csv
from energy_lab.interface_contract import map_battery_action


def main():
    output = Path("outputs/interface_readiness")
    if (output / "summary.json").exists():
        raise FileExistsError("Completed report exists")
    output.mkdir(parents=True, exist_ok=True)
    source = Path("outputs/milp_100site_parallel8/strategy_comparison_trace.csv")
    trace = pd.read_csv(source, dtype={"site": str}, parse_dates=["time"])
    trace = trace[trace.policy.eq("forecast_milp_persistence")]
    rows = []
    for site, group in trace.groupby("site"):
        raw = load_csv(Path("forecast_data") / f"{site}.csv").set_index("time")
        for step in group.itertuples(index=False):
            observation = raw.loc[step.time]
            mapping = map_battery_action(step.flow, step.power_w, observation.pv, observation.load)
            rows.append({"site": site, "time": step.time, "battery_flow": step.flow,
                         "battery_power_w": step.power_w, **vars(mapping)})
    result = pd.DataFrame(rows)
    result.to_csv(output / "mapping.csv", index=False)
    snapshot = Path("online_dry_run_latest.json")
    # Schema and file fingerprint only: never echo tokens or account values.
    snapshot_info = {"exists": snapshot.exists()}
    if snapshot.exists():
        content = json.loads(snapshot.read_text(encoding="utf-8"))
        snapshot_info.update({"sha256": hashlib.sha256(snapshot.read_bytes()).hexdigest(),
                              "top_level_keys": sorted(content) if isinstance(content, dict) else ["list"],
                              "is_new_fetch": False})
    report = {"mode": "offline_review", "writes_performed": 0, "network_requests": 0,
              "source_trace_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
              "actions": len(result), "representable_candidate_actions": int(result.representable.sum()),
              "unrepresentable_actions": int((~result.representable).sum()),
              "reason_counts": result.reason.value_counts().to_dict(), "snapshot": snapshot_info,
              "official_sop_timing": {"forecast_step_hours": 1, "wall_tick_seconds": 5,
                                      "acceleration_example": 180, "device_minutes_per_tick": 15},
              "ready_for_milp_live": False,
              "remaining_gaps": [
                  "run_live.py currently uses dispatch rules; it does not load trained forecasts or call MILP.",
                  "run_live.py indexes prices by hour and can reuse today's points; needs timestamp-aligned tomorrow.",
                  "run_live.py checks presence of freshness fields, not elapsed age; no tick/readback acknowledgement.",
                  "HAClient.set_dispatch maps zero/hold to self_consumption; this is not a zero-battery-action command.",
                  "The optimizer does not yet impose all constraints of grid-export-target actuation.",
                  "Zero-charge hold mapping is only a SOP-derived candidate until device readback confirms semantics.",
                  "Need a new official weather snapshot with issuance time and per-site fields; persistence is not that forecast.",
                  "15-minute optimization/5-minute device replanning is not validated by hourly data or achievable at every 15-minute accelerated tick."
              ]}
    (output / "summary.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
