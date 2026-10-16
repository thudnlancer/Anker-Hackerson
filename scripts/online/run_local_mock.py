"""Run the local MILP controller only during a running mock contest.

This entry point does not alter the credential-bearing config. HA writes are
enabled in memory only after the simulator reports running/mock. Never use it
for the formal contest.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import msvcrt
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))
from energy_lab.config import exchange_ha_access, load_runtime_config, require_runtime_fields
from energy_lab.ha_client import HAClient
from energy_lab.reconciliation import ReconciliationStore
from energy_lab.sim_client import SimulatorClient
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts.online.run_live import run_once_milp


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--reconciliation-state", type=Path, required=True,
                        help="Existing durable journal; never switch journals to evade an incident")
    parser.add_argument("--confirm-mock-ha-write", action="store_true", required=True)
    args = parser.parse_args()
    if not args.confirm_mock_ha_write:
        parser.error("explicit --confirm-mock-ha-write is required")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    lock_file = (args.output_dir / "runner.lock").open("a+b")
    lock_file.seek(0)
    lock_file.write(b"0")
    lock_file.flush()
    lock_file.seek(0)
    try:
        msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError as exc:
        raise SystemExit("another local mock runner owns this output directory") from exc

    cfg = load_runtime_config(args.config, "dry-run")
    require_runtime_fields(cfg, write_enabled=False)
    if cfg["live_enabled"] or cfg["allow_ha_write"] or cfg["allow_formal_contest"]:
        raise SystemExit("local mock config must keep all three persistent write flags false")
    if set(cfg["stations"]) != {"1", "2", "3"}:
        raise SystemExit("local mock requires exactly sites 1, 2, 3")
    sim = SimulatorClient(cfg["sim_base"], cfg["sim_username"], cfg["sim_password"],
                          proxy_mode=cfg["proxy_mode"])
    access = exchange_ha_access(cfg)
    ha = HAClient(cfg["ha_base"], access["access_token"], proxy_mode=cfg["proxy_mode"])
    refresh_at = (time.monotonic() + min(access["expires_in"] * .6, 1200)
                  if access["expires_in"] is not None else None)
    store = ReconciliationStore(args.reconciliation_state)
    metadata = {"started_at": now(), "config_sha256": hashlib.sha256(args.config.read_bytes()).hexdigest(),
                "mode": "local_mock_only", "strategy": "milp", "output_dir": str(args.output_dir.resolve())}
    (args.output_dir / "runtime_meta.json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"event": "started", **metadata}), flush=True)
    carry: dict = {}
    started_contest = False
    consecutive_stage_errors = 0
    while True:
        loop_started = time.monotonic()
        try:
            stage = sim.get("/api/contest")
            consecutive_stage_errors = 0
        except Exception as exc:
            consecutive_stage_errors += 1
            print(json.dumps({"event": "contest_stage_error", "at": now(),
                              "error_type": type(exc).__name__, "count": consecutive_stage_errors}), flush=True)
            if consecutive_stage_errors >= 12:
                raise SystemExit("contest stage unavailable for 12 consecutive checks; writes stopped")
            time.sleep(max(.1, cfg["poll_seconds"] - (time.monotonic() - loop_started)))
            continue
        mode, track = stage.get("mode"), stage.get("track")
        print(json.dumps({"event": "contest_stage", "at": now(), "mode": mode,
                          "track": track, "now": stage.get("now"),
                          "seconds_until_start": stage.get("seconds_until_start")}), flush=True)
        if mode == "ended" and track == "mock":
            print(json.dumps({"event": "mock_ended", "at": now(),
                              "had_running_tick": started_contest}), flush=True)
            break
        if mode == "running" and track == "mock":
            started_contest = True
            if refresh_at is not None and time.monotonic() >= refresh_at:
                try:
                    access = exchange_ha_access(cfg)
                    ha.replace_token(access["access_token"])
                    refresh_at = time.monotonic() + min(access["expires_in"] * .6, 1200)
                    print(json.dumps({"event": "ha_token_refreshed", "at": now()}), flush=True)
                except Exception as exc:
                    carry["global_delivery_halt"] = True
                    print(json.dumps({"event": "ha_token_refresh_failed_writes_halted",
                                      "at": now(), "error_type": type(exc).__name__}), flush=True)
            rows = run_once_milp(cfg, ha, sim, write_enabled=True, carry=carry,
                                 audit_csv=args.output_dir / "audit.csv", reconciliation_store=store)
            for row in rows:
                print(json.dumps({"event": "site_result", **row}, ensure_ascii=False, default=str), flush=True)
        elif mode != "waiting" or track != "mock":
            raise SystemExit(f"unexpected contest stage {mode}/{track}; writes stopped")
        time.sleep(max(.1, cfg["poll_seconds"] - (time.monotonic() - loop_started)))


if __name__ == "__main__":
    main()
