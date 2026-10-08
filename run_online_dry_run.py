from __future__ import annotations

from pathlib import Path
from datetime import datetime
import argparse
import json
import re
import sys
import requests
import time

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
from energy_lab.config import exchange_ha_token, load_runtime_config, require_runtime_fields


def ha_token(base: str, username: str, password: str) -> str:
    client = "http://localhost:8123/"
    s = requests.Session()
    s.trust_env = False
    flow = s.post(f"{base}/auth/login_flow", json={"client_id": client, "handler": ["homeassistant", None], "redirect_uri": client}, timeout=20).json()
    flow_id = flow["flow_id"]
    result = s.post(f"{base}/auth/login_flow/{flow_id}", json={"client_id": client, "username": username, "password": password}, timeout=20).json()
    code = result["result"]
    token = s.post(f"{base}/auth/token", data={"grant_type": "authorization_code", "code": code, "redirect_uri": client, "client_id": client}, timeout=20).json()
    return token["access_token"]


def discover_states(base: str, token: str) -> tuple[dict[str, str], dict]:
    r = requests.get(f"{base}/api/states", headers={"Authorization": f"Bearer {token}"}, timeout=40)
    r.raise_for_status()
    states = {item["entity_id"]: item for item in r.json()}
    suffixes = {}
    pattern = re.compile(r"^sensor\.anker_solix_solarbank_max_ac_(.+)_soc$")
    for entity_id in states:
        match = pattern.match(entity_id)
        if match:
            suffixes[match.group(1)] = match.group(1)
    if len(suffixes) < 3:
        raise RuntimeError(f"expected 3 Anker SOC entities, found {sorted(suffixes)}")
    return suffixes, states


def state(states: dict, suffix: str, name: str) -> str | None:
    item = states.get(f"sensor.anker_solix_solarbank_max_ac_{suffix}_{name}")
    return item.get("state") if item else None


def get_json(url: str, **kwargs):
    last = None
    for attempt in range(3):
        try:
            response = requests.get(url, **kwargs)
            response.raise_for_status()
            return response.json()
        except requests.RequestException as exc:
            last = exc
            if attempt < 2:
                time.sleep(2 * (attempt + 1))
    raise last


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only online dry-run against simulator and HA")
    parser.add_argument("--config", type=Path, default=Path("local_online_config.json"))
    parser.add_argument("--output", type=Path, default=Path("online_dry_run_latest.json"))
    args = parser.parse_args()
    cfg = load_runtime_config(args.config, mode_override="dry-run")
    require_runtime_fields(cfg, write_enabled=False)
    token = exchange_ha_token(cfg)
    suffixes, states = discover_states(cfg["ha_base"], token)
    ordered = sorted(suffixes.values(), key=lambda x: int(x) if x.isdigit() else x)
    sim_login = requests.post(f"{cfg['sim_base']}/api/auth/login", json={"username": cfg["sim_username"], "password": cfg["sim_password"]}, timeout=20)
    sim_login.raise_for_status()
    sim_token = sim_login.json()["token"]
    sim_headers = {"Authorization": f"Bearer {sim_token}"}
    price = get_json(f"{cfg['sim_base']}/api/price", headers=sim_headers, timeout=20)
    result = {"timestamp": datetime.now().isoformat(timespec="seconds"), "mode": "online_dry_run", "writes_performed": 0, "stations": {}, "price_keys": sorted(price.keys()) if isinstance(price, dict) else []}
    for index, suffix in enumerate(ordered, start=1):
        weather = get_json(f"{cfg['sim_base']}/api/weather/today", params={"site": index}, headers=sim_headers, timeout=20)
        result["stations"][str(index)] = {
            "suffix": suffix,
            "entities_found": sum(1 for eid in states if f"_ac_{suffix}_" in eid),
            "device_time": state(states, suffix, "device_time"),
            "soc": state(states, suffix, "soc"),
            "solar_power_w": state(states, suffix, "solar_power"),
            "home_load_w": state(states, suffix, "home_load"),
            "grid_import_power_w": state(states, suffix, "grid_import_power"),
            "grid_export_power_w": state(states, suffix, "grid_export_power"),
            "device_status": state(states, suffix, "device_status"),
            "weather_date": weather.get("date") if isinstance(weather, dict) else None,
        }
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
