import unittest
import pandas as pd

from src.energy_lab.online_inference import predict_online_power


class OnlineInferenceThresholdTests(unittest.TestCase):
    def test_invalid_minimum_history_is_rejected(self):
        with self.assertRaises(ValueError):
            predict_online_power(pd.DataFrame(), {}, None, None, minimum_history_hours=0)

