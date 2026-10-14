from pathlib import Path
import tempfile

from fastapi.testclient import TestClient

from energy_explain.api import create_app
from energy_explain.pv_accounting import account_rows
from energy_explain.worker import process_one_plan
from tests.helpers import decision_payload


def test_plan_job_is_durable_idempotent_and_demo_unsupported():
    with tempfile.TemporaryDirectory() as temp, TestClient(create_app(Path(temp) / "e4.sqlite")) as client:
        decision = client.post("/api/decisions", json=decision_payload()).json()
        request = {"decision_id": decision["id"], "target_soc_percent": 60,
                   "deadline": "2027-01-24T01:00:00Z"}
        queued = client.post("/api/plans/feasibility", json=request)
        assert queued.status_code == 202
        assert client.post("/api/plans/feasibility", json=request).json()["id"] == queued.json()["id"]
        stored = client.app.state.store
        finished = process_one_plan(stored,timeout=5)
        assert finished["status"] == "unsupported"
        assert client.get("/api/plans/" + queued.json()["id"]).json()["status"] == "unsupported"
        # Once E4-1 is integrated, an unsupported result must be retryable.
        assert client.post("/api/plans/feasibility", json=request).json()["id"] != queued.json()["id"]
        assert client.post("/api/plans/feasibility", json={**request,
            "deadline": "2027-01-24T01:00:00"}).status_code == 422


def test_pv_daily_import_is_idempotent_and_rejects_changed_identity():
    trace = {"site": "1", "time": "2031-01-01T00:00:00Z", "arm": "zero_flow",
             "pv_w": 3000, "load_w": 1000, "power_w": 1000, "flow": "charge",
             "sell": -0.1, "soc_before": 50, "soc_after": 60, "grid_export_kwh": 1,
             "negative_export_loss_eur": 0.1}
    report = account_rows([trace])[0]
    with tempfile.TemporaryDirectory() as temp, TestClient(create_app(Path(temp) / "pv.sqlite")) as client:
        assert client.post("/api/pv/accounting", json=report).json()["created"]
        assert not client.post("/api/pv/accounting", json=report).json()["created"]
        response = client.get("/api/pv/1/daily")
        assert response.status_code == 200
        assert response.json()["days"][0]["curtailment_status"] == "not_measured"
        invalid = {**report, "totals": {**report["totals"], "pv_to_grid_kwh": 2}}
        assert client.post("/api/pv/accounting", json=invalid).status_code == 422
