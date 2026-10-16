"""One-shot recovery of a stopped local mock runner; never clears its journal."""
from __future__ import annotations

from datetime import datetime, timezone
import argparse
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from energy_lab.config import exchange_ha_token, load_runtime_config, require_runtime_fields
from energy_lab.ha_client import HAClient
from energy_lab.reconciliation import ReconciliationStore


def read_controls(ha, suffix):
    prefix = f"anker_solix_solarbank_max_ac_{suffix}"
    entities = {
        "mode": f"select.{prefix}_operating_mode_device_runs_by_command_in_third_party_controlled",
        "flow": f"select.{prefix}_grid_flow",
        "setpoint": f"number.{prefix}_power_control",
        "device_time": f"sensor.{prefix}_device_time",
        "soc": f"sensor.{prefix}_soc",
        "charge_power": f"sensor.{prefix}_battery_charging_power",
        "discharge_power": f"sensor.{prefix}_battery_discharging_power",
    }
    snapshot = ha.snapshot()
    return {name: {"entity_id": entity, "state": snapshot[entity][0].get("state"),
                   "last_updated": snapshot[entity][0].get("last_updated")}
            for name, entity in entities.items()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--journal", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--confirm-runner-stopped", action="store_true", required=True)
    args = parser.parse_args()
    if not args.confirm_runner_stopped:
        parser.error("runner must be stopped")
    cfg = load_runtime_config(args.config, "dry-run")
    require_runtime_fields(cfg, write_enabled=False)
    pending = ReconciliationStore(args.journal).pending()
    if not pending:
        raise SystemExit("no pending incidents; refusing unnecessary HA writes")
    ha = HAClient(cfg["ha_base"], exchange_ha_token(cfg), proxy_mode=cfg["proxy_mode"])
    report = {"kind": "local_mock_manual_recovery", "at": datetime.now(timezone.utc).isoformat(),
              "pending_incidents": {site: incident["incident_id"] for site, incident in pending.items()},
              "sites": {}}
    for site in sorted(pending):
        suffix = cfg["stations"][site]["suffix"]
        suffix = str(suffix).split("_")[-1]
        item = {"before": read_controls(ha, suffix)}
        try:
            ha.set_self_consumption(suffix, live=True)
            item["recovery_post"] = "http_acknowledged_not_device_verified"
        except Exception as exc:
            item["recovery_post"] = "attempted_unknown"
            item["error_type"] = type(exc).__name__
        time.sleep(5)
        try:
            item["after"] = read_controls(ha, suffix)
            item["mode_echo_verified"] = item["after"]["mode"]["state"] == "self_consumption"
        except Exception as exc:
            item["mode_echo_verified"] = False
            item["readback_error_type"] = type(exc).__name__
        report["sites"][site] = item
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"site": site, "recovery_post": item["recovery_post"],
                          "mode_echo_verified": item["mode_echo_verified"]}), flush=True)
    print("journal left unchanged; manual incident review/clearance still required", flush=True)


if __name__ == "__main__":
    main()
