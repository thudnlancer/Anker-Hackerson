import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from analyze_d1_window import analyze


class D1WindowAnalysisTests(unittest.TestCase):
    def test_historical_gaps_do_not_block_fresh_current_input(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            cache = root / "cache"
            cache.mkdir()
            now = datetime(2026, 10, 14, 12, tzinfo=timezone.utc)
            ticks = (0, 15, 45, 60, 75)
            for site in ("1", "2", "3"):
                rows = [{"device_time": (now + timedelta(minutes=tick)).isoformat(),
                         "received_at": (now - timedelta(seconds=3)).isoformat(),
                         "solar_irradiance_wm2": 100} for tick in ticks]
                (cache / f"site_{site}.nodeA.jsonl").write_text(
                    "\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
            heartbeat = root / "heartbeat.json"
            heartbeat.write_text(json.dumps({"pid": 1, "cycle": 5,
                "last_cycle_at": (now-timedelta(seconds=2)).isoformat(),
                "consecutive_errors": 0, "writes_performed": 0}), encoding="utf-8")
            log = root / "collector.log"
            log.write_text(json.dumps({"iteration": 1, "over_interval": True,
                "device_times_aligned": False}) + "\n", encoding="utf-8")
            result = analyze(cache, heartbeat, log, now)
            self.assertTrue(result["basic_fresh_and_continuous"])
            self.assertEqual(result["strategy_input"], "usable_current_api_ha")
            self.assertEqual(result["gap_classification"]["shared_missing_ticks"], 1)
            self.assertEqual(result["sites"]["1"]["tick_coverage_ratio"], 0.8333)
            self.assertEqual(result["sites"]["1"]["device_time_backwards_in_file"], 0)

    def test_stale_heartbeat_requires_degradation(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            cache = root / "cache"
            cache.mkdir()
            now = datetime(2026, 10, 14, 12, tzinfo=timezone.utc)
            for site in ("1", "2", "3"):
                rows = [{"device_time": (now+timedelta(minutes=15*i)).isoformat(),
                         "received_at": now.isoformat()} for i in range(2)]
                (cache / f"site_{site}.nodeA.jsonl").write_text(
                    "\n".join(json.dumps(row) for row in rows), encoding="utf-8")
            heartbeat = root / "heartbeat.json"
            heartbeat.write_text(json.dumps({"pid": 1, "cycle": 2,
                "last_cycle_at": (now-timedelta(seconds=20)).isoformat(),
                "consecutive_errors": 0, "writes_performed": 0}), encoding="utf-8")
            log = root / "collector.log"
            log.write_text("", encoding="utf-8")
            self.assertFalse(analyze(cache, heartbeat, log, now)["basic_fresh_and_continuous"])


if __name__ == "__main__":
    unittest.main()
