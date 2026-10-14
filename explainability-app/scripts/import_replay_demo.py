"""Import three genuine historical replay traces into a separate PV demo database."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path

from fastapi.testclient import TestClient

from energy_explain.api import create_app
from energy_explain.pv_accounting import account_rows
from energy_explain.schemas import PVAccountingIn


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trace-root", type=Path, required=True)
    parser.add_argument("--sites", nargs=3, default=["1", "2", "3"])
    parser.add_argument("--arm", default="irradiance")
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if len(set(args.sites)) != 3:
        parser.error("three distinct sites are required")
    manifest_path = args.trace_root / "manifest.json"
    audit_path = args.trace_root / "audit.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    if not audit.get("passed"):
        parser.error("saved physical audit must pass")
    prepared = []
    evidence = []
    for site in args.sites:
        source = args.trace_root / f"site_{site}_trace.csv"
        with source.open(encoding="utf-8-sig", newline="") as stream:
            rows = list(csv.DictReader(stream))
        selected = [r for r in rows if r["arm"] == args.arm]
        if not selected or {r["site"] for r in selected} != {site}:
            parser.error("missing selected arm or inconsistent site identity")
        if len({r["time"] for r in selected}) != len(selected):
            parser.error("duplicate device times")
        reports = account_rows(rows, arm=args.arm, step_hours=1, max_charge_kw=5,
                               time_basis="dataset_local_hour")
        prepared.extend(PVAccountingIn.model_validate(r) for r in reports)
        totals = {k: sum(r["totals"][k] for r in reports) for k in reports[0]["totals"]}
        evidence.append({"site_id": site, "source": str(source.resolve()),
            "sha256": hashlib.sha256(source.read_bytes()).hexdigest(), "total_rows": len(rows),
            "arm": args.arm, "selected_rows": len(selected), "device_start": selected[0]["time"],
            "device_end": selected[-1]["time"], "site_days": len(reports), "totals": totals,
            "negative_price_rows": sum(float(r["sell"]) < 0 for r in selected)})
    # Validate all source reports before opening the demo database.
    args.db.parent.mkdir(parents=True, exist_ok=True)
    created = 0
    with TestClient(create_app(args.db)) as client:
        for report in prepared:
            response = client.post("/api/pv/accounting", json=report.model_dump(mode="json"))
            assert response.status_code == 200, response.text
            created += int(response.json()["created"])
        for site in args.sites:
            response = client.get(f"/api/pv/{site}/daily", params={"arm": args.arm})
            assert response.status_code == 200, response.text
            assert len(response.json()["days"]) == sum(r.site_id == site for r in prepared), response.text
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "pv-accounting.jsonl").write_text(
        "".join(r.model_dump_json() + "\n" for r in prepared), encoding="utf-8")
    result = {"passed": True, "created": created, "site_days": len(prepared), "sites": evidence,
        "database": str(args.db.resolve()), "time_basis": "dataset_local_hour",
        "input_kind": "real_household_history_offline_replay", "writes_performed": 0,
        "weather_source": manifest.get("weather_source"), "saved_physical_audit": audit,
        "audit_sha256": hashlib.sha256(audit_path.read_bytes()).hexdigest(),
        "manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        "scope": "E5_PV_data_import_only_not_online_decision_replay_or_official_settlement"}
    (args.output / "import-report.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: result[k] for k in ("passed", "created", "site_days", "scope")}))


if __name__ == "__main__":
    main()
