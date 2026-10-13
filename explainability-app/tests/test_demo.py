from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from energy_explain.demo import seed_demo
from energy_explain.store import Store


class ExplicitDemoTests(unittest.TestCase):
    def test_three_synthetic_scenarios_are_explicit_idempotent_and_unsettled(self):
        with tempfile.TemporaryDirectory() as temp:
            store = Store(Path(temp) / "demo.sqlite3")
            self.assertEqual(store.stations()["stations"], [])
            keys = seed_demo(store)
            self.assertEqual(len(keys), 3)
            self.assertEqual(seed_demo(store), keys)
            records = [store.get_decision(key) for key in keys]
            self.assertTrue(all(record["source_kind"] == "demo" for record in records))
            self.assertEqual({record["run_id"] for record in records}, {"demo-four-step-v1"})
            self.assertEqual({record["site_id"] for record in records}, {"site-01", "site-02", "site-03"})
            self.assertEqual(records[0]["snapshot"]["soc_percent"], 20)
            self.assertEqual(records[1]["snapshot"]["soc_percent"], 100)
            self.assertEqual(records[2]["snapshot"]["forecast_status"], "fallback")
            self.assertEqual(store.execution_status(keys[0]), "confirmed")
            self.assertEqual(store.execution_status(keys[1]), "candidate_only")
            self.assertIsNone(store.get_settlement("demo-four-step-v1"))
            self.assertEqual(len(store.execution_events(keys[0])), 2)


if __name__ == "__main__":
    unittest.main()
