"""Prepare an isolated E1-E5 demo database from committed samples and PV JSONL."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from energy_explain.demo import seed_demo
from energy_explain.schemas import PVAccountingIn
from energy_explain.store import Store


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--pv-jsonl", type=Path,
                        default=Path("artifacts/e5-real-replay/pv-accounting.jsonl"))
    args = parser.parse_args()
    records = [PVAccountingIn.model_validate_json(line)
               for line in args.pv_jsonl.read_text(encoding="utf-8").splitlines() if line.strip()]
    if {r.site_id for r in records} != {"1", "2", "3"} or {r.arm for r in records} != {"irradiance"}:
        parser.error("PV sample must contain irradiance reports for sites 1, 2 and 3")
    args.db.parent.mkdir(parents=True, exist_ok=True)
    store = Store(args.db)
    decision_keys = seed_demo(store)
    created = sum(store.add_pv_daily(report)["created"] for report in records)
    counts = {site: len(store.pv_daily(site, "irradiance")) for site in ("1", "2", "3")}
    if any(count != 8 for count in counts.values()):
        raise RuntimeError(f"expected eight local dates per site, got {counts}")
    print(json.dumps({"decision_keys": decision_keys, "pv_created": created,
                      "pv_days_by_site": counts, "database": str(args.db.resolve()),
                      "writes_performed": 0, "scope": "offline_demo_only"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
