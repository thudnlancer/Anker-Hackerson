"""Actual CLI loop, stream/outbox and Store; every external read is offline."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json

import pytest
import requests

from energy_explain import mock_acceptance
from energy_explain.live_source import SourceUnavailable
from energy_explain.store import Store
from energy_lab.explainability_stream import StreamCheckpoint


@pytest.fixture(autouse=True)
def forbid_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("offline mock-observer test attempted HTTP")
    monkeypatch.setattr(requests.sessions.Session, "request", forbidden)
    monkeypatch.setattr(mock_acceptance.time, "sleep", lambda seconds: None)


@pytest.fixture
def run_case(tmp_path, monkeypatch):
    wall = datetime.now(timezone.utc)
    selected = dict(track="mock", start_at=(wall - timedelta(minutes=1)).isoformat(),
        end_at=(wall + timedelta(minutes=5)).isoformat(),
        date_range="2031-01-01 ~ 2031-01-08", days=7, speed=180)
    initial_rows = [{"track": "earlier-mock"}]
    manifest = dict(run_id="unit-mock-observer", initial=dict(contest=selected,
        results_count=len(initial_rows),
        result_fingerprints=[mock_acceptance.result_fingerprint(row) for row in initial_rows]))
    config = dict(ha_base="http://ha.unit.test:8123", ha_token="NEVER-LOG-HA-TOKEN",
        sim_base="http://sim.unit.test:45678", sim_username="unit-sim-user",
        sim_password="NEVER-LOG-SIM-PASSWORD",
        stations={str(site): {"suffix": f"unit_{site}"} for site in range(1, 4)},
        live_enabled=True, allow_ha_write=True, allow_formal_contest=True)
    paths = {"config": tmp_path / "config.json", "manifest": tmp_path / "manifest.json",
        "db": tmp_path / "records.sqlite", "checkpoint": tmp_path / "checkpoint.sqlite",
        "output": tmp_path / "output"}
    paths["config"].write_text(json.dumps(config))
    paths["manifest"].write_text(json.dumps(manifest))
    instances, events, capture_errors = [], [], []

    def dashboard(mode, *, unrelated=False):
        contest = dict(selected, mode=mode)
        if unrelated:
            contest["start_at"] = (wall - timedelta(minutes=2)).isoformat()
        sites = [dict(site_id=str(site), metrics=dict(baseline_cost=10., saving=1.,
            saving_settled=-2., device_revenue=8., baseline_revenue=7., baseline_settled=5.,
            device_settled=3., saving_settled_percent=-20., device_grid_import_cost=2.,
            device_grid_export_revenue=3., device_net_grid_cost=-1., valid=True,
            shortage_count=0)) for site in range(1, 4)]
        final = dict(track="mock", date_range=selected["date_range"], days=7, speed=180,
            ended_at=(wall + timedelta(minutes=5, seconds=4)).isoformat(), sites=sites,
            total=dict(baseline_cost=30., saving=3., saving_settled=-6., device_revenue=24.,
                baseline_revenue=21., saving_settled_percent=-20., valid=True))
        return dict(contest=contest, results=dict(runs=initial_rows + ([final] if mode == "ended" else []),
                                                verify={"verified": True}))

    class OfflineSource:
        def __init__(self, cfg, *, max_auth_refreshes):
            assert max_auth_refreshes == 4
            assert cfg["live_enabled"] is cfg["allow_ha_write"] is cfg["allow_formal_contest"] is False
            self.cfg = deepcopy(cfg)
            self.refreshes = {"ha": 0, "sim": 0}
            self.capture_calls = self.dashboard_calls = self.ha_control_calls = 0
            self.closed = False
            instances.append(self)

        def dashboard(self):
            self.dashboard_calls += 1
            if not events:
                raise AssertionError("observer read past the expected lifecycle")
            value = events.pop(0)
            if isinstance(value, BaseException):
                raise value
            return deepcopy(value)

        def capture(self):
            self.capture_calls += 1
            if capture_errors:
                error = capture_errors.pop(0)
                if error is not None:
                    raise error
            captured = datetime.now(timezone.utc).isoformat()
            prices = {day: dict(date=date, points=[
                dict(start=f"{date}T{hour:02d}:00:00Z", import_price=.3, export_price=.1)
                for hour in range(24)])
                for day, date in (("today", "2031-01-01"), ("tomorrow", "2031-01-02"))}
            snapshot = dict(prices=prices, received_at=captured,
                stations={site: dict(received_at=captured,
                    state=dict(soc="45", solar_power="400", home_load="1000",
                        device_time="2031-01-01T00:15:00+00:00", device_time_last_changed=captured),
                    weather={"unavailable": True}) for site in self.cfg["stations"]})
            return snapshot, []

        def ha_client(self):
            self.ha_control_calls += 1
            raise AssertionError("read-only observer requested a HA control client")

        def close(self):
            self.closed = True

    monkeypatch.setattr(mock_acceptance, "LiveSource", OfflineSource)
    argv = [arg for key, path in paths.items() for arg in ("--" + key, str(path))]
    return dict(paths=paths, selected=selected, manifest=manifest, events=events,
                capture_errors=capture_errors, instances=instances, dashboard=dashboard, argv=argv)


def summary(case):
    return json.loads((case["paths"]["output"] / "summary.json").read_text())


def test_running_to_ended_really_collects_three_shadow_records_and_settles(run_case, capsys):
    case = run_case
    case["events"].extend([case["dashboard"]("running"), case["dashboard"]("ended")])
    assert mock_acceptance.main(case["argv"]) == 0
    report = summary(case)
    store = Store(case["paths"]["db"])
    records = store.decisions(case["manifest"]["run_id"])
    assert report["phase"] == "completed"
    assert report["rounds"] == 1
    assert report["site_counts"] == {"1": 1, "2": 1, "3": 1}
    assert len(records) == 3
    assert all(d["decision_id"].startswith("shadow-tick-") for d in records)
    assert all(store.execution_status(d["id"]) == "candidate_only" for d in records)
    settled = store.get_settlement(case["manifest"]["run_id"])
    assert settled["status"] == "complete"
    assert settled["source_kind"] == "simulator_mock"
    assert settled["saving_settled_eur"] == -6.
    assert settled["raw_fields"]["independent_signature_verification"] is False
    assert store.collection_status(case["manifest"]["run_id"])["state"] == "stopped"
    checkpoint = StreamCheckpoint(case["paths"]["checkpoint"])
    assert checkpoint.state(case["manifest"]["run_id"])["cycle"] == 1
    assert checkpoint.pending(case["manifest"]["run_id"]) == []
    with checkpoint._connect() as db:
        assert db.execute("SELECT count(*) FROM stream_records WHERE delivered=1").fetchone()[0] == 3
    assert case["instances"][0].capture_calls == 1
    assert case["instances"][0].ha_control_calls == 0
    assert case["instances"][0].closed
    assert report["ha_writes_performed"] == 0
    assert len(report["samples"]) == 3
    emitted = capsys.readouterr()
    assert "NEVER-LOG" not in emitted.out + emitted.err
    for path in case["paths"]["output"].glob("*.json"):
        assert "NEVER-LOG" not in path.read_text()


def test_dashboard_403_is_terminal_keeps_pending_and_never_captures(run_case):
    case = run_case
    case["events"].append(SourceUnavailable("sim_authentication_failed", 403))
    assert mock_acceptance.main(case["argv"]) == 1
    report = summary(case)
    assert report["phase"] == "authentication_failed"
    assert report["error"] == {"category": "sim_authentication_failed", "http_status": 403}
    assert report["decisions"] == 0
    assert report["settlement"]["status"] == "pending"
    assert case["instances"][0].capture_calls == case["instances"][0].ha_control_calls == 0
    assert case["instances"][0].closed
    assert Store(case["paths"]["db"]).collection_status(case["manifest"]["run_id"])["state"] == "stopped"


def test_changed_window_is_terminal_without_importing_another_mock(run_case):
    case = run_case
    case["events"].append(case["dashboard"]("running", unrelated=True))
    assert mock_acceptance.main(case["argv"]) == 1
    report = summary(case)
    assert report["phase"] == "contest_changed"
    assert report["decisions"] == 0
    assert report["settlement"]["status"] == "pending"
    assert case["instances"][0].capture_calls == 0
    assert case["instances"][0].ha_control_calls == 0
    assert case["instances"][0].closed


def test_capture_403_stops_main_after_one_stream_cycle_and_keeps_pending(run_case):
    case = run_case
    case["events"].append(case["dashboard"]("running"))
    case["capture_errors"].append(SourceUnavailable("sim_authentication_failed", 403))
    assert mock_acceptance.main(case["argv"]) == 1
    report = summary(case)
    assert report["phase"] == "source_failed"
    assert report["error"] == {"category": "sim_authentication_failed", "http_status": 403}
    assert report["rounds"] == 1
    assert report["decisions"] == 0
    assert report["settlement"]["status"] == "pending"
    assert case["instances"][0].capture_calls == 1
    assert case["instances"][0].ha_control_calls == 0
    assert case["instances"][0].closed


def test_source_failure_archive_does_not_relabel_previous_capture_as_current_input(run_case):
    case = run_case
    case["events"].extend([case["dashboard"]("running"), case["dashboard"]("running")])
    case["capture_errors"].extend([None, SourceUnavailable("sim_authentication_failed", 403)])
    assert mock_acceptance.main(case["argv"]) == 1
    report = summary(case)
    assert report["phase"] == "source_failed"
    assert report["rounds"] == 2
    assert report["decisions"] == 3
    failed = case["paths"]["output"] / "failed-cycles"
    assert (failed / "cycle-2-status.json").exists()
    assert not (failed / "cycle-2-snapshot.json").exists()
    status = json.loads((failed / "cycle-2-status.json").read_text())
    assert status["reason"] == "capture_failed"
    # The earlier latest input remains intact as historical evidence.
    assert (case["paths"]["output"] / "latest-snapshot.json").exists()
    assert case["instances"][0].capture_calls == 2
    assert case["instances"][0].ha_control_calls == 0


def test_solver_failure_archive_keeps_this_cycles_actual_business_snapshot(run_case, monkeypatch):
    case = run_case
    def limited(snapshot, **kwargs):
        return {"stations": {site: {"status": "limit_reached", "candidate": None}
                              for site in snapshot["stations"]}}
    monkeypatch.setattr(mock_acceptance, "shadow_evaluate", limited)
    case["events"].extend([case["dashboard"]("running"), case["dashboard"]("ended")])
    assert mock_acceptance.main(case["argv"]) == 0
    failed = case["paths"]["output"] / "failed-cycles"
    saved = json.loads((failed / "cycle-1-snapshot.json").read_text())
    latest = json.loads((case["paths"]["output"] / "latest-snapshot.json").read_text())
    assert saved == latest
    status = json.loads((failed / "cycle-1-status.json").read_text())
    assert status["sites"]["3"]["solver_status"] == "timeout"
    assert status["sites"]["3"]["error_type"] == "SolverTimeLimit"
    assert summary(case)["decisions"] == 0


def test_restart_preserves_pending_event_and_duplicate_tick_bytes_then_final_is_idempotent(run_case):
    case = run_case
    run_id = case["manifest"]["run_id"]
    case["events"].extend([case["dashboard"]("running"), KeyboardInterrupt()])
    assert mock_acceptance.main(case["argv"]) == 1
    store = Store(case["paths"]["db"])
    original_records = store.decisions(run_id)
    original_pending = store.get_settlement(run_id)
    original_owner = store.collection_status(run_id)["owner_id"]
    assert summary(case)["phase"] == "interrupted"
    assert original_pending["status"] == "pending"
    assert len(original_records) == 3

    case["events"].extend([case["dashboard"]("running"), case["dashboard"]("ended")])
    assert mock_acceptance.main(case["argv"]) == 0
    assert store.decisions(run_id) == original_records
    assert summary(case)["rounds"] == 2
    assert store.collection_status(run_id)["owner_id"] != original_owner
    assert StreamCheckpoint(case["paths"]["checkpoint"]).pending(run_id) == []
    final = store.get_settlement(run_id)
    assert final["status"] == "complete"
    with store.connect() as db:
        rows = db.execute("SELECT payload FROM settlements WHERE run_id=? ORDER BY sequence", (run_id,)).fetchall()
    assert len(rows) == 2
    assert json.loads(rows[0]["payload"]) == original_pending

    case["events"].append(case["dashboard"]("ended"))
    assert mock_acceptance.main(case["argv"]) == 0
    assert store.get_settlement(run_id) == final
    with store.connect() as db:
        assert db.execute("SELECT count(*) FROM settlements WHERE run_id=?", (run_id,)).fetchone()[0] == 2
    assert all(source.ha_control_calls == 0 and source.closed for source in case["instances"])


def test_reusing_run_output_cannot_change_the_observed_window(run_case):
    case=run_case
    case['events'].extend([case['dashboard']('running'),KeyboardInterrupt()])
    assert mock_acceptance.main(case['argv']) == 1
    original=Store(case['paths']['db']).decisions(case['manifest']['run_id'])
    edited=deepcopy(case['manifest']);edited['initial']['contest']['date_range']='other-window'
    case['paths']['manifest'].write_text(json.dumps(edited))
    with pytest.raises(SystemExit):mock_acceptance.main(case['argv'])
    assert Store(case['paths']['db']).decisions(case['manifest']['run_id']) == original
    assert case['instances'][-1].capture_calls == case['instances'][-1].dashboard_calls == 0
