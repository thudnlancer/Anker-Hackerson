"""Historical model integration with explicit synthetic inputs and no device actions."""
from __future__ import annotations

import copy
import hashlib
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd
from fastapi.testclient import TestClient
from pydantic import ValidationError

from energy_explain.api import create_app
from energy_explain.demo import seed_demo
from energy_explain.providers import ModelOutcome, get_provider
from energy_explain.recovered_provider import ADAPTED_SHA256, MODEL_VERSION, SOURCE_SHA256, RecoveredMilpProvider, verify_source
from energy_explain.recovered_validation import seed_validation, validation_records
from energy_explain.strategy_vendor.rolling_original import optimize_window as original_optimizer
from energy_explain.schemas import DecisionIn, ExecutionEvent, SettlementIn
from energy_explain.store import Conflict, Store
from tests.helpers import make_decision


class RecoveredModelIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.records = [record.model_dump(mode="json") for record in validation_records()]

    def setUp(self):
        self.provider = RecoveredMilpProvider()
        self.record = copy.deepcopy(self.records[0])

    def assert_physics(self, snapshot, result):
        dt, cap = snapshot["step_hours"], snapshot["capacity_kwh"]
        energy = snapshot["soc_percent"] * cap / 100
        purchase = revenue = degradation = 0
        self.assertEqual(len(result["plan"]), len(snapshot["load_forecast_kw"]))
        for t, step in enumerate(result["plan"]):
            c, d = step["charge_kw"], step["discharge_kw"]
            gi, go = step["grid_import_kw"], step["grid_export_kw"]
            self.assertAlmostEqual(snapshot["pv_forecast_kw"][t] + d + gi,
                snapshot["load_forecast_kw"][t] + c + go, places=6)
            energy += snapshot["charge_efficiency"] * c * dt - d * dt
            self.assertAlmostEqual(energy, step["soc_end_percent"] * cap / 100, places=6)
            self.assertGreaterEqual(step["soc_end_percent"], snapshot["soc_min_percent"] - 1e-6)
            self.assertLessEqual(step["soc_end_percent"], snapshot["soc_max_percent"] + 1e-6)
            self.assertGreaterEqual(min(c, d, gi, go), -1e-6)
            self.assertLessEqual(c, snapshot["max_charge_kw"] + 1e-6)
            self.assertLessEqual(d, snapshot["max_discharge_kw"] + 1e-6)
            self.assertLessEqual(min(c, d), 1e-6)
            self.assertLessEqual(min(gi, go), 1e-6)
            purchase += gi * snapshot["import_eur_kwh"][t] * dt
            revenue += go * snapshot["export_eur_kwh"][t] * dt
            degradation += (c + d) * snapshot["degradation_eur_kwh"] * dt
        terminal = energy * snapshot["terminal_eur_kwh"]
        expected = {"purchase_eur": purchase, "export_revenue_eur": revenue,
                    "degradation_eur": degradation, "terminal_credit_eur": terminal,
                    "objective_eur": purchase - revenue + degradation - terminal}
        for key, value in expected.items():
            self.assertAlmostEqual(result["cost"][key], value, places=6)

    def test_preserved_original_source_hash_is_locked(self):
        source = Path(__file__).parents[1] / "src/energy_explain/strategy_vendor/rolling_original.py"
        self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), SOURCE_SHA256)
        adapted = source.with_name("rolling.py")
        self.assertEqual(hashlib.sha256(adapted.read_bytes()).hexdigest(), ADAPTED_SHA256)
        verify_source()

    def test_replay_requires_explicit_saved_parameters_instead_of_schema_defaults(self):
        required = ("step_hours", "soc_min_percent", "soc_max_percent", "charge_efficiency",
                    "discharge_efficiency", "terminal_eur_kwh", "degradation_eur_kwh",
                    "pv_forecast_kw", "load_forecast_kw", "import_eur_kwh", "export_eur_kwh")
        for field in required:
            record = copy.deepcopy(self.record)
            record["snapshot"].pop(field)
            with self.subTest(field=field), self.assertRaises(ValidationError):
                DecisionIn.model_validate(record)
        # Old no-replay records keep their original optional-input/default contract.
        old = make_decision().model_dump(mode="json")
        for field in ("step_hours", "soc_min_percent", "soc_max_percent", "terminal_eur_kwh", "degradation_eur_kwh"):
            old["snapshot"].pop(field)
        self.assertIsNone(DecisionIn.model_validate(old).replay_context)

    def test_unconstrained_adapter_matches_unmodified_original_for_all_horizons(self):
        for record in self.records:
            with self.subTest(site=record["site_id"]):
                s = record["snapshot"]
                times = pd.Series(pd.date_range(s["device_time"], periods=len(s["pv_forecast_kw"]),
                    freq=pd.Timedelta(hours=s["step_hours"])))
                original = original_optimizer(times, np.array(s["pv_forecast_kw"]) * 1000,
                    np.array(s["load_forecast_kw"]) * 1000, np.array(s["import_eur_kwh"]),
                    np.array(s["export_eur_kwh"]), s["soc_percent"], capacity_kwh=s["capacity_kwh"],
                    max_power_w=s["max_charge_kw"] * 1000, charge_efficiency=s["charge_efficiency"],
                    terminal_price=s["terminal_eur_kwh"], degradation_cost_eur_kwh=s["degradation_eur_kwh"],
                    soc_min=s["soc_min_percent"], soc_max=s["soc_max_percent"], step_hours=s["step_hours"],
                    solver="milp", time_limit_seconds=10, command_contract=record["replay_context"]["command_contract"])
                adapted = self.provider.solve(s, record["replay_context"]["command_contract"])
                self.assertEqual(original.status, "optimal")
                self.assertAlmostEqual(adapted["cost"]["objective_eur"], original.objective, places=6)
                self.assertAlmostEqual(adapted["cost"]["objective_eur"], record["cost"]["objective_eur"], places=6)
                self.assert_physics(s, adapted)

    def test_fixed_rule_proof_and_free_original_candidate_use_distinct_baselines(self):
        s = self.record["snapshot"]
        fixed5 = self.provider.solve(s, "battery", first_charge_kw=5)
        fixed0 = self.provider.solve(s, "battery", first_charge_kw=0)
        free = self.provider.solve(s, "battery")
        self.assertAlmostEqual(fixed5["cost"]["objective_eur"], 0.569, places=6)
        self.assertAlmostEqual(fixed0["cost"]["objective_eur"], 1.3801052631578947, places=6)
        self.assertAlmostEqual(fixed0["cost"]["objective_eur"] - fixed5["cost"]["objective_eur"],
                               0.8111052631578947, places=6)
        self.assertAlmostEqual(free["plan"][0]["charge_kw"], 4.421052631578947, places=6)
        self.assertAlmostEqual(free["cost"]["objective_eur"], 0.5649473684210526, places=6)
        self.assertLess(free["cost"]["objective_eur"], fixed5["cost"]["objective_eur"])
        compared = self.provider.compare(self.record, 0)
        self.assertAlmostEqual(compared["baseline"]["cost"]["objective_eur"], free["cost"]["objective_eur"], places=6)
        self.assertAlmostEqual(compared["delta_eur"], 0.8151578947368421, places=6)

    def test_first_step_replay_fixes_all_four_saved_powers(self):
        first = {"charge_kw": 1, "discharge_kw": 0, "grid_import_kw": 2, "grid_export_kw": 0}
        result = self.provider.solve(self.record["snapshot"], "battery", first_step=first)
        for key, value in first.items():
            self.assertAlmostEqual(result["plan"][0][key], value, places=6)
        self.assert_physics(self.record["snapshot"], result)

    def test_simultaneous_battery_or_grid_flows_are_infeasible(self):
        impossible = (
            {"charge_kw": 1, "discharge_kw": 1, "grid_import_kw": 1, "grid_export_kw": 0},
            {"charge_kw": 1, "discharge_kw": 0, "grid_import_kw": 3, "grid_export_kw": 1},
        )
        for first in impossible:
            with self.subTest(first=first), self.assertRaises(ModelOutcome) as caught:
                self.provider.solve(self.record["snapshot"], "battery", first_step=first)
            self.assertEqual(caught.exception.status, "infeasible")

    def test_nonzero_pv_negative_prices_and_96_quarter_hour_steps(self):
        self.assertLess(min(self.records[1]["snapshot"]["import_eur_kwh"]), 0)
        for record in self.records[1:]:
            with self.subTest(site=record["site_id"]):
                s = record["snapshot"]
                self.assertGreater(max(s["pv_forecast_kw"]), 0)
                compared = self.provider.compare(record, 0)
                for rollout in (compared["baseline"], compared["alternative"]):
                    self.assert_physics(s, rollout)
                    for step in rollout["plan"]:
                        if step["discharge_kw"] > 1e-6:
                            self.assertLessEqual(step["grid_import_kw"], 1e-6)
                            self.assertLessEqual(step["grid_export_kw"], s["max_discharge_kw"] + 1e-6)
                self.assertAlmostEqual(compared["alternative"]["plan"][0]["charge_kw"], 0, places=6)
        self.assertEqual(len(self.records[2]["snapshot"]["pv_forecast_kw"]), 96)
        self.assertEqual(self.records[2]["snapshot"]["step_hours"], 0.25)

    def test_power_limit_and_full_soc_are_infeasible(self):
        with self.assertRaises(ModelOutcome) as caught:
            self.provider.solve(self.record["snapshot"], "battery", first_charge_kw=6)
        self.assertEqual(caught.exception.status, "infeasible")
        full = copy.deepcopy(self.record["snapshot"])
        full["soc_percent"] = 100
        with self.assertRaises(ModelOutcome) as caught:
            self.provider.solve(full, "battery", first_charge_kw=1)
        self.assertEqual(caught.exception.status, "infeasible")

    def test_incompatible_source_hash_power_efficiency_or_input_is_unsupported(self):
        changes = (
            lambda d: d.update(source_kind="demo"),
            lambda d: d.update(model_version="different-model"),
            lambda d: d["replay_context"].update(upstream_source_sha256="0" * 64),
            lambda d: d["snapshot"].update(max_discharge_kw=4),
            lambda d: d["snapshot"].update(discharge_efficiency=0.9),
            lambda d: d["snapshot"].update(load_forecast_kw=[]),
            lambda d: d.update(replay_context=None),
            lambda d: d["provenance"].update(input_kind="incomplete"),
            lambda d: d["action"].update(power_kw=1),
        )
        for change in changes:
            decision = copy.deepcopy(self.record)
            change(decision)
            with self.subTest(change=change):
                self.assertFalse(self.provider.supports(decision))
                self.assertIsNone(get_provider(decision))
                with self.assertRaises(ModelOutcome) as caught:
                    self.provider.compare(decision, 0)
                self.assertEqual(caught.exception.status, "unsupported")

    def test_changed_cost_components_fail_same_snapshot_replay(self):
        self.record["cost"]["purchase_eur"] += 1
        self.record["cost"]["export_revenue_eur"] += 1
        with self.assertRaises(ModelOutcome) as caught:
            self.provider.compare(self.record, 0)
        self.assertEqual(caught.exception.status, "failed")

    def test_grid_control_semantics_require_sop_contract(self):
        for kind, semantic, key in (("charge", "grid_charge", "grid_import_kw"),
                                    ("discharge", "grid_export_target", "grid_export_kw")):
            record = copy.deepcopy(self.record)
            record["action"] = {"kind": kind, "control_semantics": semantic,
                                "power_kw": record["replay_context"]["first_step"][key]}
            self.assertEqual(record["replay_context"]["command_contract"], "battery")
            with self.subTest(semantic=semantic):
                self.assertFalse(self.provider.supports(record))
                self.assertIsNone(get_provider(record))

    def test_direct_solve_rejects_unsupported_efficiency_and_asymmetric_power(self):
        for change in ({"discharge_efficiency": 0.9}, {"max_discharge_kw": 4}):
            snapshot = copy.deepcopy(self.record["snapshot"])
            snapshot.update(change)
            with self.subTest(change=change), self.assertRaises(ModelOutcome) as caught:
                self.provider.solve(snapshot, "battery")
            self.assertEqual(caught.exception.status, "unsupported")

    def test_validation_seed_has_explicit_provenance_and_no_control_or_settlement_events(self):
        with tempfile.TemporaryDirectory() as temp:
            store = Store(Path(temp) / "validation.sqlite")
            keys = seed_validation(store)
            self.assertEqual(seed_validation(store), keys)
            for key in keys:
                decision = store.get_decision(key)
                self.assertEqual(decision["source_kind"], "external")
                self.assertEqual(decision["provenance"]["origin"], "model_validation")
                self.assertEqual(decision["provenance"]["input_kind"], "synthetic")
                self.assertEqual(decision["model_version"], MODEL_VERSION)
                self.assertEqual(store.execution_events(key), [])
                self.assertEqual(store.execution_status(key), "candidate_only")
                self.assertIsNone(store.get_settlement(decision["run_id"]))

    def test_api_ingestion_to_independent_worker_recovered_model_result(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "api.sqlite"
            with TestClient(create_app(path)) as client:
                response = client.post("/api/decisions", json=self.record)
                self.assertEqual(response.status_code, 200, response.text)
                imported = response.json()
                self.assertTrue(imported["capabilities"]["counterfactual"])
                self.assertEqual(imported["execution_events"], [])
                self.assertIsNone(imported["settlement"])
                evidence = client.get(f"/api/decisions/{imported['id']}/explanation").json()
                self.assertTrue(any(f["code"] == "replay_context" for f in evidence["facts"]))
                self.assertTrue(any(f["code"] == "provenance" for f in evidence["facts"]))
                job = client.post(f"/api/decisions/{imported['id']}/counterfactuals",
                                  json={"first_charge_kw": 0}).json()
                done = subprocess.run([sys.executable, "-m", "energy_explain.worker", "--db", str(path), "--once"],
                                      capture_output=True, text=True, timeout=40)
                self.assertEqual(done.returncode, 0, done.stderr)
                result = client.get(f"/api/counterfactuals/{job['id']}").json()
                self.assertEqual(result["status"], "ready", result["error"])
                self.assertAlmostEqual(result["result"]["delta_eur"], 0.8151578947368421, places=6)
                self.assertTrue(any("人工输入" in note for note in result["result"]["assumptions"]))
                self.assertEqual(client.get(f"/api/decisions/{imported['id']}").json()["execution_status"], "candidate_only")
                # Explicit API event fixtures exercise ingestion only; no actual controller is used.
                event_path = f"/api/decisions/{imported['id']}/execution-events"
                issued = self.record["snapshot"]["device_time"]
                submit = {"event_id": "unit-submit", "instruction_id": "unit-instruction",
                          "kind": "submitted", "issued_at": issued,
                          "note": "Synthetic API ingestion fixture; no HA write."}
                self.assertEqual(client.post(event_path, json=submit).status_code, 200)
                confirm = dict(submit, event_id="unit-confirm", kind="confirmed",
                    observed_at="2026-10-13T00:00:02Z", device_time="2026-10-13T00:15:00Z",
                    observed_mode="synthetic-api-fixture", observed_power_kw=imported["action"]["power_kw"])
                confirmation = client.post(event_path, json=confirm)
                self.assertEqual(confirmation.status_code, 200, confirmation.text)
                detail = client.get(f"/api/decisions/{imported['id']}").json()
                self.assertEqual(detail["execution_status"], "confirmed")
                self.assertEqual(len(detail["execution_events"]), 2)
                self.assertEqual(detail["snapshot_hash"], imported["snapshot_hash"])
                self.assertIsNone(detail["settlement"])

    def test_optional_metadata_omission_preserves_v1_demo_idempotency(self):
        old = make_decision()
        payload = old.model_dump(mode="json")
        self.assertNotIn("replay_context", payload)
        self.assertNotIn("provenance", payload)
        explicit_none = old.model_copy(update={"replay_context": None, "provenance": None})
        self.assertEqual(payload, explicit_none.model_dump(mode="json"))
        with tempfile.TemporaryDirectory() as temp:
            store = Store(Path(temp) / "compat.sqlite")
            first = store.add_decision(old)
            self.assertFalse(store.add_decision(explicit_none)["created"])
            self.assertEqual(store.add_decision(explicit_none)["id"], first["id"])
            keys = seed_demo(store)
            seed_validation(store)
            self.assertEqual(seed_demo(store), keys)

    def test_signed_zero_decision_reimport_preserves_original_payload_and_hash(self):
        def normalize_zero(value):
            if isinstance(value, dict):
                return {key: normalize_zero(item) for key, item in value.items()}
            if isinstance(value, list):
                return [normalize_zero(item) for item in value]
            return 0.0 if isinstance(value, float) and value == 0 else value

        record = copy.deepcopy(self.record)
        record["snapshot"]["pv_kw"] = -0.0
        record["snapshot"]["pv_forecast_kw"][0] = -0.0
        record["cost"]["terminal_credit_eur"] = -0.0
        record["replay_context"]["first_step"]["discharge_kw"] = -0.0
        with tempfile.TemporaryDirectory() as temp:
            store = Store(Path(temp) / "signed-zero.sqlite")
            with TestClient(create_app(store.path)) as client:
                first = client.post("/api/decisions", json=record)
                self.assertEqual(first.status_code, 200, first.text)
                saved = first.json()
                with store.connect() as db:
                    before = db.execute("SELECT payload,snapshot_hash FROM decisions WHERE id=?", (saved["id"],)).fetchone()
                    original_bytes, original_hash = before["payload"], before["snapshot_hash"]
                self.assertIn("-0.0", original_bytes)
                # JSON.stringify in the browser turns -0.0 into 0; emulate that roundtrip.
                normalized = normalize_zero(record)
                duplicate = client.post("/api/decisions", json=normalized)
                self.assertEqual(duplicate.status_code, 200, duplicate.text)
                self.assertFalse(duplicate.json()["created"])
                self.assertEqual(duplicate.json()["id"], saved["id"])
                self.assertEqual(duplicate.json()["snapshot_hash"], original_hash)
                with store.connect() as db:
                    after = db.execute("SELECT payload,snapshot_hash FROM decisions WHERE id=?", (saved["id"],)).fetchone()
                    self.assertEqual(after["payload"], original_bytes)
                    self.assertEqual(after["snapshot_hash"], original_hash)
                    self.assertEqual(db.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0], 1)
                normalized["snapshot"]["load_kw"] += 0.1
                self.assertEqual(client.post("/api/decisions", json=normalized).status_code, 409)

    def test_signed_zero_execution_reimport_is_append_idempotent(self):
        with tempfile.TemporaryDirectory() as temp:
            store = Store(Path(temp) / "events-zero.sqlite")
            key = store.add_decision(DecisionIn.model_validate(self.record))["id"]
            issued = self.record["snapshot"]["device_time"]
            store.add_execution(key, ExecutionEvent.model_validate({"event_id": "zero-submit",
                "instruction_id": "zero-instruction", "kind": "submitted", "issued_at": issued}))
            payload = {"event_id": "zero-confirm", "instruction_id": "zero-instruction", "kind": "confirmed",
                "issued_at": issued, "device_time": "2026-10-13T00:15:00Z",
                "observed_at": "2026-10-13T00:00:02Z", "observed_mode": "synthetic-api-fixture",
                "observed_power_kw": -0.0, "note": "Signed-zero ingestion unit fixture; no device connection."}
            self.assertTrue(store.add_execution(key, ExecutionEvent.model_validate(payload))["created"])
            with store.connect() as db:
                before = db.execute("SELECT payload FROM execution_events WHERE event_id='zero-confirm'").fetchone()[0]
            duplicate = dict(payload, observed_power_kw=0.0)
            self.assertFalse(store.add_execution(key, ExecutionEvent.model_validate(duplicate))["created"])
            self.assertEqual(len(store.execution_events(key)), 2)
            self.assertEqual(store.execution_status(key), "confirmed")
            with store.connect() as db:
                self.assertEqual(db.execute("SELECT payload FROM execution_events WHERE event_id='zero-confirm'").fetchone()[0], before)
            with self.assertRaises(Conflict):
                store.add_execution(key, ExecutionEvent.model_validate(dict(payload, observed_power_kw=0.1)))

    def test_signed_zero_settlement_reimport_is_append_idempotent(self):
        with tempfile.TemporaryDirectory() as temp:
            store = Store(Path(temp) / "settlement-zero.sqlite")
            store.add_decision(make_decision(source_kind="external", run_id="unit-settlement-run"))
            payload = {"event_id": "zero-settlement", "status": "complete", "source_kind": "official",
                "captured_at": "2026-10-13T00:00:00Z", "window_start": "2027-01-24T00:00:00Z",
                "window_end": "2027-01-31T00:00:00Z", "saving_settled_eur": -0.0,
                "saving_settled_percent": -0.0, "raw_fields": {"fixture": "signed-zero ingestion unit test"}}
            self.assertTrue(store.add_settlement("unit-settlement-run", SettlementIn.model_validate(payload))["created"])
            with store.connect() as db:
                before = db.execute("SELECT payload FROM settlements WHERE event_id='zero-settlement'").fetchone()[0]
            duplicate = dict(payload, saving_settled_eur=0.0, saving_settled_percent=0.0)
            self.assertFalse(store.add_settlement("unit-settlement-run", SettlementIn.model_validate(duplicate))["created"])
            with store.connect() as db:
                self.assertEqual(db.execute("SELECT COUNT(*) FROM settlements").fetchone()[0], 1)
                self.assertEqual(db.execute("SELECT payload FROM settlements WHERE event_id='zero-settlement'").fetchone()[0], before)
            with self.assertRaises(Conflict):
                store.add_settlement("unit-settlement-run", SettlementIn.model_validate(dict(payload, saving_settled_eur=0.1)))


if __name__ == "__main__":
    unittest.main()
