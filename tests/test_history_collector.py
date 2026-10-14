import sys
import tempfile
import unittest
import json
import io
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import collect_online_history
from collect_online_history import capture


class HistoryCollectorTests(unittest.TestCase):
    def test_capture_is_read_only_and_writes_weather_fields(self):
        point = {"timestamp": "2032-04-02T00:00:00Z", "condition": "clear",
                 "temperature_c": 20, "cloud_cover_percent": 0, "solar_irradiance_wm2": 0}
        snapshot = {"stations": {"1": {"received_at": "2026-10-10T06:00:00Z",
            "state": {"soc": "50", "solar_power": "100", "home_load": "200", "device_time": "2032-04-02 00:15:00"},
            "weather": {"today": {"points": [point]}, "tomorrow": {"points": [
                {**point, "timestamp": "2032-04-03T00:00:00Z"}]}}}}}
        with tempfile.TemporaryDirectory() as temp:
            self.assertEqual(capture(snapshot, Path(temp)), 1)
            text = (Path(temp) / "site_1.jsonl").read_text(encoding="utf-8")
        self.assertIn("temperature_c", text)
        self.assertIn("cloud_cover_percent", text)
        self.assertIn("solar_irradiance_wm2", text)

    def test_collector_uses_short_target_cadence_without_price_fetch(self):
        point = {"timestamp": "2032-04-02T00:00:00Z", "condition": "clear",
                 "temperature_c": 20, "cloud_cover_percent": 0, "solar_irradiance_wm2": 0}
        snapshot = {"stations": {"1": {"received_at": "2026-10-10T06:00:00Z",
            "state": {"soc": "50", "solar_power": "100", "home_load": "200",
                      "device_time": "2032-04-02 00:15:00"},
            "weather": {"today": {"points": [point]}, "tomorrow": {"points": [
                {**point, "timestamp": "2032-04-03T00:00:00Z"}]}}}}}
        with tempfile.TemporaryDirectory() as temp, tempfile.TemporaryDirectory() as health, patch.object(sys, "argv", [
                "collect_online_history.py", "--cache-dir", temp, "--health-dir", health,
                "--iterations", "1"]), \
                patch.object(collect_online_history, "fetch_snapshot", return_value=snapshot) as fetch, \
                redirect_stdout(io.StringIO()) as output:
            collect_online_history.main()
        fetch.assert_called_once_with(Path("local_online_config.json"), include_prices=False)
        report = json.loads(output.getvalue())
        self.assertEqual(report["target_interval_seconds"], 5)
        self.assertEqual(report["writes_performed"], 0)
        self.assertEqual(report["device_times"]["1"], "2032-04-02 00:15:00")

    def test_node_id_shards_cache_and_writes_heartbeat(self):
        point = {"timestamp": "2032-04-02T00:00:00Z", "condition": "clear",
                 "temperature_c": 20, "cloud_cover_percent": 0, "solar_irradiance_wm2": 0}
        snapshot = {"stations": {"1": {"received_at": "2026-10-10T06:00:00Z",
            "state": {"soc": "50", "solar_power": "100", "home_load": "200",
                      "device_time": "2032-04-02 00:15:00"},
            "weather": {"today": {"points": [point]}, "tomorrow": {"points": [
                {**point, "timestamp": "2032-04-03T00:00:00Z"}]}}}}}
        with tempfile.TemporaryDirectory() as temp, tempfile.TemporaryDirectory() as health, \
                patch.object(sys, "argv", [
                    "collect_online_history.py", "--cache-dir", temp, "--health-dir", health,
                    "--iterations", "2", "--interval-seconds", "0.01", "--node-id", "nodeB"]), \
                patch.object(collect_online_history, "fetch_snapshot", return_value=snapshot), \
                redirect_stdout(io.StringIO()):
            collect_online_history.main()
            shard = Path(temp) / "site_1.nodeB.jsonl"
            self.assertTrue(shard.exists())
            self.assertEqual(len(shard.read_text(encoding="utf-8").splitlines()), 2)
            shard_size = shard.stat().st_size
            heartbeat = json.loads((Path(health) / "heartbeat_nodeB.json").read_text(encoding="utf-8"))
        self.assertEqual(heartbeat["node_id"], "nodeB")
        self.assertEqual(heartbeat["cycle"], 2)
        self.assertEqual(heartbeat["consecutive_errors"], 0)
        self.assertIsNotNone(heartbeat["last_success_at"])
        self.assertEqual(heartbeat["writes_performed"], 0)
        self.assertEqual(heartbeat["cache_bytes"], {"1": shard_size})

    def test_consecutive_errors_exit_nonzero(self):
        with tempfile.TemporaryDirectory() as temp, tempfile.TemporaryDirectory() as health, \
                patch.object(sys, "argv", [
                    "collect_online_history.py", "--cache-dir", temp, "--health-dir", health,
                    "--iterations", "0", "--interval-seconds", "0.01",
                    "--snapshot", str(Path(temp) / "missing.json"),
                    "--max-consecutive-errors", "2"]), \
                redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit) as ctx:
                collect_online_history.main()
            heartbeat = json.loads((Path(health) / "heartbeat_nodeA.json").read_text(encoding="utf-8"))
        self.assertEqual(ctx.exception.code, 2)
        self.assertEqual(heartbeat["consecutive_errors"], 2)
        self.assertIsNone(heartbeat["last_success_at"])
        self.assertIn("FileNotFoundError", heartbeat["last_error"])

    def test_capture_is_atomic_when_one_station_fails_validation(self):
        point = {"timestamp": "2032-04-02T00:00:00Z", "condition": "clear",
                 "temperature_c": 20, "cloud_cover_percent": 0, "solar_irradiance_wm2": 0}
        station = {"received_at": "2026-10-10T06:00:00Z",
            "state": {"soc": "50", "solar_power": "100", "home_load": "200",
                      "device_time": "2032-04-02 00:15:00"},
            "weather": {"today": {"points": [point]}, "tomorrow": {"points": [
                {**point, "timestamp": "2032-04-03T00:00:00Z"}]}}}
        bad = {**station, "weather": {"today": {"points": []}, "tomorrow": {"points": []}}}
        snapshot = {"stations": {"1": station, "2": bad}}
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(ValueError):
                capture(snapshot, Path(temp))
            self.assertFalse(list(Path(temp).glob("site_*.jsonl")))

    def test_capture_keeps_valid_site_histories_when_device_times_are_not_aligned(self):
        point = {"timestamp": "2032-04-02T00:00:00Z", "condition": "clear",
                 "temperature_c": 20, "cloud_cover_percent": 0, "solar_irradiance_wm2": 0}
        station = {"received_at": "2026-10-10T06:00:00Z",
            "state": {"soc": "50", "solar_power": "100", "home_load": "200",
                      "device_time": "2032-04-02 00:15:00"},
            "weather": {"today": {"points": [point]}, "tomorrow": {"points": [
                {**point, "timestamp": "2032-04-03T00:00:00Z"}]}}}
        second = {**station, "state": {**station["state"], "device_time": "2032-04-02 00:30:00"}}
        snapshot = {"stations": {"1": station, "2": second}}
        with tempfile.TemporaryDirectory() as temp:
            self.assertEqual(capture(snapshot, Path(temp)), 2)
            self.assertEqual(len((Path(temp) / "site_1.jsonl").read_text(encoding="utf-8").splitlines()), 1)
            self.assertEqual(len((Path(temp) / "site_2.jsonl").read_text(encoding="utf-8").splitlines()), 1)


if __name__ == "__main__":
    unittest.main()
