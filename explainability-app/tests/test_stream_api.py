"""Durable collector lifecycle and structured execution ingestion regressions."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile

from fastapi.testclient import TestClient
import pytest

from energy_explain.api import create_app
from energy_explain.store import Conflict, Store
from energy_explain.schemas import ExecutionEvent
from tests.helpers import confirmed, decision_payload


def stamp(value):
    return value.isoformat()


@pytest.fixture
def env():
    with tempfile.TemporaryDirectory() as directory:
        app = create_app(Path(directory) / "store.sqlite")
        with TestClient(app) as client:
            yield client, app.state.store


def heartbeat(wall, **changes):
    return dict(run_id="stream", owner_id="owner", state="running", started_at=stamp(wall-timedelta(seconds=5)),
        updated_at=stamp(wall), last_success_at=stamp(wall), poll_interval_seconds=5,
        freshness_threshold_seconds=15, heartbeat_expiry_seconds=30, **changes)


def test_heartbeat_lifecycle_freshness_restart_and_expiry(env):
    client, store = env
    wall = datetime.now(timezone.utc)
    status = heartbeat(wall)
    store.update_collection_status(status)
    payload = decision_payload(source_kind="external", run_id="stream")
    payload["snapshot"]["observed_at"] = stamp(wall-timedelta(seconds=1))
    record = client.post("/api/decisions", json=payload).json()
    overview = client.get("/api/stations").json()
    assert overview["collection"]["state"] == "running"
    assert overview["stations"][0]["record_freshness"] == "fresh"
    assert overview["stations"][0]["observed_at"] == record["snapshot"]["observed_at"]
    assert Store(store.path).collection_status("stream")["owner_id"] == "owner"
    assert store.collection_status("stream", wall+timedelta(seconds=31))["state"] == "unknown"
    assert store.collection_status("stream", wall-timedelta(seconds=3))["reason"] == "clock_unverified"
    with pytest.raises(Conflict):
        store.update_collection_status(dict(status, owner_id="other", started_at=stamp(wall)))
    with pytest.raises(Conflict):
        store.update_collection_status(dict(status, updated_at=stamp(wall-timedelta(seconds=1)),last_success_at=None))
    store.update_collection_status(dict(status,state="stopped"))
    store.update_collection_status(dict(status,owner_id="other",started_at=stamp(wall)))
    assert store.collection_status("stream")["owner_id"] == "other"


def test_missing_future_stale_and_demo_collection_isolation(env):
    client, store = env
    wall = datetime.now(timezone.utc)
    first = client.post("/api/decisions",json=decision_payload()).json()
    assert first["record_freshness"] == "unknown"
    with pytest.raises(Conflict):
        store.update_collection_status(dict(heartbeat(wall),run_id="test-run"))
    with pytest.raises(ValueError):
        store.update_collection_status(heartbeat(wall+timedelta(seconds=10)))
    store.update_collection_status(heartbeat(wall))
    payload = decision_payload(source_kind="external",run_id="stream")
    payload["snapshot"]["observed_at"] = stamp(wall-timedelta(seconds=20))
    assert client.post("/api/decisions",json=payload).json()["record_freshness"] == "stale"
    payload["decision_id"] = "future"
    payload["snapshot"]["observed_at"] = stamp(wall+timedelta(seconds=10))
    assert client.post("/api/decisions",json=payload).json()["record_freshness"] == "clock_unverified"


def test_old_checkpoint_cannot_erase_last_durable_cycle_outcomes(env):
    _,store=env
    wall=datetime.now(timezone.utc)
    original=dict(heartbeat(wall),state="stopped",last_error_at=stamp(wall-timedelta(seconds=1)),last_error="RuntimeError")
    store.update_collection_status(original)
    restarted=dict(original,owner_id="restarted",started_at=stamp(wall),state="running",last_success_at=None,last_error_at=None,last_error=None)
    restored=store.update_collection_status(restarted)
    assert datetime.fromisoformat(restored["last_success_at"])==wall
    assert restored["last_error"]=="RuntimeError"
    # A partial/new heartbeat can carry old checkpoint values on every round;
    # the durable values must remain monotone within the run.
    delayed=dict(restarted,last_success_at=stamp(wall-timedelta(seconds=2)),last_error_at=stamp(wall-timedelta(seconds=2)),last_error="OSError")
    latest=store.update_collection_status(delayed)
    assert latest["last_success_at"]==restored["last_success_at"]
    assert latest["last_error_at"]==restored["last_error_at"]
    assert latest["last_error"]=="RuntimeError"


def evidence(wall, instruction="instruction-1"):
    common = dict(run_id="stream",site_id="1",decision_id="same-id",instruction_id=instruction)
    receipt = dict(common,event_id="submit-"+instruction,issued_device_time="2027-01-24T00:00:00Z",
        issued_wall_time=stamp(wall-timedelta(seconds=3)),completed_wall_time=stamp(wall-timedelta(seconds=2)),
        mode="third_party_control",flow="charge",target_kw=5,semantics="battery_charge_target",
        source_ref="external-sender:receipt",sender_status="accepted",
        raw_receipt=dict(http_status=200,services_expected=3,services_completed=3))
    telemetry = dict(common,observation_id="read-"+instruction,device_time="2027-01-24T00:15:00Z",
        observed_at=stamp(wall),updated_at=stamp(wall-timedelta(seconds=1)),mode="third_party_control",
        flow="charge",setpoint_kw=5,pv_kw=0,load_kw=1,charge_kw=5,discharge_kw=0,grid_import_kw=6,grid_export_kw=0)
    return receipt,telemetry


def test_readback_bridge_is_atomic_idempotent_and_cannot_be_bypassed(env):
    client, store = env
    wall = datetime.now(timezone.utc)
    payload = decision_payload(source_kind="external",run_id="stream")
    payload["snapshot"]["observed_at"] = stamp(wall-timedelta(seconds=10))
    record = client.post("/api/decisions",json=payload).json()
    base = "/api/decisions/"+record["id"]
    receipt,telemetry = evidence(wall)
    unsupported = client.post(base+"/readbacks",json=dict(telemetry,observation_id="unmatched")).json()
    assert unsupported["verification"]["status"] == "unsupported"
    assert unsupported["execution_status"] == "candidate_only"
    assert client.post(base+"/submission-receipts",json=receipt).json()["created"]
    assert not client.post(base+"/submission-receipts",json=receipt).json()["created"]
    assert client.post(base+"/execution-events",json=confirmed()).status_code == 422
    response = client.post(base+"/readbacks",json=telemetry).json()
    assert response["verification"]["status"] == "verified"
    assert response["execution_status"] == "confirmed"
    assert not client.post(base+"/readbacks",json=telemetry).json()["created"]
    assert client.post(base+"/readbacks",json=dict(telemetry,charge_kw=4)).status_code == 409
    assert client.post(base+"/submission-receipts",json=dict(receipt,target_kw=4,instruction_id="bad",event_id="bad")).status_code == 422
    assert client.post(base+"/readbacks",json=dict(telemetry,site_id="2")).status_code == 422
    restored = Store(store.path)
    assert len(restored.execution_events(record["id"])) == 2
    assert len(restored.execution_evidence(record["id"])["readbacks"]) == 2
    # A delayed old observation must not confirm a newly submitted instruction.
    new_receipt,_ = evidence(wall,"new")
    new_receipt["issued_device_time"] = "2027-01-24T00:30:00Z"
    client.post(base+"/submission-receipts",json=new_receipt)
    old = client.post(base+"/readbacks",json=dict(telemetry,observation_id="delayed-old")).json()
    assert old["execution_status"] == "submitted"
    detail = client.get(base).json()
    assert detail["execution_evidence"]["submission_receipts"][1]["instruction_id"] == "new"


def test_receipt_and_event_rollback_together_on_conflict(env):
    client, store = env
    wall = datetime.now(timezone.utc)
    payload=decision_payload(source_kind="external",run_id="stream")
    payload["snapshot"]["observed_at"]=stamp(wall-timedelta(seconds=10))
    record=client.post("/api/decisions",json=payload).json()
    base="/api/decisions/"+record["id"]
    receipt,_=evidence(wall)
    client.post(base+"/execution-events",json=dict(event_id=receipt["event_id"],instruction_id="other",kind="submitted",issued_at=receipt["issued_device_time"]))
    assert client.post(base+"/submission-receipts",json=receipt).status_code==409
    assert store.execution_evidence(record["id"])["submission_receipts"]==[]


def test_legacy_external_confirmation_is_preserved_but_not_promoted_to_verified(env):
    client, store = env
    record=client.post("/api/decisions",json=decision_payload(source_kind="external",run_id="stream")).json()
    key=record["id"]
    # Emulate a database written by the previous release. No payload is rewritten
    # and a legacy assertion cannot stand in for the new structured evidence.
    with store.connect(write=True) as db:
        store._insert_execution(db,key,ExecutionEvent.model_validate(dict(event_id="submit-1",instruction_id="instruction-1",kind="submitted",issued_at="2027-01-24T00:00:00Z")))
        store._insert_execution(db,key,ExecutionEvent.model_validate(confirmed()))
    detail=client.get("/api/decisions/"+key).json()
    assert detail["execution_status"]=="confirmation_unverified"
    assert detail["execution_events"][1]["verification_status"]=="unverified"
    assert len(Store(store.path).execution_events(key))==2
