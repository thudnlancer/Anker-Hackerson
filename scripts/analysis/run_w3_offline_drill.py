"""Exercise the real PowerShell watchdog with isolated fixtures; never contact HA."""
from pathlib import Path
from datetime import datetime, timedelta, timezone
import argparse
import json
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts.online.collector_monitor import run_round, _append_incident


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8")


def run(output):
    output.mkdir(parents=True, exist_ok=False)
    now = datetime(2026, 10, 14, tzinfo=timezone.utc)
    health = output / "outputs/collector_health"
    results = []
    states = {}
    observations = []

    def heartbeat(age=0):
        save(health / "heartbeat_nodeA.json", {
            "last_cycle_at": (now - timedelta(seconds=age)).isoformat(),
            "consecutive_errors": 0, "pid": -1, "cycle": 1,
            "writes_performed": 0})

    def monitor(label):
        report = run_round(health, ["nodeA"], states, stale_after=15,
                           dead_after=60, recovering_rounds=3, now=now)
        for event in report.pop("transitions"):
            _append_incident(health / "incidents.jsonl", event)
        save(health / "monitor_status.json", report)
        save(output / f"{label}_monitor_status.json", report)
        observations.append({"case": label, "checked_at": now.isoformat(),
                             "state": report["nodes"]["nodeA"]["state"]})
        return report["nodes"]["nodeA"]["state"]

    def watchdog(label, exists, expected):
        fixture = output / "fixture.json"
        save(fixture, {"now": now.isoformat(), "process_exists": exists})
        proc = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
            "-File", str(Path(__file__).resolve().parents[1] / "maintenance/collector_watchdog.ps1"),
            "-OfflineFixture", str(fixture)], capture_output=True, text=True,
            encoding="utf-8", timeout=30)
        (output / f"{label}_watchdog.log").write_text(proc.stdout + proc.stderr, encoding="utf-8")
        actions = [json.loads(line)["action"] for line in proc.stdout.splitlines() if line.startswith("{")]
        assert expected in actions, (label, proc.returncode, proc.stdout, proc.stderr)
        assert proc.returncode == (3 if expected in ("disabled", "disabled_skip") else 0)
        results.append({"case": label, "actions": actions, "passed": True})

    heartbeat()
    assert monitor("01_normal") == "HEALTHY"
    watchdog("01_normal", True, "healthy_skip")
    heartbeat(120)
    assert monitor("02_expired") == "DEAD"
    watchdog("02_expired", True, "restarted")
    watchdog("03_absent", False, "restarted")
    watchdog("04_crash", False, "restarted")
    watchdog("04_backoff", False, "backoff_armed")
    watchdog("04_backoff_block", False, "backoff_skip")
    # Advance virtual time, not wall clock, then force further crash bursts.
    state_path = health / "watchdog_state_nodeA.json"
    for stage, minutes in enumerate((6, 16, 61), 1):
        now += timedelta(minutes=minutes)
        state = json.loads(state_path.read_text(encoding="utf-8-sig"))
        state["restarts"] = [now.isoformat()] * 3
        save(state_path, state)
        watchdog(f"05_stage_{stage}", False, "disabled" if stage == 3 else "backoff_armed")
    watchdog("05_disabled", False, "disabled_skip")
    recovery = []
    for i in range(3):
        now += timedelta(seconds=1)
        heartbeat()
        recovery.append(monitor(f"06_recovery_{i}"))
    assert recovery == ["RECOVERING", "RECOVERING", "HEALTHY"]
    transitions = [json.loads(line) for line in
                   (health / "incidents.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [(event["from"], event["to"]) for event in transitions] == [
        ("HEALTHY", "DEAD"), ("DEAD", "RECOVERING"), ("RECOVERING", "HEALTHY")]
    save(output / "state_transitions.json", {"observations": observations,
        "transitions": transitions,
        "note": "Fresh monitor heartbeats do not clear the watchdog disabled marker."})
    save(output / "results.json", {"mode": "offline_fixture", "ha_connections": 0,
        "device_writes": 0, "real_process_actions": 0, "cases": results,
        "recovery": recovery, "all_passed": True})
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.output.resolve()), ensure_ascii=False))
