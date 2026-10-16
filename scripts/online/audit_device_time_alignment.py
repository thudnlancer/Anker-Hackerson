"""Read-only audit of device-clock alignment across HA stations."""

from datetime import datetime, timezone
from pathlib import Path
from time import monotonic, sleep
import argparse
import json
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from scripts.online.run_shadow_dry_run import fetch_snapshot


def inspect_snapshot(snapshot: dict) -> dict:
    stations = {}
    for site, item in snapshot.get("stations", {}).items():
        state = item.get("state", {})
        stations[str(site)] = {
            "device_time": state.get("device_time"),
            "device_time_last_changed": state.get("device_time_last_changed"),
        }
    ticks = {site: value["device_time"] for site, value in stations.items()}
    return {"stations": stations,
            "aligned": bool(ticks) and all(value is not None for value in ticks.values())
            and len(set(ticks.values())) == 1}


def summarize(samples: list[dict]) -> dict:
    offset_counts: dict[str, dict[str, int]] = {}
    aligned_count = 0
    for sample in samples:
        if sample.get("aligned"):
            aligned_count += 1
        ticks = {site: value.get("device_time")
                 for site, value in sample.get("stations", {}).items()}
        valid = {site: pd.Timestamp(value) for site, value in ticks.items() if value}
        if not valid:
            continue
        latest = max(valid.values())
        for site, tick in valid.items():
            offset = int((tick - latest).total_seconds() // 60)
            counts = offset_counts.setdefault(site, {})
            key = str(offset)
            counts[key] = counts.get(key, 0) + 1
    return {"samples": len(samples), "aligned_samples": aligned_count,
            "unaligned_samples": len(samples) - aligned_count,
            "offset_minutes_from_latest_by_site": offset_counts}


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit station device-time alignment using read-only HA snapshots")
    parser.add_argument("--config", type=Path, default=Path("config/local/local_online_config.json"))
    parser.add_argument("--samples", type=int, default=12)
    parser.add_argument("--interval-seconds", type=float, default=5)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.samples <= 0 or args.interval_seconds <= 0:
        parser.error("samples and interval-seconds must be positive")
    if args.output.exists():
        raise FileExistsError(f"Use a new output file: {args.output}")

    started_at = datetime.now(timezone.utc).isoformat()
    samples = []
    for index in range(args.samples):
        cycle_started = monotonic()
        sampled_at = datetime.now(timezone.utc).isoformat()
        try:
            snapshot = fetch_snapshot(args.config, include_prices=False)
            record = inspect_snapshot(snapshot)
            record.update({"index": index + 1, "sampled_at": sampled_at,
                           "request_seconds": round(monotonic() - cycle_started, 3),
                           "error_type": None})
        except Exception as exc:
            record = {"index": index + 1, "sampled_at": sampled_at,
                      "request_seconds": round(monotonic() - cycle_started, 3),
                      "stations": {}, "aligned": False,
                      "error_type": type(exc).__name__}
        samples.append(record)
        print(json.dumps(record, ensure_ascii=False), flush=True)
        if index + 1 < args.samples:
            sleep(max(0.0, args.interval_seconds - (monotonic() - cycle_started)))

    report = {"mode": "read_only_device_time_alignment_audit",
              "started_at": started_at,
              "finished_at": datetime.now(timezone.utc).isoformat(),
              "ha_writes_performed": 0,
              "price_api_requested": False,
              "summary": summarize(samples),
              "samples": samples}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(args.output), "summary": report["summary"],
                      "ha_writes_performed": 0}, ensure_ascii=False))


if __name__ == "__main__":
    main()
