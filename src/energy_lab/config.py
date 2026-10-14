"""Configuration loading and safety gates for local and remote runs."""

from __future__ import annotations

from pathlib import Path
from typing import Any
import json
import requests


PLACEHOLDER_PREFIXES = ("<", "TODO", "CHANGE_ME")


def is_placeholder(value: object) -> bool:
    return isinstance(value, str) and (
        not value.strip() or value.strip().startswith(PLACEHOLDER_PREFIXES)
    )


def _first(mapping: dict[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        if key in mapping and mapping[key] is not None:
            return mapping[key]
    return default


def canonical_suffix(value: object) -> str:
    """Convert a station suffix or full HA entity prefix to the canonical prefix."""
    suffix = str(value or "").strip()
    if is_placeholder(suffix):
        return suffix
    if suffix.startswith("sensor."):
        suffix = suffix[7:]
    marker = "_soc"
    if suffix.endswith(marker):
        suffix = suffix[: -len(marker)]
    prefix = "anker_solix_solarbank_max_ac_"
    if suffix.startswith(prefix):
        return suffix
    return f"{prefix}{suffix}"


def _stations(source: dict[str, Any]) -> dict[str, dict[str, Any]]:
    stations = source.get("stations") or {}
    result: dict[str, dict[str, Any]] = {}
    for site, item in stations.items():
        if isinstance(item, str):
            item = {"suffix": item}
        result[str(site)] = {
            **item,
            "suffix": canonical_suffix(item.get("suffix")),
        }
    return result


def load_runtime_config(path: Path, mode_override: str | None = None) -> dict[str, Any]:
    """Load flat, local-online, or remote-experiment configuration."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    if "environment_machine" in raw:
        env = raw["environment_machine"]
        local = raw.get("local") or {}
        experiment = raw.get("experiment") or {}
        safety = raw.get("safety") or {}
        device = raw.get("device_defaults") or {}
        connection = raw.get("connection") or {}
        mode = mode_override or experiment.get("mode") or raw.get("mode") or "dry-run"
        return {
            "mode": mode,
            "ha_base": env.get("ha_base"),
            "ha_token": env.get("ha_long_lived_token"),
            "ha_username": env.get("ha_username"),
            "ha_password": env.get("ha_password"),
            "sim_base": env.get("sim_base"),
            "sim_username": env.get("sim_username"),
            "sim_password": env.get("sim_password"),
            "stations": _stations(env),
            "live_enabled": bool(_first(experiment, "live_enabled", default=safety.get("live_enabled", False))),
            "allow_ha_write": bool(_first(experiment, "allow_ha_write", default=safety.get("allow_ha_write", False))),
            "allow_formal_contest": bool(_first(experiment, "allow_formal_contest", default=safety.get("allow_formal_contest", False))),
            "poll_seconds": float(_first(experiment, "poll_seconds", default=local.get("poll_seconds", 5))),
            "stale_after_seconds": float(_first(experiment, "stale_after_seconds", default=local.get("stale_after_seconds", 15))),
            "solver_seconds": float(_first(experiment, "solver_seconds", default=local.get("solver_seconds", 2.0))),
            "capacity_kwh": float(_first(experiment, "capacity_kwh", default=device.get("capacity_kwh", 14.0))),
            "max_power_w": float(_first(experiment, "max_power_w", default=device.get("max_power_w", 5000.0))),
            "charge_efficiency": float(_first(experiment, "charge_efficiency", default=device.get("charge_efficiency", 0.95))),
            "soc_min": float(_first(experiment, "soc_min_percent", default=device.get("soc_min_percent", 0.0))),
            "soc_max": float(_first(experiment, "soc_max_percent", default=device.get("soc_max_percent", 100.0))),
            "source": str(path),
            "connection": connection,
            "remote_machine": raw.get("remote_machine") or {},
        }

    # Existing flat config.json and local_online_config.json formats.
    env = raw.get("environment_machine") or {}
    safety = raw.get("safety") or {}
    local = raw.get("local") or {}
    if env:
        mode = mode_override or raw.get("mode") or "dry-run"
        return {
            "mode": mode,
            "ha_base": env.get("ha_base"),
            "ha_token": env.get("ha_long_lived_token"),
            "ha_username": env.get("ha_username"),
            "ha_password": env.get("ha_password"),
            "sim_base": env.get("sim_base"),
            "sim_username": env.get("sim_username"),
            "sim_password": env.get("sim_password"),
            "stations": _stations(env),
            "live_enabled": bool(safety.get("live_enabled", False)),
            "allow_ha_write": bool(safety.get("allow_ha_write", False)),
            "allow_formal_contest": bool(safety.get("allow_formal_contest", False)),
            "poll_seconds": float(local.get("poll_seconds", 5)),
            "stale_after_seconds": float(local.get("stale_after_seconds", 15)),
            "solver_seconds": float(local.get("solver_seconds", 2.0)),
            "capacity_kwh": float((raw.get("device_defaults") or {}).get("capacity_kwh", 14.0)),
            "max_power_w": float((raw.get("device_defaults") or {}).get("max_power_w", 5000.0)),
            "charge_efficiency": float((raw.get("device_defaults") or {}).get("charge_efficiency", 0.95)),
            "soc_min": float((raw.get("device_defaults") or {}).get("soc_min_percent", 0.0)),
            "soc_max": float((raw.get("device_defaults") or {}).get("soc_max_percent", 100.0)),
            "source": str(path),
        }

    mode = mode_override or raw.get("mode") or "dry-run"
    return {
        "mode": mode,
        "ha_base": raw.get("ha_base"),
        "ha_token": raw.get("ha_token"),
        "ha_username": raw.get("ha_username"),
        "ha_password": raw.get("ha_password"),
        "sim_base": raw.get("sim_base"),
        "sim_username": raw.get("sim_username"),
        "sim_password": raw.get("sim_password"),
        "stations": _stations(raw),
        "live_enabled": bool(raw.get("live_enabled", False)),
        "allow_ha_write": bool(raw.get("allow_ha_write", False)),
        "allow_formal_contest": bool(raw.get("allow_formal_contest", False)),
        "poll_seconds": float(raw.get("poll_seconds", 5)),
        "stale_after_seconds": float(raw.get("stale_after_seconds", 15)),
        "solver_seconds": float(raw.get("solver_seconds", 2.0)),
        "capacity_kwh": float(raw.get("capacity_kwh", 14.0)),
        "max_power_w": float(raw.get("max_power_w", 5000.0)),
        "charge_efficiency": float(raw.get("charge_efficiency", 0.95)),
        "soc_min": float(raw.get("soc_min", 0.0)),
        "soc_max": float(raw.get("soc_max", 100.0)),
        "source": str(path),
    }


def require_runtime_fields(cfg: dict[str, Any], *, write_enabled: bool = False) -> None:
    required = ("ha_base", "sim_base", "sim_username", "sim_password")
    missing = [key for key in required if is_placeholder(cfg.get(key))]
    if is_placeholder(cfg.get("ha_token")) and is_placeholder(cfg.get("ha_username")):
        missing.append("ha_token or ha_username")
    missing.extend(site for site, item in cfg.get("stations", {}).items() if is_placeholder(item.get("suffix")))
    if missing:
        raise RuntimeError("runtime configuration is incomplete: " + ", ".join(missing))
    solver_seconds = float(cfg.get("solver_seconds", 2.0))
    poll_seconds = float(cfg.get("poll_seconds", 5.0))
    if not (solver_seconds > 0 and poll_seconds > 0 and solver_seconds < poll_seconds):
        raise RuntimeError("solver_seconds must be positive and shorter than poll_seconds")
    if write_enabled and not (cfg.get("live_enabled") and cfg.get("allow_ha_write") and cfg.get("allow_formal_contest")):
        raise RuntimeError("write mode requires live_enabled, allow_ha_write, and allow_formal_contest=true")


def exchange_ha_token(cfg: dict[str, Any]) -> str:
    if not is_placeholder(cfg.get("ha_token")):
        return str(cfg["ha_token"])
    if is_placeholder(cfg.get("ha_username")) or is_placeholder(cfg.get("ha_password")):
        raise RuntimeError("HA token is missing and HA username/password are unavailable")
    base = str(cfg["ha_base"]).rstrip("/")
    client = "http://localhost:8123/"
    session = requests.Session()
    session.trust_env = False
    flow = session.post(
        f"{base}/auth/login_flow",
        json={"client_id": client, "handler": ["homeassistant", None], "redirect_uri": client},
        timeout=20,
    )
    flow.raise_for_status()
    flow_id = flow.json()["flow_id"]
    result = session.post(
        f"{base}/auth/login_flow/{flow_id}",
        json={"client_id": client, "username": cfg["ha_username"], "password": cfg["ha_password"]},
        timeout=20,
    )
    result.raise_for_status()
    code = result.json()["result"]
    token = session.post(
        f"{base}/auth/token",
        data={"grant_type": "authorization_code", "code": code, "redirect_uri": client, "client_id": client},
        timeout=20,
    )
    token.raise_for_status()
    access_token = token.json().get("access_token")
    if not access_token:
        raise RuntimeError("HA login did not return an access token")
    return str(access_token)
