"""Pure offline SOP checks. Does not send commands or imply hardware verification."""
from dataclasses import dataclass
import math
from datetime import datetime, timezone

import pandas as pd


@dataclass
class CommandMapping:
    representable: bool
    flow: str | None
    target_w: float | None
    reason: str


@dataclass
class ReadbackCheck:
    verified: bool
    reason: str
    error_w: float | None = None


def state_is_fresh(last_changed, *, now=None, max_age_seconds=15.0, future_tolerance_seconds=2.0):
    """Validate transport freshness; never use device_time as wall-clock time."""
    try:
        if last_changed is None or not math.isfinite(float(max_age_seconds)) or max_age_seconds < 0:
            return False
        if not math.isfinite(float(future_tolerance_seconds)) or future_tolerance_seconds < 0:
            return False
        stamp = pd.Timestamp(last_changed)
        reference = pd.Timestamp(now if now is not None else datetime.now(timezone.utc))
        if pd.isna(stamp) or pd.isna(reference) or stamp.tzinfo is None or reference.tzinfo is None:
            return False
        age = (reference - stamp).total_seconds()
    except (TypeError, ValueError, OverflowError):
        return False
    # HA/server and client wall clocks can differ slightly; never reinterpret
    # device time as wall time. Reject larger skew rather than invent an offset.
    return -future_tolerance_seconds <= age <= max_age_seconds


def verify_readback(command: CommandMapping, state: dict, *, sent_device_time=None, issued_at=None,
                    now=None, max_age_seconds=15.0,
                    tolerance_w=100.0) -> ReadbackCheck:
    """Check only telemetry; never writes or changes device mode."""
    if issued_at is None or sent_device_time is None:
        return ReadbackCheck(False, "not_sent_no_acknowledgement")
    if not state_is_fresh(state.get("last_changed"), now=now, max_age_seconds=max_age_seconds):
        return ReadbackCheck(False, "stale_readback")
    if not command.representable:
        return ReadbackCheck(False, "command_not_representable")
    try:
        updated = pd.Timestamp(state["last_changed"])
        issued = pd.Timestamp(issued_at)
        if pd.isna(issued) or issued.tzinfo is None or updated <= issued:
            return ReadbackCheck(False, "awaiting_fresh_tick")
        tick, sent_tick = pd.Timestamp(state["device_time"]), pd.Timestamp(sent_device_time)
        if pd.isna(tick) or pd.isna(sent_tick) or tick <= sent_tick:
            return ReadbackCheck(False, "awaiting_device_tick")
        if state.get("operating_mode") != "third_party_control" or state.get("grid_flow") != command.flow:
            return ReadbackCheck(False, "command_state_mismatch")
        if not math.isfinite(float(state["power_control"])) or abs(float(state["power_control"])-command.target_w) > 1e-3:
            return ReadbackCheck(False, "setpoint_mismatch")
        tolerance_w = float(tolerance_w)
        if not math.isfinite(tolerance_w) or tolerance_w < 0:
            return ReadbackCheck(False, "invalid_tolerance")
        if command.flow == "charge":
            observed = float(state["battery_charging_power_w"])
            error = abs(observed - float(command.target_w))
            opposite = float(state["battery_discharging_power_w"])
        elif command.flow == "discharge":
            observed = float(state["grid_export_power_w"])
            error = abs(observed - float(command.target_w))
            opposite = float(state["battery_charging_power_w"])
        else:
            return ReadbackCheck(False, "unknown_command_flow")
    except (KeyError, TypeError, ValueError):
        return ReadbackCheck(False, "missing_or_non_numeric_readback")
    if not math.isfinite(error) or not math.isfinite(opposite) or observed < 0 or opposite < 0:
        return ReadbackCheck(False, "invalid_readback")
    if opposite > tolerance_w:
        return ReadbackCheck(False, "opposite_battery_flow", opposite)
    return ReadbackCheck(error <= tolerance_w, "verified" if error <= tolerance_w else "power_mismatch", error)


def map_battery_action(flow, power_w, pv_w, load_w, limit_w=5000., idle_mode=None):
    if not all(math.isfinite(x) for x in (power_w, pv_w, load_w, limit_w)):
        return CommandMapping(False, None, None, "nonfinite_input")
    if min(power_w, pv_w, load_w) < 0 or limit_w <= 0 or power_w > limit_w+1e-3:
        return CommandMapping(False, None, None, "invalid_power")
    if flow in ("idle", "hold"):
        # "hold" is accepted only as the pre-rename idle label read from old
        # artifacts (R6). Planned zero battery flow is never silently mapped:
        # the caller must choose the execution mode explicitly.
        if idle_mode is None:
            return CommandMapping(False, None, None, "idle_requires_explicit_idle_mode")
        if idle_mode == "self_consumption":
            return CommandMapping(True, "self_consumption", 0., "idle_explicit_self_consumption_mode")
        if idle_mode == "zero_flow":
            # Candidate third-party charge=0 command; site-level readback
            # verification is required before any live use.
            return CommandMapping(True, "charge", 0., "idle_zero_flow_command_unverified")
        return CommandMapping(False, None, None, "unknown_idle_mode")
    if flow in ("charge", "discharge") and power_w <= 1e-3:
        # SOP discharge is a grid-export target. A zero target is meaningful:
        # the device may discharge only enough to avoid importing power.
        return CommandMapping(True, flow, 0., "zero_grid_target" if flow == "discharge" else "zero_charge_target")
    if flow == "charge":
        return CommandMapping(True, flow, power_w, "battery_charge_setpoint")
    if flow != "discharge":
        return CommandMapping(False, None, None, "unknown_flow")
    target = power_w+pv_w-load_w
    if target < -1e-3:
        return CommandMapping(False, None, None, "partial_deficit_discharge_requires_negative_export_target")
    if target > limit_w+1e-3:
        return CommandMapping(False, None, None, "export_target_exceeds_command_limit")
    target = max(0., min(limit_w, target))
    implied = max(0., target-(pv_w-load_w))
    assert abs(implied-power_w) <= 1e-3
    return CommandMapping(True, "discharge", target, "grid_export_target")


def align_hourly_points(points, device_time, horizon=24):
    """Use actual timestamps; refuse missing tomorrow instead of wrapping today."""
    if not points or horizon < 1:
        raise ValueError("empty price/weather horizon")
    frame = pd.DataFrame(points)
    time_column = next((c for c in ("time", "timestamp", "datetime", "start_time", "local_hour_time") if c in frame), None)
    if time_column is None:
        raise ValueError("explicit timestamps required")
    frame["time"] = pd.to_datetime(frame[time_column], errors="coerce")
    if frame.time.isna().any():
        raise ValueError("invalid timestamps")
    device_stamp = pd.Timestamp(device_time)
    if pd.isna(device_stamp):
        raise ValueError("invalid device timestamp")
    if (frame.time.dt.tz is None) != (device_stamp.tzinfo is None):
        raise ValueError("ambiguous timezone: do not guess device timezone")
    if not frame.time.eq(frame.time.dt.floor("h")).all():
        raise ValueError("price/weather points must be hourly")
    if frame.time.duplicated().any():
        raise ValueError("duplicate timestamps")
    wanted = pd.date_range(device_stamp.floor("h"), periods=horizon, freq="h")
    frame = frame.set_index("time")
    if not wanted.isin(frame.index).all():
        raise ValueError("forecast horizon incomplete: do not reuse another day's points")
    return frame.loc[wanted].reset_index(names="time")
