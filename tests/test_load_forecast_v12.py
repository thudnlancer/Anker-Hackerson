import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from energy_lab.load_forecast_v12 import LoadForecastV12


class LoadForecastV12Tests(unittest.TestCase):
    def test_forecast_returns_12_nonnegative_points(self):
        future = LoadForecastV12().forecast_w([50.0] * 18)
        self.assertEqual(len(future), 12)
        self.assertTrue(np.isfinite(future).all())
        self.assertTrue((future >= 0).all())

    def test_low_load_follows_recent_value(self):
        # Frozen contract: at/below the low-load band the output tracks the
        # latest observation rather than the raw tree prediction.
        future = LoadForecastV12().forecast_w([200.0] * 18)
        self.assertTrue((np.abs(future - 200.0) < 100).all())

    def test_bad_history_length_rejected(self):
        with self.assertRaises((ValueError, AssertionError)):
            LoadForecastV12().forecast_w([50.0] * 10)

    def test_negative_history_rejected(self):
        with self.assertRaises((ValueError, AssertionError)):
            LoadForecastV12().forecast_w([50.0] * 17 + [-1.0])


if __name__ == "__main__":
    unittest.main()
