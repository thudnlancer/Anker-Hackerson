"""Convert HA entity observations into evidence without making network requests.

The capture wall clock is only ``observed_at``. Source update time comes from
HA, never from the caller's clock. A state response is usable only when the six
power sensors and device clock belong to one source report cohort. Older HA
versions without ``last_reported`` additionally require unchanged device-clock
observations bracketing the state request; old unchanged powers are not inferred
to have been refreshed merely because that request completed.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone, tzinfo
import hashlib
import json
import math
import re
from zoneinfo import ZoneInfo

from .execution import SubmissionReceipt, TelemetryReadback


_POWER_NAMES = {
    "pv_kw": "solar_power",
    "load_kw": "home_load",
    "charge_kw": "battery_charging_power",
    "discharge_kw": "battery_discharging_power",
    "grid_import_kw": "grid_import_power",
    "grid_export_kw": "grid_export_power",
}
_PREFIX = "anker_solix_solarbank_max_ac_"


@dataclass(frozen=True)
class ObservationUnavailable:
    """A fixed diagnostic code, with no raw HA response or authentication data."""

    reason: str
    entity_id: str | None = None


class _Unavailable(Exception):
    def __init__(self, reason: str, entity_id: str | None = None):
        self.result = ObservationUnavailable(reason, entity_id)


def _timestamp(value, *, entity_id=None) -> datetime:
    try:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError
        return parsed.astimezone(timezone.utc)
    except (AttributeError, TypeError, ValueError, OverflowError):
        raise _Unavailable("invalid_source_timestamp", entity_id) from None


def _source_time(entity: Mapping, capture: datetime) -> tuple[datetime, bool]:
    entity_id = entity["entity_id"]
    reported = "last_reported" in entity
    field = "last_reported" if reported else "last_updated"
    if field not in entity:
        raise _Unavailable("missing_source_timestamp", entity_id)
    stamp = _timestamp(entity[field], entity_id=entity_id)
    if stamp > capture:
        raise _Unavailable("source_timestamp_after_capture", entity_id)
    return stamp, reported


def _device_tick(entity: Mapping, device_timezone) -> datetime:
    try:
        value = entity.get("state")
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            if device_timezone is None:
                raise _Unavailable("ambiguous_device_timezone", entity["entity_id"])
            coordinate = ZoneInfo(device_timezone) if isinstance(device_timezone, str) else device_timezone
            if not isinstance(coordinate, tzinfo):
                raise ValueError
            early, late = parsed.replace(tzinfo=coordinate, fold=0), parsed.replace(tzinfo=coordinate, fold=1)
            if early.utcoffset() != late.utcoffset():
                raise ValueError  # Do not guess across DST gaps or repeated hours.
            parsed = early
        return parsed.astimezone(timezone.utc)
    except _Unavailable:
        raise
    except (AttributeError, TypeError, ValueError, OverflowError, KeyError):
        raise _Unavailable("invalid_device_time", entity.get("entity_id")) from None


def _power_kw(entity: Mapping) -> float:
    entity_id = entity["entity_id"]
    attributes = entity.get("attributes")
    unit = attributes.get("unit_of_measurement") if isinstance(attributes, Mapping) else None
    if unit not in ("W", "kW"):
        raise _Unavailable("unsupported_power_unit", entity_id)
    value = entity.get("state")
    try:
        if isinstance(value, bool):
            raise ValueError
        number = float(value)
        if not math.isfinite(number) or number < 0:
            raise ValueError
        return number / 1000 if unit == "W" else number
    except (TypeError, ValueError, OverflowError):
        raise _Unavailable("invalid_power", entity_id) from None


def build_telemetry_readback(
    states: Sequence[Mapping], *, suffix: str, receipt: SubmissionReceipt,
    captured_wall_time: datetime,
    device_clock_before: Mapping | None = None,
    device_clock_after: Mapping | None = None,
    device_timezone: str | tzinfo | None = None,
    source_coherence_seconds: float = 1.0,
) -> TelemetryReadback | ObservationUnavailable:
    """Build a source-coherent readback, not an execution confirmation.

    ``suffix`` may be the canonical HA prefix or its short device suffix.
    Brackets are raw HA ``device_time`` entities read before/after ``/api/states``;
    when supplied, both must name the expected entity and carry the same device
    tick as the response. Naive device times need an explicitly declared clock
    coordinate. The default one-second source cohort is shorter than HA's
    five-second poll period; callers cannot broaden it to a complete poll period.

    Genuine but mismatching modes, flows and powers remain in the observation for
    ``execution.verify_readback`` to assess. This function neither sets clocks to
    the submission time nor claims that HA acknowledges an instruction ID.
    """
    if (isinstance(source_coherence_seconds, bool)
            or not isinstance(source_coherence_seconds, (int, float))
            or not math.isfinite(source_coherence_seconds)
            or not 0 <= source_coherence_seconds < 5):
        raise ValueError("source coherence must be finite and shorter than the five-second HA poll")
    if not isinstance(receipt, SubmissionReceipt):
        raise TypeError("receipt must be a saved SubmissionReceipt")
    try:
        try:
            capture = _timestamp(captured_wall_time)
        except _Unavailable:
            raise _Unavailable("invalid_capture_timestamp") from None
        if not isinstance(suffix, str) or not re.fullmatch(r"[A-Za-z0-9_]{1,160}", suffix):
            raise _Unavailable("invalid_suffix")
        prefix = suffix if suffix.startswith(_PREFIX) else _PREFIX + suffix
        names = {key: f"sensor.{prefix}_{name}" for key, name in _POWER_NAMES.items()}
        names.update(device_time=f"sensor.{prefix}_device_time",
                     mode=f"select.{prefix}_operating_mode_device_runs_by_command_in_third_party_controlled",
                     flow=f"select.{prefix}_grid_flow", setpoint_kw=f"number.{prefix}_power_control")
        if not isinstance(states, Sequence) or isinstance(states, (str, bytes)):
            raise _Unavailable("invalid_entity_list")
        entities = {}
        wanted = set(names.values())
        for item in states:
            if not isinstance(item, Mapping) or not isinstance(item.get("entity_id"), str):
                raise _Unavailable("invalid_entity_list")
            entity_id = item["entity_id"]
            if entity_id in wanted:
                if entity_id in entities:
                    raise _Unavailable("duplicate_entity", entity_id)
                entities[entity_id] = item
        for entity_id in names.values():
            if entity_id not in entities:
                raise _Unavailable("missing_entity", entity_id)
        selected = {key: entities[entity_id] for key, entity_id in names.items()}
        tick = _device_tick(selected["device_time"], device_timezone)
        times = {key: _source_time(entity, capture) for key, entity in selected.items()}
        bracketed = device_clock_before is not None or device_clock_after is not None
        if bracketed:
            if device_clock_before is None or device_clock_after is None:
                raise _Unavailable("incomplete_device_clock_bracket")
            bracket_times = []
            for bracket in (device_clock_before, device_clock_after):
                if not isinstance(bracket, Mapping) or bracket.get("entity_id") != names["device_time"]:
                    raise _Unavailable("invalid_device_clock_bracket")
                if _device_tick(bracket, device_timezone) != tick:
                    raise _Unavailable("device_tick_changed")
                bracket_times.append(_source_time(bracket, capture)[0])
            if bracket_times[1] < bracket_times[0]:
                raise _Unavailable("device_clock_source_regression")
        cohort = [times[key] for key in (*_POWER_NAMES, "device_time")]
        if any(not reported for _, reported in cohort) and not bracketed:
            raise _Unavailable("source_coherence_requires_device_clock_bracket")
        source_stamps = [stamp for stamp, _ in cohort]
        if (max(source_stamps) - min(source_stamps)).total_seconds() > source_coherence_seconds:
            raise _Unavailable("mixed_source_tick")
        mode, flow = selected["mode"].get("state"), selected["flow"].get("state")
        if mode not in ("third_party_control", "self_consumption"):
            raise _Unavailable("invalid_operating_mode", names["mode"])
        if flow not in ("charge", "discharge"):
            raise _Unavailable("invalid_grid_flow", names["flow"])
        values = {key: _power_kw(selected[key]) for key in (*_POWER_NAMES, "setpoint_kw")}
        data = dict(run_id=receipt.run_id, site_id=receipt.site_id,
                    decision_id=receipt.decision_id, instruction_id=receipt.instruction_id,
                    observed_at=capture, updated_at=min(times[key][0] for key in _POWER_NAMES),
                    device_time=tick, mode=mode, flow=flow, **values)
        encoded = json.dumps(data, sort_keys=True, default=lambda value: value.isoformat(),
                             ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        data["observation_id"] = "ha-" + hashlib.sha256(encoded.encode()).hexdigest()[:40]
        return TelemetryReadback(**data)
    except _Unavailable as exc:
        return exc.result
