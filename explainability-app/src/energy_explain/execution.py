"""Validate externally submitted commands against subsequent telemetry; never control HA.

The external sender must supply an accepted receipt. A matching HA state alone is
not a submission receipt or a device acknowledgement. Device and wall clocks are
kept separate throughout this module.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math
from types import MappingProxyType
from typing import Annotated, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator, model_validator

from .schemas import ExecutionEvent, Identifier

NonNegative = Annotated[float, Field(ge=0)]
Fingerprint = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
RAW_RECEIPT_FIELDS = frozenset({"request_id", "http_status", "services_expected", "services_completed"})
SETPOINT_TOLERANCE_KW = 0.000001  # 0.001 W; measured power has a separate tolerance.
FUTURE_WALL_TOLERANCE_SECONDS = 2.0


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamps require an explicit timezone")
    return value.astimezone(timezone.utc)


class _FrozenEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False, str_strip_whitespace=True)


class SubmissionReceipt(_FrozenEvidence):
    run_id: Identifier
    site_id: Identifier
    decision_id: Identifier
    instruction_id: Identifier
    event_id: Identifier
    issued_device_time: datetime
    issued_wall_time: datetime
    completed_wall_time: datetime
    mode: Literal["third_party_control"]
    flow: Literal["charge", "discharge"]
    target_kw: NonNegative
    semantics: Literal["grid_export_target", "battery_charge_target"]
    source_ref: Annotated[str, Field(min_length=1, max_length=500)]
    sender_status: Literal["accepted"]
    raw_receipt: Mapping[str, str | int] = Field(default_factory=dict, validate_default=True)

    @field_validator("issued_device_time", "issued_wall_time", "completed_wall_time")
    @classmethod
    def aware(cls, value):
        return _aware(value)

    @field_validator("raw_receipt", mode="before")
    @classmethod
    def whitelist_receipt(cls, value):
        if not isinstance(value, Mapping) or set(value) - RAW_RECEIPT_FIELDS:
            raise ValueError("raw_receipt allows only request_id, http_status and service counts")
        safe = dict(value)
        if "request_id" in safe:
            request_id = safe["request_id"]
            if not isinstance(request_id, str) or not request_id.strip() or len(request_id) > 120:
                raise ValueError("request_id must be a nonempty identifier of at most 120 characters")
        for name in ("http_status", "services_expected", "services_completed"):
            if name in safe and (type(safe[name]) is not int or safe[name] < 0):
                raise ValueError(f"{name} must be a nonnegative integer")
        if "http_status" in safe and not 200 <= safe["http_status"] < 300:
            raise ValueError("an accepted receipt cannot contain a failed HTTP status")
        counts = {"services_expected", "services_completed"}
        if counts & set(safe):
            if not counts <= set(safe) or safe["services_expected"] <= 0 or safe["services_completed"] != safe["services_expected"]:
                raise ValueError("an accepted receipt requires all expected services to have completed")
        return safe

    @field_serializer("raw_receipt")
    def serialize_receipt(self, value):
        return dict(value)

    @model_validator(mode="after")
    def coherent(self):
        expected = "grid_export_target" if self.flow == "discharge" else "battery_charge_target"
        if self.semantics != expected:
            raise ValueError("command flow and target semantics disagree")
        if self.completed_wall_time < self.issued_wall_time:
            raise ValueError("submission completion cannot predate issue on the wall clock")
        object.__setattr__(self, "raw_receipt", MappingProxyType(dict(self.raw_receipt)))
        return self


class TelemetryReadback(_FrozenEvidence):
    run_id: Identifier
    site_id: Identifier
    decision_id: Identifier
    instruction_id: Identifier
    observation_id: Identifier
    observed_at: datetime  # Wall clock: when the observation was captured.
    updated_at: datetime  # Wall clock: update time of the coherent HA telemetry.
    device_time: datetime
    mode: Annotated[str, Field(min_length=1, max_length=120)]
    flow: Annotated[str, Field(min_length=1, max_length=120)]
    setpoint_kw: NonNegative
    pv_kw: NonNegative
    load_kw: NonNegative
    charge_kw: NonNegative
    discharge_kw: NonNegative
    grid_import_kw: NonNegative
    grid_export_kw: NonNegative

    @field_validator("observed_at", "updated_at", "device_time")
    @classmethod
    def aware(cls, value):
        return _aware(value)


class ReadbackResult(_FrozenEvidence):
    status: Literal["verified", "pending", "mismatch", "unsupported"]
    reasons: tuple[str, ...] = ()
    evidence_scope: Literal["post_submission_telemetry_match"] = "post_submission_telemetry_match"
    checked_at: datetime
    max_age_seconds: NonNegative = 15
    tolerance_kw: NonNegative = 0.1
    receipt_sha256: Fingerprint | None = None
    readback_sha256: Fingerprint | None = None

    @field_validator("checked_at")
    @classmethod
    def aware(cls, value):
        return _aware(value)


def _identity(evidence) -> tuple[str, ...]:
    return tuple(getattr(evidence, name) for name in ("run_id", "site_id", "decision_id", "instruction_id"))


def _fingerprint(evidence) -> str | None:
    if evidence is None:
        return None
    data = json.dumps(evidence.model_dump(mode="json"), sort_keys=True, ensure_ascii=False,
                      allow_nan=False, separators=(",", ":"))
    return hashlib.sha256(data.encode()).hexdigest()


def _within(value: float, tolerance: float) -> bool:
    # Do not reject an exact tolerance boundary because of decimal subtraction.
    return value <= tolerance + 1e-12


def verify_readback(receipt: SubmissionReceipt | None, telemetry: TelemetryReadback | None,
                    *, now: datetime, max_age_seconds: float = 15,
                    tolerance_kw: float = 0.1) -> ReadbackResult:
    """Check association, both clocks, control state and measured power balance.

    ``updated_at`` must represent a coherent observation of all supplied entities;
    the external observer must not relabel cached or mixed-tick measurements as a
    newly updated snapshot. This verifier makes no network requests or writes.
    """
    now = _aware(now)
    for name, value in (("max_age_seconds", max_age_seconds), ("tolerance_kw", tolerance_kw)):
        if isinstance(value, bool) or not math.isfinite(value) or value < 0:
            raise ValueError(f"{name} must be finite and nonnegative")
    def result(status, *reasons):
        return ReadbackResult(status=status, reasons=reasons, checked_at=now,
            max_age_seconds=max_age_seconds, tolerance_kw=tolerance_kw,
            receipt_sha256=_fingerprint(receipt), readback_sha256=_fingerprint(telemetry))
    if receipt is None:
        return result("unsupported", "missing_submission")
    if telemetry is None:
        return result("pending", "awaiting_readback")
    if _identity(receipt) != _identity(telemetry):
        return result("mismatch", "identity_mismatch")
    wall_times = (receipt.issued_wall_time, receipt.completed_wall_time,
                  telemetry.updated_at, telemetry.observed_at)
    if any((value - now).total_seconds() > FUTURE_WALL_TOLERANCE_SECONDS for value in wall_times):
        return result("pending", "clock_unverified")
    if telemetry.observed_at < telemetry.updated_at:
        return result("pending", "clock_unverified")
    pending = []
    if telemetry.device_time <= receipt.issued_device_time:
        pending.append("awaiting_device_tick")
    if telemetry.updated_at <= receipt.completed_wall_time:
        pending.append("awaiting_fresh_readback")
    if any((now - value).total_seconds() > max_age_seconds for value in (telemetry.updated_at, telemetry.observed_at)):
        pending.append("stale_readback")
    if pending:
        return result("pending", *pending)

    reasons = []
    if telemetry.mode != receipt.mode or telemetry.flow != receipt.flow:
        reasons.append("command_state_mismatch")
    if not _within(abs(telemetry.setpoint_kw - receipt.target_kw), SETPOINT_TOLERANCE_KW):
        reasons.append("setpoint_mismatch")
    supply = telemetry.pv_kw + telemetry.grid_import_kw + telemetry.discharge_kw
    demand = telemetry.load_kw + telemetry.charge_kw + telemetry.grid_export_kw
    if not _within(abs(supply - demand), tolerance_kw):
        reasons.append("power_balance_mismatch")
    if not _within(min(telemetry.charge_kw, telemetry.discharge_kw), tolerance_kw):
        reasons.append("battery_mutual_exclusion")
    if not _within(min(telemetry.grid_import_kw, telemetry.grid_export_kw), tolerance_kw):
        reasons.append("grid_mutual_exclusion")
    opposite = telemetry.charge_kw if receipt.flow == "discharge" else telemetry.discharge_kw
    measured = telemetry.grid_export_kw if receipt.flow == "discharge" else telemetry.charge_kw
    if not _within(opposite, tolerance_kw):
        reasons.append("opposite_battery_flow")
    if receipt.flow == "discharge" and not _within(telemetry.grid_import_kw, tolerance_kw):
        reasons.append("unexpected_grid_import")
    if not _within(abs(measured - receipt.target_kw), tolerance_kw):
        reasons.append("power_mismatch")
    return result("mismatch", *reasons) if reasons else result("verified")


def build_execution_event(receipt: SubmissionReceipt, readback: TelemetryReadback,
                          result: ReadbackResult) -> dict:
    """Build a confirmation only for this receipt and this verified observation.

    Recompute the check to reject a fabricated or reused result. This function
    cannot establish whether a receipt's external sender is trusted; the consuming
    API must authenticate that source and persist the matching submission first.
    """
    if result.status != "verified":
        raise ValueError("only a verified readback can generate a confirmation")
    repeated = verify_readback(receipt, readback, now=result.checked_at,
        max_age_seconds=result.max_age_seconds, tolerance_kw=result.tolerance_kw)
    if repeated.status != "verified" or repeated != result:
        raise ValueError("verification result does not match this receipt and readback")
    # Stable observation identity lets the store reject changed contents under a
    # reused observation ID, rather than hiding them behind a fresh event ID.
    identity = json.dumps([*_identity(receipt), readback.observation_id], separators=(",", ":"))
    event_id = "readback-" + hashlib.sha256(identity.encode()).hexdigest()[:40]
    return ExecutionEvent(event_id=event_id, instruction_id=receipt.instruction_id,
        kind="confirmed", issued_at=receipt.issued_device_time,
        device_time=readback.device_time, observed_at=readback.observed_at,
        observed_mode=readback.flow,
        observed_power_kw=readback.grid_export_kw if receipt.flow == "discharge" else readback.charge_kw,
        note="提交后的遥测回读一致（post_submission_telemetry_match）；不是设备对 instruction_id 的硬件 ACK。"
    ).model_dump(mode="json")
