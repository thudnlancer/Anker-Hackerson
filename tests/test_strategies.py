import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from energy_lab.strategies import STRATEGIES, StrategyRequest, get_strategy
from energy_lab.strategies.gradient import (Forecast, GradientConfig, exact,
                                            objective, repair, solve)


def make_request(n=96, soc=50.0, **overrides):
    times = pd.Series(pd.date_range("2026-10-10 08:30", periods=n, freq="15min", tz="UTC"))
    r = np.arange(n)
    pv = np.clip(np.sin((r - 34) / 96 * 2 * np.pi), 0, None) * 4000
    load = 400 + 300 * np.sin(r / 96 * 2 * np.pi + 1)
    buy = 0.25 + 0.1 * np.sin(r / 96 * 4 * np.pi)
    fields = dict(times=times, pv_w=pv, load_w=load, import_price=buy,
                  export_price=buy - 0.12, soc_percent=soc, time_limit_seconds=10.0)
    fields.update(overrides)
    return StrategyRequest(**fields)


class RegistryTests(unittest.TestCase):
    def test_registered_strategies(self):
        self.assertEqual(sorted(STRATEGIES), ["gradient", "milp"])
        self.assertEqual(get_strategy("milp").name, "milp")
        self.assertEqual(get_strategy("gradient").name, "gradient")

    def test_unknown_strategy_lists_registered(self):
        with self.assertRaisesRegex(KeyError, "gradient.*milp"):
            get_strategy("unknown")

    def test_constructor_overrides(self):
        strategy = get_strategy("gradient", iterations=3)
        self.assertEqual(strategy.overrides["iterations"], 3)


class RequestValidationTests(unittest.TestCase):
    def test_length_mismatch_rejected(self):
        request = make_request()
        request.pv_w = request.pv_w[:-1]
        with self.assertRaisesRegex(ValueError, "length"):
            request.validate()

    def test_soc_outside_bounds_rejected(self):
        with self.assertRaisesRegex(ValueError, "soc_percent"):
            make_request(soc=101.0).validate()


class GradientPhysicsTests(unittest.TestCase):
    def test_plan_is_feasible_and_deterministic(self):
        request = make_request()
        first = get_strategy("gradient").solve(request)
        second = get_strategy("gradient").solve(request)
        self.assertTrue(first.suboptimal)
        self.assertEqual(first.status, "heuristic")
        np.testing.assert_array_equal(first.plan.charge_w, second.plan.charge_w)
        soc = first.plan.soc_end.to_numpy()
        self.assertTrue((soc >= -1e-6).all() and (soc <= 100 + 1e-6).all())
        total_w = first.plan.charge_w + first.plan.discharge_w
        self.assertTrue((total_w <= 5000 + 1e-3).all())
        grid_net = first.plan.grid_import_w - first.plan.grid_export_w
        self.assertTrue((grid_net <= 15000 + 1).all() and (grid_net >= -5000 - 1).all())

    def test_time_limit_still_returns_feasible_plan(self):
        plan = get_strategy("gradient", max_solve_seconds=0.001).solve(make_request())
        self.assertTrue(plan.suboptimal)
        soc = plan.plan.soc_end.to_numpy()
        self.assertTrue((soc >= -1e-6).all() and (soc <= 100 + 1e-6).all())

    def test_hourly_step_respected(self):
        request = make_request(n=24, step_hours=1.0)
        plan = get_strategy("gradient").solve(request)
        self.assertEqual(len(plan.plan), 24)


class GradientCoreTests(unittest.TestCase):
    def config(self, **overrides):
        return GradientConfig(**overrides)

    def forecast(self, n=96):
        r = np.arange(n)
        return Forecast(pv=np.clip(np.sin((r - 34) / 96 * 2 * np.pi), 0, None) * 4,
                        load=0.4 + 0.3 * np.sin(r / 96 * 2 * np.pi + 1),
                        buy=0.25 + 0.1 * np.sin(r / 96 * 4 * np.pi),
                        sell=0.13 + 0.1 * np.sin(r / 96 * 4 * np.pi))

    def test_repair_respects_energy_bounds(self):
        c = self.config()
        d = self.forecast()
        u = repair(np.full(96, 5.0), d, 7.0, c)  # absurd raw: always max charge
        e = 7.0 + np.cumsum(u)
        self.assertTrue((e <= c.emax + 1e-9).all())
        u = repair(np.full(96, -5.0), d, 7.0, c)
        e = 7.0 + np.cumsum(u)
        self.assertTrue((e >= c.emin - 1e-9).all())

    def test_objective_gradient_finite(self):
        c = self.config()
        d = self.forecast()
        u = repair(np.zeros(96), d, 7.0, c)
        value, g = objective(u, d, 7.0, 0.0, c)
        self.assertTrue(np.isfinite(value) and np.isfinite(g).all())

    def test_gradient_descent_never_worsens_exact_best(self):
        c = self.config()
        d = self.forecast()
        plan = solve(d, 7.0, 0.0, c)
        self.assertLessEqual(plan["metrics"]["weighted_cost"],
                             plan["initial_best_weighted_cost"] + 1e-9)
        self.assertFalse(plan["is_global_optimal"])

    def test_warm_start_accepted(self):
        c = self.config()
        d = self.forecast()
        warm = np.zeros(96)
        plan = solve(d, 7.0, 0.0, c, warm_power=warm)
        self.assertIn("seed", plan["best_source"])

    def test_infeasible_initial_energy_rejected(self):
        with self.assertRaisesRegex(ValueError, "initial energy"):
            solve(self.forecast(), 99.0, 0.0, self.config())


class MilpAdapterTests(unittest.TestCase):
    def test_milp_solves_easy_window_optimally(self):
        plan = get_strategy("milp").solve(make_request())
        self.assertEqual(plan.status, "optimal")
        self.assertEqual(len(plan.plan), 96)


if __name__ == "__main__":
    unittest.main()
