import csv
import statistics
from pathlib import Path

ROOT = Path(r"D:\Anker黑客松\实验环境")
rows = list(csv.DictReader((ROOT / "outputs/local_mock_20261016_run6/audit.csv").open(encoding="utf-8")))

def f(v):
    try:
        x = float(v)
        return x if x >= 0 else None
    except Exception:
        return None

def dist(name, values):
    vals = [v for v in values if v is not None]
    if not vals:
        print(f"{name}: (none)")
        return
    q = statistics.quantiles(vals, n=10) if len(vals) >= 10 else []
    p90 = q[8] if q else None
    print(f"{name:28s} n={len(vals):4d} min={min(vals):.3f} p50={statistics.median(vals):.3f} "
          f"p90={p90:.3f} max={max(vals):.3f}" if p90 else
          f"{name:28s} n={len(vals):4d} min={min(vals):.3f} p50={statistics.median(vals):.3f} max={max(vals):.3f}")

# overall
print("=== full run time breakdown (seconds) ===")
dist("solve_elapsed_seconds", [f(r["solve_elapsed_seconds"]) for r in rows])
dist("alignment_elapsed_seconds", [f(r["alignment_elapsed_seconds"]) for r in rows])
dist("round_elapsed_seconds", [f(r["round_elapsed_seconds"]) for r in rows])

# only dispatched (solver actually ran)
disp = [r for r in rows if r["status"] == "dispatched"]
print(f"\n=== dispatched only (n={len(disp)}) ===")
dist("  solve_elapsed_seconds", [f(r["solve_elapsed_seconds"]) for r in disp])
dist("  alignment_elapsed_seconds", [f(r["alignment_elapsed_seconds"]) for r in disp])
dist("  round_elapsed_seconds", [f(r["round_elapsed_seconds"]) for r in disp])

# solver_status distribution
from collections import Counter
print("\n=== solver_status ===")
print(dict(Counter(r["solver_status"] for r in rows)))

# limit_reached rounds timing
lr = [r for r in rows if r["solver_status"] == "limit_reached"]
print(f"\n=== limit_reached rounds (n={len(lr)}) ===")
for r in lr[:8]:
    print(f"  site={r['site']} dt={r['device_time'][:16]} solve={r['solve_elapsed_seconds']} "
          f"round={r['round_elapsed_seconds']} status={r['status']}")

# solve time when dispatched: is 2s limit ever hit?
solve_times = [f(r["solve_elapsed_seconds"]) for r in disp]
solve_times = [s for s in solve_times if s is not None]
print(f"\n=== dispatched solve time > 1.5s count: {sum(1 for s in solve_times if s > 1.5)} / {len(solve_times)} ===")
