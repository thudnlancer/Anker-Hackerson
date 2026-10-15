"""Read-only three-site MILP preflight; never sends HA control commands."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
from energy_lab.config import exchange_ha_token, load_runtime_config, require_runtime_fields
from energy_lab.ha_client import HAClient
from energy_lab.sim_client import SimulatorClient
from energy_lab.interface_contract import state_is_fresh
from run_live import price_window, validate_milp_price_window
from energy_lab.price_anchor import read_anchored_tick


def inspect(config_path: Path) -> dict:
    cfg = load_runtime_config(config_path, "dry-run")
    require_runtime_fields(cfg, write_enabled=False)
    sites = cfg["stations"]
    if set(sites) != {"1", "2", "3"}:
        raise ValueError("exactly sites 1, 2, 3 are required")
    suffixes = [str(sites[key]["suffix"]) for key in ("1", "2", "3")]
    if len(set(suffixes)) != 3:
        raise ValueError("station suffixes must be unique")
    ha = HAClient(str(cfg["ha_base"]), exchange_ha_token(cfg))
    sim = SimulatorClient(str(cfg["sim_base"]), str(cfg["sim_username"]),
                          str(cfg["sim_password"]))
    batch = read_anchored_tick(cfg, ha, sim, price_window,
                               price_validator=validate_milp_price_window)
    points = batch["points"]
    report = {"mode": "dry-run", "strategy": "milp", "writes_performed": 0,
              "collected_at": datetime.now(timezone.utc).isoformat(),
              "config_sha256": hashlib.sha256(config_path.read_bytes()).hexdigest(),
              "device_timezone": cfg.get("device_timezone"),
              "device_timezone_source": cfg.get("device_timezone_source"),
              "price_points": len(points), "alignment_attempts": batch["alignment_attempts"],
              **batch["metadata"],
              "price_timestamp_sample": [p.get("time", p.get("start")) for p in points[:2]],
              "timezone_evidence_kind": "bracketed_api_current_slot_and_fresh_ha_labels",
              "stations": {}}
    for site in ("1", "2", "3"):
        item = sites[site]
        suffix = str(item["suffix"])
        try:
            if site not in batch["states"]:
                raise ValueError(batch["errors"].get(site))
            state = batch["states"][site]
            device_time = state.get("device_time")
            last_changed = state.get("device_time_last_changed")
            fresh = bool(last_changed and state_is_fresh(
                last_changed, max_age_seconds=float(cfg.get("stale_after_seconds", 15))))
            alignment = batch["errors"].get(site) or validate_milp_price_window(
                points, batch["times"].get(site))
            report["stations"][site] = {
                "suffix": suffix, "device_time": device_time,
                "device_time_last_changed": last_changed,
                "ha_device_time_raw": device_time,
                "resolved_device_time": (batch["times"][site].isoformat()
                                         if site in batch["times"] else None),
                "state_fresh": fresh, "price_alignment": alignment or "aligned",
                "eligible_for_milp_tick": fresh and alignment is None,
                "soc": state.get("soc"), "solar_power_w": state.get("solar_power"),
                "home_load_w": state.get("home_load"),
            }
        except Exception as exc:
            report["stations"][site] = {"suffix": suffix,
                "state_fresh": False, "eligible_for_milp_tick": False,
                "error": f"{type(exc).__name__}: {exc}"}
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = inspect(args.config)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
