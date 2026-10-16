import csv
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(r"D:\Anker黑客松\实验环境")
audit = ROOT / "outputs" / "local_mock_20261016_run6" / "audit.csv"
rows = list(csv.DictReader(audit.open(encoding="utf-8")))

# per-site solver_status and solve_elapsed distribution
by_site_status = defaultdict(Counter)
elapsed = defaultdict(list)
for r in rows:
    by_site_status[r["site"]][r.get("solver_status")] += 1
    try:
        e = float(r.get("solve_elapsed_seconds") or "nan")
    except ValueError:
        continue
    if e == e:  # not nan
        elapsed[r["site"]].append(e)

print("=== solver_status per site ===")
for site in sorted(by_site_status):
    print(site, dict(by_site_status[site]))

print("\n=== solve_elapsed stats per site ===")
import statistics
for site in sorted(elapsed):
    v = sorted(elapsed[site])
    n = len(v)
    print(f"site={site} n={n} p50={v[n//2]:.3f} p90={v[int(n*0.9)]:.3f} "
          f"p99={v[int(n*0.99)]:.3f} max={v[-1]:.3f} "
          f"count>=1.5s={sum(1 for x in v if x >= 1.5)}")

def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return float("nan")

print("\n=== site=2 slow solves (>=1.5s): device_time + status + mip_gap ===")
slow = [r for r in rows if r["site"] == "2" and _f(r.get("solve_elapsed_seconds")) >= 1.5]
print("count:", len(slow), "of", sum(1 for r in rows if r["site"] == "2"))
for r in slow:
    print(f"  dt={r['device_time'][:16]} status={r['status']} solver={r.get('solver_status')} "
          f"mip_gap={r.get('mip_gap')} accepted={r.get('accepted_incumbent')} "
          f"soc={r.get('observed_soc')} pv={r.get('observed_pv')} load={r.get('observed_load')}")

print("\n=== limit_reached rows (all sites) ===")
for r in rows:
    if r.get("solver_status") == "limit_reached":
        print(f"  site={r['site']} dt={r['device_time'][:16]} status={r['status']} "
              f"mip_gap={r.get('mip_gap')} accepted={r.get('accepted_incumbent')} "
              f"solve={r.get('solve_elapsed_seconds')} soc={r.get('observed_soc')}")

print("\n=== delivery_blocked rounds: which site had slow solve? ===")
blocked_rounds = defaultdict(list)
for r in rows:
    if r["status"] == "delivery_blocked":
        blocked_rounds[r["device_time"][:16]].append((r["site"], _f(r.get("solve_elapsed_seconds"))))
for dt, entries in sorted(blocked_rounds.items()):
    print(f"  {dt}: {entries}")
