"""Wait read-only for a mock contest to end, then save dashboard/HA evidence."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from energy_lab.config import load_runtime_config, require_runtime_fields
from energy_lab.sim_client import SimulatorClient


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    cfg = load_runtime_config(args.config, "dry-run")
    require_runtime_fields(cfg, write_enabled=False)
    sim = SimulatorClient(cfg["sim_base"], cfg["sim_username"], cfg["sim_password"],
                          proxy_mode=cfg["proxy_mode"])
    failures = 0
    while True:
        try:
            contest = sim.get("/api/contest")
            failures = 0
            print(json.dumps({"mode": contest.get("mode"), "track": contest.get("track"),
                              "now": contest.get("now")}), flush=True)
            if contest.get("mode") == "ended" and contest.get("track") == "mock":
                break
            if contest.get("track") != "mock":
                raise SystemExit("unexpected track; no final evidence captured")
        except Exception as exc:
            failures += 1
            print(json.dumps({"error_type": type(exc).__name__, "failures": failures}), flush=True)
            if failures >= 12:
                raise
        time.sleep(30)
    subprocess.run([sys.executable, str(Path(__file__).with_name("capture_local_mock_safety.py")),
                    "--config", str(args.config), "--output",
                    str(args.output_dir / "ended_snapshot.json")], check=True)


if __name__ == "__main__":
    main()
