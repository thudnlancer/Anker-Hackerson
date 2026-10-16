"""Validate a contest capture directory for offline replay use (milestone stage 0).

Segments the raw append-order tick stream into epochs at backward device_time
jumps (simulator timeline resets), then checks one segment (default: the last
complete one) for:

- per-site 15-minute continuity with no gaps or duplicates after dedup
- price columns present and non-null on every tick
- weather availability rate (informational)

Usage:
    python scripts/analysis/validate_capture.py <capture_dir> [--epoch N] [--json]

Exit code 0 when the selected segment passes all hard checks, 1 otherwise.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta
import json
from pathlib import Path
import sys

TICK = timedelta(minutes=15)


def load_site_rows(cache_dir: Path, site: str) -> list[dict]:
    rows: list[dict] = []
    for shard in sorted(cache_dir.glob(f"site_{site}.*.jsonl")):
        for line in shard.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(json.loads(line))
    return rows


def segment_rows(rows: list[dict]) -> list[list[dict]]:
    """Split append-order rows at backward (or equal) device_time jumps."""
    segments: list[list[dict]] = []
    current: list[dict] = []
    previous: datetime | None = None
    for row in rows:
        stamp = datetime.fromisoformat(row["device_time"])
        if previous is not None and stamp <= previous:
            segments.append(current)
            current = []
        current.append(row)
        previous = stamp
    if current:
        segments.append(current)
    return segments


def check_segment(segment: list[dict]) -> dict:
    """Hard checks on one epoch segment of a single site."""
    by_time: dict[datetime, dict] = {}
    duplicates = 0
    for row in segment:
        stamp = datetime.fromisoformat(row["device_time"])
        if stamp in by_time:
            duplicates += 1
        by_time[stamp] = row
    stamps = sorted(by_time)
    gaps = [(a.isoformat(), b.isoformat())
            for a, b in zip(stamps, stamps[1:]) if b - a != TICK]
    missing_price = [s.isoformat() for s in stamps
                     if by_time[s].get("price_status") != "ok"
                     or by_time[s].get("import_price") is None]
    weather_missing = sum(1 for s in stamps if by_time[s].get("weather_status") != "valid")
    span_hours = (stamps[-1] - stamps[0]).total_seconds() / 3600 if stamps else 0.0
    return {"start": stamps[0].isoformat() if stamps else None,
            "end": stamps[-1].isoformat() if stamps else None,
            "ticks": len(stamps), "span_hours": round(span_hours, 2),
            "duplicates_dropped": duplicates,
            "gaps": gaps, "ticks_missing_price": missing_price,
            "ticks_missing_weather": weather_missing,
            "ok": not gaps and not missing_price and bool(stamps)}


def segment_summary(segment: list[dict]) -> str:
    stamps = [datetime.fromisoformat(row["device_time"]) for row in segment]
    return f"{stamps[0]} .. {stamps[-1]}  rows={len(segment)}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("capture_dir", type=Path,
                        help="capture directory containing cache/ and price_windows.jsonl")
    parser.add_argument("--epoch", type=int, default=None,
                        help="segment index to validate (0-based, append order); "
                             "default: last segment with >1 tick")
    parser.add_argument("--sites", nargs="+", default=["1", "2", "3"])
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args()

    cache_dir = args.capture_dir / "cache"
    if not cache_dir.is_dir():
        print(f"no cache dir: {cache_dir}")
        return 1

    per_site_segments = {site: segment_rows(load_site_rows(cache_dir, site))
                         for site in args.sites}
    reference = max(per_site_segments, key=lambda s: len(per_site_segments[s]))
    segments = per_site_segments[reference]
    if not args.json:
        print(f"reference site {reference}: {len(segments)} segment(s)")
        for index, segment in enumerate(segments):
            print(f"  segment {index}: {segment_summary(segment)}")

    if args.epoch is not None:
        epoch_index = args.epoch
    else:
        epoch_index = next((i for i in range(len(segments) - 1, -1, -1)
                            if len(segments[i]) > 1), None)
    if epoch_index is None or epoch_index >= len(segments):
        print("no such segment")
        return 1

    results: dict[str, dict] = {}
    all_ok = True
    for site in args.sites:
        site_segments = per_site_segments[site]
        if epoch_index >= len(site_segments):
            results[site] = {"ok": False, "error": "segment missing for site"}
            all_ok = False
            continue
        result = check_segment(site_segments[epoch_index])
        results[site] = result
        all_ok = all_ok and result["ok"]

    if args.json:
        print(json.dumps({"epoch": epoch_index, "sites": results, "ok": all_ok},
                         ensure_ascii=False, indent=2))
    else:
        print(f"validating segment {epoch_index}:")
        for site, result in results.items():
            status = "OK " if result["ok"] else "FAIL"
            print(f"  [{status}] site {site}: {result.get('ticks', 0)} ticks, "
                  f"{result.get('span_hours', 0)}h, gaps={len(result.get('gaps', []))}, "
                  f"missing_price={len(result.get('ticks_missing_price', []))}, "
                  f"missing_weather={result.get('ticks_missing_weather', 0)}")
            for gap in result.get("gaps", [])[:5]:
                print(f"        gap: {gap[0]} -> {gap[1]}")
            for stamp in result.get("ticks_missing_price", [])[:5]:
                print(f"        no price: {stamp}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
