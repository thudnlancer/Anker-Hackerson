import json
import sys
import tempfile
import unittest
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from energy_lab.online_contract import (
    OnlineContractError, append_observation, load_observation_history,
    CONTEST_HISTORY_FIELDS, cache_contest_history, hourly_observation_history,
    model_compatibility, normalize_contest_history, normalize_station_observation, normalize_weather_payload,
)


class OnlineContractTests(unittest.TestCase):
    def setUp(self):
        self.weather = {"today": {"points": [{
            "timestamp": "2032-04-02T00:00:00Z", "condition": "clear",
            "temperature_c": 20, "cloud_cover_percent": 0, "solar_irradiance_wm2": 0,
        }]}}

    def test_weather_contract_is_explicit(self):
        frame = normalize_weather_payload(self.weather, days=("today",))
        self.assertEqual(list(frame.columns), ["time", "temperature_c", "cloud_cover_percent", "solar_irradiance_wm2", "condition"])
        self.assertEqual(frame.loc[0, "time"], pd.Timestamp("2032-04-02T00:00:00Z"))
        missing = json.loads(json.dumps(self.weather))
        del missing["today"]["points"][0]["solar_irradiance_wm2"]
        with self.assertRaisesRegex(OnlineContractError, "solar_irradiance"):
            normalize_weather_payload(missing, days=("today",))

    def test_observation_cache_round_trip(self):
        item = normalize_station_observation("1", {
            "soc": "50", "solar_power": "120", "home_load": "300",
            "device_time": "2032-04-02 00:15:00+00:00",
        }, "2026-10-10T06:29:36.376037+00:00", device_timezone="UTC")
        with tempfile.TemporaryDirectory() as temp:
            path = append_observation(Path(temp), item)
            frame = load_observation_history(path)
        self.assertEqual(len(frame), 1)
        self.assertEqual(float(frame.loc[0, "pv"]), 120)

    def test_old_model_is_rejected_without_aliases(self):
        report = model_compatibility(("temperature_c", "shortwave_radiation_wm2"),
                                      ("temperature_c", "solar_irradiance_wm2"))
        self.assertFalse(report["compatible"])
        self.assertEqual(report["decision"], "reject_until_retrained")

    def test_hourly_history_aggregates_ticks_and_rejects_short_history(self):
        frame = pd.DataFrame({"time": pd.date_range("2032-01-01", periods=8, freq="15min", tz="UTC"),
                              "received_at": pd.date_range("2026-01-01", periods=8, freq="s", tz="UTC"),
                              "soc": 50, "pv": 100, "load": 200, "site": "1"})
        with self.assertRaisesRegex(OnlineContractError, "168"):
            hourly_observation_history(frame)

    def test_hourly_history_requires_weather_for_online_model(self):
        times = pd.date_range("2032-01-01", periods=168, freq="h", tz="UTC")
        frame = pd.DataFrame({"time": times, "received_at": times, "soc": 50,
                              "pv": 100, "load": 200, "site": "1"})
        with self.assertRaisesRegex(OnlineContractError, "weather"):
            hourly_observation_history(frame)

    def test_tolerant_hourly_history_preserves_time_axis_for_small_gap(self):
        times = pd.date_range("2032-01-01", periods=24, freq="h", tz="UTC").delete(5)
        frame = pd.DataFrame({"time": times, "received_at": times, "soc": 50,
                              "pv": 100, "load": 200, "site": "1",
                              "temperature_c": 20, "cloud_cover_percent": 0})
        hourly = hourly_observation_history(frame, minimum_hours=24, allow_gaps=True,
                                             max_missing_ratio=0.05, max_gap_hours=2)
        self.assertEqual(len(hourly), 24)
        self.assertEqual(int(hourly["pv"].isna().sum()), 1)
        self.assertEqual(hourly.attrs["quality"]["1"]["missing_hours"], 1)

    def _contest_payload(self):
        points = []
        for index, stamp in enumerate(pd.date_range("2032-04-02", periods=168, freq="h")):
            point = {"time": stamp.strftime("%Y-%m-%d %H:%M:%S"), "is_day": bool(6 <= stamp.hour < 18)}
            point.update({field: 0.0 for field in CONTEST_HISTORY_FIELDS})
            point.update(pv_power_avg_w=100.0 + index, load_power_avg_w=200.0,
                         temperature_c=20.0, cloud_cover_percent=10.0)
            points.append(point)
        return {"site": 1, "days": 7, "interval_hours": 1,
                "start_time": points[0]["time"], "points": points}

    def test_contest_history_strict_contract_and_cache(self):
        payload = self._contest_payload()
        normalized, frame = normalize_contest_history(payload, requested_site=1)
        self.assertEqual(len(frame), 168)
        self.assertEqual(normalized["site"], 1)
        with tempfile.TemporaryDirectory() as temp:
            path = cache_contest_history(Path(temp), payload, requested_site=1)
            saved = json.loads(path.read_text(encoding="utf-8"))
            self.assertNotIn("token", json.dumps(saved).lower())
            self.assertEqual(saved["points_sha256"], saved["points_sha256"])

    def test_contest_history_rejects_gap_and_missing_field(self):
        payload = self._contest_payload()
        payload["points"][10]["time"] = "2032-04-02 10:30:00"
        with self.assertRaisesRegex(OnlineContractError, "continuous"):
            normalize_contest_history(payload)
        payload = self._contest_payload()
        del payload["points"][0]["load_power_avg_w"]
        with self.assertRaisesRegex(OnlineContractError, "missing fields"):
            normalize_contest_history(payload)

    def test_contest_history_rejects_wrong_site_and_interval(self):
        payload = self._contest_payload()
        payload["site"] = 2
        with self.assertRaisesRegex(OnlineContractError, "mismatch"):
            normalize_contest_history(payload, requested_site=1)
        payload = self._contest_payload()
        payload["interval_hours"] = 3
        with self.assertRaisesRegex(OnlineContractError, "interval_hours"):
            normalize_contest_history(payload)


if __name__ == "__main__":
    unittest.main()
