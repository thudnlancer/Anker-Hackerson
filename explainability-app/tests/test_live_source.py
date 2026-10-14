"""Offline authentication/source boundaries; no HA or simulator is contacted."""
from copy import deepcopy
from datetime import datetime, timezone
import json
from threading import Event, Lock, Thread
from unittest.mock import Mock

import pytest
import requests

from energy_explain import live_source
from energy_explain.live_source import LiveSource, SourceUnavailable, snapshot_with_states


CFG = {
    "ha_base": "http://ha.unit.test:8123",
    "ha_token": "unit-test-in-memory-token",
    "ha_username": "unit-test-ha-user",
    "ha_password": "NEVER-LOG-HA-PASSWORD",
    "sim_base": "http://sim.unit.test:45678",
    "sim_username": "unit-test-sim-user",
    "sim_password": "NEVER-LOG-SIM-PASSWORD",
    "stations": {str(site): {"suffix": f"unit_{site}"} for site in range(1, 4)},
}


@pytest.fixture(autouse=True)
def forbid_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("offline source test attempted a real network request")
    monkeypatch.setattr(requests.sessions.Session, "request", forbidden)
    for method in ("get", "post", "request"):
        monkeypatch.setattr(requests, method, forbidden)


def response(status=200, payload=None):
    result = Mock(status_code=status)
    result.json.return_value = {} if payload is None else payload
    if status >= 400:
        result.raise_for_status.side_effect = requests.HTTPError(
            "NEVER-LOG-ERROR-BODY", response=result)
    return result


@pytest.fixture
def session(monkeypatch):
    result = Mock()
    monkeypatch.setattr(live_source.requests, "Session", lambda: result)
    return result


@pytest.mark.parametrize("source,path", [("ha", "/api/states"), ("sim", "/api/dashboard")])
def test_read_401_refreshes_once_and_retries_only_the_same_get(session, monkeypatch, source, path):
    source_client = LiveSource(CFG)
    source_client.tokens[source] = "old-in-memory-token"
    login = Mock(return_value="new-in-memory-token")
    monkeypatch.setattr(live_source, "exchange_ha_token", login)
    session.post.return_value = response(payload={"token": "new-in-memory-token"})
    session.get.side_effect = [response(401), response(payload={"read": "ok"})]

    assert source_client._get(source, path) == {"read": "ok"}
    assert source_client.refreshes[source] == 1
    assert session.get.call_count == 2
    calls = session.get.call_args_list
    assert calls[0].args[0] == calls[1].args[0] == CFG[source + "_base"] + path
    assert [call.kwargs["headers"]["Authorization"] for call in calls] == [
        "Bearer old-in-memory-token", "Bearer new-in-memory-token"]
    assert all(call.kwargs["allow_redirects"] is False for call in calls)
    if source == "ha":
        assert login.call_count == 1
        assert login.call_args.args[0]["ha_token"] is None
        session.post.assert_not_called()
    else:
        login.assert_not_called()
        assert session.post.call_count == 1
        assert session.post.call_args.args[0] == CFG["sim_base"] + "/api/auth/login"


@pytest.mark.parametrize("source,path", [("ha", "/api/states"), ("sim", "/api/dashboard")])
def test_second_401_stops_without_third_get_or_second_refresh(session, monkeypatch, source, path):
    source_client = LiveSource(CFG)
    source_client.tokens[source] = "old-in-memory-token"
    login = Mock(return_value="replacement-in-memory-token")
    monkeypatch.setattr(live_source, "exchange_ha_token", login)
    session.post.return_value = response(payload={"token": "replacement-in-memory-token"})
    session.get.side_effect = [response(401), response(401), response(payload={"unexpected": True})]
    with pytest.raises(SourceUnavailable) as caught:
        source_client._get(source, path)
    assert caught.value.category == source + "_authentication_failed"
    assert caught.value.http_status == 401
    assert session.get.call_count == 2
    assert source_client.refreshes[source] == 1
    assert login.call_count == (1 if source == "ha" else 0)
    assert session.post.call_count == (1 if source == "sim" else 0)


def test_refresh_budget_is_for_the_source_lifetime_not_each_read(session, monkeypatch):
    source_client = LiveSource(CFG)
    source_client.tokens["ha"] = "old-token"
    login = Mock(return_value="replacement-token")
    monkeypatch.setattr(live_source, "exchange_ha_token", login)
    session.get.side_effect = [response(401), response(payload=[]), response(401)]
    assert source_client.states()[0] == []
    with pytest.raises(SourceUnavailable) as caught:
        source_client.states()
    assert caught.value.http_status == 401
    assert session.get.call_count == 3
    assert login.call_count == 1


@pytest.mark.parametrize("source,path", [("ha", "/api/states"), ("sim", "/api/dashboard")])
def test_403_is_terminal_and_never_reauthenticates(session, monkeypatch, source, path):
    source_client = LiveSource(CFG)
    source_client.tokens[source] = "existing-token"
    login = Mock()
    monkeypatch.setattr(live_source, "exchange_ha_token", login)
    session.get.return_value = response(403)
    with pytest.raises(SourceUnavailable) as caught:
        source_client._get(source, path)
    assert caught.value.http_status == 403
    assert caught.value.category == source + "_authentication_failed"
    assert source_client.refreshes[source] == 0
    assert session.get.call_count == 1
    login.assert_not_called()
    session.post.assert_not_called()


@pytest.mark.parametrize("error", [
    requests.ConnectionError("NEVER-LOG-HA-PASSWORD https://user:SECRET@host/"),
    requests.HTTPError("NEVER-LOG-ERROR-BODY", response=response(503)),
    ValueError("NEVER-LOG-SIM-PASSWORD"),
])
def test_read_errors_preserve_category_not_credentials_or_body(session, error, capsys):
    source_client = LiveSource(CFG)
    source_client.tokens["ha"] = "SECRET-BEARER-TOKEN"
    session.get.side_effect = error
    with pytest.raises(SourceUnavailable) as caught:
        source_client.states()
    assert str(caught.value) == "ha_read_failed"
    assert caught.value.__suppress_context__
    assert caught.value.http_status in (None, 503)
    assert capsys.readouterr() == ("", "")


def test_authentication_failure_is_sanitized_and_does_not_retry_credentials(session, monkeypatch, capsys):
    source_client = LiveSource(CFG)
    login = Mock(side_effect=requests.HTTPError("NEVER-LOG-HA-PASSWORD", response=response(403)))
    monkeypatch.setattr(live_source, "exchange_ha_token", login)
    with pytest.raises(SourceUnavailable) as caught:
        source_client.states()
    assert str(caught.value) == "ha_authentication_failed"
    assert caught.value.http_status == 403
    assert caught.value.__suppress_context__
    assert login.call_count == 1
    session.get.assert_not_called()
    assert capsys.readouterr() == ("", "")


def test_sim_login_has_only_fixed_auth_endpoint_and_no_redirect(session):
    source_client = LiveSource(CFG)
    session.post.return_value = response(payload={"token": "sim-in-memory-token"})
    session.get.return_value = response(payload={"contest": {"mode": "idle"}})
    assert source_client.dashboard()["contest"]["mode"] == "idle"
    assert session.post.call_count == 1
    assert session.post.call_args.args[0] == CFG["sim_base"] + "/api/auth/login"
    assert session.post.call_args.kwargs["allow_redirects"] is False
    assert session.post.call_args.kwargs["json"] == {
        "username": CFG["sim_username"], "password": CFG["sim_password"]}
    assert source_client.tokens == {"sim": "sim-in-memory-token"}


def test_get_redirect_is_a_read_failure_and_never_follows_location(session):
    source_client = LiveSource(CFG)
    source_client.tokens["ha"] = "existing-token"
    session.get.return_value = response(302)
    with pytest.raises(SourceUnavailable) as caught:
        source_client.states()
    assert caught.value.http_status == 302
    assert caught.value.category == "ha_read_failed"
    assert session.get.call_count == 1
    assert session.get.call_args.kwargs["allow_redirects"] is False
    session.post.assert_not_called()


def test_ha_client_factory_uses_current_memory_token_without_submitting_services(session, monkeypatch):
    source_client = LiveSource(CFG)
    source_client.tokens["ha"] = "current-token"
    login = Mock()
    monkeypatch.setattr(live_source, "exchange_ha_token", login)
    client = source_client.ha_client()
    assert client.base == CFG["ha_base"]
    assert client.token == "current-token"
    login.assert_not_called()
    session.get.assert_not_called()
    session.post.assert_not_called()


@pytest.mark.parametrize("source,path", [
    ("ha", "/api/services/select/select_option"),
    ("ha", "/api/states/unit_1"),
    ("ha", "https://outside.unit.test/api/states"),
    ("sim", "/api/contest/start"),
    ("sim", "/api/reset"),
    ("sim", "/api/dashboard?token=SECRET"),
    ("sim", "../api/dashboard"),
])
def test_unallowlisted_endpoint_is_rejected_before_login_or_http(session, monkeypatch, source, path):
    source_client = LiveSource(CFG)
    login = Mock()
    monkeypatch.setattr(live_source, "exchange_ha_token", login)
    with pytest.raises(ValueError, match="not allowed"):
        source_client._get(source, path)
    login.assert_not_called()
    session.get.assert_not_called()
    session.post.assert_not_called()


@pytest.mark.parametrize("url", [
    "http://user:SECRET@ha.unit.test:8123", "http://user@ha.unit.test",
    "http://ha.unit.test/?token=SECRET", "http://ha.unit.test/#SECRET",
    "file:///tmp/secret", "ftp://ha.unit.test", "http:///missing-host",
])
@pytest.mark.parametrize("base", ["ha_base", "sim_base"])
def test_unsafe_base_url_is_rejected_without_http(session, base, url):
    with pytest.raises(ValueError, match="base URL"):
        LiveSource({**CFG, base: url})
    session.get.assert_not_called()
    session.post.assert_not_called()


def test_tokens_are_ephemeral_configuration_is_not_mutated_and_nothing_is_written(session, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    original = json.loads(json.dumps(CFG))
    login = Mock(return_value="new-ephemeral-ha-token")
    monkeypatch.setattr(live_source, "exchange_ha_token", login)
    source_client = LiveSource(CFG)
    session.get.return_value = response(payload=[])
    assert source_client.states()[0] == []
    assert source_client.tokens["ha"] == "new-ephemeral-ha-token"
    assert CFG == original
    assert list(tmp_path.iterdir()) == []
    source_client.close()
    assert source_client.tokens == {}
    session.close.assert_called_once()


def test_ha_received_at_is_fixed_before_slow_weather_finishes(monkeypatch):
    all_weather_started, timestamped, release_weather = Event(), Event(), Event()
    counter_lock = Lock()
    started = [0]
    result = {}
    requests_seen = []
    rows = [{"entity_id": f"sensor.unit_{site}_{field}", "state": "50",
             "last_changed": "2031-01-01T00:00:00Z"}
            for site in range(1, 4) for field in ("soc", "solar_power", "home_load", "device_time")]

    class CaptureClock:
        @staticmethod
        def now(tz):
            value = datetime.now(tz)
            timestamped.set()
            return value

    class OfflineSession:
        trust_env = True

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.close()

        def close(self):
            pass

        def post(self, *args, **kwargs):
            raise AssertionError("capture attempted an HTTP write or credential exchange")

        def get(self, url, **kwargs):
            with counter_lock:
                requests_seen.append((url, kwargs))
            assert kwargs["allow_redirects"] is False
            if url.endswith("/api/price"):
                return response(payload={"prices": []})
            if url.endswith("/api/states"):
                assert all_weather_started.wait(2), "weather requests did not start"
                return response(payload=rows)
            assert "/api/weather/" in url
            with counter_lock:
                started[0] += 1
                if started[0] == 6:
                    all_weather_started.set()
            assert release_weather.wait(3), "test did not release weather requests"
            return response(payload={"finished_at": datetime.now(timezone.utc).isoformat()})

    monkeypatch.setattr(live_source.requests, "Session", OfflineSession)
    monkeypatch.setattr(live_source, "datetime", CaptureClock)
    source_client = LiveSource(CFG)
    source_client.tokens = {"ha": "HA-IN-MEMORY", "sim": "SIM-IN-MEMORY"}

    def run():
        try:
            result["capture"] = source_client.capture()
        except BaseException as exc:
            result["error"] = exc

    thread = Thread(target=run, daemon=True)
    thread.start()
    try:
        assert timestamped.wait(3), "HA receipt time was not stamped"
    finally:
        release_weather.set()
    thread.join(4)
    assert not thread.is_alive()
    assert "error" not in result, result.get("error")
    snapshot, observed = result["capture"]
    assert observed == rows
    assert len(requests_seen) == 8  # price + HA states + six fixed weather reads.
    captured = datetime.fromisoformat(snapshot["received_at"])
    for station in snapshot["stations"].values():
        assert station["received_at"] == snapshot["received_at"]
        for day in station["weather"].values():
            assert captured < datetime.fromisoformat(day["finished_at"])
    assert "IN-MEMORY" not in json.dumps(snapshot)
    assert "PASSWORD" not in json.dumps(snapshot)


@pytest.mark.parametrize("rows", [{"entity_id": "not-a-list"}, [None], [{"state": "50"}]])
def test_invalid_ha_entity_payload_is_unavailable(session, rows):
    source_client = LiveSource(CFG)
    source_client.tokens["ha"] = "existing-token"
    session.get.return_value = response(payload=rows)
    with pytest.raises(SourceUnavailable, match="invalid_ha_entity_list"):
        source_client.states()


def test_refresh_replaces_actual_ha_values_while_preserving_original_forecasts_and_inputs():
    old_capture = "2031-01-01T00:00:00+00:00"
    new_capture = datetime(2031, 1, 1, 0, 0, 7, tzinfo=timezone.utc)
    prices = {"today": {"date": "2031-01-01", "points": [{"import_price": .25}]}}
    original = {
        "received_at": old_capture,
        "prices": prices,
        "stations": {site: {
            "received_at": old_capture,
            "state": {"soc": "OLD_SOC", "solar_power": "OLD_PV", "home_load": "OLD_LOAD",
                      "device_time": "2031-01-01T00:00:00+00:00"},
            "weather": {"today": {"source": "original_forecast", "values": [100, 200],
                                   "fetched_at": old_capture}}}
            for site in CFG["stations"]},
    }
    saved_original = deepcopy(original)
    observations = []
    expected = {}
    for site, spec in CFG["stations"].items():
        expected[site] = {"soc": str(50 + int(site)), "solar_power": str(600 + int(site)),
                          "home_load": str(900 + int(site)), "device_time": "2031-01-01T00:15:00Z"}
        observations.extend({"entity_id": f"sensor.{spec['suffix']}_{field}", "state": value,
                             "last_changed": "2031-01-01T00:00:06.900000Z"}
                            for field, value in expected[site].items())
    saved_observations = deepcopy(observations)

    refreshed = snapshot_with_states(original, CFG["stations"], observations, new_capture)

    assert refreshed["received_at"] == new_capture.isoformat()
    assert refreshed["prices"] == original["prices"]
    for site in CFG["stations"]:
        row = refreshed["stations"][site]
        assert row["received_at"] == new_capture.isoformat()
        assert row["weather"] == original["stations"][site]["weather"]
        for field, value in expected[site].items():
            assert row["state"][field] == value
            assert row["state"][field + "_last_changed"] == "2031-01-01T00:00:06.900000Z"
    assert original == saved_original
    assert observations == saved_observations
    # A later caller mutating the result must not alter cached source payloads.
    refreshed["prices"]["today"]["points"][0]["import_price"] = 99
    refreshed["stations"]["1"]["weather"]["today"]["values"].append(999)
    refreshed["stations"]["1"]["state"]["soc"] = "MUTATED"
    assert original == saved_original
    assert observations == saved_observations


def test_missing_fresh_sensor_never_reuses_old_value_with_a_new_timestamp():
    captured = datetime(2031, 1, 1, 0, 0, 7, tzinfo=timezone.utc)
    original = {"received_at": "2031-01-01T00:00:00Z", "prices": {"unchanged": True},
                "stations": {"1": {"received_at": "2031-01-01T00:00:00Z",
                                    "state": {"soc": "90", "solar_power": "5000",
                                              "home_load": "2000", "device_time": "OLD_TICK"},
                                    "weather": {"tomorrow": {"values": [5, 6]}}}}}
    saved_original = deepcopy(original)
    observed = [{"entity_id": "sensor.unit_1_soc", "state": "60", "last_changed": captured.isoformat()},
                {"entity_id": "sensor.unit_1_device_time", "state": "2031-01-01T00:15:00Z",
                 "last_changed": captured.isoformat()},
                {"entity_id": "sensor.unrelated_solar_power", "state": "9999"}]

    refreshed = snapshot_with_states(original, {"1": CFG["stations"]["1"]}, observed, captured)

    assert refreshed["stations"]["1"]["state"] == {
        "soc": "60", "soc_last_changed": captured.isoformat(),
        "solar_power": None, "solar_power_last_changed": None,
        "home_load": None, "home_load_last_changed": None,
        "device_time": "2031-01-01T00:15:00Z", "device_time_last_changed": captured.isoformat(),
    }
    assert refreshed["stations"]["1"]["received_at"] == captured.isoformat()
    assert refreshed["prices"] == original["prices"]
    assert refreshed["stations"]["1"]["weather"] == original["stations"]["1"]["weather"]
    assert original == saved_original
