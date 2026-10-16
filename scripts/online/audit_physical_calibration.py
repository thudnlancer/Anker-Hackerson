"""B3: read-only HA physical calibration audit.

Samples all stations over several cycles via read-only GET /api/states and
checks PV sensor rating, load sign/direction, and the battery/grid three-way
power consistency (see energy_lab.calibration). No HA service is ever called:
ha_writes_performed is always 0.

Exit code 2 when any site is not calibrated, so the audit can gate later
stages (F4 pre-contest checklist).
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from time import monotonic, sleep
import argparse
import json
import sys

import requests

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from energy_lab.calibration import check_sample, summarize
from energy_lab.config import exchange_ha_token, load_runtime_config, require_runtime_fields

ENTITY_NAMES = {
    "solar_power": "solar_power",
    "home_load": "home_load",
    "battery_charging_power_w": "battery_charging_power",
    "battery_discharging_power_w": "battery_discharging_power",
    "grid_import_power_w": "grid_import_power",
    "grid_export_power_w": "grid_export_power",
}


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def fetch_states(cfg: dict, session: requests.Session, token: str) -> dict[str, dict]:
    """One read-only sweep of /api/states, reduced to the six power fields."""
    response = session.get(str(cfg["ha_base"]).rstrip("/") + "/api/states",
                           headers={"Authorization": f"Bearer {token}"}, timeout=10)
    response.raise_for_status()
    entities = {item["entity_id"]: item for item in response.json()}
    stations: dict[str, dict] = {}
    for site, spec in cfg["stations"].items():
        suffix = spec["suffix"]
        state: dict = {}
        last_changed: list[str] = []
        for key, name in ENTITY_NAMES.items():
            item = entities.get(f"sensor.{suffix}_{name}", {})
            state[key] = item.get("state")
            if item.get("last_changed"):
                last_changed.append(item["last_changed"])
        state["device_time"] = entities.get(f"sensor.{suffix}_device_time", {}).get("state")
        state["sample_spread_seconds"] = _spread_seconds(last_changed)
        stations[str(site)] = state
    return stations


def _spread_seconds(last_changed: list[str]) -> float | None:
    import pandas as pd
    stamps = []
    for value in last_changed:
        try:
            stamp = pd.Timestamp(value)
        except (TypeError, ValueError, OverflowError):
            continue
        if not pd.isna(stamp):
            stamps.append(stamp)
    if len(stamps) < 2:
        return None
    return (max(stamps) - min(stamps)).total_seconds()


def main() -> None:
    parser = argparse.ArgumentParser(description="B3 read-only HA physical calibration audit")
    parser.add_argument("--config", type=Path, default=Path("config/local/local_online_config.json"))
    parser.add_argument("--samples", type=int, default=12)
    parser.add_argument("--interval-seconds", type=float, default=5.0)
    parser.add_argument("--pv-rating-w", type=float, default=None,
                        help="optional nameplate bound; omitted means only pv >= 0 is checked and the observed max reported")
    parser.add_argument("--mutex-tolerance-w", type=float, default=100.0)
    parser.add_argument("--balance-tolerance-w", type=float, default=250.0)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.samples <= 0 or args.interval_seconds <= 0:
        parser.error("samples and interval-seconds must be positive")
    if args.output.exists():
        raise FileExistsError(f"Use a new output file: {args.output}")

    cfg = load_runtime_config(args.config, mode_override="dry-run")
    require_runtime_fields(cfg, write_enabled=False)
    token = exchange_ha_token(cfg)
    session = requests.Session()
    session.trust_env = False

    started_at = utcnow()
    samples: list[dict] = []
    results_by_site: dict[str, list[dict]] = {str(site): [] for site in cfg["stations"]}
    for index in range(args.samples):
        cycle_started = monotonic()
        sampled_at = utcnow()
        try:
            states = fetch_states(cfg, session, token)
            record: dict = {"index": index + 1, "sampled_at": sampled_at,
                            "request_seconds": round(monotonic() - cycle_started, 3),
                            "error_type": None, "sites": {}}
            for site, state in states.items():
                result = check_sample(state, pv_rating_w=args.pv_rating_w,
                                      mutex_tolerance_w=args.mutex_tolerance_w,
                                      balance_tolerance_w=args.balance_tolerance_w)
                result["device_time"] = state.get("device_time")
                result["sample_spread_seconds"] = state.get("sample_spread_seconds")
                record["sites"][site] = result
                results_by_site.setdefault(site, []).append(result)
        except (requests.RequestException, ValueError, KeyError) as exc:
            record = {"index": index + 1, "sampled_at": sampled_at,
                      "request_seconds": round(monotonic() - cycle_started, 3),
                      "error_type": type(exc).__name__, "sites": {}}
        samples.append(record)
        print(json.dumps({"index": record["index"], "error_type": record["error_type"],
                          "sites": {site: r.get("reason") for site, r in record.get("sites", {}).items()}},
                         ensure_ascii=False), flush=True)
        if index + 1 < args.samples:
            sleep(max(0.0, args.interval_seconds - (monotonic() - cycle_started)))

    summary = summarize(results_by_site)
    report = {"mode": "read_only_physical_calibration_audit",
              "task": "B3",
              "started_at": started_at,
              "finished_at": utcnow(),
              "ha_writes_performed": 0,
              "config": str(args.config),
              "tolerances": {"pv_rating_w": args.pv_rating_w,
                             "mutex_tolerance_w": args.mutex_tolerance_w,
                             "balance_tolerance_w": args.balance_tolerance_w},
              "summary": summary,
              "samples": samples}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(args.output), "all_calibrated": summary["all_calibrated"],
                      "sites": {site: item["verdict"] for site, item in summary["sites"].items()},
                      "ha_writes_performed": 0}, ensure_ascii=False, indent=2))
    if not summary["all_calibrated"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
