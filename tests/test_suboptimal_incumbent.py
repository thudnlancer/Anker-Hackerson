"""C2 graded fallback: verified suboptimal incumbent vs explicit self-use.

The incumbent path must (a) only accept solutions that independently satisfy
the original constraints, (b) stay labeled suboptimal with its mip_gap, and
(c) never be confused with the no-incumbent explicit self-use fallback (Q1).
"""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
from types import SimpleNamespace

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from energy_lab.rolling import RollingPlan, optimize_window
from energy_lab.shadow import daily_points, make_candidate


def _solve_args():
    n = 24
    times = pd.Series(pd.date_range("2026-01-01", periods=n, freq="h"))
    pv = np.where(np.arange(n) % 24 < 12, 3000.0, 0.0)
    load = np.full(n, 800.0)
    buy = np.tile([0.4, 0.3, 0.2, 0.1], 6)
    sell = np.full(n, 0.05)
    return times, pv, load, buy, sell


def _optimal_x():
    """Recover a feasible 7n vector by replaying the optimal plan's powers."""
    times, pv, load, buy, sell = _solve_args()
    plan = optimize_window(times, pv, load, buy, sell, 50., solver="milp",
                           command_contract="sop", soc_min=0, time_limit_seconds=30)
    assert plan.status == "optimal"
    n = len(pv)
    x = np.zeros(7 * n)
    x[:n] = plan.plan.charge_w.to_numpy() / 1000
    x[n:2*n] = plan.plan.discharge_w.to_numpy() / 1000
    x[2*n:3*n] = plan.plan.grid_import_w.to_numpy() / 1000
    x[3*n:4*n] = plan.plan.grid_export_w.to_numpy() / 1000
    x[4*n:5*n] = plan.plan.soc_end.to_numpy() / 100 * 14
    x[5*n:6*n] = (plan.plan.flow != "discharge").to_numpy(dtype=float)
    # z6=1 allows grid import (import <= import_max*z6); z6=0 allows export.
    x[6*n:7*n] = (plan.plan.grid_import_w > 1e-6).to_numpy(dtype=float)
    return plan, x


class IncumbentAcceptanceTests(unittest.TestCase):
    def test_verified_incumbent_returned_suboptimal_with_gap(self):
        plan, x = _optimal_x()
        n = len(x) // 7
        fake = SimpleNamespace(success=False, status=1, message="injected time limit",
                               x=x, fun=plan.objective, mip_gap=0.0042)
        with patch("energy_lab.rolling.milp", return_value=fake):
            times, pv, load, buy, sell = _solve_args()
            result = optimize_window(times, pv, load, buy, sell, 50., solver="milp",
                                     command_contract="sop", soc_min=0, time_limit_seconds=30)
        self.assertEqual(result.status, "limit_reached")
        self.assertTrue(result.suboptimal)
        self.assertAlmostEqual(result.mip_gap, 0.0042)
        self.assertAlmostEqual(result.objective, plan.objective)
        pd.testing.assert_frame_equal(result.plan, plan.plan)

    def test_incumbent_is_used_not_the_idle_placeholder(self):
        plan, x = _optimal_x()
        self.assertTrue((plan.plan.flow != "idle").any(),
                        "fixture must contain real actions for a meaningful test")
        fake = SimpleNamespace(success=False, status=1, message="t", x=x,
                               fun=plan.objective, mip_gap=0.001)
        with patch("energy_lab.rolling.milp", return_value=fake):
            times, pv, load, buy, sell = _solve_args()
            result = optimize_window(times, pv, load, buy, sell, 50., solver="milp",
                                     command_contract="sop", soc_min=0, time_limit_seconds=30)
        self.assertTrue(result.suboptimal)
        self.assertEqual(str(result.plan.iloc[0].flow), str(plan.plan.iloc[0].flow))


class IncumbentRejectionTests(unittest.TestCase):
    def _rejected(self, tamper):
        plan, x = _optimal_x()
        bad = tamper(x.copy())
        fake = SimpleNamespace(success=False, status=1, message="t", x=bad,
                               fun=plan.objective, mip_gap=0.001)
        with patch("energy_lab.rolling.milp", return_value=fake):
            times, pv, load, buy, sell = _solve_args()
            result = optimize_window(times, pv, load, buy, sell, 50., solver="milp",
                                     command_contract="sop", soc_min=0, time_limit_seconds=30)
        self.assertEqual(result.status, "limit_reached")
        self.assertFalse(result.suboptimal)
        self.assertIsNone(result.mip_gap)
        self.assertTrue(result.plan.flow.eq("idle").all())
        self.assertTrue(np.isnan(result.objective))

    def test_soc_bound_violation_rejected(self):
        self._rejected(lambda x: self._set(x, 4 * (len(x) // 7) + 3, 14.0 + 1e-4))

    def test_power_bound_violation_rejected(self):
        self._rejected(lambda x: self._set(x, 0, 5.0 + 1e-4))

    def test_exclusivity_violation_rejected(self):
        # Force simultaneous charge and discharge beyond the big-M contract.
        def tamper(x):
            n = len(x) // 7
            x[0] = 1.0
            x[n] = 1.0
            x[5 * n] = 1.0  # z_charge=1 forbids discharge
            return x
        self._rejected(tamper)

    def test_nonbinary_tail_rejected(self):
        self._rejected(lambda x: self._set(x, 5 * (len(x) // 7), 0.5))

    def test_nan_objective_rejected(self):
        plan, x = _optimal_x()
        fake = SimpleNamespace(success=False, status=1, message="t", x=x,
                               fun=float("nan"), mip_gap=None)
        with patch("energy_lab.rolling.milp", return_value=fake):
            times, pv, load, buy, sell = _solve_args()
            result = optimize_window(times, pv, load, buy, sell, 50., solver="milp",
                                     command_contract="sop", soc_min=0, time_limit_seconds=30)
        self.assertFalse(result.suboptimal)

    @staticmethod
    def _set(x, index, value):
        x[index] = value
        return x


class MockWithoutIncumbentTests(unittest.TestCase):
    def test_bare_failure_namespace_keeps_idle_placeholder(self):
        fake = SimpleNamespace(success=False, status=1, message="injected time limit")
        with patch("energy_lab.rolling.milp", return_value=fake):
            times, pv, load, buy, sell = _solve_args()
            result = optimize_window(times, pv, load, buy, sell, 50., solver="milp",
                                     command_contract="sop", soc_min=0, time_limit_seconds=30)
        self.assertEqual(result.status, "limit_reached")
        self.assertFalse(result.suboptimal)
        self.assertTrue(result.plan.flow.eq("idle").all())


class ShadowFallbackContractTests(unittest.TestCase):
    def setUp(self):
        self.prices = {day: {"date": date, "points": [
            {"start": f"{date}T{hour:02d}:00:00Z", "import_price": 0.3, "export_price": 0.1}
            for hour in range(24)]}
            for day, date in (("today", "2031-01-01"), ("tomorrow", "2031-01-02"))}
        self.state = {"soc": "45", "solar_power": "400", "home_load": "1000",
                      "device_time": "2031-01-01T00:15:00+00:00",
                      "device_time_last_changed": "2026-10-13T00:00:04Z"}
        self.points = daily_points(self.prices)

    def test_suboptimal_incumbent_yields_labeled_candidate(self):
        plan = RollingPlan(pd.DataFrame({
            "flow": ["discharge"], "charge_w": [0.0], "discharge_w": [600.0],
            "soc_end": [43.93]}), 0.05, "limit_reached", "t",
            mip_gap=0.007, suboptimal=True)
        with patch("energy_lab.shadow.optimize_window", return_value=plan):
            result = make_candidate(self.state, self.points)
        self.assertEqual(result["status"], "candidate_only")
        self.assertEqual(result["solver_status"], "limit_reached")
        self.assertTrue(result["suboptimal_incumbent"])
        self.assertAlmostEqual(result["mip_gap"], 0.007)
        self.assertEqual(result["attribution"], "optimizer-suboptimal-incumbent")
        self.assertEqual(result["candidate"]["flow"], "discharge")
        self.assertEqual(result["battery_flow"], "discharge")
        self.assertEqual(result["writes_performed"], 0)

    def test_no_incumbent_falls_back_to_explicit_self_use(self):
        plan = RollingPlan(pd.DataFrame({
            "time": pd.to_datetime(["2031-01-01T00:15:00+00:00"]),
            "flow": ["idle"], "charge_w": [0.0], "discharge_w": [0.0],
            "grid_import_w": [0.0], "grid_export_w": [0.0], "soc_end": [45.0],
            "intended_command_mode": ["zero_flow_command"]}),
            float("nan"), "limit_reached", "t")
        with patch("energy_lab.shadow.optimize_window", return_value=plan):
            result = make_candidate(self.state, self.points)
        self.assertEqual(result["status"], "limit_reached")
        self.assertIsNone(result["candidate"])
        self.assertEqual(result["fallback"], "explicit_self_use_mode")
        self.assertEqual(result["attribution"], "non-optimizer-attributed")
        self.assertEqual(result["writes_performed"], 0)
        self.assertNotIn("suboptimal_incumbent", result)


if __name__ == "__main__":
    unittest.main()
