from pathlib import Path
import sys
import unittest
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from energy_lab.rolling import optimize_window
from energy_lab.interface_contract import map_battery_action, state_is_fresh, verify_readback, align_hourly_points
from energy_lab.shadow import daily_points, evaluate_snapshot


class ShadowTests(unittest.TestCase):
    def test_constrained_plan_all_future_steps_map(self):
        for pv, load, soc in (([0, 0, 5000], [2000, 6000, 0], 11), ([8000, 0, 0], [0, 0, 1000], 80)):
            plan = optimize_window(pd.Series(pd.date_range("2026-01-01", periods=3, freq="h")),
                np.array(pv), np.array(load), np.array([.4, .1, .5]), np.array([.3, .05, .4]), soc,
                soc_min=10, solver="milp", command_contract="sop")
            self.assertEqual(plan.status, "optimal")
            for i, row in plan.plan.iterrows():
                power = row.charge_w if row.flow == "charge" else row.discharge_w if row.flow == "discharge" else 0
                self.assertTrue(map_battery_action(row.flow, max(0, power), pv[i], load[i]).representable)
        self.assertGreater(float(plan.plan.grid_export_w.iloc[0]), 5000)  # passive PV is not capped by the command range

    def test_sop_still_allows_real_discharge_and_charge(self):
        times = pd.Series(pd.date_range("2026-01-01", periods=1, freq="h"))
        plan = optimize_window(times, np.array([0]), np.array([1000]), np.array([.5]), np.array([.1]),
                               50, soc_min=10, terminal_price=.1, solver="milp", command_contract="sop")
        self.assertEqual(plan.status, "optimal")
        self.assertGreaterEqual(plan.plan.discharge_w.iloc[0], 1000-1e-3)
        charged = optimize_window(times, np.array([5000]), np.array([0]), np.array([.3]), np.array([-.2]),
                                  50, soc_min=10, terminal_price=.1, solver="milp", command_contract="sop")
        self.assertGreater(charged.plan.charge_w.iloc[0], 1000)

    def test_aware_alignment_and_stale_clock(self):
        points = [{"time": t.isoformat()} for t in pd.date_range("2026-01-01", periods=48, freq="h", tz="UTC")]
        self.assertEqual(len(align_hourly_points(points, "2026-01-01T23:15:00Z")), 24)
        with self.assertRaisesRegex(ValueError, "timezone"):
            align_hourly_points(points, "2026-01-01 23:15:00")
        self.assertFalse(state_is_fresh("2026-01-01T00:00:00Z", now="2026-01-01T00:01:00Z"))
        self.assertFalse(state_is_fresh("2026-01-01 00:00:00", now="2026-01-01T00:00:01Z"))
        self.assertTrue(state_is_fresh("2026-01-01T00:00:01Z", now="2026-01-01T00:00:00Z"))
        self.assertFalse(state_is_fresh("2026-01-01T00:00:30Z", now="2026-01-01T00:00:00Z"))

    def test_readback_requires_sent_command_later_tick_and_matching_mode(self):
        command = map_battery_action("discharge", 2000, 0, 2000)
        state = dict(last_changed="2026-10-10T00:00:05Z", device_time="2031-01-01 00:15:00",
                     operating_mode="third_party_control", grid_flow="discharge", power_control=0,
                     grid_export_power_w=0, battery_charging_power_w=0, battery_discharging_power_w=2000)
        opts = dict(sent_device_time="2031-01-01 00:00:00", issued_at="2026-10-10T00:00:00Z", now="2026-10-10T00:00:06Z")
        self.assertTrue(verify_readback(command, state, **opts).verified)
        self.assertFalse(verify_readback(command, state, now=opts["now"]).verified)
        self.assertFalse(verify_readback(command, {**state, "device_time": opts["sent_device_time"]}, **opts).verified)
        self.assertFalse(verify_readback(command, {**state, "operating_mode": "self_consumption"}, **opts).verified)
        self.assertFalse(verify_readback(command, {**state, "grid_export_power_w": float("nan")}, **opts).verified)

    def test_mixed_three_site_snapshot_isolated_no_writes(self):
        prices = {day: {"date": date, "points": [dict(hour=h, import_price=.3, export_price=.1) for h in range(24)]}
                  for day, date in (("today", "2031-01-01"), ("tomorrow", "2031-01-02"))}
        good = dict(soc=10, solar_power=0, home_load=0, device_time="2031-01-01 23:15:00",
                    device_time_last_changed="2026-10-10T00:00:04Z")
        snapshot = dict(prices=prices, stations={str(i): dict(state=s, received_at="2026-10-10T00:00:05Z")
                      for i, s in enumerate((good, {**good, "soc": "unavailable"},
                                            {**good, "device_time_last_changed": "2026-10-09T00:00:00Z"}), 1)})
        result = evaluate_snapshot(snapshot, now="2026-10-10T00:00:06Z", solver_seconds=5.)
        self.assertEqual(result["stations"]["1"]["status"], "candidate_only")
        self.assertEqual(result["stations"]["2"]["status"], "blocked")
        self.assertEqual(result["stations"]["3"]["status"], "blocked")
        self.assertEqual(result["writes_performed"], 0)
        self.assertEqual(len(daily_points(prices)), 48)

    def test_api_weather_path_is_used_in_shadow_when_available(self):
        prices = {day: {"date": date, "points": [dict(start=f"{date}T{h:02d}:00:00Z", import_price=.3, export_price=.1) for h in range(24)]}
                  for day, date in (("today", "2031-01-01"), ("tomorrow", "2031-01-02"))}
        points = []
        for day, date in (("today", "2031-01-01"), ("tomorrow", "2031-01-02")):
            for hour in range(24):
                points.append({"timestamp": f"{date}T{hour:02d}:00:00Z", "condition": "clear",
                               "temperature_c": 20, "cloud_cover_percent": 0,
                               "solar_irradiance_wm2": 500 if 8 <= hour < 18 else 0})
        weather = {"today": {"points": points[:24]}, "tomorrow": {"points": points[24:]}}
        state = dict(soc=50, solar_power=100, home_load=300, device_time="2031-01-01 00:15:00",
                     device_time_last_changed="2026-10-10T00:00:04Z")
        snapshot = dict(prices=prices, stations={"1": dict(state=state, received_at="2026-10-10T00:00:05Z",
                                                            weather=weather)})
        result = evaluate_snapshot(snapshot, now="2026-10-10T00:00:06Z", solver_seconds=5.)
        self.assertEqual(result["stations"]["1"]["status"], "candidate_only")
        self.assertIn("api_weather_uncalibrated", result["stations"]["1"]["forecast_mode"])
        self.assertEqual(result["writes_performed"], 0)

    def test_shadow_uses_full_sop_soc_range_by_default(self):
        prices = {day: {"date": date, "points": [dict(start=f"{date}T{h:02d}:00:00Z", import_price=.3, export_price=.1) for h in range(24)]}
                  for day, date in (("today", "2031-01-01"), ("tomorrow", "2031-01-02"))}
        state = dict(soc=5, solar_power=0, home_load=2000, device_time="2031-01-01 00:15:00",
                     device_time_last_changed="2026-10-10T00:00:04Z")
        snapshot = dict(prices=prices, stations={"1": dict(state=state, received_at="2026-10-10T00:00:05Z")})
        result = evaluate_snapshot(snapshot, now="2026-10-10T00:00:06Z", solver_seconds=5.)
        self.assertEqual(result["stations"]["1"]["status"], "candidate_only")
        self.assertLessEqual(result["stations"]["1"]["planned_soc_after_15min"], 5)


if __name__ == "__main__":
    unittest.main()
