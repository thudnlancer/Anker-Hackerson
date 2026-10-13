from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
import os
from pathlib import Path
from unittest.mock import patch

from energy_explain.schemas import Intervention
from energy_explain.store import Store
from energy_explain.worker import process_one
from tests.helpers import make_decision
from tests.worker_targets import crash, hang, infeasible


class IndependentWorkerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "worker.sqlite3"
        self.store = Store(self.path)
        self.id = self.store.add_decision(make_decision())["id"]

    def tearDown(self):
        self.temp.cleanup()

    def task(self, first_charge_kw=0, decision_id=None):
        return self.store.create_job(decision_id or self.id, Intervention(first_charge_kw=first_charge_kw))

    def test_independent_cli_process_reads_durable_job_and_stores_result(self):
        queued = self.task()
        completed = subprocess.run([sys.executable, "-m", "energy_explain.worker", "--once", "--db", str(self.path)],
                                   capture_output=True, text=True, timeout=40)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        restored = Store(self.path).get_job(queued["id"])
        self.assertEqual(restored["status"], "ready", restored["error"])
        self.assertAlmostEqual(restored["result"]["delta_eur"], 0.8111052631578947, places=6)

    def test_hard_timeout_and_child_exit_are_distinct(self):
        queued = self.task()
        timed_out = process_one(self.store, timeout=0.2, target=hang)
        self.assertEqual(timed_out["id"], queued["id"])
        self.assertEqual(timed_out["status"], "timeout")
        self.task()
        failed = process_one(self.store, timeout=3, target=crash)
        self.assertEqual(failed["status"], "failed")
        self.assertIsNone(failed["result"])

    def test_infeasible_does_not_become_timeout(self):
        self.task()
        result = process_one(self.store, timeout=3, target=infeasible)
        self.assertEqual(result["status"], "infeasible")
        self.assertIsNone(result["result"])

    def test_unregistered_external_record_stays_unsupported(self):
        key = self.store.add_decision(make_decision(source_kind="external", run_id="external-run"))["id"]
        self.task(decision_id=key)
        result = process_one(self.store, timeout=20)
        self.assertEqual(result["status"], "unsupported")
        self.assertIsNone(result["result"])

    def test_independent_worker_loads_explicit_provider_module(self):
        key = self.store.add_decision(make_decision(source_kind="external", run_id="registration-test",
            model_version="qa-registration-fixture-v1"))["id"]
        job = self.task(decision_id=key)
        with patch.dict(os.environ, {"ENERGY_EXPLAIN_PROVIDER_MODULE": "tests.provider_fixture"}):
            completed = subprocess.run([sys.executable, "-m", "energy_explain.worker", "--once", "--db", str(self.path)],
                                       capture_output=True, text=True, timeout=40)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = self.store.get_job(job["id"])
        self.assertEqual(result["status"], "ready", result["error"])
        self.assertIn("registration test double", result["result"]["assumptions"][0])


if __name__ == "__main__":
    unittest.main()
