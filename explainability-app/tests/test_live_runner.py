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
        restore_calls=[], controller_sites=[], captures=[], statuses={}, journal=None,
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
            return {"contest": {"mode": "ended" if case.scenario == "ended" else "running",
                "track": "mock", "start_at": "2026-10-14T08:00:00Z",
                "end_at": "2026-10-14T08:56:00Z", "date_range": "2031-01-01 ~ 2031-01-08"}}

        def rows(self, *, capture=False):
            result = []
            for site, item in STATIONS.items():
                prefix = item["suffix"]
                mode = "self_consumption" if capture else case.modes[site]
                if not capture and case.scenario == "guard_rejected":
                    mode = "third_party_control"  # Someone took control after the initial capture.
                result.extend([
                    {"entity_id": f"sensor.{prefix}_device_time", "state": DEVICE.isoformat(),
                     "last_changed": WALL.isoformat(), "last_updated": WALL.isoformat()},
                    {"entity_id": f"select.{prefix}_operating_mode_device_runs_by_command_in_third_party_controlled",
                     "state": mode, "attributes": {"options": ["self_consumption", "third_party_control"]}},
                    {"entity_id": f"select.{prefix}_grid_flow", "state": "charge",
                     "attributes": {"options": ["charge", "discharge"]}},
                    {"entity_id": f"number.{prefix}_power_control", "state": "2000", "attributes": CAPABILITY},
                ])
            return result

        def states(self):
            case.elapsed += .02
            return self.rows(), wall()

        def capture(self):
            case.captures.append(len(case.captures) + 1)
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
                            decision_id=f"decision-{site}", action={"kind": "charge", "power_kw": 2})
            return {"decisions": {site: decision}, "stations": {site: {"status": "candidate_only"}}}

        def submit(self, decision, *, client, guard, live, **kwargs):
            site, key = decision["site_id"], decision["id"]
            if not live:
                return {"status": "not_sent", "decision_key": key, "writes_performed": 0}
            command = DispatchCommand(run_id=self.run_id, site_id=site, decision_id=decision["decision_id"],
                instruction_id=f"instruction-{site}", suffix=STATIONS[site]["suffix"],
                issued_device_time=DEVICE, flow="charge", target_w=2000, semantics="battery_charge_target")
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
                instruction_id=command.instruction_id, event_id=f"event-{site}", issued_device_time=DEVICE,
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
            updated_at=captured_wall_time, device_time=DEVICE + timedelta(minutes=15),
            mode="third_party_control", flow="charge", setpoint_kw=2, pv_kw=0, load_kw=1,
            charge_kw=2, discharge_kw=0, grid_import_kw=3, grid_export_kw=0)

    monkeypatch.setattr(runner, "build_telemetry_readback", readback)

    def run(*, live=True):
        output = tmp_path / "report"
        arguments = ["run_milp_live.py", "--config", str(tmp_path / "private-config.json"),
            "--db", str(tmp_path / "decisions.sqlite"), "--journal", str(tmp_path / "control.sqlite"),
            "--run-id", "offline-runner", "--scope", "mock", "--device-timezone", "UTC",
            "--readback-seconds", ".1", "--output", str(output)]
        if live:
            arguments.append("--live")
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
    assert env.services == [] and env.restore_calls == []
    assert report["writes_performed"] == 0 and report["cleanup"] == {}
    assert all(site["status"] == "blocked" for site in report["sites"].values())
    assert {attempt["state"] for attempt in env.journal.attempts()} == {"blocked"}


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
