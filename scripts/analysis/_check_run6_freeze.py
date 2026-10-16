import csv
from collections import Counter
from pathlib import Path

ROOT = Path(r"D:\Anker黑客松\实验环境")
audit = ROOT / "outputs" / "local_mock_20261016_run6" / "audit.csv"
rows = list(csv.DictReader(audit.open(encoding="utf-8")))

# delivery_failed (freeze) rows detail
print("=== delivery_failed (freeze) rows ===")
for r in rows:
    if r["status"] == "delivery_failed":
        print(f"  site={r['site']} device_time={r['device_time']} "
              f"reason={r.get('reason')} round_elapsed={r.get('round_elapsed_seconds')} "
              f"solver={r.get('solver_status')}")

# round_elapsed of delivery_failed vs surrounding
print("\n=== round_elapsed for freeze round (day4 18:00 / 19:30) ===")
for r in rows:
    if r["device_time"][:16] in ("2026-10-14 18:00", "2026-10-14 19:30"):
        print(f"  site={r['site']} dt={r['device_time'][:16]} status={r['status']} "
              f"elapsed={r.get('round_elapsed_seconds')} solver={r.get('solver_status')}")
