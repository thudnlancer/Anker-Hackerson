"""Coherent source reports are required before HA states become readback evidence."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest

from energy_explain.execution import SubmissionReceipt, TelemetryReadback, verify_readback
from energy_explain.ha_observation import ObservationUnavailable, build_telemetry_readback


WALL = datetime(2026, 10, 14, tzinfo=timezone.utc)
DEVICE = datetime(2031, 1, 1, tzinfo=timezone.utc)
SUFFIX = "anker_solix_solarbank_max_ac_test1"
CAPTURE = WALL + timedelta(seconds=4)


def receipt():
    return SubmissionReceipt(run_id="run1", site_id="1", decision_id="decision1",
        instruction_id="instruction1", event_id="submission1", issued_device_time=DEVICE,
        issued_wall_time=WALL, completed_wall_time=WALL + timedelta(seconds=1),
        mode="third_party_control", flow="charge", target_kw=2,
        semantics="battery_charge_target", source_ref="offline-sender", sender_status="accepted")


def entities():
    values = {"solar_power": 400, "home_load": 1000, "battery_charging_power": 2000,
              "battery_discharging_power": 0, "grid_import_power": 2600, "grid_export_power": 0}
    result = []
    for index, (name, value) in enumerate(values.items()):
        stamp = WALL + timedelta(seconds=2, milliseconds=20 * index)
        result.append(dict(entity_id=f"sensor.{SUFFIX}_{name}", state=str(value),
            attributes={"unit_of_measurement": "W"}, last_changed=WALL.isoformat(),
            last_updated=WALL.isoformat(), last_reported=stamp.isoformat()))
    result.append(dict(entity_id=f"sensor.{SUFFIX}_device_time",
        state=(DEVICE + timedelta(minutes=15)).isoformat(), attributes={},
        last_changed=(WALL + timedelta(seconds=2)).isoformat(),
        last_updated=(WALL + timedelta(seconds=2)).isoformat(),
        last_reported=(WALL + timedelta(seconds=2, milliseconds=120)).isoformat()))
    for domain, name, value, attrs in [
        ("select", "operating_mode_device_runs_by_command_in_third_party_controlled", "third_party_control", {}),
        ("select", "grid_flow", "charge", {}),
        ("number", "power_control", "2000", {"unit_of_measurement": "W"}),
    ]:
        result.append(dict(entity_id=f"{domain}.{SUFFIX}_{name}", state=value, attributes=attrs,
            last_changed=WALL.isoformat(), last_updated=(WALL + timedelta(milliseconds=800)).isoformat()))
    return result


def convert(items=None, **kwargs):
    return build_telemetry_readback(entities() if items is None else items, suffix=SUFFIX,
        receipt=receipt(), captured_wall_time=CAPTURE, **kwargs)


def bracket(items):
    return deepcopy(next(item for item in items if item["entity_id"].endswith("_device_time")))


def test_reported_unchanged_values_have_real_source_time_and_exact_receipt_identity():
    observation = convert()
    assert isinstance(observation, TelemetryReadback)
    assert observation.updated_at == WALL + timedelta(seconds=2)
    assert observation.observed_at == CAPTURE
    assert observation.device_time == DEVICE + timedelta(minutes=15)
    assert observation.charge_kw == 2 and observation.grid_import_kw == 2.6
    assert (observation.run_id, observation.site_id, observation.decision_id, observation.instruction_id) == (
        "run1", "1", "decision1", "instruction1")
    assert verify_readback(receipt(), observation, now=CAPTURE).status == "verified"
    assert convert() == observation


def test_different_instruction_cannot_reuse_observation_identity():
    other = receipt().model_copy(update={"instruction_id": "instruction2"})
    observation = build_telemetry_readback(entities(), suffix="test1", receipt=other,
                                         captured_wall_time=CAPTURE)
    assert observation.instruction_id == "instruction2"
    assert observation.observation_id != convert().observation_id
    assert verify_readback(receipt(), observation, now=CAPTURE).status == "mismatch"


def test_fallback_last_updated_requires_brackets_and_fresh_cohort_for_every_power():
    items = entities()
    for item in items:
        if "last_reported" in item:
            item["last_updated"] = item.pop("last_reported")
    assert convert(items).reason == "source_coherence_requires_device_clock_bracket"
    observation = convert(items, device_clock_before=bracket(items), device_clock_after=bracket(items))
    assert isinstance(observation, TelemetryReadback)
    assert verify_readback(receipt(), observation, now=CAPTURE).status == "verified"
    items[0]["last_updated"] = WALL.isoformat()
    assert convert(items, device_clock_before=bracket(items), device_clock_after=bracket(items)).reason == "mixed_source_tick"


@pytest.mark.parametrize("position", ["before", "after"])
def test_device_clock_crossing_a_tick_rejects_even_balanced_state(position):
    items = entities()
    before, after = bracket(items), bracket(items)
    changed = before if position == "before" else after
    changed["state"] = (DEVICE + timedelta(minutes=30)).isoformat()
    assert convert(items, device_clock_before=before, device_clock_after=after).reason == "device_tick_changed"


def test_incomplete_or_wrong_station_brackets_do_not_establish_coherence():
    items = entities()
    clock = bracket(items)
    assert convert(items, device_clock_before=clock).reason == "incomplete_device_clock_bracket"
    clock["entity_id"] = clock["entity_id"].replace("test1", "test2")
    assert convert(items, device_clock_before=clock, device_clock_after=clock).reason == "invalid_device_clock_bracket"


def test_source_clock_regression_rejected_in_brackets():
    items = entities()
    before, after = bracket(items), bracket(items)
    before["last_reported"] = (WALL + timedelta(seconds=3)).isoformat()
    assert convert(items, device_clock_before=before, device_clock_after=after).reason == "device_clock_source_regression"


@pytest.mark.parametrize("index", list(range(10)))
def test_each_required_entity_must_be_present(index):
    items = entities()
    missing = items.pop(index)
    result = convert(items)
    assert result == ObservationUnavailable("missing_entity", missing["entity_id"])


def test_duplicate_station_entity_is_not_arbitrarily_selected():
    items = entities()
    items.append(deepcopy(items[0]))
    assert convert(items).reason == "duplicate_entity"


@pytest.mark.parametrize("unit", [None, "Wh", "kWh", "w", "%"])
def test_unknown_power_units_never_assume_watts(unit):
    items = entities()
    items[0]["attributes"]["unit_of_measurement"] = unit
    assert convert(items).reason == "unsupported_power_unit"


def test_explicit_kw_units_are_not_converted_twice():
    items = entities()
    items[0]["attributes"]["unit_of_measurement"] = "kW"
    items[0]["state"] = ".4"
    assert convert(items).pv_kw == .4


@pytest.mark.parametrize("value", [None, "unavailable", "unknown", "nan", "inf", "-1", True])
def test_missing_nonfinite_or_negative_power_is_unavailable(value):
    items = entities()
    items[0]["state"] = value
    assert convert(items).reason == "invalid_power"


def test_old_last_changed_is_not_a_report_timestamp_and_capture_does_not_refresh_it():
    items = entities()
    for item in items:
        item.pop("last_reported", None)
        item.pop("last_updated", None)
    assert convert(items, device_clock_before=bracket(items), device_clock_after=bracket(items)).reason == "missing_source_timestamp"


@pytest.mark.parametrize("stamp", [None, "wrong", "2026-10-14T00:00:02"])
def test_invalid_or_naive_source_times_are_not_replaced_with_capture(stamp):
    items = entities()
    items[0]["last_reported"] = stamp
    assert convert(items).reason == "invalid_source_timestamp"


def test_future_report_timestamp_is_explicitly_unavailable():
    items = entities()
    items[0]["last_reported"] = (CAPTURE + timedelta(microseconds=1)).isoformat()
    assert convert(items).reason == "source_timestamp_after_capture"


def test_mixed_power_reports_cannot_be_balanced_into_a_confirmation():
    items = entities()
    items[1]["last_reported"] = WALL.isoformat()
    assert convert(items).reason == "mixed_source_tick"


def test_ended_contest_report_pattern_keeps_old_constant_values_unavailable():
    # Minimal source-clock pattern from the read-only preflight: clock/PV/active
    # charge keep reporting, while load and unchanged zero flows stay much older.
    # The numbers deliberately use the balanced synthetic fixture above.
    items = entities()
    fresh = "2026-10-14T07:18:07.312710+00:00"
    for index in (0, 2, 6):
        items[index]["last_reported"] = fresh
    items[1]["last_reported"] = "2026-10-14T06:29:47.313544+00:00"
    items[3]["last_reported"] = "2026-10-14T06:29:27.313475+00:00"
    items[4]["last_reported"] = "2026-10-14T05:33:57.313930+00:00"
    items[5]["last_reported"] = "2026-10-14T06:25:27.314027+00:00"
    result = build_telemetry_readback(items, suffix=SUFFIX, receipt=receipt(),
        captured_wall_time=datetime(2026, 10, 14, 7, 18, 10, tzinfo=timezone.utc),
        device_clock_before=bracket(items), device_clock_after=bracket(items))
    assert result == ObservationUnavailable("mixed_source_tick")


def test_source_cohort_with_old_device_tick_remains_pending_in_verifier():
    items = entities()
    items[6]["state"] = DEVICE.isoformat()
    observation = convert(items)
    assert isinstance(observation, TelemetryReadback)
    assert verify_readback(receipt(), observation, now=CAPTURE).reasons == ("awaiting_device_tick",)


def test_explicit_device_coordinate_required_for_naive_device_time():
    items = entities()
    items[6]["state"] = "2031-01-01 00:15:00"
    assert convert(items).reason == "ambiguous_device_timezone"
    assert convert(items, device_timezone="UTC").device_time == DEVICE + timedelta(minutes=15)


def test_ambiguous_dst_device_hour_is_not_guessed():
    items = entities()
    items[6]["state"] = "2026-11-01 01:15:00"
    assert convert(items, device_timezone="America/New_York").reason == "invalid_device_time"


def test_real_control_or_physical_mismatches_are_preserved_for_verifier():
    items = entities()
    items[7]["state"] = "self_consumption"
    items[9]["state"] = "3000"
    observation = convert(items)
    assert isinstance(observation, TelemetryReadback)
    result = verify_readback(receipt(), observation, now=CAPTURE)
    assert result.status == "mismatch"
    assert set(result.reasons) == {"command_state_mismatch", "setpoint_mismatch"}


@pytest.mark.parametrize("index,value,reason", [(7, "unavailable", "invalid_operating_mode"),
                                               (8, "idle", "invalid_grid_flow")])
def test_invalid_control_values_are_not_replaced_with_receipt_values(index, value, reason):
    items = entities()
    items[index]["state"] = value
    assert convert(items).reason == reason


def test_old_source_cohort_stays_old_even_after_a_new_capture():
    observation = build_telemetry_readback(entities(), suffix=SUFFIX, receipt=receipt(),
        captured_wall_time=CAPTURE + timedelta(seconds=20))
    assert observation.updated_at == WALL + timedelta(seconds=2)
    assert "stale_readback" in verify_readback(receipt(), observation, now=observation.observed_at).reasons


@pytest.mark.parametrize("items", [{}, "states", [None], [{"state": "2000"}]])
def test_invalid_entity_response_has_a_fixed_diagnostic(items):
    assert convert(items).reason == "invalid_entity_list"


def test_naive_capture_time_does_not_become_source_time():
    result = build_telemetry_readback(entities(), suffix=SUFFIX, receipt=receipt(),
                                    captured_wall_time=CAPTURE.replace(tzinfo=None))
    assert result.reason == "invalid_capture_timestamp"


@pytest.mark.parametrize("window", [True, -1, float("nan"), 5, 10])
def test_coherence_window_cannot_span_a_full_poll_period(window):
    with pytest.raises(ValueError):
        convert(source_coherence_seconds=window)
