"""Validate a continuous three-site MILP dry-run console/audit pair."""
from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path

import pandas as pd
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
from energy_lab.price_anchor import anchor_device_time, slot


def validate(console: Path, audit: Path, min_ticks: int = 3) -> dict:
    raw = console.read_bytes()
    encoding = "utf-16" if raw.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig"
    lines = raw.decode(encoding).splitlines()
    headers = [line for line in lines if line.startswith("mode=")]
    if not headers or any("mode=dry-run" not in line or "write_enabled=False" not in line
                          or "strategy=milp" not in line for line in headers):
        raise ValueError("missing or unsafe MILP dry-run startup header")
    records = []
    for line in lines:
        if line.startswith("{'site':"):
            value = ast.literal_eval(line)
            if not isinstance(value, dict):
                raise ValueError("invalid site record")
            records.append(value)
    if len(records) < 3 * min_ticks or len(records) % 3:
        raise ValueError("insufficient or incomplete three-site ticks")
    for pos in range(0, len(records), 3):
        batch = records[pos:pos + 3]
        if [str(row.get("site")) for row in batch] != ["1", "2", "3"]:
            raise ValueError(f"tick {pos // 3}: site order/count mismatch")
        if any(row.get("write_enabled", False) is not False or
               row.get("writes_performed", 0) != 0 for row in batch):
            raise ValueError(f"tick {pos // 3}: nonzero or enabled write")
    expected_audited = sum(row.get("status") not in
                           ("stale_or_unavailable", "station_error", "candidate_error")
                           for row in records)
    if audit.exists():
        frame = pd.read_csv(audit)
        if len(frame) and ("writes_performed" not in frame or
                           pd.to_numeric(frame.writes_performed, errors="coerce").isna().any() or
                           pd.to_numeric(frame.writes_performed).ne(0).any()):
            raise ValueError("audit has missing/nonzero writes_performed")
        audited_rows = len(frame)
    else:
        audited_rows = 0
    if audited_rows != expected_audited:
        raise ValueError("console/audit row count mismatch")
    all_eligible = all(row.get("status") != "stale_or_unavailable" for row in records)
    if not all_eligible:
        raise ValueError("one or more stations remained stale during continuous M3")
    previous = {}
    for row in records:
        if row.get("alignment_source") != "api_current_slot" or row.get("alignment_rejection_reason"):
            raise ValueError("missing or rejected API current-slot alignment")
        if row.get("status") in ("station_error", "candidate_error"):
            raise ValueError("station/candidate error during continuous M3")
        try:
            start, _ = slot({"start": row["api_current_start"], "end": row["api_current_end"]})
            resolved = anchor_device_time(row["ha_device_time_raw"], start)
            if resolved != pd.Timestamp(row["resolved_device_time"]) or start != pd.Timestamp(row["selected_price_slot"]):
                raise ValueError("slot mismatch")
            if row["site"] in previous and resolved <= previous[row["site"]]:
                raise ValueError("device time did not increase")
            previous[row["site"]] = resolved
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"invalid API current-slot evidence: {exc}") from exc
    if "alignment_source" not in frame or not frame.alignment_source.eq("api_current_slot").all():
        raise ValueError("audit lacks API current-slot evidence")
    return {"valid": True, "mode": "dry-run", "strategy": "milp",
            "ticks": len(records) // 3, "site_records": len(records),
            "audited_rows": audited_rows, "writes_performed": 0,
            "all_sites_each_tick": True,
            "all_sites_eligible": all_eligible, "api_current_slot_alignment": True,
            "device_time_increasing": True}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--console", type=Path, required=True)
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--min-ticks", type=int, default=3)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = validate(args.console, args.audit, args.min_ticks)
    content = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(content, encoding="utf-8")
    print(content, end="")


if __name__ == "__main__":
    main()
