"""Build standardized offline-test-field epoch datasets from a capture directory.

The simulator timeline free-runs while idle (resets happen only on contest
restarts), so epochs are partitioned here: fixed-length windows over the
longest continuous segment.  With fewer ticks than min_epochs * epoch length
the windows slide with an overlap that is documented in the manifest (never
silently).

Layout written to --output (default data/epochs):

    manifest.json                 dataset-level provenance + per-epoch stats
    epoch_00/site_1.csv           15-min rows: time, pv_w, load_w, soc_percent,
                                  import_price, export_price, weather fields
    ...

Hard requirements per epoch x site: 15-minute continuity, one row per tick,
non-null import/export price on every tick.  Violations abort the build.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts.analysis.validate_capture import load_site_rows, segment_rows

COLUMNS = ["time", "pv_w", "load_w", "soc_percent", "import_price", "export_price",
           "temperature_c", "cloud_cover_percent", "solar_irradiance_wm2", "filled"]
ROW_MAP = {"time": "device_time", "pv_w": "pv", "load_w": "load", "soc_percent": "soc"}
NUMERIC_FILL = ["pv", "load", "soc", "temperature_c", "cloud_cover_percent",
                "solar_irradiance_wm2"]


def repair_missing_prices(rows: list[dict]) -> tuple[list[dict], int]:
    """Copy hourly prices onto ticks whose price is missing, from another tick
    in the same day+hour (prices are hour-aligned, so this is exact, not an
    interpolation).  Ticks with no priced sibling in their hour keep None and
    are rejected downstream.
    """
    by_hour: dict[tuple, dict] = {}
    for row in rows:
        if row.get("import_price") is None or row.get("export_price") is None:
            continue
        stamp = datetime.fromisoformat(row["device_time"])
        by_hour.setdefault((stamp.date(), stamp.hour), row)
    repaired = 0
    for row in rows:
        if row.get("import_price") is not None and row.get("export_price") is not None:
            continue
        stamp = datetime.fromisoformat(row["device_time"])
        donor = by_hour.get((stamp.date(), stamp.hour))
        if donor is None:
            continue
        row["import_price"] = donor["import_price"]
        row["export_price"] = donor["export_price"]
        repaired += 1
    return rows, repaired


def fill_segment_gaps(rows: list[dict]) -> tuple[list[dict], set[str]]:
    """Fill isolated missing 15-min ticks across a whole segment by linear
    interpolation; returns (rows, set of filled stamps).  Prices are
    hour-aligned, so a missing tick copies the neighbor sharing its hour.
    Gaps at the segment edge or consecutive gaps are left unfilled (the
    per-window continuity check rejects them later).  Filled rows are flagged
    filled=True; no extrapolation is ever performed.
    """
    by_time = {row["device_time"]: dict(row) for row in rows}
    stamps = sorted(by_time)
    expected = []
    current = datetime.fromisoformat(stamps[0])
    end = datetime.fromisoformat(stamps[-1])
    while current <= end:
        expected.append(current)
        current += timedelta(minutes=15)
    filled: set[str] = set()
    for stamp in expected:
        key = stamp.isoformat()
        if key in by_time:
            continue
        prev_row = by_time.get((stamp - timedelta(minutes=15)).isoformat())
        next_row = by_time.get((stamp + timedelta(minutes=15)).isoformat())
        if prev_row is None or next_row is None:
            continue  # edge or consecutive gap: cannot interpolate
        row = {"device_time": key, "site": prev_row["site"], "filled": True}
        for field in NUMERIC_FILL:
            row[field] = (float(prev_row[field]) + float(next_row[field])) / 2
        for price_field in ("import_price", "export_price"):
            if prev_row[price_field] is None or next_row[price_field] is None:
                row[price_field] = None
            elif stamp.hour == (stamp - timedelta(minutes=15)).hour:
                row[price_field] = prev_row[price_field]
            elif stamp.hour == (stamp + timedelta(minutes=15)).hour:
                row[price_field] = next_row[price_field]
            else:
                row[price_field] = (float(prev_row[price_field])
                                    + float(next_row[price_field])) / 2
        by_time[key] = row
        filled.add(key)
    return [by_time[key] for key in (s.isoformat() for s in expected) if key in by_time], filled


def code_revision() -> str | None:
    try:
        out = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, timeout=10,
                             cwd=Path(__file__).resolve().parents[2])
        return out.stdout.decode().strip() or None if out.returncode == 0 else None
    except Exception:
        return None


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def longest_segment_rows(cache_dir: Path, site: str) -> list[dict]:
    segments = [s for s in segment_rows(load_site_rows(cache_dir, site)) if len(s) > 1]
    if not segments:
        raise ValueError(f"site {site}: no continuous segment found")
    return max(segments, key=len)


def dedup_sorted(rows: list[dict]) -> list[dict]:
    by_time = {}
    for row in rows:
        by_time[row["device_time"]] = row
    return [by_time[key] for key in sorted(by_time)]


def epoch_starts(n_ticks: int, length: int, min_epochs: int) -> list[int]:
    """Start offsets; sliding with documented overlap when data is short."""
    if n_ticks >= length * min_epochs:
        return [i * length for i in range(min_epochs)]
    if n_ticks < length:
        raise ValueError(f"segment has {n_ticks} ticks, shorter than one epoch ({length})")
    stride = (n_ticks - length) // (min_epochs - 1)
    return [i * stride for i in range(min_epochs - 1)] + [n_ticks - length]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("capture_dir", type=Path)
    parser.add_argument("--output", type=Path, default=Path("data/epochs"))
    parser.add_argument("--sites", nargs="+", default=["1", "2", "3"])
    parser.add_argument("--epoch-hours", type=float, default=168.0)
    parser.add_argument("--min-epochs", type=int, default=5)
    parser.add_argument("--max-fill-ticks", type=int, default=0,
                        help="allow filling up to this many isolated missing 15-min ticks "
                             "per epoch window by interpolation (flagged filled=True)")
    args = parser.parse_args()
    if args.epoch_hours <= 0 or args.min_epochs < 1:
        parser.error("epoch-hours must be positive and min-epochs >= 1")
    if args.max_fill_ticks < 0:
        parser.error("max-fill-ticks must be nonnegative")
    length_ticks = int(round(args.epoch_hours * 4))
    if length_ticks < 1:
        parser.error("epoch must cover at least one 15-minute tick")

    cache_dir = args.capture_dir / "cache"
    per_site: dict[str, list[dict]] = {}
    filled_stamps: dict[str, set[str]] = {}
    for site in args.sites:
        rows, filled = fill_segment_gaps(dedup_sorted(longest_segment_rows(cache_dir, site)))
        rows, repaired = repair_missing_prices(rows)
        if repaired:
            print(f"site {site}: repaired {repaired} ticks with hour-aligned prices")
        first_priced = next((i for i, row in enumerate(rows)
                             if row.get("import_price") is not None
                             and row.get("export_price") is not None), None)
        if first_priced is None:
            raise ValueError(f"site {site}: no priced ticks at all")
        if first_priced:
            print(f"site {site}: trimmed {first_priced} leading price-less ticks")
        per_site[site] = rows[first_priced:]
        filled_stamps[site] = filled
    # align all sites to a common time range (leading trims differ per site)
    common_start = max(datetime.fromisoformat(rows[0]["device_time"]) for rows in per_site.values())
    common_end = min(datetime.fromisoformat(rows[-1]["device_time"]) for rows in per_site.values())
    for site, rows in per_site.items():
        per_site[site] = [row for row in rows
                          if common_start <= datetime.fromisoformat(row["device_time"]) <= common_end]
    reference = max(per_site, key=lambda s: len(per_site[s]))
    n_ticks = len(per_site[reference])
    starts = epoch_starts(n_ticks, length_ticks, args.min_epochs)
    overlap = any(b - a < length_ticks for a, b in zip(starts, starts[1:]))

    args.output.mkdir(parents=True, exist_ok=True)
    manifest = {"source_capture": str(args.capture_dir), "code_sha256": code_revision(),
                "reference_site": reference, "segment_ticks": n_ticks,
                "segment_start": per_site[reference][0]["device_time"],
                "segment_end": per_site[reference][-1]["device_time"],
                "epoch_hours": args.epoch_hours, "ticks_per_epoch": length_ticks,
                "overlap": overlap, "epochs": []}

    for epoch_index, start in enumerate(starts):
        epoch_dir = args.output / f"epoch_{epoch_index:02d}"
        epoch_dir.mkdir(parents=True, exist_ok=True)
        epoch_info: dict = {"index": epoch_index, "sites": {}}
        ref = per_site[reference]
        window_start = datetime.fromisoformat(ref[start]["device_time"])
        window_end = window_start + (length_ticks - 1) * timedelta(minutes=15)
        for site, rows in per_site.items():
            window = [row for row in rows
                      if window_start <= datetime.fromisoformat(row["device_time"]) <= window_end]
            filled = sum(1 for row in window if row["device_time"] in filled_stamps[site])
            if filled > args.max_fill_ticks:
                raise ValueError(f"epoch {epoch_index} site {site}: {filled} filled ticks "
                                 f"exceed limit {args.max_fill_ticks}")
            if len(window) != length_ticks:
                raise ValueError(f"epoch {epoch_index} site {site}: "
                                 f"{len(window)} rows, expected {length_ticks}")
            stamps = [row["device_time"] for row in window]
            base = datetime.fromisoformat(stamps[0])
            expected = [(base + i * timedelta(minutes=15)).isoformat()
                        for i in range(length_ticks)]
            if stamps != expected:
                raise ValueError(f"epoch {epoch_index} site {site}: 15-min continuity broken")
            missing_price = [s for row, s in zip(window, stamps)
                             if row.get("import_price") is None or row.get("export_price") is None]
            if missing_price:
                raise ValueError(f"epoch {epoch_index} site {site}: missing prices at "
                                 f"{missing_price[:3]} ({len(missing_price)} total)")
            out = epoch_dir / f"site_{site}.csv"
            header = ",".join(COLUMNS)
            lines = [header]
            for row in window:
                lines.append(",".join(str(row.get(ROW_MAP.get(c, c), "")) for c in COLUMNS))
            out.write_text("\n".join(lines) + "\n", encoding="utf-8")
            epoch_info["sites"][site] = {"rows": length_ticks, "filled": filled,
                                         "sha256": digest(out)}
        epoch_info["start"] = per_site[reference][start]["device_time"]
        epoch_info["end"] = per_site[reference][start + length_ticks - 1]["device_time"]
        manifest["epochs"].append(epoch_info)
        print(f"epoch {epoch_index}: {epoch_info['start']} .. {epoch_info['end']} "
              f"(filled: {sum(s['filled'] for s in epoch_info['sites'].values())})")

    (args.output / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"built {len(starts)} epochs (overlap={overlap}) -> {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
