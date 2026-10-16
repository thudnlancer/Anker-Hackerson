import csv
from pathlib import Path

ROOT = Path(r"D:\Anker黑客松\实验环境")
audit = ROOT / "outputs" / "local_mock_20261016_run6" / "audit.csv"
rows = list(csv.DictReader(audit.open(encoding="utf-8")))

keys = ["site", "device_time", "status", "reason",
        "alignment_budget_seconds", "alignment_elapsed_seconds", "alignment_stop_reason",
        "alignment_attempts", "solve_elapsed_seconds", "round_elapsed_seconds",
        "round_deadline_seconds", "serial_tick_fits_poll"]

for r in rows:
    if r["status"] in ("delivery_failed", "delivery_blocked") or (
        r["device_time"][:16] in ("2026-10-14 17:45", "2026-10-14 18:00", "2026-10-14 19:30")
    ):
        vals = {k: r.get(k) for k in keys}
        print(vals)
