"""B4.3 collector health state machine (pure logic, no I/O).

Per-node states over heartbeat age (seconds since last_cycle_at):

    HEALTHY --age > dead_after--> DEAD
    HEALTHY --age > stale_after--> STALE
    STALE   --age > dead_after--> DEAD
    STALE   --age <= stale_after-> HEALTHY
    DEAD    --heartbeat fresh again--> RECOVERING (fresh_streak = 1)
    RECOVERING --streak reaches recovering_rounds--> HEALTHY
    RECOVERING --age > stale_after--> DEAD

RECOVERING is the anti-flap gate: a node that keeps dying right after a
restart must not bounce straight back to HEALTHY on one lucky cycle.
Cluster classification separates "all nodes fail together" (suspected HA /
upstream outage: do NOT restart processes) from per-node faults.
"""
from __future__ import annotations

from dataclasses import dataclass


STATES = ("HEALTHY", "STALE", "DEAD", "RECOVERING")


@dataclass
class NodeHealth:
    state: str = "HEALTHY"
    fresh_streak: int = 0

    def __post_init__(self) -> None:
        if self.state not in STATES:
            raise ValueError(f"unknown state {self.state!r}")
        if self.fresh_streak < 0:
            raise ValueError("fresh_streak must be nonnegative")


def evaluate_node(health: NodeHealth, age_seconds: float | None, *,
                  stale_after: float = 15.0, dead_after: float = 60.0,
                  recovering_rounds: int = 3) -> tuple[NodeHealth, dict | None]:
    """Advance one node's state machine. Returns (new_health, event|None).

    ``age_seconds=None`` means the heartbeat is missing/unreadable: treat as
    infinitely old. Events are {"from", "to", "reason", "age_seconds"}.
    """
    if stale_after <= 0 or dead_after <= stale_after or recovering_rounds <= 0:
        raise ValueError("require 0 < stale_after < dead_after and recovering_rounds > 0")
    state, streak = health.state, health.fresh_streak
    age = float("inf") if age_seconds is None else float(age_seconds)
    fresh = age <= stale_after

    def event(to: str, reason: str) -> dict:
        return {"from": state, "to": to, "reason": reason,
                "age_seconds": (None if age == float("inf") else age)}

    if state == "HEALTHY":
        if age > dead_after:
            return NodeHealth("DEAD", 0), event("DEAD", "heartbeat_age_exceeds_dead_after")
        if age > stale_after:
            return NodeHealth("STALE", 0), event("STALE", "heartbeat_age_exceeds_stale_after")
        return NodeHealth("HEALTHY", 0), None
    if state == "STALE":
        if age > dead_after:
            return NodeHealth("DEAD", 0), event("DEAD", "heartbeat_age_exceeds_dead_after")
        if fresh:
            return NodeHealth("HEALTHY", 0), event("HEALTHY", "heartbeat_fresh_again")
        return NodeHealth("STALE", 0), None
    if state == "DEAD":
        if fresh:
            return NodeHealth("RECOVERING", 1), event("RECOVERING", "heartbeat_resumed")
        return NodeHealth("DEAD", 0), None
    # RECOVERING
    if not fresh:
        return NodeHealth("DEAD", 0), event("DEAD", "relapsed_during_recovery")
    streak += 1
    if streak >= recovering_rounds:
        return NodeHealth("HEALTHY", 0), event("HEALTHY", "recovery_confirmed")
    return NodeHealth("RECOVERING", streak), None


def classify_cluster(consecutive_errors: dict[str, int]) -> str:
    """Cluster-level view from each node's consecutive_errors counter.

    every node erroring simultaneously => upstream/HA is the likely fault and
    restarting local processes is pointless; report, don't act.
    """
    if not consecutive_errors:
        return "no_nodes"
    if all(errors > 0 for errors in consecutive_errors.values()):
        return "cluster_upstream_suspected"
    if any(errors > 0 for errors in consecutive_errors.values()):
        return "partial_node_errors"
    return "all_clear"
