import sys
import tempfile
import unittest
import json
import io
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import scripts.online.collect_online_history as collect_online_history
from scripts.online.collect_online_history import capture
from scripts.online.collect_online_history import configured_device_timezone


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
        fetch.assert_called_once_with(Path("config/local/local_online_config.json"), include_prices=False)
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
            self.assertEqual(len(shard.read_text(encoding="utf-8").splitlines()), 1)
            shard_size = shard.stat().st_size
            heartbeat = json.loads((Path(health) / "heartbeat_nodeB.json").read_text(encoding="utf-8"))
        self.assertEqual(heartbeat["node_id"], "nodeB")
        self.assertEqual(heartbeat["cycle"], 2)
        self.assertEqual(heartbeat["consecutive_errors"], 0)
        self.assertIsNotNone(heartbeat["last_success_at"])
        self.assertEqual(heartbeat["writes_performed"], 0)
        self.assertEqual(heartbeat["cache_bytes"], {"1": shard_size})

    def test_explicit_epoch_is_in_cache_and_current_heartbeat(self):
        point = {"timestamp": "2032-04-02T00:00:00Z", "condition": "clear",
                 "temperature_c": 20, "cloud_cover_percent": 0, "solar_irradiance_wm2": 100}
        snapshot = {"stations": {"1": {"received_at": "2026-10-10T06:00:00Z",
            "state": {"soc": "50", "solar_power": "100", "home_load": "200",
                      "device_time": "2032-04-02 00:15:00"},
            "weather": {"today": {"points": [point]}}}}}
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config = root / "config.json"
            config.write_text(json.dumps({"local": {"device_timezone": "UTC"}}), encoding="utf-8")
            with patch.object(sys, "argv", ["collector", "--config", str(config),
                      "--cache-dir", str(root / "cache"), "--health-dir", str(root / "health"),
                      "--epoch-id", "new-window", "--iterations", "1"]), \
                    patch.object(collect_online_history, "fetch_snapshot", return_value=snapshot), \
                    redirect_stdout(io.StringIO()):
                collect_online_history.main()
            row = json.loads((root / "cache/site_1.nodeA.jsonl").read_text(encoding="utf-8"))
            heartbeat = json.loads((root / "health/heartbeat_nodeA.json").read_text(encoding="utf-8"))
        self.assertEqual(row["epoch_id"], heartbeat["epoch_id"])
        self.assertEqual(row["epoch_id"], "new-window")

    def test_same_device_tick_with_changed_payload_is_retained(self):
        point = {"timestamp": "2032-04-02T00:00:00Z", "condition": "clear",
                 "temperature_c": 20, "cloud_cover_percent": 0, "solar_irradiance_wm2": 0}
        base = {"received_at": "2026-10-10T06:00:00Z",
            "state": {"soc": "50", "solar_power": "100", "home_load": "200", "device_time": "2032-04-02 00:15:00"},
            "weather": {"today": {"points": [point]}, "tomorrow": {"points": [{**point, "timestamp": "2032-04-03T00:00:00Z"}]}}}
        changed = {**base, "received_at": "2026-10-10T06:00:05Z",
                   "state": {**base["state"], "home_load": "201"}}
        with tempfile.TemporaryDirectory() as temp:
            self.assertEqual(capture({"stations": {"1": base}}, Path(temp), node_id="nodeA"), 1)
            self.assertEqual(capture({"stations": {"1": changed}}, Path(temp), node_id="nodeA"), 1)
            self.assertEqual(len((Path(temp) / "site_1.nodeA.jsonl").read_text(encoding="utf-8").splitlines()), 2)

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

    def test_empty_weather_keeps_valid_ha_for_all_stations(self):
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
            self.assertEqual(capture(snapshot, Path(temp), device_timezone="UTC"), 2)
            row = json.loads((Path(temp) / "site_2.jsonl").read_text(encoding="utf-8"))
            self.assertEqual(row["weather_status"], "missing")

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

    def test_weather_gap_keeps_ha_sample_and_marks_missing(self):
        point = {"timestamp": "2032-04-02T00:00:00Z", "condition": "clear",
                 "temperature_c": 20, "cloud_cover_percent": 0, "solar_irradiance_wm2": 0}
        station = {"received_at": "2026-10-10T06:00:00Z",
            "state": {"soc": "50", "solar_power": "100", "home_load": "200",
                      "device_time": "2032-04-02 01:15:00"},
            "weather": {"today": {"points": [point]}, "tomorrow": {"points": []}}}
        with tempfile.TemporaryDirectory() as temp:
            self.assertEqual(capture({"stations": {"1": station}}, Path(temp)), 1)
            row = json.loads((Path(temp) / "site_1.jsonl").read_text(encoding="utf-8"))
        self.assertEqual(row["pv"], 100.0)
        self.assertIsNone(row["temperature_c"])
        self.assertEqual(row["weather_status"], "missing")
        self.assertIn("no forecast point", row["weather_missing_reason"])
        self.assertIsNone(row["irradiance_source"])

    def test_bad_weather_does_not_block_other_stations(self):
        point = {"timestamp": "2032-04-02T00:00:00Z", "condition": "clear",
                 "temperature_c": 20, "cloud_cover_percent": 0, "solar_irradiance_wm2": 0}
        valid = {"received_at": "2026-10-10T06:00:00Z",
            "state": {"soc": "50", "solar_power": "100", "home_load": "200", "device_time": "2032-04-02 00:15:00"},
            "weather": {"today": {"points": [{**point, "timestamp": "2032-04-02T00:00:00"}]}, "tomorrow": {"points": []}}}
        bad = {**valid, "weather": {"today": {"points": [{"timestamp": "bad"}]}, "tomorrow": {"points": []}}}
        with tempfile.TemporaryDirectory() as temp:
            self.assertEqual(capture({"stations": {"1": valid, "2": bad}}, Path(temp), device_timezone="UTC"), 2)
            self.assertTrue((Path(temp) / "site_1.jsonl").exists())
            row = json.loads((Path(temp) / "site_2.jsonl").read_text(encoding="utf-8"))
        self.assertEqual(row["weather_status"], "missing")

    def test_invalid_ha_state_is_still_rejected(self):
        point = {"timestamp": "2032-04-02T00:00:00Z", "condition": "clear",
                 "temperature_c": 20, "cloud_cover_percent": 0, "solar_irradiance_wm2": 0}
        station = {"received_at": "2026-10-10T06:00:00Z",
            "state": {"soc": "unknown", "solar_power": "100", "home_load": "200", "device_time": "2032-04-02 00:15:00"},
            "weather": {"today": {"points": [point]}, "tomorrow": {"points": [point]}}}
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(ValueError):
                capture({"stations": {"1": station}}, Path(temp))
            self.assertFalse(list(Path(temp).glob("site_*.jsonl")))

    def test_missing_weather_recovers_without_carry_forward_and_deduplicates(self):
        point = {"timestamp": "2032-04-02T00:00:00Z", "condition": "clear",
                 "temperature_c": 20, "cloud_cover_percent": 0, "solar_irradiance_wm2": 12}
        station = {"received_at": "2026-10-10T06:00:00Z",
            "state": {"soc": "50", "solar_power": "100", "home_load": "200", "device_time": "2032-04-02 00:15:00"},
            "weather": {"today": {"points": [point]}}}
        missing = {**station, "weather": {"today": {"unavailable": "HTTPError"}}}
        bad_ha = {**station, "state": {**station["state"], "soc": "unknown"}}
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)
            self.assertEqual(capture({"stations": {"1": station}}, path, "UTC"), 1)
            self.assertEqual(capture({"stations": {"1": missing}}, path, "UTC"), 1)
            diagnostics = {}
            self.assertEqual(capture({"stations": {"1": station, "2": bad_ha}}, path, "UTC", diagnostics=diagnostics), 1)
            self.assertIn("2", diagnostics["rejected_stations"])
            self.assertEqual(capture({"stations": {"1": {**station, "received_at": "2026-10-10T06:00:05Z"}}}, path, "UTC"), 0)
            rows = [json.loads(line) for line in (path / "site_1.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertEqual([row["solar_irradiance_wm2"] for row in rows], [12, None, 12])
            from energy_lab.online_contract import load_observation_history
            self.assertEqual(len(load_observation_history(path / "site_1.jsonl")), 1)

    def test_configured_timezone_has_priority_over_api_and_is_recorded(self):
        point = {"timestamp": "2032-04-02T00:00:00Z", "condition": "clear",
                 "temperature_c": 20, "cloud_cover_percent": 0, "solar_irradiance_wm2": 0}
        station = {"received_at": "2026-10-10T06:00:00Z",
            "state": {"soc": "50", "solar_power": "100", "home_load": "200", "device_time": "2032-04-02 08:15:00"},
            "weather": {"today": {"points": [point]}, "tomorrow": {"points": [point]}}}
        with tempfile.TemporaryDirectory() as temp:
            cfg = Path(temp) / "cfg.json"
            cfg.write_text(json.dumps({"local": {"device_timezone": "Asia/Shanghai"}}), encoding="utf-8")
            self.assertEqual(configured_device_timezone(cfg), "Asia/Shanghai")
            self.assertEqual(capture({"stations": {"1": station}}, Path(temp), configured_timezone="Asia/Shanghai"), 1)
            row = json.loads((Path(temp) / "site_1.jsonl").read_text(encoding="utf-8"))
        self.assertEqual(row["device_time"], "2032-04-02T00:15:00+00:00")
        self.assertEqual(row["device_timezone_source"], "confirmed_config")

    def test_naive_device_time_without_any_trusted_timezone_is_rejected(self):
        point = {"timestamp": "2032-04-02T00:00:00Z", "condition": "clear",
                 "temperature_c": 20, "cloud_cover_percent": 0, "solar_irradiance_wm2": 0}
        station = {"received_at": "2026-10-10T06:00:00Z",
            "state": {"soc": "50", "solar_power": "100", "home_load": "200", "device_time": "2032-04-02 00:15:00"},
            "weather": {"today": {"points": [{**point, "timestamp": "2032-04-02T00:00:00"}]}, "tomorrow": {"points": []}}}
        with tempfile.TemporaryDirectory() as temp:
            diagnostics = {}
            with self.assertRaisesRegex(ValueError, "no valid HA stations"):
                capture({"stations": {"1": station}}, Path(temp), diagnostics=diagnostics)
            self.assertIn("timezone", diagnostics["rejected_stations"]["1"])

    def test_cli_timezone_wins_over_confirmed_config(self):
        point = {"timestamp": "2032-04-02T00:00:00Z", "condition": "clear",
                 "temperature_c": 20, "cloud_cover_percent": 0, "solar_irradiance_wm2": 500}
        station = {"received_at": "2026-10-10T06:00:00Z",
            "state": {"soc": "50", "solar_power": "100", "home_load": "200", "device_time": "2032-04-02 00:15:00"},
            "weather": {"today": {"points": [point]}}}
        with tempfile.TemporaryDirectory() as temp:
            self.assertEqual(capture({"stations": {"1": station}}, Path(temp), "UTC",
                                     configured_timezone="Asia/Shanghai"), 1)
            row = json.loads((Path(temp) / "site_1.jsonl").read_text(encoding="utf-8"))
        self.assertEqual(row["device_timezone_source"], "cli")
        self.assertEqual(row["weather_status"], "valid")

    def test_generic_local_timezone_is_not_a_confirmed_device_clock(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "config.json"
            path.write_text(json.dumps({"local": {"timezone": "Asia/Shanghai"}}), encoding="utf-8")
            self.assertIsNone(configured_device_timezone(path))

    def test_cache_null_to_nan_excludes_missing_calibration_pairs(self):
        from energy_lab.online_contract import load_observation_history, cached_pv_calibration
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "site_1.jsonl"
            rows = [{"site": "1", "received_at": f"2026-10-10T06:00:0{i}Z",
                     "device_time": f"2032-04-02T0{i}:00:00Z", "soc": 50, "pv": 100,
                     "load": 200, "source": "home_assistant_read_only_snapshot",
                     "temperature_c": None, "cloud_cover_percent": None,
                     "solar_irradiance_wm2": None if i == 0 else 500} for i in range(3)]
            path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
            frame = load_observation_history(path)
            self.assertTrue(frame.loc[0, "solar_irradiance_wm2"] != frame.loc[0, "solar_irradiance_wm2"])
            result = cached_pv_calibration(frame, minimum_device_hours=3)
        self.assertEqual(result["mode"], "current_pv_persistence")
        self.assertEqual(result["reason"], "insufficient_valid_daylight_device_hours")
        self.assertEqual(result["eligible_device_hours"], 2)


if __name__ == "__main__":
    unittest.main()
