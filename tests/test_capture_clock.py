"""A delayed forecast must not move the receipt clock of older HA telemetry."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import run_shadow_dry_run as runner
from energy_lab.shadow import validate_state


class CaptureClockTests(unittest.TestCase):
    def test_weather_wait_does_not_relabel_old_ha_observations_as_fresh(self):
        for include_prices in (True, False):
            with self.subTest(include_prices=include_prices):
                start = datetime(2026, 10, 13, tzinfo=timezone.utc)
                ha_receipt, weather_receipt = start + timedelta(seconds=1), start + timedelta(seconds=20)
                clock = [start]
                cfg = {"ha_base": "http://unit.test:8123", "ha_token": "unit-ha-token",
                       "sim_base": "http://unit.test:45678", "sim_username": "unit-user", "sim_password": "unit-password",
                       "stations": {str(site): {"suffix": f"anker_solix_solarbank_max_ac_unit_{site}"} for site in (1, 2, 3)}}
                states = [{"entity_id": f"sensor.{spec['suffix']}_{key}", "state": value, "last_changed": ha_receipt.isoformat()}
                          for spec in cfg["stations"].values()
                          for key, value in (("soc", "45"), ("solar_power", "400"), ("home_load", "1000"),
                                             ("device_time", "2031-01-01T00:15:00Z"))]

                def response(body):
                    result = Mock()
                    result.json.return_value = body
                    return result

                def get(url, **kwargs):
                    path = urlsplit(url).path
                    if path == "/api/states":
                        clock[0] = ha_receipt
                        return response(states)
                    if path == "/api/price":
                        return response({"today": {"points": []}})
                    raise AssertionError("unexpected endpoint")

                def wait_for_weather():
                    clock[0] = weather_receipt
                    return {"points": []}

                future = Mock()
                future.result.side_effect = wait_for_weather
                pool = Mock()
                pool.submit.return_value = future
                executor = Mock()
                executor.__enter__ = Mock(return_value=pool)
                executor.__exit__ = Mock(return_value=False)
                session = Mock()
                session.post.return_value = response({"token": "unit-sim-token"})
                session.get.side_effect = get
                with patch.object(runner, "ThreadPoolExecutor", return_value=executor), \
                     patch.object(runner.requests, "Session", return_value=session), \
                     patch.object(runner, "utcnow", side_effect=lambda: clock[0].isoformat()):
                    snapshot = runner.fetch_snapshot_from_config(cfg, include_prices=include_prices)
                self.assertEqual(clock[0], weather_receipt)
                self.assertEqual(pool.submit.call_count, 6)
                self.assertEqual(future.result.call_count, 6)
                self.assertEqual(snapshot["started_at"], start.isoformat())
                self.assertEqual(snapshot["received_at"], ha_receipt.isoformat())
                for item in snapshot["stations"].values():
                    self.assertEqual(item["received_at"], ha_receipt.isoformat())
                    validate_state(item["state"], item["received_at"], now=ha_receipt, max_age=15)
                    with self.assertRaisesRegex(ValueError, "stale_snapshot"):
                        validate_state(item["state"], item["received_at"], now=weather_receipt, max_age=15)


if __name__ == "__main__": unittest.main()
