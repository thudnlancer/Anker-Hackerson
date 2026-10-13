"""External receipts and telemetry are evidence, never a reason to write HA."""
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from energy_explain.execution import (
    ReadbackResult, SubmissionReceipt, TelemetryReadback,
    build_execution_event, verify_readback,
)
from energy_explain.schemas import ExecutionEvent


WALL = datetime(2026, 10, 14, tzinfo=timezone.utc)
DEVICE = datetime(2031, 1, 1, tzinfo=timezone.utc)
NOW = WALL + timedelta(seconds=4)


def receipt_dict(flow="charge"):
    return dict(run_id="live-run", site_id="1", decision_id="tick-123",
        instruction_id="sender-instruction-123", event_id="submission-123",
        issued_device_time=DEVICE, issued_wall_time=WALL,
        completed_wall_time=WALL + timedelta(seconds=1),
        mode="third_party_control", flow=flow, target_kw=2 if flow == "charge" else 0.5,
        semantics="battery_charge_target" if flow == "charge" else "grid_export_target",
        source_ref="trusted-external-sender", sender_status="accepted",
        raw_receipt={"request_id": "request-123", "http_status": 200,
                     "services_expected": 3, "services_completed": 3})


def readback_dict(flow="charge"):
    values = dict(run_id="live-run", site_id="1", decision_id="tick-123",
        instruction_id="sender-instruction-123", observation_id="observation-123",
        observed_at=WALL + timedelta(seconds=3), updated_at=WALL + timedelta(seconds=2),
        device_time=DEVICE + timedelta(minutes=15), mode="third_party_control", flow=flow,
        setpoint_kw=2 if flow == "charge" else 0.5, pv_kw=0.4, load_kw=1,
        charge_kw=2 if flow == "charge" else 0,
        discharge_kw=0 if flow == "charge" else 1.1,
        grid_import_kw=2.6 if flow == "charge" else 0,
        grid_export_kw=0 if flow == "charge" else 0.5)
    return values


def check(receipt_changes=None, readback_changes=None, *, flow="charge", **kwargs):
    receipt = receipt_dict(flow)
    readback = readback_dict(flow)
    receipt.update(receipt_changes or {})
    readback.update(readback_changes or {})
    return verify_readback(SubmissionReceipt(**receipt), TelemetryReadback(**readback), now=NOW, **kwargs)


@pytest.mark.parametrize("flow,measured", [("charge", 2), ("discharge", 0.5)])
def test_valid_post_submission_event_preserves_clocks_and_command_semantics(flow, measured):
    receipt = SubmissionReceipt(**receipt_dict(flow))
    readback = TelemetryReadback(**readback_dict(flow))
    result = verify_readback(receipt, readback, now=NOW)
    assert result.status == "verified"
    assert result.reasons == ()
    assert result.evidence_scope == "post_submission_telemetry_match"
    event = ExecutionEvent.model_validate(build_execution_event(receipt, readback, result))
    assert event.kind == "confirmed"
    assert event.issued_at == DEVICE
    assert event.observed_at == WALL + timedelta(seconds=3)
    assert event.device_time == DEVICE + timedelta(minutes=15)
    assert event.observed_mode == flow
    assert event.observed_power_kw == measured
    assert "不是" in event.note and "硬件 ACK" in event.note
    assert build_execution_event(receipt, readback, result) == event.model_dump(mode="json")


def test_matching_ha_state_without_submission_never_confirms():
    result = verify_readback(None, TelemetryReadback(**readback_dict()), now=NOW)
    assert result.status == "unsupported"
    assert result.reasons == ("missing_submission",)
    assert result.receipt_sha256 is None
    waiting = verify_readback(SubmissionReceipt(**receipt_dict()), None, now=NOW)
    assert waiting.status == "pending"
    assert waiting.reasons == ("awaiting_readback",)


@pytest.mark.parametrize("field,value,reason", [
    ("device_time", DEVICE, "awaiting_device_tick"),
    ("device_time", DEVICE - timedelta(minutes=1), "awaiting_device_tick"),
    ("updated_at", WALL + timedelta(seconds=1), "awaiting_fresh_readback"),
    ("updated_at", WALL, "awaiting_fresh_readback"),
    ("observed_at", WALL + timedelta(seconds=1), "clock_unverified"),
])
def test_old_ticks_and_pre_submission_or_inconsistent_wall_times_remain_pending(field, value, reason):
    result = check(readback_changes={field: value})
    assert result.status == "pending"
    assert reason in result.reasons


@pytest.mark.parametrize("field", ["updated_at", "observed_at"])
def test_future_wall_clock_is_unverified(field):
    result = check(readback_changes={field: NOW + timedelta(seconds=2, microseconds=1)})
    assert result.status == "pending"
    assert result.reasons == ("clock_unverified",)


def test_future_submission_wall_clock_is_unverified_without_comparing_device_clock():
    result = check(receipt_changes={"issued_wall_time": NOW + timedelta(seconds=3),
        "completed_wall_time": NOW + timedelta(seconds=4)})
    assert result.status == "pending"
    assert result.reasons == ("clock_unverified",)
    assert check().status == "verified"  # Device 2031 vs wall 2026 is intentional.


def test_freshness_boundary_and_old_updated_measurements():
    receipt = SubmissionReceipt(**receipt_dict())
    telemetry = TelemetryReadback(**readback_dict())
    boundary = telemetry.updated_at + timedelta(seconds=15)
    assert verify_readback(receipt, telemetry, now=boundary).status == "verified"
    expired = verify_readback(receipt, telemetry, now=boundary + timedelta(microseconds=1))
    assert expired.status == "pending" and "stale_readback" in expired.reasons
    # Recapturing an old measurement cannot refresh its source update time.
    values = readback_dict()
    values["observed_at"] = boundary + timedelta(seconds=1)
    assert verify_readback(receipt, TelemetryReadback(**values), now=values["observed_at"]).status == "pending"


@pytest.mark.parametrize("field", ["run_id", "site_id", "decision_id", "instruction_id"])
def test_exact_association_is_required(field):
    result = check(readback_changes={field: "different"})
    assert result.status == "mismatch" and result.reasons == ("identity_mismatch",)


@pytest.mark.parametrize("changes,reason", [
    ({"mode": "self_consumption"}, "command_state_mismatch"),
    ({"flow": "discharge"}, "command_state_mismatch"),
    ({"setpoint_kw": 2.001}, "setpoint_mismatch"),
    ({"grid_import_kw": 2.71}, "power_balance_mismatch"),
    ({"charge_kw": 2.11, "grid_import_kw": 2.71}, "power_mismatch"),
    ({"discharge_kw": 0.11, "grid_import_kw": 2.49}, "opposite_battery_flow"),
    ({"discharge_kw": 0.11, "grid_import_kw": 2.49}, "battery_mutual_exclusion"),
    ({"grid_import_kw": 3.6, "grid_export_kw": 1}, "grid_mutual_exclusion"),
])
def test_control_and_physical_mismatches_do_not_confirm(changes, reason):
    result = check(readback_changes=changes)
    assert result.status == "mismatch" and reason in result.reasons


def test_zero_export_target_does_not_confirm_while_grid_still_supplies_load():
    # The former control verifier passed this perfectly conserved but wrong state.
    result = check(flow="discharge", receipt_changes={"target_kw": 0},
        readback_changes={"setpoint_kw": 0, "discharge_kw": 0, "grid_import_kw": 0.6, "grid_export_kw": 0})
    assert result.status == "mismatch"
    assert "unexpected_grid_import" in result.reasons
    assert "power_balance_mismatch" not in result.reasons
    valid = check(flow="discharge", receipt_changes={"target_kw": 0},
        readback_changes={"setpoint_kw": 0, "discharge_kw": 0.6, "grid_export_kw": 0})
    assert valid.status == "verified"


@pytest.mark.parametrize("flow,offset", [("charge", -0.1), ("charge", 0.1), ("discharge", -0.1), ("discharge", 0.1)])
def test_positive_and_negative_measured_tolerance_boundaries(flow, offset):
    changes = ({"charge_kw": 2 + offset, "grid_import_kw": 2.6 + offset} if flow == "charge"
               else {"grid_export_kw": 0.5 + offset, "discharge_kw": 1.1 + offset})
    assert check(flow=flow, readback_changes=changes).status == "verified"
    values = readback_dict(flow)
    changes = ({"charge_kw": 2 + offset * 1.00001, "grid_import_kw": 2.6 + offset * 1.00001} if flow == "charge"
               else {"grid_export_kw": 0.5 + offset * 1.00001, "discharge_kw": 1.1 + offset * 1.00001})
    values.update(changes)
    assert check(flow=flow, readback_changes=values).status == "mismatch"


def test_setpoint_tolerance_and_power_balance_boundaries_are_independent():
    assert check(readback_changes={"setpoint_kw": 2.000001}).status == "verified"
    assert check(readback_changes={"setpoint_kw": 2.000001001}).status == "mismatch"
    assert check(readback_changes={"grid_import_kw": 2.7}).status == "verified"
    assert check(readback_changes={"grid_import_kw": 2.700001}).status == "mismatch"
    assert check(flow="discharge", receipt_changes={"target_kw": 0}, readback_changes={
        "setpoint_kw": 0, "grid_export_kw": 0, "grid_import_kw": 0.1, "discharge_kw": 0.5}).status == "verified"


@pytest.mark.parametrize("changes", [
    {"sender_status": "partial"}, {"sender_status": "failed"},
    {"sender_status": "unknown"}, {"raw_receipt": {"http_status": 500}},
    {"raw_receipt": {"services_expected": 3, "services_completed": 2}},
    {"raw_receipt": {"services_completed": 3}},
    {"raw_receipt": {"services_expected": 0, "services_completed": 0}},
    {"flow": "hold"}, {"mode": "self_consumption"},
    {"flow": "discharge", "semantics": "battery_charge_target"},
    {"source_ref": "  "},
])
def test_partial_unknown_or_unrepresentable_submissions_cannot_be_accepted(changes):
    values = receipt_dict()
    values.update(changes)
    with pytest.raises(ValidationError):
        SubmissionReceipt(**values)


@pytest.mark.parametrize("extra", ["headers", "password", "token", "body", "config"])
def test_raw_receipts_reject_sensitive_or_unnecessary_fields(extra):
    values = receipt_dict()
    values["raw_receipt"] = {extra: "do-not-store"}
    with pytest.raises(ValidationError):
        SubmissionReceipt(**values)


@pytest.mark.parametrize("field", ["setpoint_kw", "pv_kw", "load_kw", "charge_kw", "discharge_kw", "grid_import_kw", "grid_export_kw"])
@pytest.mark.parametrize("value", [-0.01, float("nan"), float("inf")])
def test_measurements_require_finite_nonnegative_numbers(field, value):
    values = readback_dict()
    values[field] = value
    with pytest.raises(ValidationError):
        TelemetryReadback(**values)


@pytest.mark.parametrize("field", ["issued_device_time", "issued_wall_time", "completed_wall_time"])
def test_submission_requires_aware_clocks(field):
    values = receipt_dict()
    values[field] = WALL.replace(tzinfo=None)
    with pytest.raises(ValidationError):
        SubmissionReceipt(**values)


@pytest.mark.parametrize("field", ["observed_at", "updated_at", "device_time"])
def test_telemetry_requires_aware_clocks(field):
    values = readback_dict()
    values[field] = WALL.replace(tzinfo=None)
    with pytest.raises(ValidationError):
        TelemetryReadback(**values)


def test_frozen_models_extra_forbid_and_receipt_metadata_cannot_mutate():
    receipt = SubmissionReceipt(**receipt_dict())
    telemetry = TelemetryReadback(**readback_dict())
    result = verify_readback(receipt, telemetry, now=NOW)
    for instance, field in ((receipt, "target_kw"), (telemetry, "charge_kw"), (result, "status")):
        with pytest.raises(ValidationError):
            setattr(instance, field, "changed")
        with pytest.raises(ValidationError):
            type(instance).model_validate({**instance.model_dump(), "unexpected": "value"})
    with pytest.raises(TypeError):
        receipt.raw_receipt["http_status"] = 500
    assert receipt.model_dump(mode="json")["raw_receipt"]["http_status"] == 200
    assert SubmissionReceipt.model_validate_json(receipt.model_dump_json()) == receipt


def test_verification_results_cannot_be_reused_or_fabricated_for_different_evidence():
    receipt = SubmissionReceipt(**receipt_dict())
    readback = TelemetryReadback(**readback_dict())
    result = verify_readback(receipt, readback, now=NOW)
    changed = TelemetryReadback(**{**readback_dict(), "observation_id": "other-observation"})
    with pytest.raises(ValueError, match="does not match"):
        build_execution_event(receipt, changed, result)
    mismatch = TelemetryReadback(**{**readback_dict(), "grid_import_kw": 0})
    fabricated = ReadbackResult(status="verified", checked_at=NOW)
    with pytest.raises(ValueError, match="does not match"):
        build_execution_event(receipt, mismatch, fabricated)
    pending = verify_readback(receipt, None, now=NOW)
    with pytest.raises(ValueError, match="only a verified"):
        build_execution_event(receipt, readback, pending)


def test_observation_identity_stays_stable_to_allow_append_conflict_detection():
    receipt = SubmissionReceipt(**receipt_dict())
    readback = TelemetryReadback(**readback_dict())
    changed = TelemetryReadback(**{**readback_dict(), "charge_kw": 2.05, "grid_import_kw": 2.65})
    first = build_execution_event(receipt, readback, verify_readback(receipt, readback, now=NOW))
    second = build_execution_event(receipt, changed, verify_readback(receipt, changed, now=NOW))
    assert first["event_id"] == second["event_id"]
    assert first["observed_power_kw"] != second["observed_power_kw"]


@pytest.mark.parametrize("parameter", ["max_age_seconds", "tolerance_kw"])
@pytest.mark.parametrize("value", [-1, float("nan"), float("inf"), True])
def test_invalid_verifier_parameters_cannot_relax_checks(parameter, value):
    with pytest.raises(ValueError):
        check(**{parameter: value})
