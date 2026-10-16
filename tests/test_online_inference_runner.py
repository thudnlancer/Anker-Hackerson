import sys
import unittest
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.online.run_online_inference_dry_run import (tail_is_contiguous, history_at_snapshot,
                                          select_history_window, OnlineContractError)


class OnlineInferenceRunnerTests(unittest.TestCase):
    def history(self, hours):
        return pd.DataFrame({"time": pd.date_range("2027-01-01", periods=hours, freq="h", tz="UTC"),
                             "site": "1", "pv": 100., "load": 200., "soc": 50.,
                             "temperature_c": 20., "cloud_cover_percent": 10.})

    def test_snapshot_excludes_future_ticks_even_in_same_hour(self):
        history = self.history(3)
        history.loc[2, "time"] = pd.Timestamp("2027-01-01T01:45:00Z")
        selected = history_at_snapshot(history, "2027-01-01 01:15:00", "UTC")
        self.assertEqual(len(selected), 2)
        self.assertEqual(len(history), 3)

    def test_snapshot_timezone_is_respected(self):
        selected = history_at_snapshot(self.history(3), "2027-01-01T03:00:00+02:00")
        self.assertEqual(len(selected), 2)

    def test_snapshot_requires_timezone(self):
        with self.assertRaises(OnlineContractError):
            history_at_snapshot(self.history(3), "2027-01-01 01:15:00")

    def test_prefers_full_window(self):
        hours, hourly = select_history_window(self.history(200))
        self.assertEqual(hours, 168)
        self.assertEqual(len(hourly), 168)

    def test_falls_back_to_short_window(self):
        hours, _ = select_history_window(self.history(30))
        self.assertEqual(hours, 24)

    def test_tolerates_one_missing_hour_in_short_window(self):
        hours, hourly = select_history_window(self.history(24).drop(index=5))
        self.assertEqual(hours, 24)
        self.assertEqual(hourly.attrs["quality"]["1"]["missing_hours"], 1)

    def test_rejects_large_gap_in_both_windows(self):
        with self.assertRaises(OnlineContractError):
            select_history_window(self.history(168).drop(index=range(150, 156)))

    def _hourly(self, last):
        return pd.DataFrame({"time": pd.to_datetime([last], utc=True)})

    def test_tail_within_same_hour_is_contiguous(self):
        ok, reason = tail_is_contiguous(self._hourly("2027-04-24 10:00:00+00:00"),
                                       "2027-04-24 10:15:00+00:00")
        self.assertTrue(ok)
        self.assertEqual(reason, "contiguous")

    def test_tail_one_hour_ahead_is_contiguous(self):
        ok, _ = tail_is_contiguous(self._hourly("2027-04-24 10:00:00+00:00"),
                                   "2027-04-24 11:05:00+00:00")
        self.assertTrue(ok)

    def test_stale_tail_is_rejected_with_gap(self):
        ok, reason = tail_is_contiguous(self._hourly("2027-04-24 10:00:00+00:00"),
                                       "2027-06-30 04:15:00+00:00")
        self.assertFalse(ok)
        self.assertIn("stale", reason)

    def test_short_stale_tail_is_tolerated_as_degraded(self):
        ok, reason = tail_is_contiguous(self._hourly("2027-04-24 10:00:00+00:00"),
                                       "2027-04-24 13:15:00+00:00", max_stale_hours=3.0)
        self.assertTrue(ok)
        self.assertIn("stale_within_tolerance", reason)

    def test_stale_beyond_tolerance_is_rejected(self):
        ok, reason = tail_is_contiguous(self._hourly("2027-04-24 10:00:00+00:00"),
                                       "2027-04-24 15:00:00+00:00", max_stale_hours=3.0)
        self.assertFalse(ok)
        self.assertIn("stale", reason)

    def test_naive_device_time_is_rejected_without_api_timezone(self):
        ok, reason = tail_is_contiguous(self._hourly("2027-04-24 10:00:00+00:00"),
                                       "2027-04-24 10:15:00")
        self.assertFalse(ok)
        self.assertEqual(reason, "device_time_without_timezone")


if __name__ == "__main__":
    unittest.main()
