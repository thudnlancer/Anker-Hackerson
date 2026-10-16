import csv
import json
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(r"D:\Anker黑客松\实验环境")
audit = ROOT / "outputs" / "local_mock_20261016_run6" / "audit.csv"

rows = list(csv.DictReader(audit.open(encoding="utf-8")))
print(f"total audit rows: {len(rows)}")
print(f"rounds per site: {len(rows)//3}")

print("\n=== status distribution (full run) ===")
for k, v in Counter(r["status"] for r in rows).most_common():
    print(f"  {k:28s} {v}")

print("\n=== per-site status ===")
for site in ("1", "2", "3"):
    sr = [r for r in rows if r["site"] == site]
    print(f"  site{site}: " + ", ".join(f"{k}={v}" for k, v in Counter(r['status'] for r in sr).most_common()))

print("\n=== requires_reconciliation / freeze timeline ===")
recon = [r for r in rows if r.get("requires_reconciliation") == "True" or r.get("status") == "reconciliation_required"]
by_site = defaultdict(list)
for r in recon:
    by_site[r["site"]].append(r.get("device_time"))
for site in ("1", "2", "3"):
    dts = by_site.get(site, [])
    print(f"  site{site}: {len(dts)} rows, first={dts[0] if dts else '-'} last={dts[-1] if dts else '-'}")

# rounds where site refused due to previous delivery unknown (frozen)
print("\n=== reconciliation_required (refused after freeze) per site ===")
for site in ("1", "2", "3"):
    rr = [r for r in rows if r["site"] == site and r["status"] == "reconciliation_required"]
    print(f"  site{site}: {len(rr)} rounds refused, device_time {rr[0]['device_time'] if rr else '-'} -> {rr[-1]['device_time'] if rr else '-'}")

# round count per site by status after freeze
print("\n=== total rounds (should be ~672 for 15-min step over 7 days) ===")
by_site_gen = {s: set() for s in ("1","2","3")}
for r in rows:
    by_site_gen[r["site"]].add(r.get("round_generation"))
for s in ("1","2","3"):
    print(f"  site{s}: {len(by_site_gen[s])} distinct generations")

# optimizer_attributed vs not
print("\n=== optimizer_attributed ===")
print(dict(Counter(r.get("optimizer_attributed") for r in rows)))
