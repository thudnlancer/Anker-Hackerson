import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path
import shutil
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    category = "analysis" if name in {"audit_w3_cache", "run_w3_offline_drill"} else "online"
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / category / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CacheAuditTests(unittest.TestCase):
    def test_accelerated_device_time_is_not_wall_freshness(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            cache = root / "cache"
            cache.mkdir()
            rows = [{"received_at": "2026-10-14T00:00:00Z", "device_time": value,
                     "solar_irradiance_wm2": 0} for value in
                    ("2028-04-12T00:00:00Z", "2028-04-12T00:30:00Z")]
            (cache / "site_1.jsonl").write_text(
                "\n".join(json.dumps(r) for r in rows) + "\ninvalid", encoding="utf-8")
            result = load("audit_w3_cache").audit(root, datetime(2026, 10, 14, 1, tzinfo=timezone.utc))
            site = result["caches"][0]["sites"][0]
            self.assertEqual(site["wall_age_seconds"], 3600)
            self.assertEqual(site["device_gap_count"], 1)
            self.assertEqual(site["invalid_rows"], 1)
            self.assertEqual(result["caches"][0]["missing_expected_sites"], ["2", "3"])
            self.assertFalse(result["collector_restored"])
            self.assertFalse(result["d3_started"])

    def test_legacy_and_node_shards_share_site_coverage(self):
        with tempfile.TemporaryDirectory() as folder:
            cache = Path(folder) / "cache"
            cache.mkdir()
            row = {"received_at": "2026-10-14T00:00:00Z", "device_time": "2028-01-01T00:00:00Z"}
            for name in ("site_1.jsonl", "site_1.nodeA.jsonl"):
                (cache / name).write_text(json.dumps(row) + "\n", encoding="utf-8")
            result = load("audit_w3_cache").audit(Path(folder),
                datetime(2026, 10, 14, 1, tzinfo=timezone.utc))
            self.assertEqual(result["caches"][0]["expected_site_coverage"], ["1"])
            self.assertEqual(result["caches"][0]["sites"][0]["valid_rows"], 2)

    @unittest.skipUnless(shutil.which("powershell"), "requires Windows PowerShell")
    def test_six_offline_scenarios_and_evidence(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "drill"
            load("run_w3_offline_drill").run(output)
            result = json.loads((output / "results.json").read_text(encoding="utf-8"))
            self.assertTrue(result["all_passed"])
            self.assertEqual(result["real_process_actions"], 0)
            self.assertEqual(result["ha_connections"], 0)
            self.assertEqual(result["device_writes"], 0)
            self.assertEqual(result["recovery"], ["RECOVERING", "RECOVERING", "HEALTHY"])
            health = output / "outputs/collector_health"
            for name in ("watchdog.jsonl", "monitor_status.json", "incidents.jsonl", "nodeA.disabled"):
                self.assertTrue((health / name).exists(), name)
            self.assertTrue((output / "state_transitions.json").exists())


if __name__ == "__main__":
    unittest.main()
