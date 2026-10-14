"""Offline D1 baseline: inspect local JSONL only, never call HA or weather APIs."""
from pathlib import Path
from datetime import datetime, timezone
import argparse
import json


def stamp(value):
    if not value:
        return None
    result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("timezone missing")
    return result


def audit(root, now, step_minutes=15):
    reports = []
    for folder in sorted(root.iterdir()):
        if not folder.is_dir():
            continue
        sites = []
        grouped = {}
        for path in sorted(folder.glob("site_*.jsonl")):
            site_id = path.name.removeprefix("site_").split(".")[0]
            grouped.setdefault(site_id, []).append(path)
        for site_id, paths in sorted(grouped.items()):
            received, device, invalid, irradiance = [], [], 0, 0
            latest_by_shard = {}
            for path in paths:
                shard_device = []
                shard_received = []
                for line in path.read_text(encoding="utf-8-sig").splitlines():
                    if not line.strip():
                        continue
                    try:
                        row = json.loads(line)
                        r, d = stamp(row.get("received_at")), stamp(row.get("device_time"))
                        if r is None or d is None:
                            raise ValueError("missing timestamp")
                    except (ValueError, TypeError):
                        invalid += 1
                        continue
                    received.append(r)
                    device.append(d)
                    shard_received.append(r)
                    shard_device.append(d)
                    irradiance += row.get("solar_irradiance_wm2") is not None
                if shard_received:
                    latest_by_shard[path.name] = {"last_received_at": max(shard_received).isoformat(),
                                                  "last_device_time": shard_device[-1].isoformat(),
                                                  "valid_rows": len(shard_device)}
            ordered = sorted(set(device))
            gaps = [{"from": a.isoformat(), "to": b.isoformat(),
                     "minutes": (b-a).total_seconds()/60}
                    for a, b in zip(ordered, ordered[1:])
                    if (b-a).total_seconds() > step_minutes*60]
            sites.append({"site": site_id, "shards": latest_by_shard,
                "valid_rows": len(device), "invalid_rows": invalid,
                "irradiance_rows": irradiance, "unique_device_ticks": len(ordered),
                "last_received_at": max(received).isoformat() if received else None,
                "wall_age_seconds": (now-max(received)).total_seconds() if received else None,
                "device_start": ordered[0].isoformat() if ordered else None,
                "device_end": ordered[-1].isoformat() if ordered else None,
                "device_backwards_in_file": sum(b<a for a,b in zip(device,device[1:])),
                "device_gap_count": len(gaps), "device_gaps": gaps})
        if sites:
            reports.append({"directory": str(folder.resolve()), "sites": sites,
                "expected_site_coverage": sorted(s["site"] for s in sites if s["valid_rows"]),
                "missing_expected_sites": sorted({"1", "2", "3"} -
                    {s["site"] for s in sites if s["valid_rows"]})})
    return {"checked_at": now.isoformat(), "mode": "offline_existing_cache_only",
        "collector_restored": False, "d3_started": False,
        "step_minutes": step_minutes,
        "time_semantics": "Wall age uses received_at only. Accelerated device_time is not wall time. No live API tail comparison was performed.",
        "ha_connections": 0, "device_writes": 0, "caches": reports}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = audit(args.data_root, datetime.now(timezone.utc))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"caches": len(result["caches"]), "output": str(args.output)}, ensure_ascii=True))
