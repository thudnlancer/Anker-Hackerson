"""V1 step-0 contract disambiguation: idle is explicit, never silently mapped."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from energy_lab.ha_client import HAClient
from energy_lab.interface_contract import map_battery_action
from energy_lab.model import Battery
from energy_lab.rolling import RollingPlan, optimize_window
from energy_lab.shadow import daily_points, make_candidate
from energy_lab.strategy_backtest import _policy_step


class DispatchRejectsIdleTests(unittest.TestCase):
    def test_set_dispatch_rejects_idle_and_legacy_hold_before_any_live_check(self):
        client = HAClient("http://unused", "token")
        for flow in ("idle", "hold"):
            with self.assertRaisesRegex(ValueError, "set_dispatch"):
                client.set_dispatch("1502", flow, 0.0, live=False)

    def test_set_dispatch_still_accepts_explicit_charge_discharge_in_dry_run(self):
        client = HAClient("http://unused", "token")
        client.set_dispatch("1502", "charge", 100.0, live=False)
        client.set_dispatch("1502", "discharge", 100.0, live=False)

    def test_self_consumption_is_only_an_explicit_mode_switch(self):
        client = HAClient("http://unused", "token")
        with patch.object(HAClient, "call") as call:
            client.set_self_consumption("1502", live=False)
            call.assert_not_called()
            client.set_self_consumption("1502", live=True)
            call.assert_called_once()
            self.assertEqual(call.call_args[1]["option"], "self_consumption")


class IdleMappingTests(unittest.TestCase):
    def test_idle_without_idle_mode_is_refused(self):
        for flow in ("idle", "hold"):
            mapping = map_battery_action(flow, 0.0, 1000.0, 2000.0)
            self.assertFalse(mapping.representable)
            self.assertEqual(mapping.reason, "idle_requires_explicit_idle_mode")

    def test_explicit_self_consumption_mode(self):
        mapping = map_battery_action("idle", 0.0, 1000.0, 2000.0, idle_mode="self_consumption")
        self.assertTrue(mapping.representable)
        self.assertEqual((mapping.flow, mapping.target_w), ("self_consumption", 0.0))
        self.assertEqual(mapping.reason, "idle_explicit_self_consumption_mode")

    def test_explicit_zero_flow_mode_is_marked_unverified(self):
        mapping = map_battery_action("idle", 0.0, 1000.0, 2000.0, idle_mode="zero_flow")
        self.assertTrue(mapping.representable)
        self.assertEqual((mapping.flow, mapping.target_w), ("charge", 0.0))
        self.assertEqual(mapping.reason, "idle_zero_flow_command_unverified")

    def test_unknown_idle_mode_is_refused(self):
        mapping = map_battery_action("idle", 0.0, 1000.0, 2000.0, idle_mode="auto")
        self.assertFalse(mapping.representable)
        self.assertEqual(mapping.reason, "unknown_idle_mode")


class RollingPlanLabelTests(unittest.TestCase):
    def test_plan_carries_idle_labels_and_intended_command_mode(self):
        times = pd.Series(pd.date_range("2032-01-01", periods=3, freq="h"))
        result = optimize_window(times, np.array([0., 0., 0.]), np.array([1000., 1000., 1000.]),
                                 np.array([.3, .3, .3]), np.array([.1, .1, .1]), 50,
                                 solver="milp", command_contract="sop", terminal_price=None)
        self.assertEqual(result.status, "optimal")
        plan = result.plan
        self.assertIn("intended_command_mode", plan.columns)
        self.assertFalse(plan.flow.eq("hold").any())
        idle = plan.flow.eq("idle")
        self.assertTrue(plan.loc[idle, "intended_command_mode"].eq("zero_flow_command").all())
        self.assertTrue(plan.loc[~idle, "intended_command_mode"].eq("dispatch").all())


class LegacyLabelReadCompatTests(unittest.TestCase):
    """Old artifacts keep the pre-rename "hold" label; readers accept both (R6)."""

    def test_policy_step_accepts_legacy_hold_and_outputs_idle(self):
        row = pd.Series({"pv": 1000.0, "load": 500.0})
        step = _policy_step(Battery(soc=50.0), "hold", 0.0, row, 0.3, 0.1, 10.0)
        self.assertEqual(step["flow"], "idle")
        self.assertEqual(step["power_w"], 0.0)

    def test_shadow_maps_legacy_hold_plan_with_explicit_policy(self):
        prices = {day: {"date": date, "points": [
            {"start": f"{date}T{hour:02d}:00:00Z", "import_price": 0.3, "export_price": 0.1}
            for hour in range(24)]}
            for day, date in (("today", "2031-01-01"), ("tomorrow", "2031-01-02"))}
        state = {"soc": "45", "solar_power": "400", "home_load": "1000",
                 "device_time": "2031-01-01T00:15:00+00:00",
                 "device_time_last_changed": "2026-10-13T00:00:04Z"}
        plan = RollingPlan(pd.DataFrame({"flow": ["hold"], "charge_w": [0.], "discharge_w": [0.],
                                         "soc_end": [45.]}), 0.0, "optimal", "legacy mock")
        points = daily_points(prices)
        with patch("energy_lab.shadow.optimize_window", return_value=plan):
            legacy = make_candidate(state, points, idle_candidate="legacy_hold")
            zero = make_candidate(state, points, idle_candidate="zero_charge_unverified")
        self.assertEqual(legacy["candidate"]["flow"], "self_consumption")
        self.assertEqual(legacy["candidate"]["reason"], "idle_explicit_self_consumption_mode")
        self.assertEqual(zero["candidate"]["flow"], "charge")
        self.assertEqual(zero["candidate"]["reason"], "idle_zero_flow_command_unverified")
        self.assertEqual(legacy["battery_flow"], "hold")


if __name__ == "__main__":
    unittest.main()
