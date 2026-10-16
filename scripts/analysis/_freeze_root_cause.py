import csv
from collections import Counter
from pathlib import Path

ROOT = Path(r"D:\Anker黑客松\实验环境")
audit = ROOT / "outputs" / "local_mock_20261016_run6" / "audit.csv"
rows = list(csv.DictReader(audit.open(encoding="utf-8")))

print("total rows", len(rows))
print("columns:", list(rows[0].keys()))
print()
print("=== status counts ===")
print(Counter(r["status"] for r in rows))
print()
print("=== delivery_failed rows ===")
for r in rows:
    if r["status"] == "delivery_failed":
        print(f"site={r['site']} dt={r['device_time']} reason={r.get('reason')} "
              f"delivery_error={r.get('delivery_error')} round_elapsed={r.get('round_elapsed_seconds')} "
              f"solve={r.get('solve_elapsed_seconds')}")
        print(f"    steps={r.get('delivery_steps')}")
print()
print("=== delivery_blocked rows ===")
for r in rows:
    if r["status"] == "delivery_blocked":
        print(f"site={r['site']} dt={r['device_time']} reason={r.get('reason')} "
              f"round_elapsed={r.get('round_elapsed_seconds')}")
print()
# round_elapsed distribution near the freeze rounds
print("=== round_elapsed for freeze device_times (day5 18:00 / 19:30) ===")
for r in rows:
    dt = r["device_time"][:16]
    if dt in ("2026-10-14 18:00", "2026-10-14 19:30"):
        print(f"site={r['site']} dt={dt} status={r['status']} "
              f"elapsed={r.get('round_elapsed_seconds')} solve={r.get('solve_elapsed_seconds')} "
              f"reason={r.get('reason')}")
