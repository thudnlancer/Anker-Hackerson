from __future__ import annotations

from dataclasses import dataclass
from typing import Any
import requests
import threading
import time
from .config import canonical_suffix, proxy_mode
from .read_budget import timed_read
from .write_transaction import ACTIVE_WRITE
from urllib3.util import Timeout


@dataclass
class HAClient:
    base: str
    token: str
    timeout: float = 10.0
    proxy_mode: str = "environment"

    def __post_init__(self) -> None:
        self.base = self.base.rstrip("/")
        self.session = requests.Session()
        self.session.trust_env = proxy_mode(self.proxy_mode) == "environment"
        # ``requests.Session`` is not documented thread-safe. The read path
        # (state/snapshot) runs serialized in the ``bounded-read`` worker and
        # keeps the shared ``self.session``; only parallel delivery workers call
        # ``call()`` concurrently, and each lazily gets its own Session via
        # ``_session()``. The constructing thread seeds its local with the
        # shared ``self.session`` so single-threaded callers and tests that
        # patch ``ha.session.post`` behave exactly as before.
        self._local = threading.local()
        self._local.session = self.session

    def _session(self) -> requests.Session:
        """Thread-local Session for concurrent ``call()`` delivery workers."""
        session = getattr(self._local, "session", None)
        if session is None:
            session = requests.Session()
            session.trust_env = self.session.trust_env
            self._local.session = session
        return session

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"}

    def replace_token(self, token: str) -> None:
        if not token:
            raise ValueError("empty HA token")
        self.token = token

    def state(self, entity_id: str) -> dict[str, Any]:
        def read(timeout):
            r = self.session.get(f"{self.base}/api/states/{entity_id}", headers=self.headers, timeout=timeout)
            r.raise_for_status()
            return r.json()
        return timed_read(f"ha.states/{entity_id}", read, self.timeout)

    def snapshot(self) -> dict[str, list[dict[str, Any]]]:
        def read(timeout):
            r = self.session.get(f"{self.base}/api/states", headers=self.headers, timeout=timeout)
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
            r = self._session().post(
            f"{self.base}/api/services/{domain}/{service}",
            headers=self.headers,
            json={"entity_id": entity_id, **data},
                timeout=Timeout(total=remaining, connect=min(self.timeout, remaining),
                                read=min(self.timeout, remaining)),
            )
            try:
                r.raise_for_status()
            except requests.HTTPError:
                # A rejected HTTP response is not proof that the device did
                # not apply the command. Preserve the numeric status without
                # logging the response body or credentials.
                step["status"] = "http_rejected_not_device_verified"
                step["http_status"] = r.status_code
                if transaction:
                    transaction.checkpoint(transaction.steps)
                raise
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
