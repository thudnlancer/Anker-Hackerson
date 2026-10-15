"""Durable write quarantine and restart behavior; no real HA POST."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from energy_lab.execute import execute_candidate
from energy_lab.ha_client import HAClient
from energy_lab.reconciliation import ReconciliationStore
from run_live import run_once_milp
from test_run_live_chain import StubHA, StubSim, cfg, make_prices, state


def candidate():
    return {"status": "candidate_only", "battery_flow": "charge",
            "battery_power_w": 100, "candidate": {"target_w": 100},
            "device_time": "2031-01-01T00:15:00+00:00"}


class Response:
    def raise_for_status(self):
        return None

    def json(self):
        return []


class ReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "journal.json"
        self.ha = HAClient("http://mock.invalid", "fake-token")

    def test_timeout_persists_and_restart_blocks_site(self):
        store = ReconciliationStore(self.path)
        with patch("energy_lab.ha_client.requests.post", side_effect=requests.Timeout("lost")) as post:
            row = execute_candidate(candidate(), suffix="1502", site="1", ha=self.ha,
                                    live=True, reconciliation_store=store)
        self.assertEqual(post.call_count, 1)
        self.assertTrue(row["requires_reconciliation"])
        restored = ReconciliationStore(self.path)
        self.assertEqual(len(restored.pending()), 1)
        fake_ha = StubHA({"1502": state()})
        rows = run_once_milp(cfg(verified=True), fake_ha, StubSim(make_prices()),
                             write_enabled=True, carry={}, reconciliation_store=restored)
        self.assertEqual(rows[0]["status"], "reconciliation_required")
        self.assertEqual(fake_ha.calls, [])
        self.assertEqual(rows[0]["previous_delivery"]["incident_id"],
                         restored.pending()["1"]["incident_id"])

    def test_checkpoint_exists_before_post_and_full_ack_clears_it(self):
        store = ReconciliationStore(self.path)
        observations = []
        def post(*args, **kwargs):
            observations.append(ReconciliationStore(self.path).pending()["1"]["steps"][-1]["status"])
            return Response()
        with patch("energy_lab.ha_client.requests.post", side_effect=post), \
             patch("energy_lab.write_transaction.time.sleep"):
            row = execute_candidate(candidate(), suffix="1502", site="1", ha=self.ha,
                                    live=True, reconciliation_store=store)
        self.assertEqual(observations, ["attempted_unknown"] * 3)
        self.assertEqual(row["write_requests_acknowledged"], 3)
        self.assertEqual(ReconciliationStore(self.path).pending(), {})

    def test_manual_clear_requires_matching_incident_and_evidence(self):
        store = ReconciliationStore(self.path)
        store.checkpoint("1", "1502", "2031-01-01T00:15:00+00:00", [])
        incident = store.pending()["1"]["incident_id"]
        evidence = Path(self.temp.name) / "readback.txt"
        with self.assertRaises(ValueError):
            store.clear("1", "wrong", "operator", evidence, "checked")
        with self.assertRaises(ValueError):
            store.clear("1", incident, "operator", evidence, "checked")
        evidence.write_text("manual HA mode, flow and power readback recorded", encoding="utf-8")
        store.clear("1", incident, "operator", evidence, "manual mode/flow/power verified")
        restored = ReconciliationStore(self.path)
        self.assertEqual(restored.pending(), {})
        self.assertEqual(restored._data["clearances"][0]["incident_id"], incident)

    def test_direct_execute_cannot_bypass_pending_incident(self):
        store = ReconciliationStore(self.path)
        store.checkpoint("1", "1502", "2031-01-01T00:15:00+00:00", [])
        with patch("energy_lab.ha_client.requests.post") as post:
            with self.assertRaisesRegex(ValueError, "reconciliation_required"):
                execute_candidate(candidate(), suffix="1502", site="1", ha=self.ha,
                                  live=True, reconciliation_store=store)
        post.assert_not_called()

    def test_dry_run_cannot_clear_pending_incident(self):
        store = ReconciliationStore(self.path)
        store.checkpoint("1", "1502", "2031-01-01T00:15:00+00:00", [])
        incident = store.pending()["1"]["incident_id"]
        execute_candidate(candidate(), suffix="1502", site="1", ha=self.ha,
                          live=False, reconciliation_store=store)
        self.assertEqual(ReconciliationStore(self.path).pending()["1"]["incident_id"], incident)

    def test_corrupt_journal_fails_closed(self):
        self.path.write_text("broken-json", encoding="utf-8")
        with self.assertRaises(ValueError):
            ReconciliationStore(self.path)


if __name__ == "__main__":
    unittest.main()
