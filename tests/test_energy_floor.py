import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from energy_lab.rolling import optimize_window


def solve(floor=None, solver="lp", soc=10, **kwargs):
    times = pd.Series(pd.date_range("2026-01-01", periods=3, freq="h"))
    return optimize_window(
        times, np.zeros(3), np.zeros(3), np.array([0.4, 0.3, 0.2]),
        np.full(3, 0.05), soc, capacity_kwh=14, max_power_w=5000,
        charge_efficiency=0.95, terminal_price=0.1,
        degradation_cost_eur_kwh=0.02, solver=solver,
        energy_floor_kwh=floor, **kwargs,
    )


class EnergyFloorTests(unittest.TestCase):
    def test_deadline_forces_charging_in_both_solvers(self):
        for solver in ("lp", "milp"):
            with self.subTest(solver=solver):
                constrained = solve([None, 6.0, None], solver=solver)
                unconstrained = solve(solver=solver)
                self.assertEqual(constrained.status, "optimal")
                self.assertGreaterEqual(constrained.plan.soc_end.iloc[1] * 0.14, 6 - 1e-6)
                self.assertLess(unconstrained.plan.soc_end.iloc[1] * 0.14, 6 - 1e-3)

    def test_out_of_bounds_and_unreachable_keep_failure_shape(self):
        above_capacity = solve([15.0, None, None])
        self.assertEqual(above_capacity.status, "infeasible")
        self.assertEqual(above_capacity.message, "energy floor exceeds an original variable bound")
        self.assertTrue((above_capacity.plan.flow == "idle").all())
        for solver in ("lp", "milp"):
            unreachable = solve([7.0, None, None], solver=solver)
            self.assertEqual(unreachable.status, "infeasible")

    def test_validation_and_noop(self):
        for bad in ([1], [None, -1, None], [None, float("nan"), None], [None, True, None]):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                solve(bad)
        self.assertAlmostEqual(solve([None] * 3).objective, solve().objective)


if __name__ == "__main__":
    unittest.main()
