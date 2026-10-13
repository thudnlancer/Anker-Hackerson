import sys
from pathlib import Path
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from energy_lab.decision import decide, self_consumption
from energy_lab.model import Battery
from energy_lab.strategy_backtest import _policy_step


class DecisionTests(unittest.TestCase):
    def test_timeout_executes_fallback_then_recovers_chronologically(self):
        battery = Battery(soc=50)
        states = []
        for tick in range(3):
            args = (pd.Series(pd.date_range("2026-01-01", periods=2, freq="h")),
                    np.zeros(2), np.ones(2)*1000, np.array([.2, .4]), np.ones(2)*.1, battery.soc)
            opts = dict(observed_pv=0, observed_load=1000, soc_min=10, solver="milp", terminal_price=.1)
            if tick == 1:
                with patch("energy_lab.rolling.milp", return_value=SimpleNamespace(
                        success=False, status=1, message="injected timeout")):
                    decision = decide(*args, **opts)
            else:
                decision = decide(*args, **opts)
            before = battery.soc
            step = _policy_step(battery, decision.flow, decision.power_w,
                                pd.Series({"pv": 0, "load": 1000}), .2, .1, 10)
            states.append(decision.status)
            self.assertGreaterEqual(battery.soc, 10-1e-8)
            self.assertEqual(step["soc_before"], before)
            self.assertEqual(decision.fallback, tick == 1)
        self.assertEqual(states, ["optimal", "limit_reached", "optimal"])

    def test_bad_forecast_uses_observed_self_use_bad_observation_rejects(self):
        args = (pd.Series(pd.date_range("2026-01-01", periods=2, freq="h")),
                [1000, np.nan], [500, 500], [.2, .2], [.1, .1], 50)
        d = decide(*args, observed_pv=1000, observed_load=500, solver="milp")
        self.assertEqual((d.status, d.flow, d.power_w), ("invalid_input", "charge", 500))
        with self.assertRaisesRegex(ValueError, "observation"):
            decide(*args, observed_pv=np.nan, observed_load=500, solver="milp")

    def test_subhour_charge_and_discharge_use_actual_duration(self):
        for dt in (.25, 1/12):
            b = Battery(soc=99)
            step = _policy_step(b, "charge", 5000, pd.Series({"pv": 0, "load": 1000}), .3, .1, 10, dt)
            self.assertAlmostEqual(step["throughput_kwh"], .14/.95)
            self.assertAlmostEqual(step["grid_import_kwh"], .14/.95+dt)
            self.assertAlmostEqual(b.soc, 100)
            b.soc = 11
            step = _policy_step(b, "discharge", 5000, pd.Series({"pv": 0, "load": 1000}), .3, .1, 10, dt)
            self.assertAlmostEqual(step["throughput_kwh"], .14)
            self.assertAlmostEqual(b.soc, 10)

    def test_self_use_does_not_create_grid_arbitrage_or_claim_zero_export(self):
        flow, power = self_consumption(5000, 1000)
        step = _policy_step(Battery(soc=100), flow, power, pd.Series({"pv": 5000, "load": 1000}), .3, -.1, 10)
        self.assertEqual(step["grid_export_kwh"], 4)
        flow, power = self_consumption(0, 1000)
        step = _policy_step(Battery(soc=10), flow, power, pd.Series({"pv": 0, "load": 1000}), .3, .1, 10)
        self.assertEqual(step["grid_import_kwh"], 1)


if __name__ == "__main__":
    unittest.main()
