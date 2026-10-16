"""B4.3 collector monitor: read node heartbeats, advance the health state
machine, and append every state transition to incidents.jsonl.

Detection and recording only — process restarts are the watchdog's job
(collector_watchdog.ps1 reads monitor_status.json). Read-only toward HA and
the config; ha_writes_performed is always 0.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from time import monotonic, sleep
import argparse
import hashlib
import json
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from energy_lab.collector_health import NodeHealth, classify_cluster, evaluate_node


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _age_seconds(last_cycle_at: str | None, now: datetime) -> float | None:
    if not last_cycle_at:
        return None
    try:
        stamp = datetime.fromisoformat(str(last_cycle_at).replace("Z", "+00:00"))
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return max(0.0, (now - stamp).total_seconds())


def _append_incident(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(payload)
    payload["sha256"] = hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def run_round(health_dir: Path, nodes: list[str], states: dict[str, NodeHealth], *,
              stale_after: float, dead_after: float, recovering_rounds: int,
              now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    node_reports: dict[str, dict] = {}
    transitions: list[dict] = []
    for node in nodes:
        heartbeat_path = health_dir / f"heartbeat_{node}.json"
        heartbeat: dict = {}
        if heartbeat_path.exists():
            try:
                heartbeat = json.loads(heartbeat_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                heartbeat = {}
        age = _age_seconds(heartbeat.get("last_cycle_at"), now)
        previous = states.get(node, NodeHealth())
        updated, event = evaluate_node(previous, age, stale_after=stale_after,
                                       dead_after=dead_after, recovering_rounds=recovering_rounds)
        states[node] = updated
        if event:
            transitions.append({"ts": now.isoformat(), "type": "node_transition", "node": node, **event})
        node_reports[node] = {
            "state": updated.state, "age_seconds": age, "fresh_streak": updated.fresh_streak,
            "pid": heartbeat.get("pid"), "cycle": heartbeat.get("cycle"),
            "consecutive_errors": int(heartbeat.get("consecutive_errors") or 0),
            "last_error": heartbeat.get("last_error"),
            "cache_bytes": heartbeat.get("cache_bytes") or {},
        }
    cluster = classify_cluster({node: report["consecutive_errors"] for node, report in node_reports.items()})
    if cluster == "cluster_upstream_suspected":
        transitions.append({"ts": now.isoformat(), "type": "cluster",
                            "reason": "all_nodes_erroring_upstream_suspected_do_not_restart"})
    return {"checked_at": now.isoformat(), "cluster": cluster, "nodes": node_reports,
            "transitions": transitions, "ha_writes_performed": 0}


def main() -> None:
    parser = argparse.ArgumentParser(description="B4 collector health monitor (detection + incidents only)")
    parser.add_argument("--health-dir", type=Path, default=Path("outputs/collector_health"))
    parser.add_argument("--nodes", nargs="+", default=["nodeA"])
    parser.add_argument("--interval-seconds", type=float, default=30.0)
    parser.add_argument("--iterations", type=int, default=1, help="use 0 to run until interrupted")
    parser.add_argument("--stale-after", type=float, default=15.0)
    parser.add_argument("--dead-after", type=float, default=60.0)
    parser.add_argument("--recovering-rounds", type=int, default=3)
    args = parser.parse_args()
    if args.interval_seconds <= 0 or args.iterations < 0:
        parser.error("interval-seconds must be positive and iterations nonnegative")

    states: dict[str, NodeHealth] = {}
    incidents_path = args.health_dir / "incidents.jsonl"
    status_path = args.health_dir / "monitor_status.json"
    iteration = 0
    while args.iterations == 0 or iteration < args.iterations:
        cycle_started = monotonic()
        report = run_round(args.health_dir, list(args.nodes), states,
                           stale_after=args.stale_after, dead_after=args.dead_after,
                           recovering_rounds=args.recovering_rounds)
        for transition in report.pop("transitions"):
            _append_incident(incidents_path, transition)
        args.health_dir.mkdir(parents=True, exist_ok=True)
        status_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False), flush=True)
        iteration += 1
        if args.iterations and iteration >= args.iterations:
            break
        sleep(max(0.0, args.interval_seconds - (monotonic() - cycle_started)))


if __name__ == "__main__":
    main()
