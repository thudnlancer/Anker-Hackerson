"""Offline control acceptance: real MILP, immutable Store and durable send journal.

HTTP is mocked at the transport boundary. These tests check observable safety
and audit outcomes rather than replacing the controller with a fake controller.
"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from functools import partial
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import requests

from energy_lab.ha_client import HAClient
from energy_lab.ha_dispatch import dispatch_command
from energy_explain.execution import TelemetryReadback
from energy_explain.live_control import ControlJournal, MILPController, bind_contest, contest_matches
from energy_explain.store import Store


DEVICE = datetime(2031, 1, 1, 0, 45, tzinfo=timezone.utc)
CAPABILITIES = {"unit_of_measurement": "W", "min": 0, "max": 5000, "step": 1}
STATIONS = {str(site): {"suffix": f"anker_solix_solarbank_max_ac_unit_{site}"} for site in range(1, 4)}
RESOURCE = "http://unit.test:8123"


def snapshot(wall, *, device_time=DEVICE, soc=50, first_hour_cheap=False):
    prices = {}
    for day, date in (("today", "2031-01-01"), ("tomorrow", "2031-01-02")):
        prices[day] = {"date": date, "points": [
            {"start": f"{date}T{hour:02d}:00:00Z",
             "import_price": .1 if first_hour_cheap and day == "today" and hour == 0 else .3,
             "export_price": .1}
            for hour in range(24)]}
    return {"prices": prices, "stations": {site: {
        "received_at": wall.isoformat(), "weather": {"unavailable": True},
        "state": {"soc": soc, "solar_power": 0, "home_load": 1000,
                  "device_time": device_time.isoformat(), "device_time_last_changed": wall.isoformat(),
                  "token": "NEVER-PERSIST-SNAPSHOT-SECRET"}}
        for site in STATIONS}}


@pytest.fixture(autouse=True)
def forbid_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("offline control test attempted an actual network request")
    # Guard the actual HTTP entry point only: constructing a Session is not a
    # network request, and HAClient eagerly creates one at construction.
    monkeypatch.setattr(requests.sessions.Session, "request", forbidden)


@pytest.fixture
def env(tmp_path):
    wall = datetime.now(timezone.utc) - timedelta(seconds=1)
    clock = SimpleNamespace(now=wall + timedelta(milliseconds=100))
    store = Store(tmp_path / "decisions.sqlite")
    journal = ControlJournal(tmp_path / "control.sqlite", resource_key=RESOURCE, lock_directory=tmp_path / "locks")
    controller = MILPController(run_id="control-run", store=store, journal=journal, stations=STATIONS,
                                clock=lambda: clock.now, solver_seconds=10)
    planned = controller.plan(snapshot(wall))
    assert len(planned["decisions"]) == 3, planned
    return SimpleNamespace(store=store, journal=journal, controller=controller, planned=planned,
                           decision=planned["decisions"]["1"], wall=wall, clock=clock,
                           client=HAClient("http://unit.test:8123", "unit-test-token"))


def http_response(status=200):
    response = Mock(status_code=status)
    response.json.return_value = []
    return response


def transport(env, statuses=(200, 200, 200)):
    post = Mock(side_effect=[http_response(status) for status in statuses])
    send = partial(dispatch_command, post=post, sleep=lambda _: None, clock=lambda: env.clock.now)
    return post, send


def submit(env, **kwargs):
    return env.controller.submit(env.decision, client=env.client, capabilities=CAPABILITIES,
                                 guard=lambda *_: True, **kwargs)


def unchanged(env, original):
    saved = env.store.get_decision(env.decision["id"])
    assert saved["action"] == original["action"]
    assert saved["snapshot_hash"] == original["snapshot_hash"]
    assert saved["snapshot"] == original["snapshot"]
    assert env.store.execution_events(saved["id"]) == []
    assert env.store.execution_evidence(saved["id"])["submission_receipts"] == []


def test_same_milp_zero_battery_plan_is_saved_as_explicit_zero_charge_candidate(env):
    planned = env.planned
    assert planned["mode"] == "milp_control_candidate"
    assert planned["writes_performed"] == 0
    assert env.journal.attempts() == []
    for site, decision in planned["decisions"].items():
        result = planned["stations"][site]
        assert result["battery_flow"] == "idle"
        assert result["battery_power_w"] == 0
        assert result["candidate"]["flow"] == "charge"
        assert result["candidate"]["target_w"] == 0
        assert not result["hardware_verified"]
        assert decision["action"] == {"kind": "charge", "power_kw": 0,
                                       "control_semantics": "battery_charge"}
        assert decision["source_kind"] == "external"
        assert decision["provenance"]["input_kind"] == "mixed"
        assert decision["policy_version"] == "milp-ha-control-sop-v1"
        assert "需遥测校验" in decision["provenance"]["note"]
        assert "仅为候选" in decision["provenance"]["note"]
        assert decision["snapshot"]["sources"]["pv"] == "observed"
        assert decision["snapshot"]["sources"]["load_forecast"] == "assumption"
        assert decision["snapshot"]["forecast_status"] == "fallback"
        assert decision["replay_context"]["first_step"] == {
            "charge_kw": 0, "discharge_kw": 0, "grid_import_kw": 1, "grid_export_kw": 0}
        assert len(decision["snapshot"]["load_forecast_kw"]) == 96
        assert decision["cost"]["purchase_eur"] == pytest.approx(24 * .3)
        assert decision["cost"]["terminal_credit_eur"] == pytest.approx(7 * .3)
        assert decision["cost"]["objective_eur"] == pytest.approx(5.1)
        assert env.store.execution_status(decision["id"]) == "candidate_only"
        assert "NEVER-PERSIST-SNAPSHOT-SECRET" not in json.dumps(decision)


@pytest.mark.parametrize("explicit", [False, True])
def test_default_and_explicit_dry_run_do_not_send_or_claim(env, explicit):
    send = Mock(side_effect=AssertionError("dry-run sent HA services"))
    guard = Mock(side_effect=AssertionError("dry-run consulted write guard"))
    kwargs = {"live": False} if explicit else {}
    result = env.controller.submit(env.decision, client=env.client, capabilities=CAPABILITIES,
                                   guard=guard, send=send, **kwargs)
    assert result["status"] == "not_sent"
    assert result["writes_performed"] == 0
    send.assert_not_called()
    guard.assert_not_called()
    assert env.journal.attempts() == []
    assert env.store.execution_events(env.decision["id"]) == []


@pytest.mark.parametrize("live", [1, "true", "false", None])
def test_truthy_or_ambiguous_live_permission_cannot_claim_or_send(env, live):
    send = Mock()
    with pytest.raises(ValueError, match="boolean"):
        submit(env, live=live, send=send)
    send.assert_not_called()
    assert env.journal.attempts() == []
    assert env.store.execution_events(env.decision["id"]) == []


def test_real_fractional_milp_target_cannot_be_rounded_to_one_watt_step(env):
    fractional = MILPController(run_id="fractional-run", store=env.store, journal=env.journal,
                                stations=STATIONS, clock=lambda: env.clock.now, solver_seconds=10)
    planned = fractional.plan(snapshot(env.wall, soc=99.9, first_hour_cheap=True))
    # The independent battery capacity bound leaves 0.014 kWh, hence this exact
    # quarter-hour charge target. The saved solve must not silently become 59 W.
    decision = planned["decisions"]["1"]
    target_w = decision["action"]["power_kw"] * 1000
    assert target_w == pytest.approx(.014 / .95 / .25 * 1000)
    assert abs(target_w - round(target_w)) > .001
    saved_action = deepcopy(decision["action"])
    saved_hash = decision["snapshot_hash"]
    send = Mock()
    with pytest.raises(ValueError, match="precision"):
        fractional.submit(decision, client=env.client, capabilities=CAPABILITIES,
                          guard=lambda *_: True, live=True, send=send)
    send.assert_not_called()
    assert env.journal.attempts() == []
    assert env.store.get_decision(decision["id"])["action"] == saved_action
    assert env.store.get_decision(decision["id"])["snapshot_hash"] == saved_hash
    assert env.store.execution_events(decision["id"]) == []


@pytest.mark.parametrize("capabilities", [
    {**CAPABILITIES, "unit_of_measurement": "kW"},
    {**CAPABILITIES, "min": 1},
    {**CAPABILITIES, "max": 5001},
    {**CAPABILITIES, "step": 0},
])
def test_wrong_unit_range_or_capability_refuses_before_claim_and_preserves_plan(env, capabilities):
    original = deepcopy(env.decision)
    send = Mock()
    with pytest.raises(ValueError):
        env.controller.submit(env.decision, client=env.client, capabilities=capabilities,
                              guard=lambda *_: True, live=True, send=send)
    send.assert_not_called()
    assert env.journal.attempts() == []
    unchanged(env, original)


def test_candidate_from_another_run_cannot_send_through_this_controller(env):
    other = MILPController(run_id="other-run", store=env.store, journal=env.journal, stations=STATIONS,
                           clock=lambda: env.clock.now, solver_seconds=10)
    decision = other.plan(snapshot(env.wall))["decisions"]["1"]
    send = Mock()
    with pytest.raises(ValueError, match="identity"):
        env.controller.submit(decision, client=env.client, capabilities=CAPABILITIES,
                              guard=lambda *_: True, live=True, send=send)
    send.assert_not_called()
    assert env.journal.attempts() == []


def test_modified_caller_action_cannot_override_the_immutable_saved_candidate(env):
    altered = deepcopy(env.decision)
    altered["action"]["power_kw"] = 1
    send = Mock()
    with pytest.raises(ValueError):
        env.controller.submit(altered, client=env.client, capabilities=CAPABILITIES,
                              guard=lambda *_: True, live=True, send=send)
    send.assert_not_called()
    assert env.journal.attempts() == []
    unchanged(env, env.decision)


def test_stale_candidate_cannot_be_refreshed_by_changing_caller_capture_time(env):
    altered = deepcopy(env.decision)
    env.clock.now += timedelta(seconds=16)
    altered["snapshot"]["observed_at"] = env.clock.now.isoformat()
    send = Mock()
    with pytest.raises(ValueError):
        env.controller.submit(altered, client=env.client, capabilities=CAPABILITIES,
                              guard=lambda *_: True, live=True, send=send)
    send.assert_not_called()
    assert env.journal.attempts() == []
    unchanged(env, env.decision)


def test_complete_http_submission_is_appended_but_remains_unconfirmed(env):
    post, send = transport(env)
    original = deepcopy(env.decision)
    result = submit(env, live=True, send=send)
    assert result["status"] == "accepted"
    assert result["writes_performed"] == 3
    assert post.call_count == 3
    assert [call.kwargs["json"].get("option", call.kwargs["json"].get("value"))
            for call in post.call_args_list] == ["third_party_control", "charge", 0]
    assert env.store.execution_status(env.decision["id"]) == "submitted"
    events = env.store.execution_events(env.decision["id"])
    assert [event["kind"] for event in events] == ["submitted"]
    evidence = env.store.execution_evidence(env.decision["id"])
    assert len(evidence["submission_receipts"]) == 1
    assert evidence["readbacks"] == []
    assert result["submission"]["target_kw"] == original["action"]["power_kw"]
    assert result["submission"]["semantics"] == "battery_charge_target"
    assert env.journal.attempts()[0]["state"] == "awaiting_readback"
    assert env.store.get_decision(env.decision["id"])["action"] == original["action"]
    assert env.store.get_decision(env.decision["id"])["snapshot_hash"] == original["snapshot_hash"]


def test_same_decision_is_not_sent_twice_even_after_controller_restart(env):
    post, send = transport(env)
    first = submit(env, live=True, send=send)
    reopened = ControlJournal(env.journal.path, resource_key=RESOURCE,
                              lock_directory=env.journal.lock_path.parent)
    controller = MILPController(run_id="control-run", store=Store(env.store.path), journal=reopened,
                                stations=STATIONS, clock=lambda: env.clock.now)
    forbidden = Mock()
    repeated = controller.submit(env.decision, client=env.client, capabilities=CAPABILITIES,
                                 guard=lambda *_: True, live=True, send=forbidden)
    assert first["status"] == "accepted"
    assert repeated["status"] == "not_sent_duplicate_or_pending"
    assert repeated["writes_performed"] == 0
    forbidden.assert_not_called()
    assert post.call_count == 3
    assert len(reopened.attempts()) == 1
    assert len(env.store.execution_events(env.decision["id"])) == 1


def test_crash_after_durable_claim_blocks_resend_new_decision_and_new_run(env):
    crash = Mock(side_effect=RuntimeError("unit fixture: submission outcome was lost"))
    with pytest.raises(RuntimeError):
        submit(env, live=True, send=crash)
    reopened = ControlJournal(env.journal.path, resource_key=RESOURCE,
                              lock_directory=env.journal.lock_path.parent)
    assert reopened.attempts()[0]["state"] == "claimed"
    assert reopened.attempts()[0]["outcome"] is None
    assert env.store.execution_events(env.decision["id"]) == []
    forbidden = Mock()
    recovered = MILPController(run_id="control-run", store=env.store, journal=reopened, stations=STATIONS,
                               clock=lambda: env.clock.now, solver_seconds=10)
    later = recovered.plan(snapshot(env.wall, device_time=DEVICE + timedelta(minutes=15)))["decisions"]["1"]
    new_run = MILPController(run_id="next-run", store=env.store, journal=reopened, stations=STATIONS,
                             clock=lambda: env.clock.now, solver_seconds=10)
    other = new_run.plan(snapshot(env.wall))["decisions"]["1"]
    for controller, decision in ((recovered, env.decision), (recovered, later), (new_run, other)):
        result = controller.submit(decision, client=env.client, capabilities=CAPABILITIES,
                                   guard=lambda *_: True, live=True, send=forbidden)
        assert result["status"] == "not_sent_duplicate_or_pending"
        assert result["writes_performed"] == 0
    forbidden.assert_not_called()
    assert len(reopened.attempts()) == 1


@pytest.mark.parametrize("statuses", [(400,), (200, 400), (200, 200, 400), (200, 200, 500)])
def test_partial_or_failed_submission_never_fabricates_store_submission(env, statuses):
    post, send = transport(env, statuses)
    original = deepcopy(env.decision)
    result = submit(env, live=True, send=send)
    assert result["status"] != "accepted"
    assert result["writes_performed"] == len(statuses)
    assert post.call_count == len(statuses)
    assert "submission" not in result
    unchanged(env, original)
    if statuses[-1] == 500:
        assert env.journal.attempts()[0]["state"] == "unknown"
        assert result["receipt"]["uncertain_side_effects"]
    else:
        assert env.journal.attempts()[0]["state"] == ("failed" if len(statuses) == 1 else "partial_failure")


def test_guard_rejection_before_first_service_preserves_auditable_blocked_intent(env):
    post, send = transport(env)
    guard = Mock(return_value=False)
    result = env.controller.submit(env.decision, client=env.client, capabilities=CAPABILITIES,
                                   guard=guard, live=True, send=send)
    assert result["status"] == "blocked"
    assert result["writes_performed"] == 0
    post.assert_not_called()
    assert guard.call_count == 1
    assert env.journal.attempts()[0]["state"] == "blocked"
    assert env.store.execution_events(env.decision["id"]) == []


def test_later_coherent_telemetry_confirms_submission_and_releases_site(env):
    post, send = transport(env)
    submitted = submit(env, live=True, send=send)
    instruction = submitted["instruction_id"]
    # Wall time advances independently of the accelerated device clock. The
    # source update must be after submission, rather than relabelling an old tick.
    telemetry = TelemetryReadback(run_id="control-run", site_id="1",
        decision_id=env.decision["decision_id"], instruction_id=instruction,
        observation_id="unit-readback", observed_at=env.clock.now + timedelta(milliseconds=200),
        updated_at=env.clock.now + timedelta(milliseconds=100), device_time=DEVICE + timedelta(minutes=15),
        mode="third_party_control", flow="charge", setpoint_kw=0, pv_kw=0, load_kw=1,
        charge_kw=0, discharge_kw=0, grid_import_kw=1, grid_export_kw=0)
    result = env.controller.observe(env.decision["id"], telemetry)
    assert result["verification"]["status"] == "verified"
    assert env.journal.attempts()[0]["state"] == "verified"
    assert env.store.execution_status(env.decision["id"]) == "confirmed"
    assert [event["kind"] for event in env.store.execution_events(env.decision["id"])] == ["submitted", "confirmed"]
    # Once this specific intent is verified, a later decision can be claimed.
    later = env.controller.plan(snapshot(env.wall, device_time=DEVICE + timedelta(minutes=15)))["decisions"]["1"]
    post2, send2 = transport(env)
    new = env.controller.submit(later, client=env.client, capabilities=CAPABILITIES,
                                guard=lambda *_: True, live=True, send=send2)
    assert new["status"] == "accepted"
    assert post2.call_count == 3
    assert len(env.journal.attempts()) == 2
    assert post.call_count == 3


def test_old_readback_cannot_release_a_submitted_intent(env):
    _, send = transport(env)
    submitted = submit(env, live=True, send=send)
    telemetry = TelemetryReadback(run_id="control-run", site_id="1",
        decision_id=env.decision["decision_id"], instruction_id=submitted["instruction_id"],
        observation_id="old-tick", observed_at=env.clock.now + timedelta(milliseconds=200),
        updated_at=env.clock.now + timedelta(milliseconds=100), device_time=DEVICE,
        mode="third_party_control", flow="charge", setpoint_kw=0, pv_kw=0, load_kw=1,
        charge_kw=0, discharge_kw=0, grid_import_kw=1, grid_export_kw=0)
    result = env.controller.observe(env.decision["id"], telemetry)
    assert result["verification"]["status"] == "pending"
    assert "awaiting_device_tick" in result["verification"]["reasons"]
    assert env.journal.attempts()[0]["state"] == "awaiting_readback"
    assert env.store.execution_status(env.decision["id"]) == "submitted"


def test_mock_contest_binding_cannot_drift_and_formal_control_is_rejected(env):
    contest = {"track": "mock", "mode": "running", "start_at": "2026-10-14T06:00:00Z",
               "end_at": "2026-10-14T07:00:00Z", "date_range": "2031-01-01 ~ 2031-01-08"}
    bound = bind_contest(contest, scope="mock")
    env.journal.bind("control-run", {"contest": bound, "stations": STATIONS})
    assert contest_matches(contest, bound, scope="mock")
    changed = dict(contest, start_at="2026-10-14T08:00:00Z")
    assert not contest_matches(changed, bound, scope="mock")
    with pytest.raises(ValueError):
        env.journal.bind("control-run", {"contest": bind_contest(changed, scope="mock"), "stations": STATIONS})
    with pytest.raises(ValueError, match="formal"):
        bind_contest(dict(contest, track="formal"), scope="formal")
    with pytest.raises(ValueError, match="active mock"):
        bind_contest(dict(contest, track="formal"), scope="mock")


def test_free_run_requires_idle_simulator():
    assert bind_contest({"mode": "idle"}, scope="free-run") == {"mode": "idle"}
    with pytest.raises(ValueError, match="mode=idle"):
        bind_contest({"mode": "running", "track": "mock"}, scope="free-run")


def test_same_ha_authority_cannot_take_concurrent_lock_using_a_different_journal(env, tmp_path):
    other = ControlJournal(tmp_path / "other-output" / "other.sqlite",
                           resource_key="http://UNIT.TEST:8123/api/states",
                           lock_directory=env.journal.lock_path.parent)
    assert other.path.parent != env.journal.path.parent
    assert other.lock_path == env.journal.lock_path
    with env.journal.exclusive():
        with pytest.raises(RuntimeError, match="another controller"):
            with other.exclusive():
                pytest.fail("second journal took the active HA controller lock")


def test_new_journal_cannot_bypass_old_pending_effects_after_owner_exits(env, tmp_path):
    _, send = transport(env)
    with env.journal.exclusive():
        assert submit(env, live=True, send=send)["status"] == "accepted"
    other = ControlJournal(tmp_path / "different-output" / "other.sqlite", resource_key=RESOURCE,
                           lock_directory=env.journal.lock_path.parent)
    with pytest.raises(RuntimeError, match="unresolved HA effects"):
        with other.exclusive():
            pytest.fail("new output path bypassed the pending HA instruction")
    assert other.attempts() == []
    assert env.journal.attempts()[0]["state"] == "awaiting_readback"
    assert json.loads(env.journal.registry_path.read_text())["journal"] == str(env.journal.path.resolve())


def test_missing_prior_journal_does_not_erase_handoff_inspection_requirement(env, tmp_path):
    with env.journal.exclusive():
        pass
    env.journal.path.unlink()
    other = ControlJournal(tmp_path / "replacement" / "other.sqlite", resource_key=RESOURCE,
                           lock_directory=env.journal.lock_path.parent)
    with pytest.raises(RuntimeError, match="must be inspected"):
        with other.exclusive():
            pytest.fail("missing prior journal was silently replaced")

