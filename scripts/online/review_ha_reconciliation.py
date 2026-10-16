"""Review local HA readback evidence against pending incidents without clearing them."""
from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path


def timestamp(value: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None
    except ValueError:
        return None


def device_time(site: dict) -> datetime | None:
    return timestamp(site.get("device_time", {}).get("state"))


def review(journal: dict, before: dict, after: dict) -> dict:
    pending = journal.get("pending", {})
    if journal.get("version") != 1 or not isinstance(pending, dict):
        raise ValueError("invalid_reconciliation_state")
    rows = {}
    for site, incident in sorted(pending.items()):
        previous = before.get("sites", {}).get(site, {})
        current = after.get("sites", {}).get(site, {})
        prior_tick, current_tick = device_time(previous), device_time(current)
        incident_at = timestamp(incident.get("created_at"))
        mode_updated = timestamp(current.get("mode", {}).get("last_updated"))
        checks = {
            "same_station_entity": bool(
                incident.get("suffix") and
                str(incident["suffix"]) in current.get("mode", {}).get("entity_id", "") and
                str(incident["suffix"]) in current.get("device_time", {}).get("entity_id", "")
            ),
            "safe_mode_echo": current.get("mode", {}).get("state") == "self_consumption",
            "mode_updated_after_incident": bool(
                mode_updated and incident_at and mode_updated > incident_at),
            "device_time_advanced": bool(
                prior_tick and current_tick and current_tick > prior_tick),
            "snapshot_after_incident": bool(
                timestamp(after.get("captured_at")) and incident_at and
                timestamp(after["captured_at"]) > incident_at),
        }
        rows[site] = {"incident_id": incident.get("incident_id"), "checks": checks,
                      "readback_candidate": all(checks.values())}
    return {"schema": "ha_reconciliation_review/v1", "read_only": True,
            "pending_sites": list(rows), "sites": rows,
            "all_readback_candidates": bool(rows) and all(
                row["readback_candidate"] for row in rows.values()),
            "operator_clearance_performed": False,
            "ha_writes_performed": 0,
            "note": "Mechanical checks only; operator must verify physical safety, runner stop, and evidence before any clearance."}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--journal", type=Path, required=True)
    parser.add_argument("--before", type=Path, required=True)
    parser.add_argument("--after", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = review(*(json.loads(path.read_text(encoding="utf-8"))
                      for path in (args.journal, args.before, args.after)))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output), "all_readback_candidates":
                      report["all_readback_candidates"], "pending_sites": report["pending_sites"]}))


if __name__ == "__main__":
    main()
