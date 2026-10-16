import csv
import statistics
from collections import defaultdict
from pathlib import Path

ROOT = Path(r"D:\Anker黑客松\实验环境")
audit = ROOT / "outputs" / "local_mock_20261016_run6" / "audit.csv"
rows = list(csv.DictReader(audit.open(encoding="utf-8")))

# per site: device_time sequence and round_generation
for site in ("1", "2", "3"):
    sr = [r for r in rows if r["site"] == site]
    gens = [int(float(r["round_generation"])) for r in sr if r.get("round_generation") not in ("", None)]
    dts = [r["device_time"][:16] for r in sr if r.get("device_time")]
    print(f"site{site}: {len(sr)} rows, gen {min(gens)}..{max(gens)}, "
          f"device_time {dts[0]} .. {dts[-1]}")
    # count generation gaps
    gen_set = set(gens)
    gaps = [g for g in range(min(gens), max(gens) + 1) if g not in gen_set]
    print(f"  gen gaps (skipped generations): {len(gaps)}  e.g. {gaps[:10]}")

# round_elapsed distribution full run
elapsed = [float(r["round_elapsed_seconds"]) for r in rows if r.get("round_elapsed_seconds")]
elapsed = [e for e in elapsed if e > 0]
print(f"\nround_elapsed: n={len(elapsed)} p50={statistics.median(elapsed):.2f} "
      f"p90={statistics.quantiles(elapsed, n=10)[8]:.2f} max={max(elapsed):.2f} "
      f"over_5s={sum(1 for e in elapsed if e > 5)} over_4.5s={sum(1 for e in elapsed if e > 4.5)}")

# device_time gaps within a site (detect skipped 15-min slots)
from datetime import datetime
def parse_dt(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))
for site in ("1",):
    sr = sorted(rows, key=lambda r: int(r["round_generation"]))
    sr = [r for r in sr if r["site"] == site]
    skips = 0
    prev = None
    for r in sr:
        cur = parse_dt(r["device_time"])
        if prev is not None:
            delta_min = (cur - prev).total_seconds() / 60
            if delta_min > 16:  # more than one 15-min step
                skips += 1
                if skips <= 15:
                    print(f"  SKIP at gen={r['round_generation']}: {prev} -> {cur} (delta {delta_min:.0f} min)")
        prev = cur
    print(f"\nsite1 total device_time skips (>16min): {skips}")
