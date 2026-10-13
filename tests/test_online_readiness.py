import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from check_online_readiness import check


class OnlineReadinessTests(unittest.TestCase):
    def test_missing_history_blocks_readiness(self):
        with tempfile.TemporaryDirectory() as temp:
            model_dir = Path(temp) / "models"
            # The readiness checker must fail safely before loading absent models.
            report = check(Path(temp) / "cache", model_dir, ["1"])
        self.assertFalse(report["ready"])
        self.assertEqual(report["reason"], "missing_model_pv")


if __name__ == "__main__":
    unittest.main()
