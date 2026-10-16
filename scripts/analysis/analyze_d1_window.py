"""Summarize live collector quality; historical gaps are not a strategy hard gate."""
from datetime import datetime, timezone
from pathlib import Path
import argparse
import json


def parse(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def analyze(cache_dir: Path, heartbeat_path: Path, log_path: Path, now=None):
    now = now or datetime.now(timezone.utc)
    heartbeat = json.loads(heartbeat_path.read_text(encoding="utf-8-sig"))
    clock_sets = {}
    sites = {}
    for site in ("1", "2", "3"):
        path = cache_dir / f"site_{site}.nodeA.jsonl"
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        times = sorted({parse(row["device_time"]) for row in rows})
        gaps = [round((b-a).total_seconds()/60) for a, b in zip(times, times[1:])
                if (b-a).total_seconds() > 900]
        missing = {a.timestamp()+900*i for a, b in zip(times, times[1:])
                   for i in range(1, round((b-a).total_seconds()/900))}
        clock_sets[site] = missing
        newest = max(rows, key=lambda row: parse(row["received_at"]))
        sites[site] = {"rows": len(rows), "unique_ticks": len(times),
            "first_device_time": times[0].isoformat(), "last_device_time": times[-1].isoformat(),
            "last_received_at": newest["received_at"],
            "wall_age_seconds": round((now-parse(newest["received_at"])).total_seconds(), 2),
            "gap_count": len(gaps), "missing_tick_count": len(missing),
            "expected_ticks_between_first_last": round((times[-1]-times[0]).total_seconds()/900)+1,
            "tick_coverage_ratio": round(len(times)/(round((times[-1]-times[0]).total_seconds()/900)+1), 4),
            "max_gap_minutes": max(gaps, default=0),
            "tail_gap_minutes": round((times[-1]-times[-2]).total_seconds()/60) if len(times)>1 else None,
            "device_time_backwards_in_file": sum(parse(b["device_time"]) < parse(a["device_time"])
                for a, b in zip(rows, rows[1:])),
            "irradiance_coverage": sum(row.get("solar_irradiance_wm2") is not None for row in rows)}
    skew = round((max(parse(v["last_device_time"]) for v in sites.values())-
                  min(parse(v["last_device_time"]) for v in sites.values())).total_seconds()/60)
    cycles = []
    for line in log_path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if "iteration" in row:
            cycles.append(row)
    overlap = set.intersection(*clock_sets.values())
    union = set.union(*clock_sets.values())
    heartbeat_age = round((now-parse(heartbeat["last_cycle_at"])).total_seconds(), 2)
    fresh = 0 <= heartbeat_age <= 15 and all(s["wall_age_seconds"] <= 15 for s in sites.values())
    usable = fresh and skew <= 15 and heartbeat.get("consecutive_errors") == 0
    return {"checked_at": now.isoformat(), "mode": "read_only_d1_quality",
        "heartbeat": {"pid": heartbeat["pid"], "cycle": heartbeat["cycle"],
            "age_seconds": heartbeat_age, "errors": heartbeat["consecutive_errors"],
            "writes_performed": heartbeat["writes_performed"]},
        "sites": sites, "latest_cross_site_skew_minutes": skew,
        "cycle_count": len(cycles),
        "unaligned_cycle_count": sum(not row.get("device_times_aligned", True) for row in cycles),
        "over_interval_count": sum(row.get("over_interval", False) for row in cycles),
        "gap_classification": {"shared_missing_ticks": len(overlap),
            "site_specific_missing_ticks": {site: len(missing-overlap) for site, missing in clock_sets.items()},
            "any_missing_ticks": len(union)},
        "history_quality": "imperfect_but_usable_for_context" if all(
            s["tick_coverage_ratio"] >= 0.8 and s["max_gap_minutes"] <= 45 and
            s["device_time_backwards_in_file"] == 0 for s in sites.values()) else "degraded",
        "basic_fresh_and_continuous": usable,
        "strategy_input": "usable_current_api_ha" if usable else "degrade_to_current_per_site_or_safe_fallback",
        "degradation": {
            "historical_isolated_gaps": "Do not block API-first decisions; exclude missing ticks from model fitting and lower forecast confidence.",
            "snapshot_boundary_skew_up_to_15_min": "Use each station's own HA device_time and current PV/load; do not join the three stations by a shared snapshot tick.",
            "skew_over_15_min_or_stale_over_15_sec": "Treat affected station history as degraded; use current valid HA state and API prices/weather, or safety fallback if current state is stale.",
            "consecutive_or_large_gaps": "Disable history-dependent forecast for affected site; keep API-first strategy and safe fallback available."},
        "d3_started": False}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache-dir", type=Path, required=True)
    parser.add_argument("--heartbeat", type=Path, required=True)
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = analyze(args.cache_dir, args.heartbeat, args.log)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"basic_fresh_and_continuous": report["basic_fresh_and_continuous"],
                      "site_ages": {k:v["wall_age_seconds"] for k,v in report["sites"].items()},
                      "skew_minutes": report["latest_cross_site_skew_minutes"]}, ensure_ascii=False))
