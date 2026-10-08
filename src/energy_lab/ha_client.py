from __future__ import annotations

from dataclasses import dataclass
from typing import Any
import requests
from .config import canonical_suffix


@dataclass
class HAClient:
    base: str
    token: str
    timeout: float = 10.0

    def __post_init__(self) -> None:
        self.base = self.base.rstrip("/")

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"}

    def state(self, entity_id: str) -> dict[str, Any]:
        r = requests.get(f"{self.base}/api/states/{entity_id}", headers=self.headers, timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    def call(self, domain: str, service: str, entity_id: str, **data: Any) -> Any:
        r = requests.post(
            f"{self.base}/api/services/{domain}/{service}",
            headers=self.headers,
            json={"entity_id": entity_id, **data},
            timeout=self.timeout,
        )
        r.raise_for_status()
        return r.json()

    def station_state(self, suffix: str) -> dict[str, Any]:
        suffix = canonical_suffix(suffix)
        names = {
            "soc": f"sensor.{suffix}_soc",
            "solar_power": f"sensor.{suffix}_solar_power",
            "home_load": f"sensor.{suffix}_home_load",
            "charge_power": f"sensor.{suffix}_battery_charging_power",
            "discharge_power": f"sensor.{suffix}_battery_discharging_power",
            "grid_import": f"sensor.{suffix}_grid_import_power",
            "grid_export": f"sensor.{suffix}_grid_export_power",
            "device_time": f"sensor.{suffix}_device_time",
        }
        result: dict[str, Any] = {}
        for key, entity in names.items():
            item = self.state(entity)
            result[key] = item.get("state")
            result[f"{key}_last_changed"] = item.get("last_changed")
        return result

    def set_power(self, suffix: str, flow: str, watts: float, live: bool = False) -> None:
        if not live:
            return
        self.call(
            "select",
            "select_option",
            f"select.{suffix}_operating_mode_device_runs_by_command_in_third_party_controlled",
            option="third_party_control",
        )
        self.call("select", "select_option", f"select.{suffix}_grid_flow", option=flow)
        self.call("number", "set_value", f"number.{suffix}_power_control", value=max(0, min(5000, watts)))

    def set_dispatch(self, suffix: str, flow: str, watts: float, live: bool = False) -> None:
        if not live:
            return
        if flow == "hold" or watts <= 0:
            self.set_self_consumption(suffix, live=True)
            return
        self.call(
            "select",
            "select_option",
            f"select.{suffix}_operating_mode_device_runs_by_command_in_third_party_controlled",
            option="third_party_control",
        )
        self.call("select", "select_option", f"select.{suffix}_grid_flow", option=flow)
        self.call("number", "set_value", f"number.{suffix}_power_control", value=max(0, min(5000, watts)))

    def set_self_consumption(self, suffix: str, live: bool = False) -> None:
        if live:
            self.call(
                "select",
                "select_option",
                f"select.{suffix}_operating_mode_device_runs_by_command_in_third_party_controlled",
                option="self_consumption",
            )
