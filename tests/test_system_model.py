import sys
import unittest

sys.path.insert(0, str(__import__('pathlib').Path(__file__).resolve().parents[1] / 'src'))
from energy_lab.system_model import (
    PhysicalParameters, calibrate_pv_scale, load_interval, next_soc_percent,
    power_balance, pv_from_irradiance,
)


class SystemModelTests(unittest.TestCase):
    def test_power_balance_and_residual(self):
        result = power_balance(1200, 800, 500, 0, grid_import_w=100, grid_export_w=0)
        self.assertAlmostEqual(result.net_grid_w, 100)
        self.assertAlmostEqual(result.residual_w, 0)

    def test_soc_uses_duration_and_efficiencies(self):
        params = PhysicalParameters(charge_efficiency=.95, discharge_efficiency=.9)
        self.assertGreater(next_soc_percent(50, 1000, 0, .25, params), 50)
        self.assertLess(next_soc_percent(50, 0, 1000, .25, params), 50)

    def test_sop_default_does_not_apply_second_discharge_loss(self):
        params = PhysicalParameters()
        self.assertAlmostEqual(next_soc_percent(50, 0, 1000, .25, params),
                               50 - 0.25 / 14 * 100, places=8)

    def test_pv_calibration_is_capped(self):
        calibration = calibrate_pv_scale([500, 1000, 1500], [1000, 2000, 3000])
        self.assertEqual(calibration["samples"], 3)
        self.assertEqual(pv_from_irradiance(5000, calibration, max_power_w=5000), 5000)

    def test_load_interval_is_not_a_point_forecast(self):
        interval = load_interval([100, 200, 1000])
        self.assertLessEqual(interval["p_low_w"], interval["p50_w"])
        self.assertLessEqual(interval["p50_w"], interval["p_high_w"])


if __name__ == '__main__':
    unittest.main()
