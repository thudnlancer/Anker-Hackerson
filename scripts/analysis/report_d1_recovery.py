"""Summarize a live read-only D1 collector without contacting HA."""
from datetime import datetime, timezone
from pathlib import Path
import argparse
import json
import os
import subprocess

from audit_w3_cache import audit, stamp


def summarize(data_root: Path, health_dir: Path, node_id: str = "nodeA") -> dict:
    now = datetime.now(timezone.utc)
    heartbeat = json.loads((health_dir / f"heartbeat_{node_id}.json").read_text(encoding="utf-8-sig"))
    pid = heartbeat.get("pid")
    process_alive = False
    if pid and os.name == "nt":
        probe = subprocess.run(["powershell", "-NoProfile", "-Command",
            f"(Get-CimInstance Win32_Process -Filter 'ProcessId={int(pid)}').CommandLine"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10)
        command = probe.stdout.strip()
        process_alive = probe.returncode == 0 and "collect_online_history.py" in command and \
            f"--node-id {node_id}" in command
    checked = audit(data_root, now)
    by_name = {Path(item["directory"]).name: item for item in checked["caches"]}
    current = by_name.get("live_history_d1_20261014", {"sites": []})
    legacy = by_name.get("live_history_post_reboot_20261013", {"sites": []})
    legacy_sites = {site["site"]: site for site in legacy["sites"]}
    sites = []
    for site in current["sites"]:
        old = legacy_sites.get(site["site"], {})
        old_file = old.get("shards", {}).get(f"site_{site['site']}.jsonl", {})
        old_tail = stamp(old_file.get("last_device_time"))
        new_start = stamp(site.get("device_start"))
        sites.append({"site": site["site"], "new_rows": site["valid_rows"],
            "weather_missing_rows": site["valid_rows"] - site.get("irradiance_rows", 0),
            "last_received_at": site["last_received_at"],
            "wall_age_seconds": site["wall_age_seconds"],
            "new_unique_device_ticks": site["unique_device_ticks"],
            "new_device_start": site["device_start"], "new_device_end": site["device_end"],
            "new_device_gap_count": site["device_gap_count"],
            "old_legacy_tail": old_file.get("last_device_time"),
            "old_to_new_device_delta_minutes":
                (new_start-old_tail).total_seconds()/60 if new_start and old_tail else None,
            "continuous_with_old_cache": bool(new_start and old_tail and
                                              (new_start-old_tail).total_seconds() in (0, 900))})
    heartbeat_age = (now-stamp(heartbeat.get("last_cycle_at"))).total_seconds()
    return {"checked_at": now.isoformat(), "mode": "read_only_live_collector",
        "node_id": node_id, "heartbeat": heartbeat, "heartbeat_age_seconds": heartbeat_age,
        "process_alive": process_alive,
        "current_heartbeat_confirmed": process_alive and pid == heartbeat.get("pid") and
            0 <= heartbeat_age <= 15 and heartbeat.get("last_success_at") is not None and
            heartbeat.get("consecutive_errors") == 0 and heartbeat.get("writes_performed") == 0,
        "site_coverage": sorted(s["site"] for s in sites),
        "missing_sites": sorted({"1", "2", "3"}-{s["site"] for s in sites}),
        "current_station_diagnostics": heartbeat.get("station_diagnostics", {}),
        "device_time_stalled": bool(sites) and all(s["new_unique_device_ticks"] <= 1 for s in sites),
        "device_time_aligned_across_sites": len({s["new_device_end"] for s in sites}) <= 1,
        "sites": sites, "d3_started": False,
        "warning": "Current device time is an accelerated simulation clock. Old 2028 cache and new 2026 data are separate epochs; do not merge into contiguous history."}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, default=Path("data"))
    parser.add_argument("--health-dir", type=Path, default=Path("outputs/collector_health"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = summarize(args.data_root, args.health_dir)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"heartbeat_confirmed": result["current_heartbeat_confirmed"],
                      "sites": result["site_coverage"], "output": str(args.output)}, ensure_ascii=True))
