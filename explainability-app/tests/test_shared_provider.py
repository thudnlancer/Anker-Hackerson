"""Export → import → shared model replay; no HA client or real telemetry is involved."""
from __future__ import annotations

import builtins
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

import energy_explain
from energy_explain.api import create_app
from energy_explain.providers import ModelOutcome, get_provider
from energy_explain.schemas import DecisionIn
from energy_explain.shared_provider import MODEL_VERSION, SOURCE_SHA256, SharedMilpProvider
from energy_explain.store import Store
from energy_lab.rolling import optimize_window
from energy_lab.shadow import evaluate_snapshot

PROJECT = Path(__file__).resolve().parents[2]
APP = PROJECT / "explainability-app"


def snapshot_fixture():
    prices = {day: {"date": date, "points": [
        {"start": f"{date}T{hour:02d}:00:00Z", "import_price": 0.3, "export_price": 0.1}
        for hour in range(24)]}
        for day, date in (("today", "2031-01-01"), ("tomorrow", "2031-01-02"))}
    state = {"soc": "45", "solar_power": "400", "home_load": "1000",
             "device_time": "2031-01-01T00:15:00+00:00",
             "device_time_last_changed": "2026-10-13T00:00:04Z"}
    return {"received_at": "2026-10-13T00:00:05Z", "prices": prices,
            "stations": {"1": {"state": state, "received_at": "2026-10-13T00:00:05Z",
                                 "weather": {"unavailable": True}}}}


def child_environment():
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join((str(APP / "src"), str(PROJECT / "src")))
    env.pop("ENERGY_EXPLAIN_PROVIDER_MODULE", None)
    return env


class SharedProviderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        snapshot = snapshot_fixture()
        records = []
        result = evaluate_snapshot(snapshot, now=snapshot["received_at"], solver_seconds=10,
            evidence_sink=records.append, evidence_identity={"run_id": "unit-shared", "input_kind": "synthetic"})
        if result["stations"]["1"]["status"] != "candidate_only" or len(records) != 1:
            raise AssertionError("shared strategy fixture did not export a complete successful record")
        cls.original_record = records[0]

    def setUp(self):
        self.record = copy.deepcopy(self.original_record)
        self.provider = SharedMilpProvider()

    def test_common_package_and_canonical_optimizer_are_used(self):
        self.assertTrue(Path(energy_explain.__file__).resolve().is_relative_to(APP / "src"))
        self.assertIs(self.provider.optimizer(), optimize_window)
        self.assertEqual(hashlib.sha256((PROJECT / "src/energy_lab/rolling.py").read_bytes()).hexdigest(), SOURCE_SHA256)
        with patch("energy_explain.strategy_vendor.rolling.optimize_window", side_effect=AssertionError("frozen fallback called")):
            result = self.provider.solve(self.record["snapshot"], "sop")
        self.assertEqual(result["status"], "optimal")
        for key, value in self.record["cost"].items():
            self.assertAlmostEqual(result["cost"][key], value, places=6)

    def test_complete_sidecar_is_pydantic_compatible_and_costs_reconcile(self):
        parsed = DecisionIn.model_validate(self.record)
        self.assertEqual(parsed.model_version, MODEL_VERSION)
        self.assertEqual(parsed.replay_context.upstream_source_sha256, SOURCE_SHA256)
        self.assertEqual(parsed.replay_context.command_contract, "sop")
        self.assertEqual(parsed.snapshot.degradation_eur_kwh, 0.02)
        self.assertEqual(parsed.snapshot.terminal_eur_kwh, 0.3)
        self.assertEqual(len(parsed.snapshot.pv_forecast_kw), 96)
        self.assertEqual(parsed.provenance.input_kind, "synthetic")
        self.assertIsInstance(get_provider(self.record), SharedMilpProvider)
        comparison = self.provider.compare(self.record, 0)
        for key, value in self.record["cost"].items():
            self.assertAlmostEqual(comparison["baseline"]["cost"][key], value, places=6)
        self.assertAlmostEqual(comparison["baseline"]["cost"]["purchase_eur"], 24 * 0.6 * 0.3, places=6)
        self.assertAlmostEqual(comparison["baseline"]["cost"]["terminal_credit_eur"], 14 * 0.45 * 0.3, places=6)
        self.assertAlmostEqual(comparison["delta_eur"], 0, places=6)
        self.assertTrue(any("共同仓库" in note for note in comparison["assumptions"]))
        self.assertTrue(any("人工输入" in note for note in comparison["assumptions"]))

    def test_wrong_version_hash_or_grid_contract_has_no_capability(self):
        mutations = (
            lambda d: d.update(model_version="different-shared-version"),
            lambda d: d["replay_context"].update(upstream_source_sha256="0" * 64),
            lambda d: d.update(source_kind="demo"),
            lambda d: d["provenance"].update(input_kind="incomplete"),
            lambda d: d["snapshot"].update(discharge_efficiency=0.9),
        )
        for mutate in mutations:
            record = copy.deepcopy(self.record)
            mutate(record)
            with self.subTest(mutate=mutate):
                self.assertFalse(self.provider.supports(record))
                self.assertIsNone(get_provider(record))
        for kind, semantic, key in (("charge", "grid_charge", "grid_import_kw"),
                                    ("discharge", "grid_export_target", "grid_export_kw")):
            record = copy.deepcopy(self.record)
            record["replay_context"]["command_contract"] = "battery"
            record["action"] = {"kind": kind, "control_semantics": semantic,
                                "power_kw": record["replay_context"]["first_step"][key]}
            with self.subTest(semantic=semantic):
                self.assertFalse(self.provider.supports(record))

    def test_changed_shared_file_hash_fails_without_frozen_fallback(self):
        with patch("energy_explain.shared_provider.Path.read_bytes", return_value=b"changed shared optimizer source"):
            self.assertFalse(self.provider.supports(self.record))
            with self.assertRaises(ModelOutcome) as caught:
                self.provider.solve(self.record["snapshot"], "sop")
            self.assertEqual(caught.exception.status, "failed")

    def test_missing_shared_source_is_unsupported_without_frozen_fallback(self):
        native_import = builtins.__import__
        def missing(name, *args, **kwargs):
            if name == "energy_lab.rolling":
                raise ImportError("test fixture: shared source unavailable")
            return native_import(name, *args, **kwargs)
        with patch("builtins.__import__", side_effect=missing):
            self.assertFalse(self.provider.supports(self.record))
            with self.assertRaises(ModelOutcome) as caught:
                self.provider.optimizer()
            self.assertEqual(caught.exception.status, "unsupported")

    def test_api_import_idempotency_and_independent_worker_ledger(self):
        with tempfile.TemporaryDirectory() as temp:
            db = Path(temp) / "shared.sqlite"
            with TestClient(create_app(db)) as client:
                imported = client.post("/api/decisions", json=self.record)
                self.assertEqual(imported.status_code, 200, imported.text)
                decision = imported.json()
                self.assertTrue(decision["created"])
                self.assertTrue(decision["capabilities"]["counterfactual"])
                self.assertEqual(decision["execution_status"], "candidate_only")
                self.assertEqual(decision["execution_events"], [])
                self.assertIsNone(decision["settlement"])
                explanation = client.get(f"/api/decisions/{decision['id']}/explanation").json()
                self.assertEqual(explanation["template_version"], "facts-template-v2")
                self.assertIn("共同策略", explanation["scope"])
                self.assertIn("人工", explanation["scope"])
                self.assertNotIn("恢复的历史", explanation["scope"])
                duplicate = client.post("/api/decisions", json=self.record).json()
                self.assertFalse(duplicate["created"])
                self.assertEqual(duplicate["id"], decision["id"])
                self.assertEqual(duplicate["snapshot_hash"], decision["snapshot_hash"])
                queued = client.post(f"/api/decisions/{decision['id']}/counterfactuals", json={"first_charge_kw": 0})
                self.assertEqual(queued.status_code, 202)
                job = queued.json()
                worker = subprocess.run([sys.executable, "-m", "energy_explain.worker", "--db", str(db), "--once"],
                    cwd=APP, env=child_environment(), capture_output=True, text=True, timeout=40)
                self.assertEqual(worker.returncode, 0, worker.stderr)
                result = client.get(f"/api/counterfactuals/{job['id']}").json()
                self.assertEqual(result["status"], "ready", result["error"])
                for key, value in self.record["cost"].items():
                    self.assertAlmostEqual(result["result"]["baseline"]["cost"][key], value, places=6)
                self.assertEqual(client.get(f"/api/decisions/{decision['id']}").json()["execution_events"], [])

    def test_offline_runner_sidecar_import_cli_is_repeatable_and_replayable(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            snapshot = directory / "snapshot.json"
            snapshot.write_text(json.dumps(snapshot_fixture()), encoding="utf-8")
            output = directory / "shadow-output"
            runner = subprocess.run([sys.executable, str(PROJECT / "run_shadow_dry_run.py"),
                "--snapshot", str(snapshot), "--replay-at-capture", "--config", str(directory / "unused-config.json"),
                "--model-directory", str(directory / "missing-models"), "--output", str(output),
                "--explainability-run-id", "unit-cli-import", "--explainability-input-kind", "synthetic"],
                cwd=PROJECT, env=child_environment(), capture_output=True, text=True, timeout=40)
            self.assertEqual(runner.returncode, 0, runner.stderr)
            evidence = output / "explainability-decisions.jsonl"
            rows = [DecisionIn.model_validate_json(line) for line in evidence.read_text().splitlines()]
            self.assertEqual(len(rows), 1)
            db = directory / "imported.sqlite"
            args = [sys.executable, "-m", "energy_explain", "import-records", "--file", str(evidence), "--db", str(db)]
            first = subprocess.run(args, cwd=APP, env=child_environment(), capture_output=True, text=True, timeout=20)
            self.assertEqual(first.returncode, 0, first.stderr)
            imported = json.loads(first.stdout)
            self.assertEqual(imported["created"], 1)
            duplicate = subprocess.run(args, cwd=APP, env=child_environment(), capture_output=True, text=True, timeout=20)
            self.assertEqual(duplicate.returncode, 0, duplicate.stderr)
            self.assertEqual(json.loads(duplicate.stdout)["created"], 0)
            store = Store(db)
            record = store.get_decision(imported["decision_keys"][0])
            self.assertEqual(record["cost"], rows[0].cost.model_dump())
            self.assertEqual(store.execution_status(record["id"]), "candidate_only")
            self.assertEqual(store.execution_events(record["id"]), [])
            self.assertIsNone(store.get_settlement(record["run_id"]))
            result = self.provider.compare(record, 0)
            self.assertAlmostEqual(result["baseline"]["cost"]["objective_eur"], record["cost"]["objective_eur"], places=6)

    def test_import_cli_validates_complete_file_before_starting_database(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            evidence = directory / "invalid.jsonl"
            evidence.write_text(json.dumps(self.record) + "\n" + json.dumps({"snapshot": "PRIVATE-INVALID-ROW"}) + "\n")
            db = directory / "must-not-exist.sqlite"
            result = subprocess.run([sys.executable, "-m", "energy_explain", "import-records", "--file", str(evidence), "--db", str(db)],
                cwd=APP, env=child_environment(), capture_output=True, text=True, timeout=20)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(db.exists())
            self.assertNotIn("PRIVATE-INVALID-ROW", result.stderr)


if __name__ == "__main__":
    unittest.main()
