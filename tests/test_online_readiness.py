import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.online.check_online_readiness import check, check_control_config


class OnlineReadinessTests(unittest.TestCase):
    def test_missing_history_blocks_readiness(self):
        with tempfile.TemporaryDirectory() as temp:
            model_dir = Path(temp) / "models"
            # The readiness checker must fail safely before loading absent models.
            report = check(Path(temp) / "cache", model_dir, ["1"])
        self.assertFalse(report["ready"])
        self.assertEqual(report["reason"], "missing_model_pv")

    def test_control_config_snapshot_is_sanitized_and_checks_three_fields(self):
        with tempfile.TemporaryDirectory() as temp:
            config = Path(temp) / "runtime.json"
            payload = {"ha_token": "SECRET_DO_NOT_EXPORT", "poll_seconds": 5,
                       "solver_seconds": 2, "soc_min": 0}
            config.write_text(json.dumps(payload), encoding="utf-8")
            good = check_control_config(config)
            self.assertTrue(good["ready"])
            self.assertEqual(good["snapshot"]["solver_seconds"], 2)
            self.assertNotIn("SECRET_DO_NOT_EXPORT", json.dumps(good))
            report = check(Path(temp) / "cache", Path(temp) / "models", ["1"], config)
            self.assertEqual(report["control_config"], good)
            for updates, issue in (({"solver_seconds": 5}, "solver_seconds_must_be_positive_and_below_poll_seconds"),
                                   ({"poll_seconds": 0}, "poll_seconds_must_be_positive"),
                                   ({"soc_min": 10}, "soc_min_percent_must_be_zero_for_contest")):
                with self.subTest(updates=updates):
                    config.write_text(json.dumps({**payload, **updates}), encoding="utf-8")
                    failed = check_control_config(config)
                    self.assertFalse(failed["ready"])
                    self.assertIn(issue, failed["issues"])
                    report = check(Path(temp) / "cache", Path(temp) / "models", ["1"], config)
                    self.assertFalse(report["strategy_ready"])
                    self.assertEqual(report["reason"], "invalid_control_config")

    def test_missing_config_is_explicit(self):
        with tempfile.TemporaryDirectory() as temp:
            result = check_control_config(Path(temp) / "missing.json")
        self.assertEqual(result["issues"], ["config_missing"])


if __name__ == "__main__":
    unittest.main()
