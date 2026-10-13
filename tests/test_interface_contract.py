from pathlib import Path
import sys
import unittest

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from energy_lab.interface_contract import map_battery_action, align_hourly_points


class ContractTests(unittest.TestCase):
    def test_export_target_roundtrip_and_unrepresentable_partial_discharge(self):
        mapped = map_battery_action("discharge", 2000, 3000, 1000)
        self.assertTrue(mapped.representable)
        self.assertEqual(mapped.target_w, 4000)
        self.assertFalse(map_battery_action("discharge", 1000, 0, 2000).representable)
        self.assertFalse(map_battery_action("discharge", 5000, 5000, 0).representable)

    def test_zero_export_target_is_not_no_discharge(self):
        mapped = map_battery_action("discharge", 2000, 0, 2000)
        self.assertTrue(mapped.representable)
        self.assertEqual((mapped.flow, mapped.target_w), ("discharge", 0))
        held = map_battery_action("hold", 0, 0, 2000)
        self.assertFalse(held.representable)
        self.assertEqual(held.reason, "idle_requires_explicit_idle_mode")
        zero = map_battery_action("idle", 0, 0, 2000, idle_mode="zero_flow")
        self.assertTrue(zero.representable)
        self.assertEqual((zero.flow, zero.target_w), ("charge", 0))
        self.assertIn("unverified", zero.reason)

    def test_cross_midnight_requires_real_tomorrow(self):
        times = pd.date_range("2026-01-01", periods=48, freq="h")
        points = [{"time": str(t), "value": i} for i, t in enumerate(times)]
        aligned = align_hourly_points(points, "2026-01-01 23:15:00")
        self.assertEqual(aligned.value.tolist(), list(range(23, 47)))
        with self.assertRaisesRegex(ValueError, "incomplete"):
            align_hourly_points(points[:24], "2026-01-01 23:15:00")
        with self.assertRaisesRegex(ValueError, "duplicate"):
            align_hourly_points(points+[points[0]], "2026-01-01 00:00:00")


if __name__ == "__main__":
    unittest.main()
