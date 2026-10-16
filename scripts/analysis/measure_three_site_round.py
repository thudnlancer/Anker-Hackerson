"""Offline three-site full MILP dry-run with injected fresh states; zero HA writes."""
from datetime import datetime, timezone
import argparse
import json
from pathlib import Path
import sys
import time
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tests"))
from test_run_live_chain import StubHA, StubSim, make_prices, state
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts.online.run_live import run_once_milp


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--rounds", type=int, default=10)
    args = parser.parse_args()
    if args.rounds < 1:
        parser.error("--rounds must be positive")
    output = Path("outputs/m3_schedule_20261016/offline_three_site_multiround.json")
    cfg = {"stations": {str(i): {"suffix": f"15{i:02d}", "zero_flow_verified": True}
                        for i in (1, 2, 3)},
           "stale_after_seconds": 15, "capacity_kwh": 14, "max_power_w": 5000,
           "soc_min": 0, "soc_max": 100, "solver_seconds": 2,
           "poll_seconds": 5, "alignment_budget_seconds": 2.5,
           "round_deadline_seconds": 5, "device_timezone": "UTC"}
    ha = StubHA({f"15{i:02d}": state() for i in (1, 2, 3)})
    carry = {}
    rounds = []
    started = time.monotonic()
    for index in range(args.rounds):
        round_started = time.monotonic()
        rows = run_once_milp(cfg, ha, StubSim(make_prices()), write_enabled=False,
                             carry=carry)
        rounds.append({"round": index + 1, "elapsed_seconds": time.monotonic() - round_started,
                       "stations": [{"site": r["site"], "status": r["status"],
                                     "solve_elapsed_seconds": r.get("solve_elapsed_seconds"),
                                     "execution_safety_rejection": r.get("execution_safety_rejection")}
                                    for r in rows],
                       "skipped_stations": sum(r.get("status") in ("station_error", "candidate_error",
                                               "stale_or_unavailable", "reconciliation_required") or
                                               bool(r.get("execution_safety_rejection")) for r in rows)})
    # A separate, deliberately overloaded schedule measures actual skip paths.
    # It is not an estimate of the real simulator's skip rate.
    slow_cfg = {**cfg, "round_deadline_seconds": 0.02}
    slow_carry = {}
    stressed = []
    def slow_candidate(*args, **kwargs):
        time.sleep(0.08)
        return {"status": "candidate_only", "battery_flow": "charge",
                "battery_power_w": 100, "candidate": {"target_w": 100},
                "device_time": "2031-01-01T00:15:00+00:00"}
    with patch("run_live.make_candidate", side_effect=slow_candidate):
        for index in range(args.rounds):
            tick_start = time.monotonic()
            slow_rows = run_once_milp(slow_cfg, ha, StubSim(make_prices()),
                                      write_enabled=False, carry=slow_carry)
            stressed.append({"round": index + 1,
                             "elapsed_seconds": time.monotonic() - tick_start,
                             "station_status": [{"site": r["site"], "status": r["status"],
                                                 "rejection": r.get("execution_safety_rejection")}
                                                for r in slow_rows],
                             "skipped_stations": sum(bool(r.get("execution_safety_rejection"))
                                                     for r in slow_rows)})
    report = {"kind": "offline_injected_fresh_state_full_solver_not_real_M3",
              "captured_at": datetime.now(timezone.utc).isoformat(),
              "elapsed_seconds": time.monotonic() - started,
              "round_count": args.rounds,
              "skipped_station_rounds": sum(r["skipped_stations"] for r in rounds),
              "round_elapsed_seconds": [r["elapsed_seconds"] for r in rounds],
              "rounds": rounds,
              "overload_injection": {"solver_sleep_seconds": 0.08, "deadline_seconds": 0.02,
                                     "skipped_station_rounds": sum(r["skipped_stations"] for r in stressed),
                                     "rounds": stressed},
              "writes_performed": 0,
              "ha_write_calls": ha.calls,
              "limitations": "Injected fresh HA states and stub price API; no real network or new simulator window."}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
