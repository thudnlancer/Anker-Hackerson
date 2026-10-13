"""Unified explicit write path for one shadow candidate (design step 2).

Decision table (HOLD_SEMANTICS_DESIGN §4 step 2):

| candidate            | zero_flow_verified=True           | zero_flow_verified=False |
|----------------------|-----------------------------------|--------------------------|
| charge / discharge   | set_dispatch + next-tick readback | same                     |
| idle                 | third-party charge=0 + readback   | refuse: idle_unverified_noop; never silently downgraded |
| non-optimal fallback | explicit self_use_mode (Q1), marked non-optimizer-attributed | same |

Nothing is written unless ``live=True``; the default is a pure dry-run audit
record. A missing or failed readback is recorded as such, never faked (R5).
The input is a fresh ``shadow.make_candidate`` record; this layer produces new
audit artifacts only, so unknown planned labels raise instead of being
reinterpreted.
"""
from __future__ import annotations

from .interface_contract import CommandMapping, verify_readback

# Q1-decided explicit fallback label (§8.1); shared by online and offline paths
# so the fallback branch carries one name everywhere (G3).
FALLBACK_COMMAND_MODE = "self_use_mode"

AUDIT_FIELDS = ("site", "device_time", "status", "solver_status",
                "planned_flow", "planned_power_w", "command_mode", "command_flow",
                "target_w", "readback_verdict", "realized_soc",
                "optimizer_attributed", "writes_performed", "reason")


def _record(**fields):
    return {key: fields.get(key) for key in AUDIT_FIELDS}


def _soc(readback_state):
    if isinstance(readback_state, dict):
        try:
            return float(readback_state["soc"])
        except (KeyError, TypeError, ValueError):
            return None
    return None


def _command_readback(flow, target_w, state, *, live, device_time, issued_at, now, tolerance_w):
    if not live:
        return "not_sent_dry_run"
    if state is None:
        # Dispatch readback is due at the next device tick, not synchronously.
        return "pending_next_tick"
    check = verify_readback(CommandMapping(True, flow, target_w, "executed_command"), state,
                            sent_device_time=device_time, issued_at=issued_at, now=now,
                            tolerance_w=tolerance_w)
    return check.reason


def _fallback_readback(state, *, live):
    if not live:
        return "not_sent_dry_run"
    if state is None:
        return "pending_next_tick"
    mode = state.get("operating_mode") if isinstance(state, dict) else None
    return "mode_echo_verified" if mode == "self_consumption" else "mode_echo_mismatch"


def execute_candidate(candidate, *, suffix, ha=None, site=None, zero_flow_verified=False,
                      live=False, readback_state=None, issued_at=None, now=None,
                      readback_tolerance_w=100.0):
    """Decide and optionally perform the write for one shadow candidate.

    ``candidate`` is the dict returned by ``shadow.make_candidate`` (or the
    per-station entry of ``evaluate_snapshot``). ``ha`` only needs the explicit
    write methods ``set_dispatch``/``set_self_consumption``; it is required when
    ``live=True``. ``readback_state`` is the station state read at the next
    device tick; omit it to record the readback as pending.
    """
    site = str(site if site is not None else suffix)
    if live and ha is None:
        raise ValueError("live execution requires an HA client")
    if not isinstance(candidate, dict):
        raise ValueError("candidate must be a shadow.make_candidate record")
    device_time = candidate.get("device_time")
    solver_status = candidate.get("solver_status", candidate.get("status"))

    if candidate.get("status") != "candidate_only":
        # Fallback branch (Q1): explicit self-use mode switch. The step is
        # never optimizer-attributed, whatever the device does afterwards.
        if live:
            ha.set_self_consumption(suffix, live=True)
        return _record(site=site, device_time=device_time, status="fallback_self_use_mode",
                       solver_status=solver_status, planned_flow=None, planned_power_w=None,
                       command_mode=FALLBACK_COMMAND_MODE, command_flow="self_consumption",
                       target_w=None,
                       readback_verdict=_fallback_readback(readback_state, live=live),
                       realized_soc=_soc(readback_state), optimizer_attributed=False,
                       writes_performed=int(live),
                       reason=candidate.get("fallback", candidate.get("reason")))

    planned_flow = candidate.get("battery_flow")
    planned_power = candidate.get("battery_power_w")
    mapping = candidate.get("candidate") or {}

    if planned_flow in ("charge", "discharge"):
        command_flow, target_w, command_mode = planned_flow, float(mapping["target_w"]), "dispatch"
    elif planned_flow == "idle":
        if not zero_flow_verified:
            # Hard refusal: an unplanned self-use downgrade would silently
            # change semantics, so no write happens on this path at all.
            return _record(site=site, device_time=device_time, status="idle_unverified_noop",
                           solver_status=solver_status, planned_flow="idle",
                           planned_power_w=planned_power, command_mode=None, command_flow=None,
                           target_w=None, readback_verdict="not_applicable_no_command",
                           realized_soc=_soc(readback_state), optimizer_attributed=False,
                           writes_performed=0, reason="zero_flow_unverified_site_refusal")
        # The decision table is authoritative: a verified idle is executed as
        # the third-party charge=0 command, whatever mapping policy produced
        # the candidate.
        command_flow, target_w, command_mode = "charge", 0.0, "zero_flow_command"
    else:
        raise ValueError(f"unknown planned flow {planned_flow!r}; refusing implicit handling")

    if live:
        ha.set_dispatch(suffix, command_flow, target_w, live=True)
    status = "dispatched" if command_mode == "dispatch" else "zero_flow_dispatched"
    return _record(site=site, device_time=device_time, status=status, solver_status=solver_status,
                   planned_flow=planned_flow, planned_power_w=planned_power,
                   command_mode=command_mode, command_flow=command_flow, target_w=target_w,
                   readback_verdict=_command_readback(command_flow, target_w, readback_state,
                                                      live=live, device_time=device_time,
                                                      issued_at=issued_at, now=now,
                                                      tolerance_w=readback_tolerance_w),
                   realized_soc=_soc(readback_state), optimizer_attributed=True,
                   writes_performed=int(live), reason=None)
