"""Build daily PV accounting JSONL from a replay trace CSV (read-only)."""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

from energy_explain.pv_accounting import account_rows


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("trace", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--arm", default="zero_flow")
    parser.add_argument("--step-hours", type=float, default=1.0)
    parser.add_argument("--max-charge-kw", type=float)
    parser.add_argument("--time-basis", choices=("timezone_aware", "dataset_local_hour"),
                        default="timezone_aware", help="explicit offline dataset clock; never guesses UTC")
    args = parser.parse_args(argv)
    with args.trace.open("r", encoding="utf-8-sig", newline="") as stream:
        reports = account_rows(list(csv.DictReader(stream)), arm=args.arm,
                               step_hours=args.step_hours, max_charge_kw=args.max_charge_kw,
                               time_basis=args.time_basis)
    if not reports:
        parser.error("no rows for the selected arm; no output written")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as stream:
        for report in reports:
            stream.write(json.dumps(report, ensure_ascii=False, allow_nan=False) + "\n")
    print(f"wrote {len(reports)} site-days to {args.output}")


if __name__ == "__main__":
    main()
