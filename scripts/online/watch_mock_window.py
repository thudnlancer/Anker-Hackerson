"""Watch the simulator contest stage and auto-launch the local mock runner.

Polls ``/api/contest`` read-only. When the track is ``mock`` and the mode
transitions to ``waiting`` or ``running``, it launches
``run_local_mock.py`` as a blocking child and waits for the runner to finish
(the runner itself exits on ``ended/mock``). The watcher then exits.

This performs no HA writes by itself; the runner only writes during a
``running/mock`` window and only after ``--confirm-mock-ha-write``.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from energy_lab.config import load_runtime_config, require_runtime_fields
from energy_lab.sim_client import SimulatorClient


def log(event: dict) -> None:
    print(json.dumps(event, ensure_ascii=False), flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--reconciliation-state", type=Path, required=True)
    parser.add_argument("--poll-seconds", type=float, default=10.0)
    args = parser.parse_args()

    cfg = load_runtime_config(args.config, "dry-run")
    require_runtime_fields(cfg, write_enabled=False)
    sim = SimulatorClient(cfg["sim_base"], cfg["sim_username"], cfg["sim_password"],
                          proxy_mode=cfg["proxy_mode"])
    log({"event": "watch_started", "config": str(args.config),
         "output_dir": str(args.output_dir),
         "reconciliation_state": str(args.reconciliation_state)})

    consecutive_errors = 0
    while True:
        try:
            stage = sim.get("/api/contest")
            consecutive_errors = 0
        except Exception as exc:
            consecutive_errors += 1
            log({"event": "watch_error", "error_type": type(exc).__name__,
                 "count": consecutive_errors})
            if consecutive_errors >= 12:
                log({"event": "watch_abort", "reason": "contest stage unavailable 12 consecutive times"})
                return
            time.sleep(args.poll_seconds)
            continue

        mode, track = stage.get("mode"), stage.get("track")
        log({"event": "watch_stage", "mode": mode, "track": track,
             "now": stage.get("now"), "seconds_until_start": stage.get("seconds_until_start")})

        if track == "mock" and mode in ("waiting", "running"):
            log({"event": "window_opened", "mode": mode, "track": track})
            cmd = [sys.executable, str(ROOT / "scripts" / "online" / "run_local_mock.py"),
                   "--config", str(args.config),
                   "--output-dir", str(args.output_dir),
                   "--reconciliation-state", str(args.reconciliation_state),
                   "--confirm-mock-ha-write"]
            result = subprocess.run(cmd)
            log({"event": "runner_exited", "returncode": result.returncode})
            return

        time.sleep(args.poll_seconds)


if __name__ == "__main__":
    main()
