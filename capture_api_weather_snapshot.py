"""Read-only API weather snapshot with retrieval and target-clock provenance."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import argparse
import hashlib
import json
import requests

from src.energy_lab.config import load_runtime_config


def capture(config_path: Path, output: Path, *, epoch_id: str | None = None) -> dict:
    cfg = load_runtime_config(config_path, mode_override="dry-run")
    session = requests.Session()
    session.trust_env = False
    login = session.post(cfg["sim_base"].rstrip("/") + "/api/auth/login",
                         json={"username": cfg["sim_username"], "password": cfg["sim_password"]}, timeout=10)
    login.raise_for_status()
    headers = {"Authorization": "Bearer " + login.json()["token"]}
    retrieved_at = datetime.now(timezone.utc).isoformat()
    health = session.get(cfg["sim_base"].rstrip("/") + "/api/health", headers=headers, timeout=10)
    health.raise_for_status()
    source_device_time = health.json().get("time")
    sites = {}
    def device_clock():
        response = session.get(cfg["sim_base"].rstrip("/") + "/api/health", headers=headers, timeout=10)
        response.raise_for_status()
        return response.json().get("time")

    for site in sorted(cfg["stations"], key=str):
        sites[site] = {}
        for day in ("today", "tomorrow"):
            before = device_clock()
            response = session.get(cfg["sim_base"].rstrip("/") + "/api/weather/" + day,
                                   params={"site": int(site)}, headers=headers, timeout=10)
            response.raise_for_status()
            response_retrieved_at = datetime.now(timezone.utc).isoformat()
            payload = response.json()
            after = device_clock()
            raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
            points = []
            for point in payload.get("points", []):
                points.append({"target_device_time": point.get("timestamp", point.get("start")),
                    "condition": point.get("condition"),
                    "cloud_cover_percent": point.get("cloud_cover_percent"),
                    "solar_irradiance_wm2": point.get("solar_irradiance_wm2"),
                    "temperature_c": point.get("temperature_c")})
            sites[site][day] = {"date": payload.get("date"),
                "retrieved_at": response_retrieved_at,
                "source_device_time_at_capture": source_device_time,
                "source_device_time_before_response": before,
                "source_device_time_after_response": after,
                "causal_pairing_rule": "target strictly after after_response device clock; subsequent independent HA PV only; exclude clock resets/ambiguous alignment",
                "api_published_at": payload.get("published_at", payload.get("issued_at")),
                "forecast_source": "simulator_api_weather",
                "response_sha256": hashlib.sha256(raw).hexdigest(),
                "raw_response": payload,
                "points": points}
    result = {"captured_at": retrieved_at, "mode": "read_only_api_weather_snapshot",
              "epoch_id": epoch_id,
              "api_base": cfg["sim_base"], "ha_connections": 0, "device_writes": 0,
              "source_device_time_at_capture": source_device_time,
              "publication_time_note": "API response has no published_at/issued_at; retrieved_at is collection time, not forecast issuance time.",
              "sites": sites}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("local_online_config.json"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--epoch-id", required=True, help="same explicit simulator epoch as the HA collector")
    args = parser.parse_args()
    result = capture(args.config, args.output, epoch_id=args.epoch_id)
    print(json.dumps({"captured_at": result["captured_at"], "sites": sorted(result["sites"]),
                      "days": ["today", "tomorrow"], "api_published_at": None,
                      "ha_connections": 0, "device_writes": 0}, ensure_ascii=False))
