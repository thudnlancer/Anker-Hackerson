import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.online.capture_dashboard_snapshot import capture


class _Response:
    def __init__(self, payload):
        self.content = json.dumps(payload, separators=(",", ":")).encode()
        self.headers = {"content-type": "application/json"}

    def raise_for_status(self):
        return None

    def json(self):
        return json.loads(self.content)


def _dashboard(runs, mode="ended"):
    return {"contest": {"mode": mode, "track": "mock", "days": 7},
            "results": {"runs": runs}, "sites": [], "total": {}}


class CaptureDashboardSnapshotTests(unittest.TestCase):
    def test_prestart_and_new_result_link_without_token_leak(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "config.json"
            config.write_text(json.dumps({"sim_base": "https://example.invalid", "sim_username": "u",
                                          "sim_password": "secret-value", "ha_token": "ha-secret"}))
            old = {"ended_at": "old", "track": "mock", "days": 7}
            new = {"ended_at": "new", "track": "mock", "days": 7,
                   "sites": [{"site_id": str(i), "metrics": {}} for i in (1, 2, 3)]}
            with patch("scripts.online.capture_dashboard_snapshot.requests.post", return_value=_Response({"token": "test-token"})), \
                 patch("scripts.online.capture_dashboard_snapshot.requests.get", side_effect=[_Response(_dashboard([old])),
                                                                                _Response(_dashboard([old, new]))]):
                pre = capture(config, root / "out", "run-1", "prestart")
                end = capture(config, root / "out", "run-1", "end")
            self.assertEqual(end["new_result_indices_since_prestart"], [1])
            self.assertEqual(end["selected_result_index"], 1)
            self.assertEqual(end["prestart_dashboard_sha256"], pre["dashboard_sha256"])
            self.assertEqual(hashlib.sha256((root / "out" / pre["dashboard_raw_file"]).read_bytes()).hexdigest(),
                             pre["dashboard_sha256"])
            self.assertNotIn("secret-value", (root / "out" / "run-1.prestart.manifest.json").read_text())
            self.assertNotIn("test-token", (root / "out" / "run-1.prestart.dashboard.json").read_text())

    def test_refuses_old_result_and_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "config.json"
            config.write_text(json.dumps({"sim_base": "https://example.invalid", "sim_username": "u",
                                          "sim_password": "p"}))
            old = {"ended_at": "old", "track": "mock", "days": 7}
            with patch("scripts.online.capture_dashboard_snapshot.requests.post", return_value=_Response({"token": "t"})), \
                 patch("scripts.online.capture_dashboard_snapshot.requests.get", side_effect=[_Response(_dashboard([old])),
                                                                                _Response(_dashboard([old]))]):
                capture(config, root / "out", "run-1", "prestart")
                with self.assertRaisesRegex(ValueError, "no new settled run"):
                    capture(config, root / "out", "run-1", "end")
            self.assertFalse((root / "out" / "run-1.end.dashboard.json").exists())
            with self.assertRaises(FileExistsError):
                capture(config, root / "out", "run-1", "prestart")

    def test_multiple_new_runs_require_explicit_selection(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "config.json"
            config.write_text(json.dumps({"sim_base": "https://example.invalid", "sim_username": "u",
                                          "sim_password": "p"}))
            old = {"ended_at": "old", "track": "mock", "days": 7}
            first = {"ended_at": "first", "track": "mock", "days": 7}
            second = {"ended_at": "second", "track": "mock", "days": 7}
            with patch("scripts.online.capture_dashboard_snapshot.requests.post", return_value=_Response({"token": "t"})), \
                 patch("scripts.online.capture_dashboard_snapshot.requests.get", side_effect=[_Response(_dashboard([old])),
                                                                                _Response(_dashboard([old, first, second])),
                                                                                _Response(_dashboard([old, first, second]))]):
                capture(config, root / "out", "run-2", "prestart")
                with self.assertRaisesRegex(ValueError, "multiple new settled runs"):
                    capture(config, root / "out", "run-2", "end")
                with self.assertRaisesRegex(ValueError, "not a new post-prestart run"):
                    capture(config, root / "out", "run-2", "end", selected_run_index=0)
            self.assertFalse((root / "out" / "run-2.end.dashboard.json").exists())


if __name__ == "__main__":
    unittest.main()
