"""Read-only B3 runner contract tests; no network or HA writes."""

from scripts.online.audit_physical_calibration import fetch_states


class _Response:
    def __init__(self, entities):
        self.entities = entities

    def raise_for_status(self):
        pass

    def json(self):
        return self.entities


class _Session:
    def __init__(self, entities):
        self.entities = entities
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return _Response(self.entities)


def test_fetch_states_uses_only_read_only_get_and_maps_site_fields():
    suffix = "anker_solix_solarbank_max_ac_1502"
    fields = {"solar_power": "1000", "home_load": "800",
              "battery_charging_power": "100", "battery_discharging_power": "0",
              "grid_import_power": "0", "grid_export_power": "100",
              "device_time": "2032-04-02 00:15:00"}
    entities = [{"entity_id": f"sensor.{suffix}_{key}", "state": value,
                 "last_changed": "2026-10-16T00:00:00Z"}
                for key, value in fields.items()]
    session = _Session(entities)
    result = fetch_states({"ha_base": "https://example.invalid",
                           "stations": {"1": {"suffix": suffix}}}, session, "test-token")
    assert session.calls == [("https://example.invalid/api/states",
                              {"headers": {"Authorization": "Bearer test-token"}, "timeout": 10})]
    assert result["1"]["solar_power"] == "1000"
    assert result["1"]["battery_charging_power_w"] == "100"
    assert result["1"]["device_time"] == "2032-04-02 00:15:00"
    assert result["1"]["sample_spread_seconds"] == 0
