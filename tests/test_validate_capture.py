import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.analysis.validate_capture import segment_rows, check_segment

BASE = datetime(2026, 10, 10, 8, 30)


def tick(minutes, price=True):
    stamp = BASE + timedelta(minutes=minutes)
    return {"device_time": stamp.isoformat(),
            "price_status": "ok" if price else "missing",
            "import_price": 0.3 if price else None,
            "weather_status": "valid"}


class SegmentRowsTests(unittest.TestCase):
    def test_backward_jump_splits_epoch(self):
        rows = [tick(m) for m in range(0, 60, 15)] + [tick(0)]
        segments = segment_rows(rows)
        self.assertEqual(len(segments), 2)
        self.assertEqual(len(segments[0]), 4)

    def test_equal_timestamp_is_duplicate_not_reset(self):
        rows = [tick(0), tick(0), tick(15)]
        segments = segment_rows(rows)
        self.assertEqual(len(segments), 1)
        self.assertEqual(len(segments[0]), 3)


class CheckSegmentTests(unittest.TestCase):
    def test_continuous_segment_passes(self):
        rows = [tick(m) for m in range(0, 120, 15)]
        result = check_segment(rows)
        self.assertTrue(result["ok"])
        self.assertEqual(result["ticks"], 8)
        self.assertEqual(result["gaps"], [])

    def test_gap_and_missing_price_fail(self):
        rows = [tick(0), tick(30, price=False)]  # 15-minute tick missing
        result = check_segment(rows)
        self.assertFalse(result["ok"])
        self.assertEqual(len(result["gaps"]), 1)
        self.assertEqual(len(result["ticks_missing_price"]), 1)

    def test_duplicate_tick_is_dropped(self):
        rows = [tick(0), tick(0), tick(15)]
        result = check_segment(rows)
        self.assertTrue(result["ok"])
        self.assertEqual(result["ticks"], 2)
        self.assertEqual(result["duplicates_dropped"], 1)


if __name__ == "__main__":
    unittest.main()
