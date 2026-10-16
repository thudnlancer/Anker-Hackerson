"""One read-only GET /api/states; save required sensors only, no credentials."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import time

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from energy_lab.config import load_runtime_config, exchange_ha_token, canonical_suffix

FIELDS = {"soc": "soc", "solar_power": "solar_power", "home_load": "home_load",
          "charge_power": "battery_charging_power", "discharge_power": "battery_discharging_power",
          "grid_import": "grid_import_power", "grid_export": "grid_export_power",
          "device_time": "device_time"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    cfg = load_runtime_config(args.config, "dry-run")
    token = exchange_ha_token(cfg)  # Startup authentication excluded from GET timing.
    started = time.perf_counter()
    response = requests.get(str(cfg["ha_base"]).rstrip("/") + "/api/states",
                            headers={"Authorization": f"Bearer {token}"}, timeout=10)
    response.raise_for_status()
    payload = response.json()
    elapsed = time.perf_counter() - started
    if not isinstance(payload, list):
        raise ValueError("HA /api/states must return a list")
    report = {"captured_at": datetime.now(timezone.utc).isoformat(),
              "method": "GET", "path": "/api/states", "get_requests": 1,
              "elapsed_seconds": elapsed, "response_bytes": len(response.content),
              "entity_count": len(payload), "writes_performed": 0,
              "config_sha256": hashlib.sha256(args.config.read_bytes()).hexdigest(),
              "stations": {}}
    for site, item in cfg["stations"].items():
        suffix = canonical_suffix(str(item["suffix"]))
        sensors = {}
        for key, tail in FIELDS.items():
            entity = f"sensor.{suffix}_{tail}"
            matches = [p for p in payload if p.get("entity_id") == entity]
            sensors[key] = {"entity_id": entity, "match_count": len(matches),
                            "state": matches[0].get("state") if len(matches) == 1 else None,
                            "last_changed": matches[0].get("last_changed") if len(matches) == 1 else None,
                            "last_updated": matches[0].get("last_updated") if len(matches) == 1 else None}
        report["stations"][site] = sensors
    report["all_required_entities_unique"] = all(
        field["match_count"] == 1 for site in report["stations"].values() for field in site.values())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "stations"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
