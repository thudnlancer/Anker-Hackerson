"""Unified explicit write path for one shadow candidate (design steps 2+3).

Decision table (HOLD_SEMANTICS_DESIGN §4 step 2):

| candidate            | zero_flow_verified=True           | zero_flow_verified=False |
|----------------------|-----------------------------------|--------------------------|
| charge / discharge   | set_dispatch + next-tick readback | same                     |
| idle                 | third-party charge=0 + readback   | explicit self_use_mode (idle_self_use_mode); never a stale-command refusal |
| non-optimal fallback | explicit self_use_mode (Q1), marked non-optimizer-attributed | same |

Step 3 adds the per-tick SOC divergence guard (``check_soc_divergence``): a
verified idle step whose realized SOC diverges from the plan beyond the Q2
threshold revokes the site's zero_flow_verified; divergence on dispatch steps
is recorded for forecast-calibration monitoring only and never triggers a
command-layer action.

Nothing is written unless ``live=True``; the default is a pure dry-run audit
record. A missing or failed readback is recorded as such, never faked (R5).
The candidate input is a fresh ``shadow.make_candidate`` record; this layer
produces new audit artifacts only, so unknown planned labels raise instead of
being reinterpreted.
"""
from __future__ import annotations

import math
import time
from .write_transaction import ACTIVE_WRITE, WriteTransaction

from .interface_contract import CommandMapping, verify_readback

# Q1-decided explicit fallback label (§8.1); shared by online and offline paths
# so the fallback branch carries one name everywhere (G3).
FALLBACK_COMMAND_MODE = "self_use_mode"

# Q2 decision (2026-10-13): initial guard threshold of 2 SOC percent points.
# A 15 min step at the 5 kW limit can move a 14 kWh battery by ~8.9 points, so
# the threshold leaves ample room for forecast error while still catching a
# device that actively works during a commanded zero-flow idle step.
SOC_DIVERGENCE_THRESHOLD_PERCENT = 2.0

# Minimum round budget reserved before a delivery begins, by delivery shape.
# A self-use switch is one select_option POST; a dispatch is operating_mode +
# the SOP mode-gap + grid_flow + power_control. Reserving up front keeps a
# late-round delivery from timing out part way through (partial write ->
# uncertain delivery -> quarantine). See run_live.py round-deadline overflow.
SELF_USE_DELIVERY_BUDGET = 0.5
DISPATCH_DELIVERY_BUDGET = 2.0


def _reserve_delivery_budget(seconds: float) -> None:
    """Refuse a live delivery unless ``seconds`` remain before the round deadline."""
    transaction = ACTIVE_WRITE.get()
    if transaction is not None:
        transaction.reserve(seconds)

AUDIT_FIELDS = ("site", "device_time", "status", "solver_status",
                "accepted_incumbent", "mip_gap",
                "planned_flow", "planned_power_w", "command_mode", "command_flow",
                "target_w", "readback_verdict", "realized_soc",
                "optimizer_attributed", "writes_performed", "reason",
                "delivery_steps", "write_requests_attempted",
                "write_requests_acknowledged", "requires_reconciliation")


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


def execute_candidate(candidate, *, deadline=None, current=None, reconciliation_store=None, **kwargs):
    """Audit partial/unknown delivery without retrying or rolling it back.

    An HTTP timeout cannot prove the server did not apply the request. Such a
    transaction requires readback/reconciliation, not an automatic retry.
    """
    # Programming/configuration errors must remain visible to the caller; only
    # failures after a real HA delivery attempt become uncertain delivery.
    if kwargs.get("live") and kwargs.get("ha") is None:
        raise ValueError("live execution requires an HA client")
    if not isinstance(candidate, dict):
        raise ValueError("candidate must be a shadow.make_candidate record")
    if candidate.get("status") == "candidate_only" and candidate.get("battery_flow") not in (
            "charge", "discharge", "idle"):
        raise ValueError(f"unknown planned flow {candidate.get('battery_flow')!r}; refusing implicit handling")
    if kwargs.get("live") and reconciliation_store is not None and str(
            kwargs.get("site", kwargs.get("suffix"))) in reconciliation_store.pending():
        raise ValueError("reconciliation_required_site_refusal")
    transaction = WriteTransaction(deadline if deadline is not None else time.monotonic() + 5,
                                   current=current, store=reconciliation_store,
                                   site=str(kwargs.get("site", kwargs.get("suffix"))),
                                   suffix=str(kwargs.get("suffix")),
                                   device_time=str(candidate.get("device_time")))
    token = ACTIVE_WRITE.set(transaction)
    try:
        if kwargs.get("live"):
            transaction.remaining()
        record = _execute_candidate(candidate, **kwargs)
        if kwargs.get("live") and reconciliation_store is not None and transaction.steps:
            reconciliation_store.complete(transaction.site)
    except Exception as exc:
        if not kwargs.get("live") or not transaction.steps and not isinstance(exc, TimeoutError):
            raise
        if not transaction.steps:
            # Refused before any write (round deadline / delivery budget): the
            # device is untouched, so there is nothing to reconcile and no
            # quarantine. Distinct from a mid-write failure, which may have
            # applied a prefix of the command.
            record = _record(site=str(kwargs.get("site", kwargs.get("suffix"))),
                             device_time=candidate.get("device_time"), status="delivery_blocked",
                             solver_status=candidate.get("solver_status"),
                             writes_performed=0, optimizer_attributed=False,
                             readback_verdict="not_sent",
                             reason=f"delivery_blocked:{type(exc).__name__}")
        else:
            record = _record(site=str(kwargs.get("site", kwargs.get("suffix"))),
                             device_time=candidate.get("device_time"), status="delivery_failed",
                             solver_status=candidate.get("solver_status"),
                             writes_performed=0, optimizer_attributed=False,
                             readback_verdict="required_delivery_unknown",
                             reason=f"delivery_error:{type(exc).__name__}")
            record["delivery_error"] = str(exc) if isinstance(exc, TimeoutError) else type(exc).__name__
            record["requires_reconciliation"] = bool(transaction.steps)
    finally:
        ACTIVE_WRITE.reset(token)
    record["delivery_steps"] = transaction.steps
    record["write_requests_attempted"] = len(transaction.steps)
    record["write_requests_acknowledged"] = sum(
        s["status"] == "http_acknowledged_not_device_verified" for s in transaction.steps)
    if record["requires_reconciliation"] is None:
        record["requires_reconciliation"] = False
    return record


def _execute_candidate(candidate, *, suffix, ha=None, site=None, zero_flow_verified=False,
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
            _reserve_delivery_budget(SELF_USE_DELIVERY_BUDGET)
            ha.set_self_consumption(suffix, live=True)
        return _record(site=site, device_time=device_time, status="fallback_self_use_mode",
                       solver_status=solver_status, accepted_incumbent=False, mip_gap=None,
                       planned_flow=None, planned_power_w=None,
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
            # Zero-flow (charge=0W) is unverified on this device: mock evidence
            # shows charge=0W still self-consumes PV surplus, so it is not a
            # valid "hold". A planned idle is therefore executed as the device's
            # explicit self-use mode (HOLD_SEMANTICS_DESIGN Q1 decision B,
            # SOP-compliant), never as a stale-command refusal (rejected
            # option A) and never as an unverifiable charge=0.
            if live:
                _reserve_delivery_budget(SELF_USE_DELIVERY_BUDGET)
                ha.set_self_consumption(suffix, live=True)
            return _record(site=site, device_time=device_time, status="idle_self_use_mode",
                           solver_status=solver_status, accepted_incumbent=False, mip_gap=None,
                           planned_flow="idle",
                           planned_power_w=planned_power, command_mode=FALLBACK_COMMAND_MODE,
                           command_flow="self_consumption", target_w=None,
                           readback_verdict=_fallback_readback(readback_state, live=live),
                           realized_soc=_soc(readback_state), optimizer_attributed=False,
                           writes_performed=int(live), reason="zero_flow_unverified_explicit_self_use")
        # A verified idle is executed as the third-party charge=0 command,
        # whatever mapping policy produced the candidate.
        command_flow, target_w, command_mode = "charge", 0.0, "zero_flow_command"
    else:
        raise ValueError(f"unknown planned flow {planned_flow!r}; refusing implicit handling")

    if live:
        _reserve_delivery_budget(DISPATCH_DELIVERY_BUDGET)
        ha.set_dispatch(suffix, command_flow, target_w, live=True)
    status = "dispatched" if command_mode == "dispatch" else "zero_flow_dispatched"
    return _record(site=site, device_time=device_time, status=status, solver_status=solver_status,
                   accepted_incumbent=bool(candidate.get("accepted_incumbent", False)),
                   mip_gap=candidate.get("mip_gap"),
                   planned_flow=planned_flow, planned_power_w=planned_power,
                   command_mode=command_mode, command_flow=command_flow, target_w=target_w,
                   readback_verdict=_command_readback(command_flow, target_w, readback_state,
                                                      live=live, device_time=device_time,
                                                      issued_at=issued_at, now=now,
                                                      tolerance_w=readback_tolerance_w),
                   realized_soc=_soc(readback_state), optimizer_attributed=True,
                   writes_performed=int(live), reason=None)


def check_soc_divergence(*, planned_flow, planned_soc_percent, actual_soc_percent,
                         zero_flow_verified,
                         threshold_percent=SOC_DIVERGENCE_THRESHOLD_PERCENT):
    """Per-tick SOC divergence guard (design step 3, G4).

    Compares the realized SOC read back at the next tick with the previous
    step's ``planned_soc_after_15min``. Only a *verified* idle step can revoke
    ``zero_flow_verified``: divergence there means the device did not honor the
    charge=0 command, so the site falls back to explicit self-use and the
    verdict must be persisted by the caller. Dispatch-step divergence is
    recorded for PV/load calibration monitoring only. Missing or non-finite
    input never triggers an action (R5). The returned dict is the diagnostic
    snapshot; it carries no side effects.
    """
    def finite(value):
        try:
            return math.isfinite(float(value))
        except (TypeError, ValueError):
            return False

    base = {"planned_flow": planned_flow, "planned_soc_percent": None,
            "actual_soc_percent": None, "soc_divergence_percent": None,
            "threshold_percent": float(threshold_percent),
            "zero_flow_verified_before": bool(zero_flow_verified),
            "zero_flow_verified_after": bool(zero_flow_verified)}
    if not math.isfinite(float(threshold_percent)) or threshold_percent < 0:
        return base | {"guard_action": "none", "reason": "invalid_threshold"}
    if planned_flow not in ("idle", "charge", "discharge"):
        return base | {"guard_action": "none", "reason": "unknown_planned_flow"}
    if not (finite(planned_soc_percent) and finite(actual_soc_percent)):
        return base | {"guard_action": "none", "reason": "guard_input_missing"}

    planned, actual = float(planned_soc_percent), float(actual_soc_percent)
    divergence = actual - planned
    base.update({"planned_soc_percent": planned, "actual_soc_percent": actual,
                 "soc_divergence_percent": divergence})
    if abs(divergence) <= float(threshold_percent):
        return base | {"guard_action": "none", "reason": "within_threshold"}
    if planned_flow == "idle":
        if not zero_flow_verified:
            # No zero-flow command was sent, so there is nothing to revoke;
            # SOC movement under an explicit self-use step is expected.
            return base | {"guard_action": "none", "reason": "idle_unverified_no_command"}
        return base | {"guard_action": "revoke_zero_flow_verified",
                       "zero_flow_verified_after": False,
                       "reason": "idle_soc_diverged_device_not_honoring_zero_flow"}
    return base | {"guard_action": "record_only",
                   "reason": "dispatch_soc_diverged_recorded_for_calibration"}
