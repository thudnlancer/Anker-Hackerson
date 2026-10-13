import sys
import unittest
from pathlib import Path
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from run_persistence_vs_auxiliary_replay import forecast_paths, execute, decompose_saving
from energy_lab.model import Battery


class InspectPredictor:
    def __init__(self, target):
        self.target = target
        self.calls = []

    def predict(self, history, future):
        assert len(history) == 168
        assert set(future.columns) == {"time", "temperature_c", "cloud_cover_percent"}
        assert future.time.min() > history.time.max()
        self.calls.append((history.copy(), future.copy()))
        return pd.DataFrame({"prediction": np.full(len(future), float(history[self.target].iloc[-1]))})


class PairedReplayTests(unittest.TestCase):
    def test_cost_decomposition_does_not_double_count_inventory(self):
        baseline = {"cost_eur": 10., "settled_cost_eur": 8., "degradation_cost_eur": 1.}
        candidate = {"cost_eur": 9., "settled_cost_eur": 4., "degradation_cost_eur": 2.}
        result = decompose_saving(baseline, candidate)
        self.assertEqual(result["operating_saving_eur"], 1.)
        self.assertEqual(result["inventory_contribution_eur"], 3.)
        self.assertEqual(result["settled_saving_eur"], 4.)
        self.assertEqual(result["extra_degradation_eur"], 1.)
        self.assertEqual(result["settled_saving_after_degradation_eur"], 3.)

    def test_ablation_changes_only_selected_forecast(self):
        class DifferentPredictor(InspectPredictor):
            def predict(self, history, future):
                result = super().predict(history, future)
                result.prediction *= 2
                return result
        paths = forecast_paths(self.frame(), 170, DifferentPredictor("pv"), DifferentPredictor("load"))
        np.testing.assert_array_equal(paths["pv_only"][0], paths["auxiliary"][0])
        np.testing.assert_array_equal(paths["pv_only"][1], paths["persistence"][1])
        np.testing.assert_array_equal(paths["load_only"][0], paths["persistence"][0])
        np.testing.assert_array_equal(paths["load_only"][1], paths["auxiliary"][1])
        self.assertFalse(np.array_equal(paths["auxiliary"][0], paths["persistence"][0]))
        for pv, load in paths.values():
            self.assertEqual(pv[0], 100.)
            self.assertEqual(load[0], 1000.)

    def test_ablation_arrays_do_not_alias(self):
        paths = forecast_paths(self.frame(), 170, InspectPredictor("pv"), InspectPredictor("load"))
        paths["pv_only"][0][5] = -10
        self.assertEqual(paths["auxiliary"][0][5], 100.)

    def frame(self):
        return pd.DataFrame({"time": pd.date_range("2027-01-01", periods=220, freq="h"),
                             "pv": 100., "load": 1000., "temperature_c": np.arange(220),
                             "cloud_cover_percent": 20., "buy": .3, "sell": -.1})

    def test_future_target_mutation_does_not_change_paths(self):
        frame = self.frame()
        pv, load = InspectPredictor("pv"), InspectPredictor("load")
        first = forecast_paths(frame, 170, pv, load)
        frame.loc[171:, ["pv", "load"]] = 999999.
        second = forecast_paths(frame, 170, pv, load)
        for arm in first:
            np.testing.assert_array_equal(first[arm], second[arm])
        self.assertEqual(len(first["auxiliary"][0]), 24)

    def test_weather_persistence_uses_current_weather(self):
        frame = self.frame()
        pv, load = InspectPredictor("pv"), InspectPredictor("load")
        forecast_paths(frame, 170, pv, load, "persistence")
        self.assertTrue((pv.calls[0][1].temperature_c == 170).all())

    def test_discharge_maps_to_grid_target_and_conserves_energy(self):
        current = self.frame().iloc[0]
        action = pd.Series({"flow": "discharge", "charge_w": 0., "discharge_w": 900.})
        result = execute(Battery(soc=50.), action, current, 0.)
        self.assertAlmostEqual(result["command_target_w"], 0.)
        self.assertAlmostEqual(result["cost_eur"], 0.)
        self.assertAlmostEqual(result["energy_error_kwh"], 0.)

    def test_unrepresentable_discharge_is_rejected(self):
        action = pd.Series({"flow": "discharge", "charge_w": 0., "discharge_w": 100.})
        with self.assertRaisesRegex(ValueError, "unrepresentable"):
            execute(Battery(), action, self.frame().iloc[0], 0.)

    def test_import_not_subsidized_and_negative_export_costs_money(self):
        current = self.frame().iloc[0].copy()
        action = pd.Series({"flow": "idle", "charge_w": 0., "discharge_w": 0.})
        result = execute(Battery(), action, current, 0.)
        self.assertAlmostEqual(result["cost_eur"], .27)
        current.pv = 2000.
        result = execute(Battery(), action, current, 0.)
        self.assertAlmostEqual(result["cost_eur"], .1)


if __name__ == "__main__":
    unittest.main()
