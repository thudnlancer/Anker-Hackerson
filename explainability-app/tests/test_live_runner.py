"""Offline CLI fault paths use the real scope/freshness guard and owner lock.

Source observations and MILP computation are fixtures; no HTTP transport exists.
The injected sender invokes the actual CLI guard before recording a simulated
service effect, so restoration assertions cover pre-send touched registration.
"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import fcntl
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests

from energy_lab.ha_dispatch import DispatchCommand
from energy_explain.execution import SubmissionReceipt, TelemetryReadback
from energy_explain.ha_observation import ObservationUnavailable
from energy_explain.live_control import ControlJournal


_SPEC = importlib.util.spec_from_file_location(
    "_offline_milp_runner", Path(__file__).resolve().parents[2] / "run_milp_live.py")
runner = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(runner)

WALL = datetime(2026, 10, 14, 8, 0, tzinfo=timezone.utc)
DEVICE = datetime(2031, 1, 1, tzinfo=timezone.utc)
STATIONS = {str(site): {"suffix": f"anker_solix_solarbank_max_ac_runner_{site}"}
            for site in range(1, 4)}
CAPABILITY = {"unit_of_measurement": "W", "min": 0, "max": 5000, "step": 1}


@pytest.fixture(autouse=True)
def forbid_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("offline runner test attempted real HTTP")
    for name in ("get", "post", "request", "Session"):
        monkeypatch.setattr(requests, name, forbidden)


@pytest.fixture
def env(tmp_path, monkeypatch):
    case = SimpleNamespace(scenario="verified", elapsed=0., guard_calls=[], services=[],
        restore_calls=[], controller_sites=[], submit_calls=[], captures=[], statuses={}, journal=None,
        preparation_calls=[], control_delays={}, unavailable_ready_reads=[], dashboard_reads=0, prepare_requested=False,
        modes={site: "self_consumption" for site in STATIONS})
    cfg = dict(ha_base="http://offline-runner.test:8123", sim_base="http://offline-runner.test:45678",
               live_enabled=True, allow_ha_write=True, allow_formal_contest=False,
               stations=deepcopy(STATIONS))

    def wall():
        return WALL + timedelta(seconds=case.elapsed)

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return wall().astimezone(tz) if tz is not None else wall().replace(tzinfo=None)

    def sleep(seconds):
        case.elapsed += seconds

    monkeypatch.setattr(runner, "datetime", FrozenDateTime)
    monkeypatch.setattr(runner, "time", SimpleNamespace(monotonic=lambda: case.elapsed, sleep=sleep))
    monkeypatch.setattr(runner, "load_runtime_config", lambda _: deepcopy(cfg))

    def journal_factory(path, *, resource_key):
        case.journal = ControlJournal(path, resource_key=resource_key, lock_directory=tmp_path / "owner-locks")
        return case.journal

    monkeypatch.setattr(runner, "ControlJournal", journal_factory)

    class StoreFixture:
        def __init__(self, path):
            case.store = self

        def execution_status(self, key):
            return case.statuses.get(key, "candidate_only")

    monkeypatch.setattr(runner, "Store", StoreFixture)

    class ClientFixture:
        def __init__(self, site):
            self.site, self.token = site, "offline-token"

        def call(self, domain, service, entity_id, **data):
            assert (domain, service, entity_id, data) == (
                "select", "select_option",
                f"select.{STATIONS[self.site]['suffix']}_operating_mode_device_runs_by_command_in_third_party_controlled",
                {"option": "third_party_control"})
            # Preparation is a separate durable intent before any simulated HA
            # effect, not a candidate submission or acknowledgement.
            with case.journal.connect() as db:
                rows = db.execute('SELECT payload FROM control_preparations WHERE site_id=?', (self.site,)).fetchall()
            assert len(rows) == 1
            assert json.loads(rows[0]["payload"])["scope"] == "before_candidate_generation_not_a_MILP_submission"
            with case.journal.lock_path.open("a") as other:
                with pytest.raises(BlockingIOError):
                    fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
            case.preparation_calls.append(self.site)
            case.services.append(("prepare", self.site))
            case.modes[self.site] = "third_party_control"
            case.control_delays[self.site] = 1000 if case.scenario == "preparation_never_ready" else 1
            if case.scenario == "preparation_runtime_error":
                raise RuntimeError("offline mode preparation failed after a possible HA effect")
            if case.scenario == "preparation_keyboard_interrupt":
                raise KeyboardInterrupt
            return []

        def set_self_consumption(self, suffix, *, live):
            assert live is True and suffix == STATIONS[self.site]["suffix"]
            # A second FD cannot own this flock while cleanup runs. This checks
            # that both immediate and exception cleanup retain the real lock.
            with case.journal.lock_path.open("a") as other:
                with pytest.raises(BlockingIOError):
                    fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
            case.restore_calls.append(self.site)
            case.services.append(("restore", self.site))
            if case.scenario == "partial_restore_failure":
                raise RuntimeError("offline restoration failure")
            case.modes[self.site] = "self_consumption"

    class SourceFixture:
        def __init__(self, loaded):
            assert loaded == cfg
            self.tokens = {"ha": "offline-token", "sim": "offline-simulator-token"}
            self.refreshes = {"ha": 0, "sim": 0}
            self.closed, self.active_site = False, None
            case.source = self

        def dashboard(self):
            case.dashboard_reads += 1
            return {"contest": {"mode": "ended" if case.scenario == "ended" else "running",
                "track": "mock", "start_at": "2026-10-14T08:00:00Z",
                "end_at": "2026-10-14T08:56:00Z", "date_range": "2031-01-01 ~ 2031-01-08"}}

        def rows(self, *, capture=False):
            result = []
            for site, item in STATIONS.items():
                prefix = item["suffix"]
                mode = "self_consumption" if capture else case.modes[site]
                clock_index = int(case.elapsed / .5) if case.prepare_requested else 0
                clock_tick = DEVICE + timedelta(minutes=15 * clock_index)
                clock_report = WALL + timedelta(seconds=.5 * clock_index)
                controls_unavailable = case.prepare_requested and (
                    mode == "self_consumption" or case.control_delays.get(site, 0) > 0)
                result.extend([
                    {"entity_id": f"sensor.{prefix}_device_time", "state": clock_tick.isoformat(),
                     "last_changed": clock_report.isoformat(), "last_updated": clock_report.isoformat()},
                    {"entity_id": f"select.{prefix}_operating_mode_device_runs_by_command_in_third_party_controlled",
                     "state": mode, "attributes": {"options": ["self_consumption", "third_party_control"]}},
                    {"entity_id": f"select.{prefix}_grid_flow", "state": "unavailable" if controls_unavailable else "charge",
                     "attributes": {"options": ["discharge"] if case.scenario == "prepared_capability_blocked" else ["charge", "discharge"]}},
                    {"entity_id": f"number.{prefix}_power_control", "state": "unavailable" if controls_unavailable else "2000", "attributes": CAPABILITY},
                ])
            return result

        def states(self):
            case.elapsed += .02
            result = self.rows()
            for site, delay in list(case.control_delays.items()):
                if delay:
                    case.unavailable_ready_reads.append(site)
                    case.control_delays[site] -= 1
            return result, wall()

        def capture(self):
            case.captures.append(len(case.captures) + 1)
            self.active_site = list(STATIONS)[len(case.captures) - 1]
            return {"prices": {}, "stations": {site: {"state": {}, "received_at": wall().isoformat()}
                                                    for site in STATIONS}}, self.rows(capture=True)

        def ha_client(self):
            return ClientFixture(self.active_site)

        def close(self):
            self.closed = True

    monkeypatch.setattr(runner, "LiveSource", SourceFixture)

    class ControllerFixture:
        def __init__(self, *, run_id, store, journal, stations, **kwargs):
            self.run_id, self.store, self.journal = run_id, store, journal

        def plan(self, snapshot):
            site = next(iter(snapshot["stations"]))
            case.controller_sites.append(site)
            case.source.active_site = site
            if case.scenario == "no_candidate":
                return {"decisions": {}, "stations": {site: {"status": "limit_reached"}}}
            decision = dict(id=f"key-{site}", run_id=self.run_id, site_id=site,
                            decision_id=f"decision-{site}", action={"kind": "charge", "power_kw": 2},
                            device_time=snapshot["stations"][site]["state"].get("device_time", DEVICE.isoformat()))
            return {"decisions": {site: decision}, "stations": {site: {"status": "candidate_only"}}}

        def submit(self, decision, *, client, guard, live, **kwargs):
            site, key = decision["site_id"], decision["id"]
            case.submit_calls.append(site)
            if not live:
                return {"status": "not_sent", "decision_key": key, "writes_performed": 0}
            if case.scenario == "guard_rejected":
                # The forecast capture AND the fresh alignment observation were
                # self-use. A competing controller starts only after those reads,
                # immediately before the real pre-HTTP guard is invoked.
                case.modes[site] = "third_party_control"
            command = DispatchCommand(run_id=self.run_id, site_id=site, decision_id=decision["decision_id"],
                instruction_id=f"instruction-{site}", suffix=STATIONS[site]["suffix"],
                issued_device_time=datetime.fromisoformat(decision["device_time"]),
                flow="charge", target_w=2000, semantics="battery_charge_target")
            assert self.journal.claim(decision, command)
            issued = wall()
            for index in range(3):
                allowed = guard(command, index)
                case.guard_calls.append((site, index, allowed))
                if not allowed:
                    assert index == 0
                    self.journal.outcome(command, "blocked", {"guard_rejected": True})
                    return {"status": "blocked", "decision_key": key, "writes_performed": 0}
                case.services.append(("submit", site, index))
                if index == 0:
                    case.modes[site] = "third_party_control"
                    if case.scenario == "persistence_runtime_error":
                        raise RuntimeError("offline local persistence failed after possible HA write")
                    if case.scenario == "persistence_keyboard_interrupt":
                        raise KeyboardInterrupt
                    if case.scenario == "partial_restore_failure":
                        self.journal.outcome(command, "partial_failure", {"services_attempted": 1})
                        return {"status": "partial_failure", "decision_key": key, "writes_performed": 1}
            evidence = SubmissionReceipt(run_id=self.run_id, site_id=site, decision_id=decision["decision_id"],
                instruction_id=command.instruction_id, event_id=f"event-{site}", issued_device_time=command.issued_device_time,
                issued_wall_time=issued, completed_wall_time=wall(), mode="third_party_control", flow="charge",
                target_kw=2, semantics="battery_charge_target", source_ref="offline-controller",
                sender_status="accepted", raw_receipt={"http_status": 200, "services_expected": 3, "services_completed": 3})
            self.journal.outcome(command, "awaiting_readback", evidence.model_dump(mode="json"))
            case.statuses[key] = "submitted"
            return {"status": "accepted", "decision_key": key, "instruction_id": command.instruction_id,
                    "writes_performed": 3, "submission": evidence.model_dump(mode="json")}

        def observe(self, decision_key, observation):
            case.statuses[decision_key] = "confirmed"
            return {"verification": {"status": "verified", "reasons": []}}

    monkeypatch.setattr(runner, "MILPController", ControllerFixture)

    def readback(states, *, receipt, captured_wall_time, **kwargs):
        if case.scenario == "unverified":
            return ObservationUnavailable("mixed_source_tick")
        return TelemetryReadback(run_id=receipt.run_id, site_id=receipt.site_id,
            decision_id=receipt.decision_id, instruction_id=receipt.instruction_id,
            observation_id=f"observation-{receipt.site_id}", observed_at=captured_wall_time,
            updated_at=captured_wall_time, device_time=receipt.issued_device_time + timedelta(minutes=15),
            mode="third_party_control", flow="charge", setpoint_kw=2, pv_kw=0, load_kw=1,
            charge_kw=2, discharge_kw=0, grid_import_kw=3, grid_export_kw=0)

    monkeypatch.setattr(runner, "build_telemetry_readback", readback)

    def run(*, live=True, prepare_controls=False):
        case.prepare_requested = prepare_controls
        output = tmp_path / "report"
        arguments = ["run_milp_live.py", "--config", str(tmp_path / "private-config.json"),
            "--db", str(tmp_path / "decisions.sqlite"), "--journal", str(tmp_path / "control.sqlite"),
            "--run-id", "offline-runner", "--scope", "mock", "--device-timezone", "UTC",
            "--readback-seconds", ".1", "--output", str(output)]
        if live:
            arguments.append("--live")
        if prepare_controls:
            arguments.append("--prepare-controls")
        monkeypatch.setattr(runner.sys, "argv", arguments)
        code = runner.main()
        return code, json.loads((output / "summary.json").read_text())

    case.run = run
    return case


@pytest.mark.parametrize("scenario,outcome", [
    ("persistence_runtime_error", "failed"), ("persistence_keyboard_interrupt", "interrupted")])
def test_possible_http_effect_followed_by_local_failure_restores_inside_owner_lock(env, scenario, outcome):
    env.scenario = scenario
    code, report = env.run()
    assert code == 1 and report["outcome"] == outcome
    assert env.guard_calls == [("1", 0, True)]
    assert env.services == [("submit", "1", 0), ("restore", "1")]
    assert env.restore_calls == ["1"]
    assert report["cleanup"]["1"]["restored"] is True
    assert env.modes == {site: "self_consumption" for site in STATIONS}
    assert env.controller_sites == ["1"]
    attempts = env.journal.attempts()
    assert len(attempts) == 1 and attempts[0]["state"] == "restored"
    assert json.loads(attempts[0]["outcome"])["submission"] is None
    assert env.source.closed is True


def test_guard_rejecting_before_any_http_does_not_restore_unrelated_stations(env):
    env.scenario = "guard_rejected"
    code, report = env.run()
    assert code == 1 and report["outcome"] != "completed"
    assert env.guard_calls == [(site, 0, False) for site in STATIONS]
    assert env.submit_calls == list(STATIONS)
    assert env.services == [] and env.restore_calls == []
    assert report["writes_performed"] == 0 and report["cleanup"] == {}
    assert all(site["status"] == "blocked" for site in report["sites"].values())
    assert {attempt["state"] for attempt in env.journal.attempts()} == {"blocked"}
    assert env.modes == {site: "third_party_control" for site in STATIONS}


def test_fresh_alignment_already_under_another_controller_never_reaches_submit(env):
    # The initial forecast capture carries older self-use controls; the real
    # alignment read sees the current third-party owner before submission.
    env.modes = {site: "third_party_control" for site in STATIONS}
    code, report = env.run()
    assert code == 1 and report["outcome"] != "completed"
    assert len(report["sites"]) == 3
    assert all(site["status"] == "blocked_existing_controller" for site in report["sites"].values())
    assert env.submit_calls == [] and env.guard_calls == []
    assert env.services == [] and env.restore_calls == []
    assert env.journal.attempts() == []
    assert report["writes_performed"] == 0 and report["cleanup"] == {}
    assert env.modes == {site: "third_party_control" for site in STATIONS}


def test_three_accepted_but_unverified_sites_do_not_return_success(env):
    env.scenario = "unverified"
    code, report = env.run()
    assert code == 1 and report["outcome"] != "completed"
    assert len(report["sites"]) == 3 and len(report["cleanup"]) == 3
    assert all(site["status"] == "accepted" and site["execution_status"] == "submitted"
               for site in report["sites"].values())
    assert all(site["readback_checks"] for site in report["sites"].values())
    assert env.restore_calls == ["1", "2", "3"]
    assert {attempt["state"] for attempt in env.journal.attempts()} == {"restored"}


def test_three_sites_without_candidates_do_not_return_live_success(env):
    env.scenario = "no_candidate"
    code, report = env.run()
    assert code == 1 and report["outcome"] != "completed"
    assert len(report["sites"]) == 3
    assert all(site["status"] == "no_candidate" for site in report["sites"].values())
    assert env.guard_calls == [] and env.services == [] and env.restore_calls == []


def test_ended_contest_rejects_live_before_capture_claim_or_service(env):
    env.scenario = "ended"
    code, report = env.run()
    assert code == 1 and report["outcome"] == "failed"
    assert report["error_type"] == "ValueError"
    assert env.captures == [] and env.controller_sites == []
    assert env.guard_calls == [] and env.services == [] and env.restore_calls == []
    assert env.journal.attempts() == []
    assert report["writes_performed"] == 0 and report["cleanup"] == {}
    assert env.source.closed is True


def test_verified_sites_are_restored_before_the_next_site_starts(env):
    code, report = env.run()
    assert code == 0 and report["outcome"] == "completed"
    assert env.services == [event for site in STATIONS for event in (
        ("submit", site, 0), ("submit", site, 1), ("submit", site, 2), ("restore", site))]
    assert report["writes_performed"] == 12
    assert all(site["execution_status"] == "confirmed" for site in report["sites"].values())
    assert all(cleanup["restored"] for cleanup in report["cleanup"].values())
    assert {attempt["state"] for attempt in env.journal.attempts()} == {"restored"}


def test_restoration_failure_after_partial_service_stops_before_another_site(env):
    env.scenario = "partial_restore_failure"
    code, report = env.run()
    assert code == 1 and report["outcome"] == "failed"
    assert env.controller_sites == ["1"]
    assert all(event[1] == "1" for event in env.services)
    assert report["cleanup"]["1"]["restored"] is False
    assert env.journal.attempts()[0]["state"] == "partial_failure"


def test_default_candidate_capture_never_invokes_service_guard_or_claim(env):
    code, report = env.run(live=False)
    assert code == 0
    assert report["mode"] == "candidate_only"
    assert len(report["sites"]) == 3 and all(site["status"] == "not_sent" for site in report["sites"].values())
    assert env.guard_calls == [] and env.services == [] and env.restore_calls == []
    assert env.journal.attempts() == [] and report["writes_performed"] == 0


def preparation_payloads(env):
    with env.journal.connect() as db:
        rows = db.execute('SELECT site_id,payload FROM control_preparations ORDER BY site_id').fetchall()
    return {row["site_id"]: json.loads(row["payload"]) for row in rows}


def test_prepared_controls_wait_until_available_then_submit_and_restore_per_site(env):
    code, report = env.run(prepare_controls=True)
    assert code == 0 and report["outcome"] == "completed"
    assert env.preparation_calls == ["1", "2", "3"]
    assert env.unavailable_ready_reads == ["1", "2", "3"]
    assert env.services == [event for site in STATIONS for event in (
        ("prepare", site), ("submit", site, 0), ("submit", site, 1), ("submit", site, 2), ("restore", site))]
    assert env.guard_calls == [(site, index, True) for site in STATIONS for index in range(3)]
    assert report["writes_performed"] == 15
    assert all(site["execution_status"] == "confirmed" for site in report["sites"].values())
    payloads = preparation_payloads(env)
    assert len(payloads) == 3
    assert all(payload["ready"] and payload["restoration"]["restored"] for payload in payloads.values())
    assert {attempt["state"] for attempt in env.journal.attempts()} == {"restored"}


def test_preparation_with_no_candidate_restores_before_preparing_next_site(env):
    env.scenario = "no_candidate"
    code, report = env.run(prepare_controls=True)
    assert code == 1 and report["outcome"] != "completed"
    assert env.services == [event for site in STATIONS for event in (("prepare", site), ("restore", site))]
    assert env.submit_calls == [] and env.guard_calls == []
    assert env.journal.attempts() == []
    assert report["writes_performed"] == 6
    assert len(report["sites"]) == 3 and all(site["status"] == "no_candidate" for site in report["sites"].values())
    assert all(payload["ready"] and payload["restoration"]["restored"]
               for payload in preparation_payloads(env).values())
    assert env.modes == {site: "self_consumption" for site in STATIONS}


def test_prepared_candidate_with_unsupported_flow_restores_before_next_site(env):
    env.scenario = "prepared_capability_blocked"
    code, report = env.run(prepare_controls=True)
    assert code == 1 and report["outcome"] != "completed"
    assert env.services == [event for site in STATIONS for event in (("prepare", site), ("restore", site))]
    assert env.submit_calls == [] and env.guard_calls == [] and env.journal.attempts() == []
    assert len(report["sites"]) == 3
    assert all(site["status"] == "blocked_control_capability" for site in report["sites"].values())
    assert all(payload["ready"] and payload["restoration"]["restored"]
               for payload in preparation_payloads(env).values())
    assert env.modes == {site: "self_consumption" for site in STATIONS}


def test_historical_unrestored_preparation_stops_live_before_any_source_read_or_write(env, tmp_path):
    prior = ControlJournal(tmp_path / "control.sqlite", resource_key="http://offline-runner.test:8123",
                           lock_directory=tmp_path / "owner-locks")
    payload = {"phase": "mode_preparation", "service_accepted": True,
               "scope": "before_candidate_generation_not_a_MILP_submission"}
    with prior.connect() as db:
        db.execute('CREATE TABLE control_preparations (run_id TEXT, site_id TEXT, started_at TEXT PRIMARY KEY, payload TEXT)')
        db.execute('INSERT INTO control_preparations VALUES (?,?,?,?)',
                   ("previous-run", "2", WALL.isoformat(), json.dumps(payload)))
    code, report = env.run(prepare_controls=True)
    assert code == 1 and report["outcome"] == "failed" and report["error_type"] == "RuntimeError"
    assert env.dashboard_reads == 0 and env.captures == [] and env.controller_sites == []
    assert env.submit_calls == [] and env.guard_calls == [] and env.services == [] and env.restore_calls == []
    assert report["writes_performed"] == 0 and report["cleanup"] == {} and report["sites"] == {}
    assert env.journal.attempts() == [] and env.source.closed is True
    assert preparation_payloads(env) == {"2": payload}  # No silent inspection, restoration or erasure.


def test_prepared_mode_with_controls_never_available_times_out_and_restores_once(env):
    env.scenario = "preparation_never_ready"
    code, report = env.run(prepare_controls=True)
    assert code == 1 and report["outcome"] == "failed"
    assert env.services == [("prepare", "1"), ("restore", "1")]
    assert env.controller_sites == [] and env.submit_calls == [] and env.guard_calls == []
    assert env.journal.attempts() == []
    assert report["writes_performed"] == 2 and report["cleanup"]["1"]["restored"] is True
    payloads = preparation_payloads(env)
    assert not payloads["1"].get("ready", False) and payloads["1"]["restoration"]["restored"] is True
    assert env.modes == {site: "self_consumption" for site in STATIONS}


@pytest.mark.parametrize("scenario,outcome", [("preparation_runtime_error", "failed"),
                                               ("preparation_keyboard_interrupt", "interrupted")])
def test_preparation_exception_after_possible_mode_effect_restores_without_submission(env, scenario, outcome):
    env.scenario = scenario
    code, report = env.run(prepare_controls=True)
    assert code == 1 and report["outcome"] == outcome
    assert env.services == [("prepare", "1"), ("restore", "1")]
    assert env.controller_sites == [] and env.submit_calls == [] and env.guard_calls == []
    assert env.journal.attempts() == []
    payloads = preparation_payloads(env)
    assert list(payloads) == ["1"]
    assert payloads["1"]["restoration"]["restored"] is True
    assert not payloads["1"].get("ready", False)
    assert report["writes_performed"] == 2
    assert env.modes == {site: "self_consumption" for site in STATIONS}
