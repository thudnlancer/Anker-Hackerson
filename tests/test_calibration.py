from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from energy_lab.calibration import check_sample, summarize, summarize_site


def balanced_state(**overrides):
    # pv 2000 + import 1000 + discharge 500 == load 3000 + export 0 + charge 500
    state = dict(solar_power=2000, home_load=3000,
                 battery_charging_power_w=500, battery_discharging_power_w=500,
                 grid_import_power_w=1000, grid_export_power_w=0)
    # mutual exclusion: keep one battery side inactive
    state["battery_discharging_power_w"] = 0
    state["grid_import_power_w"] = 1500  # 2000 + 1500 == 3000 + 0 + 500
    state.update(overrides)
    return state


class CheckSampleTests(unittest.TestCase):
    def test_balanced_sample_passes_all(self):
        result = check_sample(balanced_state())
        self.assertTrue(result["usable"])
        self.assertEqual(result["reason"], "checked")
        self.assertTrue(all(item["passed"] for item in result["checks"].values()))
        self.assertAlmostEqual(result["residual_w"], 0.0)

    def test_discharge_case_balance(self):
        # pv 0 + import 0 + discharge 2000 == load 2500 + export 0 + charge 0 -> imbalance 500 fails
        result = check_sample(balanced_state(solar_power=0, home_load=2500,
                                             battery_charging_power_w=0, battery_discharging_power_w=2000,
                                             grid_import_power_w=0))
        self.assertIn("power_balance", result["reason"])

    def test_export_case_passes(self):
        # pv 4000 + import 0 + discharge 0 == load 1000 + export 2000 + charge 1000
        result = check_sample(balanced_state(solar_power=4000, home_load=1000,
                                             battery_charging_power_w=1000, grid_import_power_w=0,
                                             grid_export_power_w=2000))
        self.assertTrue(all(item["passed"] for item in result["checks"].values()))

    def test_pv_rating_bound(self):
        self.assertFalse(check_sample(balanced_state(), pv_rating_w=1500)["checks"]["pv_sensor_rating"]["passed"])
        self.assertTrue(check_sample(balanced_state(), pv_rating_w=2000)["checks"]["pv_sensor_rating"]["passed"])
        self.assertFalse(check_sample(balanced_state(solar_power=-5))["checks"]["pv_sensor_rating"]["passed"])

    def test_load_sign(self):
        result = check_sample(balanced_state(home_load=-100))
        self.assertFalse(result["checks"]["load_sign_direction"]["passed"])

    def test_battery_mutex_violation(self):
        result = check_sample(balanced_state(battery_discharging_power_w=800))
        self.assertFalse(result["checks"]["battery_mutual_exclusion"]["passed"])

    def test_grid_mutex_violation(self):
        result = check_sample(balanced_state(grid_export_power_w=300))
        self.assertFalse(result["checks"]["grid_mutual_exclusion"]["passed"])

    def test_balance_tolerance_boundary(self):
        result = check_sample(balanced_state(grid_import_power_w=1750), balance_tolerance_w=250)
        self.assertTrue(result["checks"]["power_balance"]["passed"])
        result = check_sample(balanced_state(grid_import_power_w=1751), balance_tolerance_w=250)
        self.assertFalse(result["checks"]["power_balance"]["passed"])

    def test_unusable_on_unavailable_fields(self):
        for bad in ("unavailable", "unknown", None, float("nan")):
            result = check_sample(balanced_state(battery_charging_power_w=bad))
            self.assertFalse(result["usable"], bad)
            self.assertIn("battery_charging_power_w", result["reason"])

    def test_invalid_inputs_raise(self):
        with self.assertRaises(TypeError):
            check_sample("not a dict")
        with self.assertRaises(ValueError):
            check_sample(balanced_state(), mutex_tolerance_w=-1)
        with self.assertRaises(ValueError):
            check_sample(balanced_state(), pv_rating_w=0)


class SummarizeTests(unittest.TestCase):
    def test_calibrated_site(self):
        results = [check_sample(balanced_state()) for _ in range(3)]
        summary = summarize_site(results)
        self.assertTrue(summary["calibrated"])
        self.assertEqual(summary["verdict"], "calibrated")
        self.assertEqual(summary["pv_max_observed_w"], 2000.0)

    def test_violations_counted_per_check(self):
        results = [check_sample(balanced_state(battery_discharging_power_w=800)) for _ in range(2)]
        summary = summarize_site(results)
        self.assertFalse(summary["calibrated"])
        self.assertEqual(summary["verdict"], "violations_detected")
        self.assertEqual(summary["failures_by_check"].get("battery_mutual_exclusion"), 2)

    def test_unusable_samples_block_calibration(self):
        results = [check_sample(balanced_state()), check_sample(balanced_state(solar_power="unavailable"))]
        summary = summarize_site(results)
        self.assertFalse(summary["calibrated"])
        self.assertEqual(summary["verdict"], "unusable_samples")
        self.assertEqual(summary["unusable"], 1)

    def test_empty_site(self):
        self.assertEqual(summarize_site([])["verdict"], "insufficient_samples")

    def test_all_sites_gate(self):
        good = [check_sample(balanced_state())]
        bad = [check_sample(balanced_state(home_load=-1))]
        summary = summarize({"1": good, "2": bad})
        self.assertFalse(summary["all_calibrated"])
        self.assertTrue(summarize({"1": good})["all_calibrated"])


if __name__ == "__main__":
    unittest.main()
