from __future__ import annotations

from dataclasses import dataclass
from typing import Any
import requests
import time
from .config import canonical_suffix
from .read_budget import timed_read
from .write_transaction import ACTIVE_WRITE
from urllib3.util import Timeout


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
        def read(timeout):
            r = requests.get(f"{self.base}/api/states/{entity_id}", headers=self.headers, timeout=timeout)
            r.raise_for_status()
            return r.json()
        return timed_read(f"ha.states/{entity_id}", read, self.timeout)

    def snapshot(self) -> dict[str, list[dict[str, Any]]]:
        def read(timeout):
            r = requests.get(f"{self.base}/api/states", headers=self.headers, timeout=timeout)
            r.raise_for_status()
            payload = r.json()
            if not isinstance(payload, list):
                raise ValueError("ha_states_not_list")
            return payload

        payload = timed_read("ha.states.bulk", read, self.timeout)
        indexed: dict[str, list[dict[str, Any]]] = {}
        for item in payload:
            if isinstance(item, dict) and isinstance(item.get("entity_id"), str):
                indexed.setdefault(item["entity_id"], []).append(item)
        return indexed

    def station_from_snapshot(self, suffix: str, snapshot: dict) -> dict[str, Any]:
        suffix = canonical_suffix(suffix)
        names = self._station_names(suffix)
        result: dict[str, Any] = {}
        diagnostics: dict[str, Any] = {}
        for key, entity in names.items():
            matches = snapshot.get(entity, [])
            if len(matches) != 1:
                diagnostics[key] = {"entity_id": entity, "match_count": len(matches),
                                    "reason": "missing" if not matches else "duplicate"}
                continue
            item = matches[0]
            result[key] = item.get("state")
            result[f"{key}_last_changed"] = item.get("last_changed")
            result[f"{key}_last_updated"] = item.get("last_updated")
            diagnostics[key] = {"entity_id": entity, "match_count": 1,
                                "last_changed": item.get("last_changed"),
                                "last_updated": item.get("last_updated")}
        result["entity_diagnostics"] = diagnostics
        result["missing_entities"] = [v["entity_id"] for v in diagnostics.values()
                                      if v["match_count"] != 1]
        return result

    @staticmethod
    def _station_names(suffix: str) -> dict[str, str]:
        return {
            "soc": f"sensor.{suffix}_soc",
            "solar_power": f"sensor.{suffix}_solar_power",
            "home_load": f"sensor.{suffix}_home_load",
            "charge_power": f"sensor.{suffix}_battery_charging_power",
            "discharge_power": f"sensor.{suffix}_battery_discharging_power",
            "grid_import": f"sensor.{suffix}_grid_import_power",
            "grid_export": f"sensor.{suffix}_grid_export_power",
            "device_time": f"sensor.{suffix}_device_time",
        }

    def call(self, domain: str, service: str, entity_id: str, **data: Any) -> Any:
        transaction = ACTIVE_WRITE.get()
        remaining = transaction.remaining() if transaction else self.timeout
        step = {"service": f"{domain}/{service}", "entity_id": entity_id,
                "status": "attempted_unknown"}
        if transaction:
            transaction.checkpoint([*transaction.steps, step])
            transaction.remaining()
            transaction.steps.append(step)
        started = time.monotonic()
        try:
            r = requests.post(
            f"{self.base}/api/services/{domain}/{service}",
            headers=self.headers,
            json={"entity_id": entity_id, **data},
                timeout=Timeout(total=remaining, connect=min(self.timeout, remaining),
                                read=min(self.timeout, remaining)),
            )
            r.raise_for_status()
            step["status"] = "http_acknowledged_not_device_verified"
            if transaction:
                transaction.checkpoint(transaction.steps)
            result = r.json()
            if transaction:
                transaction.remaining()
            return result
        finally:
            step["elapsed_seconds"] = time.monotonic() - started

    def station_state(self, suffix: str) -> dict[str, Any]:
        suffix = canonical_suffix(suffix)
        names = self._station_names(suffix)
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
        # Only explicit third-party dispatch commands are accepted. Planned
        # zero-flow ("idle"/legacy "hold") and mode switches must go through
        # their own explicit paths; no silent self_consumption mapping exists.
        if flow not in ("charge", "discharge"):
            raise ValueError(
                f"set_dispatch requires an explicit charge/discharge flow, got {flow!r}; "
                "use set_self_consumption for a self-use mode switch")
        if not live:
            return
        self.call(
            "select",
            "select_option",
            f"select.{suffix}_operating_mode_device_runs_by_command_in_third_party_controlled",
            option="third_party_control",
        )
        # SOP recommends a short processing gap after changing the mode.
        transaction = ACTIVE_WRITE.get()
        if transaction:
            transaction.gap(1.0)
        else:
            time.sleep(1.0)
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
