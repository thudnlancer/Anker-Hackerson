"""Read-only, durable snapshot for local mock recovery and score comparison."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from energy_lab.config import exchange_ha_token, load_runtime_config, require_runtime_fields
from energy_lab.ha_client import HAClient
from energy_lab.sim_client import SimulatorClient
from scripts.online.recover_local_mock import read_controls


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    cfg = load_runtime_config(args.config, "dry-run")
    require_runtime_fields(cfg, write_enabled=False)
    sim = SimulatorClient(cfg["sim_base"], cfg["sim_username"], cfg["sim_password"],
                          proxy_mode=cfg["proxy_mode"])
    ha = HAClient(cfg["ha_base"], exchange_ha_token(cfg), proxy_mode=cfg["proxy_mode"])
    dashboard = sim.get("/api/dashboard")
    payload = {"captured_at": datetime.now(timezone.utc).isoformat(),
               "contest": sim.get("/api/contest"),
               "dashboard": dashboard,
               "dashboard_sha256": hashlib.sha256(json.dumps(
                   dashboard, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest(),
               "sites": {site: read_controls(ha, str(item["suffix"]).split("_")[-1])
                         for site, item in sorted(cfg["stations"].items())}}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output), "contest": payload["contest"],
                      "dashboard_sha256": payload["dashboard_sha256"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
