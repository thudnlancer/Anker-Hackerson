import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from run_live import current_price_and_future, price_window


class RunLiveTimeTests(unittest.TestCase):
    def test_price_window_uses_explicit_today_and_tomorrow_timestamps(self):
        response = {
            "today": {"date": "2032-04-02", "points": [
                {"start": f"2032-04-02T{hour:02d}:00:00Z", "import_price": .2, "export_price": .1}
                for hour in range(24)
            ]},
            "tomorrow": {"date": "2032-04-03", "points": [
                {"start": f"2032-04-03T{hour:02d}:00:00Z", "import_price": .3, "export_price": .1}
                for hour in range(24)
            ]},
        }
        points = price_window(response)
        current, future = current_price_and_future(points, "2032-04-02 23:15:00", "UTC")
        self.assertEqual(current["import_price"], .2)
        self.assertEqual(future[0], .3)

    def test_missing_device_slot_is_rejected_instead_of_wrapping(self):
        response = {"today": {"date": "2032-04-02", "points": [
            {"start": f"2032-04-02T{hour:02d}:00:00Z", "import_price": .2, "export_price": .1}
            for hour in range(24)
        ]}}
        with self.assertRaises(ValueError):
            current_price_and_future(price_window(response), "2032-04-03 00:15:00", "UTC")

    def test_api_timezone_cannot_supply_missing_device_timezone(self):
        with self.assertRaisesRegex(ValueError, "missing_trusted_device_timezone"):
            current_price_and_future([], "2032-04-03 00:15:00+00:00")

    def test_explicit_device_timezone_overrides_price_timestamp_timezone(self):
        points = [{"time": "2032-04-02T15:00:00Z", "import_price": .2}]
        current, _ = current_price_and_future(points, "2032-04-02 23:15:00", "Asia/Shanghai")
        self.assertEqual(current["import_price"], .2)


if __name__ == "__main__":
    unittest.main()
