import sys
import unittest
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from energy_lab.rolling import optimize_window


def solve(pv, load, buy, sell, soc=50, solver="milp", **kwargs):
    return optimize_window(pd.Series(pd.date_range("2026-01-01", periods=len(pv), freq="h")),
                           np.array(pv), np.array(load), np.array(buy), np.array(sell), soc,
                           solver=solver, soc_min=10, degradation_cost_eur_kwh=.02,
                           terminal_price=.1, **kwargs)


class MilpTests(unittest.TestCase):
    def test_negative_export_at_full_soc_cannot_dissipate_energy_by_cycling(self):
        lp = solve([5000], [0], [.3], [-2], soc=100, solver="lp")
        integer = solve([5000], [0], [.3], [-2], soc=100)
        self.assertEqual(integer.status, "optimal")
        self.assertGreater(lp.plan.charge_w.iloc[0], 0)
        self.assertGreater(lp.plan.discharge_w.iloc[0], 0)
        self.assertAlmostEqual(integer.plan.grid_export_w.iloc[0], 5000)
        self.assertAlmostEqual(integer.plan.charge_w.iloc[0], 0)
        self.assertAlmostEqual(integer.plan.discharge_w.iloc[0], 0)

    def test_price_inversion_does_not_allow_unlimited_grid_arbitrage(self):
        lp = solve([0], [0], [.1], [.4], soc=10, solver="lp")
        integer = solve([0], [0], [.1], [.4], soc=10)
        self.assertNotEqual(lp.status, "optimal")
        self.assertEqual(integer.status, "optimal")
        self.assertAlmostEqual(integer.plan.grid_export_w.iloc[0], 0)

    def test_ordinary_prices_match_lp_objective(self):
        args = ([0, 3000, 0], [1000, 500, 2000], [.1, .15, .4], [.05, .08, .1])
        lp, integer = solve(*args, solver="lp"), solve(*args)
        self.assertEqual(integer.status, "optimal")
        self.assertAlmostEqual(lp.objective, integer.objective)

    def test_infeasible_initial_energy_and_timeout_are_distinct(self):
        result = solve([0], [0], [.3], [.1], soc=0, step_hours=.01)
        self.assertEqual(result.status, "infeasible")
        with patch("energy_lab.rolling.milp", return_value=SimpleNamespace(
                success=False, status=1, message="injected time limit")):
            result = solve([0], [0], [.3], [.1])
        self.assertEqual(result.status, "limit_reached")
        self.assertTrue(result.plan.flow.eq("hold").all())

    def test_nonfinite_input_rejected(self):
        with self.assertRaises(ValueError):
            solve([np.nan], [0], [.3], [.1])


if __name__ == "__main__":
    unittest.main()
