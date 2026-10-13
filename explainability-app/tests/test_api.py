from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from energy_explain.api import create_app
from tests.helpers import confirmed, decision_payload, submitted


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "api.sqlite3"
        self.client = TestClient(create_app(db_path=self.path))
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.temp.cleanup()

    def ingest(self, **changes):
        response = self.client.post("/api/decisions", json=decision_payload(**changes))
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_new_service_has_no_implicit_demo_and_openapi_is_available(self):
        self.assertEqual(self.client.get("/api/health").status_code, 200)
        self.assertEqual(self.client.get("/api/stations").json()["stations"], [])
        self.assertEqual(self.client.get("/api/decisions").json()["decisions"], [])
        spec = self.client.get("/openapi.json").json()
        self.assertIn("/api/questions", spec["paths"])
        self.assertIn("DecisionIn", spec["components"]["schemas"])

    def test_idempotency_timezone_normalization_and_conflict_http_status(self):
        first = self.ingest()
        self.assertTrue(first["created"])
        payload = decision_payload()
        payload["snapshot"]["device_time"] = "2027-01-24T08:00:00+08:00"
        duplicate = self.client.post("/api/decisions", json=payload)
        self.assertEqual(duplicate.status_code, 200)
        self.assertFalse(duplicate.json()["created"])
        self.assertEqual(duplicate.json()["snapshot_hash"], first["snapshot_hash"])
        conflict = self.client.post("/api/decisions", json=decision_payload(policy_version="changed"))
        self.assertEqual(conflict.status_code, 409)
        invalid = decision_payload()
        invalid["snapshot"]["units"] = {"power": "W"}
        self.assertEqual(self.client.post("/api/decisions", json=invalid).status_code, 422)

    def test_run_site_queries_and_missing_ids_are_isolated(self):
        self.ingest()
        self.ingest(site_id="2")
        self.ingest(run_id="second-run")
        filtered = self.client.get("/api/decisions", params={"run_id": "test-run", "site_id": "2"}).json()
        self.assertEqual(len(filtered["decisions"]), 1)
        self.assertEqual(filtered["decisions"][0]["site_id"], "2")
        self.assertEqual(len(self.client.get("/api/stations", params={"run_id": "test-run"}).json()["stations"]), 2)
        self.assertEqual(len(self.client.get("/api/stations", params={"run_id": "second-run"}).json()["stations"]), 1)
        for path in ("/api/decisions/missing", "/api/counterfactuals/missing", "/api/runs/missing/settlements"):
            self.assertEqual(self.client.get(path).status_code, 404)

    def test_facts_reference_saved_evidence_and_do_not_wait_for_worker(self):
        decision = self.ingest()
        explanation = self.client.get(f"/api/decisions/{decision['id']}/explanation").json()
        self.assertEqual(explanation["source_kind"], "demo")
        self.assertEqual(explanation["execution_status"], "candidate_only")
        self.assertIn("最低", next(f["text"] for f in explanation["facts"] if f["code"] == "price"))
        for fact in explanation["facts"]:
            self.assertTrue(fact["evidence_refs"])
            for reference in fact["evidence_refs"]:
                identity, pointer = reference.split("#", 1)
                self.assertEqual(identity, decision["id"])
                value = decision
                for field in pointer.strip("/").split("/"):
                    value = value[field.replace("~1", "/").replace("~0", "~")]

    def test_historical_fallback_and_full_soc_have_distinct_explanations(self):
        payload = decision_payload()
        payload["snapshot"].update(forecast_status="fallback", history_mode="live_history",
            cache_tail="2027-01-22T13:00:00Z", fallback_reason="cache_tail_stale_by_35.00h")
        decision = self.client.post("/api/decisions", json=payload).json()
        facts = self.client.get(f"/api/decisions/{decision['id']}/explanation").json()["facts"]
        self.assertIn("35 小时", next(f["text"] for f in facts if f["code"] == "history"))
        self.assertIn("cache_tail_stale_by_35.00h", next(f["text"] for f in facts if f["code"] == "fallback"))
        payload = decision_payload(site_id="2")
        payload["snapshot"]["soc_percent"] = 100
        payload["action"] = dict(kind="hold", power_kw=0, control_semantics="self_consumption")
        full = self.client.post("/api/decisions", json=payload).json()
        facts = self.client.get(f"/api/decisions/{full['id']}/explanation").json()["facts"]
        self.assertIn("已达上界", next(f["text"] for f in facts if f["code"] == "soc"))

    def test_execution_http_errors_old_readback_and_unsettled_state(self):
        record = self.ingest()
        path = f"/api/decisions/{record['id']}/execution-events"
        self.assertIsNone(record["settlement"])
        self.assertEqual(self.client.post(path, json=confirmed()).status_code, 422)
        self.assertEqual(self.client.post(path, json=submitted()).status_code, 200)
        self.client.post(path, json=submitted("new-sub", "new-inst", "2027-01-24T00:15:00Z"))
        old_readback = self.client.post(path, json=confirmed())
        self.assertEqual(old_readback.status_code, 200)
        self.assertEqual(old_readback.json()["execution_status"], "submitted")
        self.assertEqual(self.client.post(path, json=confirmed(device_time="2027-01-23T23:00:00Z")).status_code, 422)
        changed = submitted(issued_at="2027-01-24T00:00:01Z")
        self.assertEqual(self.client.post(path, json=changed).status_code, 409)
        detail = self.client.get(f"/api/decisions/{record['id']}").json()
        self.assertEqual(len(detail["execution_events"]), 3)
        self.assertIsNone(detail["settlement"])

    def test_limited_questions_and_async_comparison(self):
        record = self.ingest()
        def question(text):
            return self.client.post("/api/questions", json={"decision_id": record["id"], "question": text}).json()
        why = question("为什么这样做？")
        self.assertEqual(why["intent"], "why")
        self.assertTrue(why["evidence_refs"])
        self.assertIsNone(why["job"])
        limit = question("为什么不能增加功率")
        self.assertEqual(limit["intent"], "power_limit")
        self.assertIn("5 kW", limit["answer"])
        unknown = question("替我买股票并预测明年收益")
        self.assertEqual(unknown["intent"], "unsupported")
        self.assertIsNone(unknown["job"])
        task = question("首步不充会怎样")["job"]
        self.assertEqual(task["status"], "queued")
        direct = self.client.post(f"/api/decisions/{record['id']}/counterfactuals", json={"first_charge_kw": 0})
        self.assertEqual(direct.status_code, 202)
        self.assertEqual(direct.json()["id"], task["id"])

    def test_external_decision_with_demo_version_has_no_demo_capability(self):
        record = self.ingest(source_kind="external", run_id="external-run")
        self.assertFalse(record["capabilities"]["counterfactual"])
        response = self.client.post("/api/questions", json={"decision_id": record["id"], "question": "首步不充会怎样"}).json()
        self.assertIsNone(response["job"])
        self.assertIn("未注册", response["answer"])

    def test_external_ingestion_example_is_valid_without_registered_model(self):
        example = json.loads((Path(__file__).parents[1] / "docs/examples/external-decision.json").read_text())
        response = self.client.post("/api/decisions", json=example)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertFalse(response.json()["capabilities"]["counterfactual"])

    def test_api_loads_explicit_provider_module_for_matching_version(self):
        with patch.dict(os.environ, {"ENERGY_EXPLAIN_PROVIDER_MODULE": "tests.provider_fixture"}):
            record = self.ingest(source_kind="external", run_id="registration-test",
                model_version="qa-registration-fixture-v1")
            self.assertTrue(record["capabilities"]["counterfactual"])
            self.assertEqual(record["capabilities"]["supported_interventions"], ["first_charge_kw"])

    def test_service_recreation_restores_records_and_queued_task(self):
        record = self.ingest()
        job = self.client.post(f"/api/decisions/{record['id']}/counterfactuals", json={"first_charge_kw": 0}).json()
        with TestClient(create_app(db_path=self.path)) as restarted:
            self.assertEqual(restarted.get(f"/api/decisions/{record['id']}").json()["snapshot_hash"], record["snapshot_hash"])
            self.assertEqual(restarted.get(f"/api/counterfactuals/{job['id']}").json()["status"], "queued")


if __name__ == "__main__":
    unittest.main()
