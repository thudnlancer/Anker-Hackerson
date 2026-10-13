from __future__ import annotations

import copy
import unittest
from unittest.mock import patch

from energy_explain.providers import DemoMilpProvider, ModelOutcome, get_provider, run_comparison
from tests.helpers import make_decision


class SyntheticModelTests(unittest.TestCase):
    def setUp(self):
        self.provider = DemoMilpProvider()
        self.record = make_decision().model_dump(mode="json")

    def assert_physics_and_cost(self, snapshot, rollout):
        dt, cap = snapshot["step_hours"], snapshot["capacity_kwh"]
        energy = cap * snapshot["soc_percent"] / 100
        purchase = revenue = wear = 0
        for t, step in enumerate(rollout["plan"]):
            c, d = step["charge_kw"], step["discharge_kw"]
            gi, go = step["grid_import_kw"], step["grid_export_kw"]
            self.assertAlmostEqual(snapshot["pv_forecast_kw"][t] + d + gi,
                                   snapshot["load_forecast_kw"][t] + c + go, places=6)
            energy += snapshot["charge_efficiency"] * c * dt - d * dt / snapshot["discharge_efficiency"]
            self.assertAlmostEqual(step["soc_end_percent"] * cap / 100, energy, places=6)
            self.assertGreaterEqual(step["soc_end_percent"], snapshot["soc_min_percent"] - 1e-6)
            self.assertLessEqual(step["soc_end_percent"], snapshot["soc_max_percent"] + 1e-6)
            self.assertLessEqual(c, snapshot["max_charge_kw"] + 1e-6)
            self.assertLessEqual(d, snapshot["max_discharge_kw"] + 1e-6)
            self.assertGreaterEqual(min(c, d, gi, go), -1e-6)
            self.assertLessEqual(min(c, d), 1e-6)
            self.assertLessEqual(min(gi, go), 1e-6)
            purchase += gi * snapshot["import_eur_kwh"][t] * dt
            revenue += go * snapshot["export_eur_kwh"][t] * dt
            wear += (c + d) * snapshot["degradation_eur_kwh"] * dt
        terminal = energy * snapshot["terminal_eur_kwh"]
        expected = dict(purchase_eur=purchase, export_revenue_eur=revenue,
                        degradation_eur=wear, terminal_credit_eur=terminal,
                        objective_eur=purchase - revenue + wear - terminal)
        for field, value in expected.items():
            self.assertAlmostEqual(rollout["cost"][field], value, places=6)

    def test_original_four_step_proof_and_independent_physics(self):
        result = run_comparison(self.record, 0)
        self.assertAlmostEqual(result["baseline"]["cost"]["objective_eur"], 0.569, places=6)
        self.assertAlmostEqual(result["alternative"]["cost"]["objective_eur"], 1.3801052631578947, places=6)
        self.assertAlmostEqual(result["delta_eur"], 0.8111052631578947, places=6)
        self.assertAlmostEqual(result["baseline"]["plan"][0]["charge_kw"], 5, places=6)
        self.assertAlmostEqual(result["alternative"]["plan"][0]["charge_kw"], 0, places=6)
        self.assert_physics_and_cost(self.record["snapshot"], result["baseline"])
        self.assert_physics_and_cost(self.record["snapshot"], result["alternative"])

    def test_independent_energy_check_uses_saved_efficiencies_and_reserve(self):
        snapshot = copy.deepcopy(self.record["snapshot"])
        snapshot.update(discharge_efficiency=0.8, soc_min_percent=10)
        result = self.provider.solve(snapshot, 5)
        self.assert_physics_and_cost(snapshot, result)

    def test_negative_difference_is_preserved(self):
        self.record["snapshot"]["import_eur_kwh"][0] = 0.5
        result = run_comparison(self.record, 0)
        self.assertLess(result["delta_eur"], -1)
        self.assertAlmostEqual(result["delta_eur"],
            result["alternative"]["cost"]["objective_eur"] - result["baseline"]["cost"]["objective_eur"], places=8)

    def test_zero_and_negative_prices_still_obey_mutual_exclusion(self):
        self.record["snapshot"]["import_eur_kwh"] = [-0.1, 0, 0.4, 0.15]
        result = run_comparison(self.record, 0)
        self.assert_physics_and_cost(self.record["snapshot"], result["baseline"])
        self.assert_physics_and_cost(self.record["snapshot"], result["alternative"])

    def test_charge_above_power_limit_and_full_soc_are_infeasible(self):
        with self.assertRaises(ModelOutcome) as caught:
            self.provider.solve(self.record["snapshot"], 6)
        self.assertEqual(caught.exception.status, "infeasible")
        snapshot = copy.deepcopy(self.record["snapshot"])
        snapshot["soc_percent"] = 100
        with self.assertRaises(ModelOutcome) as caught:
            self.provider.solve(snapshot, 1)
        self.assertEqual(caught.exception.status, "infeasible")

    def test_real_decision_cannot_use_demo_model_even_with_forged_version(self):
        self.record["source_kind"] = "external"
        self.assertIsNone(get_provider(self.record))
        with self.assertRaises(ModelOutcome) as caught:
            run_comparison(self.record, 0)
        self.assertEqual(caught.exception.status, "unsupported")

    def test_unknown_version_and_nonzero_pv_do_not_use_demo_provider(self):
        self.record["model_version"] = "real-milp-v99"
        self.assertIsNone(get_provider(self.record))
        self.record["model_version"] = "demo-milp-v1"
        self.record["snapshot"]["pv_forecast_kw"][0] = 0.2
        self.assertIsNone(get_provider(self.record))

    def test_saved_baseline_cost_cannot_be_silently_replaced(self):
        self.record["cost"] = dict(purchase_eur=0, export_revenue_eur=0,
                                   degradation_eur=0, terminal_credit_eur=0, objective_eur=0)
        with self.assertRaises(ModelOutcome) as caught:
            run_comparison(self.record, 0)
        self.assertEqual(caught.exception.status, "failed")

    def test_equal_objective_with_different_cost_components_fails_replay(self):
        baseline = self.provider.solve(self.record["snapshot"], 5)
        self.record["cost"] = dict(baseline["cost"])
        self.record["cost"]["purchase_eur"] += 1
        self.record["cost"]["export_revenue_eur"] += 1
        with self.assertRaises(ModelOutcome) as caught:
            run_comparison(self.record, 0)
        self.assertEqual(caught.exception.status, "failed")

    def test_baseline_timeout_is_not_reported_as_failed_or_infeasible(self):
        with patch.object(self.provider, "solve", side_effect=ModelOutcome("timeout", "baseline deadline")):
            with self.assertRaises(ModelOutcome) as caught:
                self.provider.compare(self.record, 0)
        self.assertEqual(caught.exception.status, "timeout")


if __name__ == "__main__":
    unittest.main()
