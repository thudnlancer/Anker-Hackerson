"""Runtime authentication regression tests using only fake values and mocked HTTP."""
from __future__ import annotations

from pathlib import Path
import json
import sys
import unittest
from unittest.mock import Mock, call, patch
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from energy_lab.config import exchange_ha_token, require_runtime_fields, load_runtime_config
from energy_lab.ha_client import HAClient
from energy_lab.sim_client import SimulatorClient
import run_shadow_dry_run as shadow_runner


def configuration():
    return {
        "ha_base": "http://unit.test:8123", "ha_username": "unit-test-user",
        "ha_password": "unit-test-password", "sim_base": "http://unit.test:45678",
        "sim_username": "unit-sim-user", "sim_password": "unit-sim-password",
        "stations": {"1": {"suffix": "unit-device"}},
        "live_enabled": False, "allow_ha_write": False, "allow_formal_contest": False,
    }


class RuntimeAuthenticationTests(unittest.TestCase):
    def test_proxy_policy_is_shared_by_auth_ha_and_simulator(self):
        for mode, expected in (("environment", True), ("direct", False)):
            with self.subTest(mode=mode):
                cfg = configuration() | {"proxy_mode": mode}
                responses = [Mock(), Mock(), Mock()]
                for response, body in zip(responses, (
                    {"flow_id": "unit-flow"}, {"result": "unit-code"},
                    {"access_token": "unit-exchanged-token"},
                )):
                    response.json.return_value = body
                with patch("energy_lab.config.requests.Session") as factory:
                    factory.return_value.post.side_effect = responses
                    exchange_ha_token(cfg)
                    self.assertEqual(factory.return_value.trust_env, expected)
                self.assertEqual(HAClient("http://unit.test", "fake", proxy_mode=mode).session.trust_env,
                                 expected)
                self.assertEqual(SimulatorClient("http://unit.test", "fake", "fake",
                                                 proxy_mode=mode).session.trust_env, expected)

    def test_invalid_proxy_policy_rejected_before_requests(self):
        with self.assertRaisesRegex(ValueError, "proxy_mode"):
            HAClient("http://unit.test", "fake", proxy_mode="invalid")
        with self.assertRaisesRegex(ValueError, "proxy_mode"):
            SimulatorClient("http://unit.test", "fake", "fake", proxy_mode="invalid")
        with self.assertRaisesRegex(ValueError, "proxy_mode"):
            exchange_ha_token(configuration() | {"proxy_mode": "invalid"})

    def test_local_and_remote_example_proxy_modes(self):
        root = Path(__file__).resolve().parents[1]
        self.assertEqual(load_runtime_config(root / "local_online_config.example.json")["proxy_mode"],
                         "environment")
        self.assertEqual(load_runtime_config(root / "remote_runtime_config.example.json")["proxy_mode"],
                         "direct")

    def test_absent_or_null_token_uses_account_exchange_instead_of_bearer_none(self):
        for supplied in (False, True):
            with self.subTest(token_present=supplied):
                cfg = configuration()
                if supplied:
                    cfg["ha_token"] = None
                responses = [Mock(), Mock(), Mock()]
                for response, body in zip(responses, (
                    {"flow_id": "unit-flow"}, {"result": "unit-code"},
                    {"access_token": "unit-exchanged-token"},
                )):
                    response.json.return_value = body
                with patch("energy_lab.config.requests.Session") as factory:
                    session = factory.return_value
                    session.post.side_effect = responses
                    token = exchange_ha_token(cfg)
                self.assertEqual(token, "unit-exchanged-token")
                self.assertTrue(session.trust_env)
                client = "http://localhost:8123/"
                self.assertEqual(session.post.call_args_list, [
                    call("http://unit.test:8123/auth/login_flow", json={
                        "client_id": client, "handler": ["homeassistant", None],
                        "redirect_uri": client}, timeout=20),
                    call("http://unit.test:8123/auth/login_flow/unit-flow", json={
                        "client_id": client, "username": "unit-test-user",
                        "password": "unit-test-password"}, timeout=20),
                    call("http://unit.test:8123/auth/token", data={
                        "grant_type": "authorization_code", "code": "unit-code",
                        "redirect_uri": client, "client_id": client}, timeout=20),
                ])
                for response in responses:
                    response.raise_for_status.assert_called_once_with()

    def test_existing_token_skips_account_exchange(self):
        cfg = configuration()
        cfg.update(ha_token="unit-existing-token", ha_username=None, ha_password=None)
        with patch("energy_lab.config.requests.Session") as factory:
            self.assertEqual(exchange_ha_token(cfg), "unit-existing-token")
        factory.assert_not_called()

    def test_null_required_connection_or_simulator_field_is_rejected(self):
        for field in ("ha_base", "sim_base", "sim_username", "sim_password"):
            with self.subTest(field=field):
                cfg = configuration()
                cfg[field] = None
                with self.assertRaisesRegex(RuntimeError, field):
                    require_runtime_fields(cfg, write_enabled=False)

    def test_null_ha_token_and_username_are_rejected_before_requests(self):
        cfg = configuration()
        cfg.update(ha_token=None, ha_username=None)
        with patch("energy_lab.config.requests.Session") as factory:
            with self.assertRaisesRegex(RuntimeError, "ha_token or ha_username"):
                require_runtime_fields(cfg, write_enabled=False)
        factory.assert_not_called()

    def test_null_password_cannot_start_exchange(self):
        cfg = configuration()
        cfg.update(ha_token=None, ha_password=None)
        with patch("energy_lab.config.requests.Session") as factory:
            with self.assertRaisesRegex(RuntimeError, "username/password are unavailable"):
                exchange_ha_token(cfg)
        factory.assert_not_called()

    def test_null_station_suffix_is_rejected(self):
        cfg = configuration()
        cfg["stations"]["1"]["suffix"] = None
        with self.assertRaisesRegex(RuntimeError, "runtime configuration is incomplete"):
            require_runtime_fields(cfg, write_enabled=False)


class InMemorySnapshotTests(unittest.TestCase):
    def test_file_wrapper_preserves_normalization_and_include_prices_delegation(self):
        cfg, sentinel = configuration(), {"unit-snapshot": True}
        path = Path("unit-not-read-config.json")
        with patch.object(shadow_runner, "load_runtime_config", return_value=cfg) as loader, \
             patch.object(shadow_runner, "fetch_snapshot_from_config", return_value=sentinel) as capture:
            result = shadow_runner.fetch_snapshot(path, include_prices=False)
        self.assertIs(result, sentinel)
        loader.assert_called_once_with(path, mode_override="dry-run")
        capture.assert_called_once_with(cfg, include_prices=False)

    def test_memory_capture_authenticates_without_config_read_or_control_endpoints(self):
        for include_prices in (True, False):
            with self.subTest(include_prices=include_prices):
                cfg = configuration()
                # The collector must stay read-only even if a caller supplied live flags.
                cfg.update(mode="live", live_enabled=True, allow_ha_write=True, allow_formal_contest=True)
                cfg["stations"] = {str(site): {"suffix": f"anker_solix_solarbank_max_ac_unit_{site}"}
                                   for site in (1, 2, 3)}
                states = []
                for station in cfg["stations"].values():
                    for name, value in (("soc", "45"), ("solar_power", "400"), ("home_load", "1000"),
                                        ("device_time", "2031-01-01T00:15:00Z")):
                        states.append({"entity_id": f"sensor.{station['suffix']}_{name}", "state": value,
                                       "last_changed": "2026-10-13T00:00:04Z",
                                       "attributes": {"debug_secret": "unit-unused-entity-secret"}})
                sessions, reads = [], []

                def response(body):
                    result = Mock()
                    result.json.return_value = body
                    return result

                def read(url, **kwargs):
                    path = urlsplit(url).path
                    if path not in {"/api/states", "/api/price", "/api/weather/today", "/api/weather/tomorrow"}:
                        raise AssertionError("collector attempted a non-read endpoint")
                    reads.append((path, kwargs))
                    if path == "/api/states":
                        return response(states)
                    if path == "/api/price":
                        return response({"today": {"points": []}})
                    return response({"points": [], "site": kwargs["params"]["site"]})

                def new_session():
                    session = Mock()
                    session.post.return_value = response({"token": "unit-sim-token"})
                    session.get.side_effect = read
                    sessions.append(session)
                    return session

                with patch.object(shadow_runner, "load_runtime_config", side_effect=AssertionError("memory capture read a config file")), \
                     patch.object(shadow_runner, "require_runtime_fields", wraps=require_runtime_fields) as validator, \
                     patch.object(shadow_runner, "exchange_ha_token", return_value="unit-ha-token") as exchange, \
                     patch.object(shadow_runner.requests, "Session", side_effect=new_session):
                    snapshot = shadow_runner.fetch_snapshot_from_config(cfg, include_prices=include_prices)
                validator.assert_called_once_with(cfg, write_enabled=False)
                exchange.assert_called_once_with(cfg)
                self.assertEqual(set(snapshot["stations"]), {"1", "2", "3"})
                self.assertEqual("prices" in snapshot, include_prices)
                self.assertEqual(len(sessions), 7)  # One main session plus six independent weather readers.
                self.assertTrue(all(session.trust_env is False for session in sessions))
                posts = [entry for session in sessions for entry in session.post.call_args_list]
                self.assertEqual(posts, [call("http://unit.test:45678/api/auth/login",
                    json={"username": "unit-sim-user", "password": "unit-sim-password"}, timeout=10)])
                self.assertEqual(sum(path == "/api/states" for path, _ in reads), 1)
                self.assertEqual(sum(path == "/api/price" for path, _ in reads), int(include_prices))
                self.assertEqual(sum(path.startswith("/api/weather/") for path, _ in reads), 6)
                for path, kwargs in reads:
                    expected = "unit-ha-token" if path == "/api/states" else "unit-sim-token"
                    self.assertEqual(kwargs["headers"]["Authorization"], "Bearer " + expected)
                serialized = json.dumps(snapshot)
                for secret in ("unit-test-password", "unit-sim-password", "unit-ha-token",
                               "unit-sim-token", "unit-unused-entity-secret"):
                    self.assertNotIn(secret, serialized)


if __name__ == "__main__":
    unittest.main()
