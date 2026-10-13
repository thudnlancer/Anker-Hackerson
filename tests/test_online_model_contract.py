import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from train_online_model import ONLINE_TRAINING_WEATHER


class OnlineModelContractTests(unittest.TestCase):
    def test_contract_contains_only_exact_api_weather_fields(self):
        self.assertEqual(ONLINE_TRAINING_WEATHER, ("temperature_c", "cloud_cover_percent"))
        self.assertNotIn("shortwave_radiation_wm2", ONLINE_TRAINING_WEATHER)
        self.assertNotIn("solar_irradiance_wm2", ONLINE_TRAINING_WEATHER)


if __name__ == "__main__":
    unittest.main()
