from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from energy_explain.schemas import Intervention
from energy_explain.store import Conflict, Store
from tests.helpers import make_decision


class DurableQueueTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "queue.sqlite3"
        self.store = Store(self.path)
        self.id = self.store.add_decision(make_decision())["id"]

    def tearDown(self):
        self.temp.cleanup()

    def job(self, first_charge_kw=0):
        return self.store.create_job(self.id, Intervention(first_charge_kw=first_charge_kw))

    def test_same_intervention_deduplicates_but_distinct_interventions_do_not(self):
        one = self.job(0)
        self.assertEqual(one["id"], self.job(0)["id"])
        two = self.job(1)
        self.assertNotEqual(one["id"], two["id"])
        self.assertEqual(Store(self.path).get_job(one["id"])["status"], "queued")

    def test_claim_is_single_global_concurrency_and_terminal_result_is_persistent(self):
        self.job(0)
        self.job(1)
        claimed = self.store.claim_job()
        self.assertEqual(claimed["status"], "running")
        other_worker = Store(self.path)
        self.assertIsNone(other_worker.claim_job())
        result = {"delta_eur": -0.2}
        self.store.finish_job(claimed["id"], "ready", result=result, attempt=claimed["attempts"])
        restored = Store(self.path).get_job(claimed["id"])
        self.assertEqual(restored["status"], "ready")
        self.assertEqual(restored["result"]["delta_eur"], -0.2)
        self.assertIsNotNone(other_worker.claim_job())

    def test_expired_lease_recovers_and_old_attempt_cannot_complete(self):
        job = self.job()
        with patch("energy_explain.store.time.time", return_value=100):
            first = self.store.claim_job(lease_seconds=40)
        with patch("energy_explain.store.time.time", return_value=141):
            recovered = Store(self.path).claim_job(lease_seconds=40)
        self.assertEqual(first["id"], recovered["id"])
        self.assertEqual(recovered["attempts"], 2)
        with self.assertRaises(Conflict):
            self.store.finish_job(job["id"], "ready", result={}, attempt=first["attempts"])
        self.store.finish_job(job["id"], "ready", result={}, attempt=recovered["attempts"])

    def test_repeated_worker_loss_stops_retrying(self):
        job = self.job()
        with patch("energy_explain.store.time.time", return_value=100):
            self.store.claim_job(lease_seconds=40)
        with patch("energy_explain.store.time.time", return_value=141):
            self.store.claim_job(lease_seconds=40)
        with patch("energy_explain.store.time.time", return_value=182):
            self.assertIsNone(self.store.claim_job(lease_seconds=40))
        persisted = Store(self.path).get_job(job["id"])
        self.assertEqual(persisted["status"], "failed")
        self.assertIn("lease expired", persisted["error"])

    def test_timeout_is_distinct_from_infeasible_and_can_be_retried(self):
        first = self.job()
        claimed = self.store.claim_job()
        self.store.finish_job(first["id"], "timeout", error="30 second limit", attempt=claimed["attempts"])
        self.assertEqual(self.store.get_job(first["id"])["status"], "timeout")
        retry = self.job()
        self.assertNotEqual(retry["id"], first["id"])
        claimed_retry = self.store.claim_job()
        self.store.finish_job(retry["id"], "infeasible", error="no feasible plan", attempt=claimed_retry["attempts"])
        self.assertEqual(self.job()["id"], retry["id"])

    def test_unsupported_can_be_retried_after_provider_registration(self):
        first = self.job()
        claimed = self.store.claim_job()
        self.store.finish_job(first["id"], "unsupported", error="missing model", attempt=claimed["attempts"])
        retry = self.job()
        self.assertNotEqual(first["id"], retry["id"])
        self.assertEqual(retry["status"], "queued")


if __name__ == "__main__":
    unittest.main()
