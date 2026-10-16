import csv
from pathlib import Path

ROOT = Path(r"D:\Anker黑客松\实验环境")
audit = ROOT / "outputs" / "local_mock_20261016_run6" / "audit.csv"
rows = list(csv.DictReader(audit.open(encoding="utf-8")))

# Timeline around the two freeze rounds: day5 18:00 (site1+2 fail) and 19:30 (site3 fail)
print("=== rounds around 2026-10-14 17:45 .. 20:00 (site-level) ===")
for r in rows:
    dt = r["device_time"]
    if dt[:10] == "2026-10-14" and "17:" <= dt[11:16] <= "20:":
        print(f"site={r['site']} dt={dt[11:16]} status={r['status']:<24} "
              f"align_elapsed={r.get('alignment_elapsed_seconds'):<8} "
              f"align_stop={r.get('alignment_stop_reason'):<28} "
              f"solve={r.get('solve_elapsed_seconds'):<8} "
              f"round_elapsed={r.get('round_elapsed_seconds'):<8} "
              f"reason={r.get('reason')}")
