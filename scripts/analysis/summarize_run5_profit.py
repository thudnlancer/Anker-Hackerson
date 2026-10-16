"""Summarize run5 profit attribution: baseline/actual/theoretical from dashboard + audit."""
import csv
import json
import statistics
from pathlib import Path

BASE = Path("outputs/local_mock_20261016_run5")


def main() -> None:
    d = json.loads((BASE / "dashboard.json").read_text(encoding="utf-8"))
    runs = d["results"]["runs"]

    print("=== RUN HISTORY (total settled saving) ===")
    for i, r in enumerate(runs):
        t = r.get("total", {})
        ss = t.get("saving_settled")
        sp = t.get("saving_settled_percent")
        tag = " <== run5 (ours)" if i == 8 else ""
        print(f"  idx{i} ended={r['ended_at']} range={r.get('date_range')} "
              f"saving_settled={ss:.4f} ({sp:.3f}%){tag}")

    run = runs[8]
    total = run["total"]
    print("\n=== run5 (ours) TOTAL ===")
    for k, v in total.items():
        print(f"  {k}: {v}")
    print("\n=== run5 per-site settled saving ===")
    for s in run["sites"]:
        m = s["metrics"]
        print(f"  site {s['site_id']}: saving_settled={m['saving_settled']:.4f} "
              f"({m['saving_settled_percent']:.3f}%)  baseline_settled={m['baseline_settled']:.4f} "
              f"device_settled={m['device_settled']:.4f}  shortage={m['shortage_count']}")


if __name__ == "__main__":
    main()
