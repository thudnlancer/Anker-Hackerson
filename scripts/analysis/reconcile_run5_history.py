"""Reconcile run5 audit device_time/price-slots against contest history times and values."""
import json
import csv
from pathlib import Path

BASE = Path("outputs/local_mock_20261016_run5")


def main() -> None:
    hist = {}
    for s in (1, 2, 3):
        hist[s] = json.loads((BASE / f"contest_history_site{s}.json").read_text(encoding="utf-8"))
    pts1 = {p["time"]: p for p in hist[1]["points"]}
    times = sorted(pts1.keys())
    print("history site1 time range:", times[0], "->", times[-1], f"({len(times)} pts)")

    rows = list(csv.DictReader(open(BASE / "audit.csv", encoding="utf-8")))
    # audit price-slot range
    slots = sorted(set(r["api_current_start"] for r in rows))
    print("audit api_current_start range:", slots[0], "->", slots[-1], f"({len(slots)} unique)")

    # check: does audit observed load/pv (site1) match history at corresponding hour?
    # build a map from audit (device_time rounded to hour) -> (pv, load) for site1
    import pandas as pd
    site1 = [r for r in rows if r["site"] == "1"]
    match = 0
    total = 0
    for r in site1:
        dt = r["device_time"]
        # try direct hour match
        hour = dt[:13] + ":00:00"  # 2026-10-12 08:30 -> 2026-10-12 08:00
        p = pts1.get(hour)
        if p is None:
            continue
        total += 1
        if abs(float(p["load_power_avg_w"]) - float(r["observed_load"])) < 5.0:
            match += 1
    print(f"site1 audit load vs history load (hour match): {match}/{total} within 5W")

    # show a few sample matches
    shown = 0
    for r in site1:
        hour = r["device_time"][:13] + ":00:00"
        p = pts1.get(hour)
        if p is not None and shown < 5:
            print(f"  dt={r['device_time']} audit_load={r['observed_load']} hist_load={p['load_power_avg_w']} audit_pv={r['observed_pv']} hist_pv={p['pv_power_avg_w']}")
            shown += 1


if __name__ == "__main__":
    main()
