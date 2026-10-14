import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from energy_lab.api_policy import build_api_power_path


class ApiPolicyTests(unittest.TestCase):
    def weather(self):
        points = []
        for day, date in (("today", "2032-04-02"), ("tomorrow", "2032-04-03")):
            for hour in range(24):
                points.append({"timestamp": f"{date}T{hour:02d}:00:00Z", "condition": "clear",
                               "temperature_c": 20, "cloud_cover_percent": 0,
                               "solar_irradiance_wm2": 1000 if 8 <= hour < 18 else 0})
        return {"today": {"points": points[:24]}, "tomorrow": {"points": points[24:]}}

    def test_api_weather_is_used_only_after_pv_calibration(self):
        state = {"solar_power": "100", "home_load": "300"}
        path = build_api_power_path(state, self.weather(), "2032-04-02T08:15:00Z")
        self.assertIn("current_pv_persistence", path.mode)
        calibrated = build_api_power_path(state, self.weather(), "2032-04-02T08:15:00Z",
                                           calibration={"scale_w_per_wm2": .5})
        self.assertIn("calibrated_pv", calibrated.mode)
        self.assertGreater(float(calibrated.frame.pv.iloc[1]), float(path.frame.pv.iloc[1]))

    def test_current_observation_overrides_first_path_step(self):
        path = build_api_power_path({"solar_power": "123", "home_load": "456"}, self.weather(),
                                    "2032-04-02T08:15:00Z", calibration={"scale_w_per_wm2": .5})
        self.assertEqual(float(path.frame.pv.iloc[0]), 123)
        self.assertEqual(float(path.frame.load.iloc[0]), 456)

    def test_device_timezone_is_converted_to_utc(self):
        path = build_api_power_path({"solar_power": "123", "home_load": "456"}, self.weather(),
                                    "2032-04-02T08:15:00+08:00")
        self.assertEqual(str(path.frame.time.iloc[0]), "2032-04-02 00:15:00+00:00")

    def test_incomplete_tomorrow_payload_is_rejected(self):
        payload = self.weather()
        payload.pop("tomorrow")
        with self.assertRaises(ValueError):
            build_api_power_path({"solar_power": "100", "home_load": "300"}, payload,
                                 "2032-04-02T08:15:00Z")

    def test_missing_weather_uses_persistence_path(self):
        payload = self.weather()
        payload["today"]["points"][8]["solar_irradiance_wm2"] = None
        with self.assertRaises(ValueError):
            build_api_power_path({"solar_power": "100", "home_load": "300"}, payload,
                                 "2032-04-02T08:15:00Z")


if __name__ == "__main__":
    unittest.main()
