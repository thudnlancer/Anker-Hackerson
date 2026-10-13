import sys
import unittest
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from energy_lab.model import Battery
from energy_lab.strategy_backtest import _policy_step


class BatteryExecutionTests(unittest.TestCase):
    def test_near_full_charge_uses_actual_headroom_for_grid_accounting(self):
        battery = Battery(soc=99.0)
        row = pd.Series({"pv": 0.0, "load": 1000.0})
        step = _policy_step(battery, "charge", 5000, row, .3, .1, 10)
        energy = .14 / .95
        self.assertAlmostEqual(step["power_w"], energy * 1000)
        self.assertAlmostEqual(step["soc_after"], 100)
        self.assertAlmostEqual(step["grid_import_kwh"], 1 + energy)
        self.assertAlmostEqual(step["cost_eur"], (1 + energy) * .3)

    def test_full_battery_cannot_absorb_surplus(self):
        row = pd.Series({"pv": 4000.0, "load": 1000.0})
        step = _policy_step(Battery(soc=100), "charge", 5000, row, .3, -.1, 10)
        self.assertEqual(step["flow"], "hold")
        self.assertEqual(step["power_w"], 0)
        self.assertAlmostEqual(step["grid_export_kwh"], 3)
        self.assertAlmostEqual(step["negative_export_loss_eur"], .3)

    def test_discharge_respects_reserve_and_energy_balance(self):
        row = pd.Series({"pv": 0.0, "load": 1000.0})
        step = _policy_step(Battery(soc=11), "discharge", 5000, row, .3, .1, 10)
        self.assertAlmostEqual(step["power_w"], 140)
        self.assertAlmostEqual(step["soc_after"], 10)
        self.assertAlmostEqual(step["grid_import_kwh"], .86)


if __name__ == "__main__":
    unittest.main()
