"""B1: single-site supervised zero-flow (charge=0 W) hardware verification.

Full step-1 flow from HOLD_SEMANTICS_DESIGN §4: baseline read-only record ->
third-party charge=0 W dispatch -> next device tick -> readback judgement ->
restore the baseline mode and confirm.

Default is dry-run: every phase runs, no HA service call is made
(writes_performed == 0), and the verdict is explicitly ``null`` because no
command was sent. A real write requires ``--mode live``,
``--authorization-note``, and all three safety flags in the config
(live_enabled, allow_ha_write, allow_formal_contest). This is the only write
path of workstream B; keep it supervised and outside contest windows (R3).
"""
from __future__ import annotations

from pathlib import Path
from dataclasses import asdict
from datetime import datetime, timezone
import argparse
import json
import sys
import time

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from energy_lab.config import exchange_ha_token, load_runtime_config, require_runtime_fields
from energy_lab.ha_client import HAClient
from energy_lab.interface_contract import state_is_fresh
from energy_lab.reconciliation import ReconciliationStore
from energy_lab.zero_flow import (apply_zero_flow_conclusion, build_zero_flow_command,
                                  confirm_restored, evidence_hash, judge_zero_flow_state,
                                  plan_restore)

COMMAND_ENTITIES = ("select operating_mode -> third_party_control",
                    "wait ~1s (SOP processing gap)",
                    "select grid_flow -> charge",
                    "number power_control -> 0 W")


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def pending_reconciliation_journals(root: Path, selected: Path) -> dict[str, list[str]]:
    """Check the selected journal and every local incident journal, without altering them."""
    paths = {selected.resolve()}
    outputs = root / "outputs"
    if outputs.is_dir():
        paths.update(path.resolve() for path in outputs.rglob("ha_reconciliation_state.json"))
    blocked = {}
    for path in sorted(paths):
        pending = ReconciliationStore(path).pending()
        if pending:
            blocked[str(path)] = sorted(pending)
    return blocked


def read_verification_state(ha: HAClient, suffix: str) -> dict:
    """Read every entity the verification needs. Strictly read-only."""
    raw = ha.station_state(suffix)
    state = {
        "soc": raw.get("soc"),
        "solar_power": raw.get("solar_power"),
        "home_load": raw.get("home_load"),
        "battery_charging_power_w": raw.get("charge_power"),
        "battery_discharging_power_w": raw.get("discharge_power"),
        "grid_import_power_w": raw.get("grid_import"),
        "grid_export_power_w": raw.get("grid_export"),
        "device_time": raw.get("device_time"),
        "device_time_last_changed": raw.get("device_time_last_changed"),
    }
    entities = {
        "operating_mode": f"select.{suffix}_operating_mode_device_runs_by_command_in_third_party_controlled",
        "grid_flow": f"select.{suffix}_grid_flow",
        "power_control": f"number.{suffix}_power_control",
    }
    for key, entity in entities.items():
        item = ha.state(entity)
        state[key] = item.get("state")
        state[f"{key}_last_changed"] = item.get("last_changed")
    return state


def device_tick(state: dict) -> pd.Timestamp | None:
    value = state.get("device_time")
    if value in (None, "", "unavailable", "unknown"):
        return None
    try:
        stamp = pd.Timestamp(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return None if pd.isna(stamp) else stamp


def await_device_tick(read_state, *, since_device_time, timeout_s: float, poll_s: float) -> dict:
    """Poll until device_time advances past the recorded tick. Read-only."""
    sent = pd.Timestamp(since_device_time)
    if pd.isna(sent):
        raise ValueError("cannot await a tick without a valid baseline device_time")
    deadline = time.monotonic() + float(timeout_s)
    while time.monotonic() < deadline:
        state = read_state()
        tick = device_tick(state)
        if tick is not None and tick > sent:
            return state
        time.sleep(max(0.5, float(poll_s)))
    raise TimeoutError(f"device tick did not advance within {timeout_s}s")


def write_json(path: Path, payload: object) -> str:
    text = json.dumps(payload, indent=2, ensure_ascii=False, default=str)
    path.write_text(text, encoding="utf-8")
    return evidence_hash(payload)


def main() -> None:
    parser = argparse.ArgumentParser(description="B1 supervised zero-flow hardware verification (single site)")
    parser.add_argument("--config", type=Path, default=Path("config/local/local_online_config.json"))
    parser.add_argument("--site", required=True, help="single site id from the config stations mapping")
    parser.add_argument("--mode", choices=("dry-run", "live"), default="dry-run",
                        help="dry-run is the default; live must be explicitly requested")
    parser.add_argument("--authorization-note",
                        help="required in live mode: reference to the explicit authorization (e.g. review decision)")
    parser.add_argument("--reconciliation-state", type=Path,
                        help="required in live mode: existing HA incident journal; any pending incident blocks writes")
    parser.add_argument("--snapshot", type=Path,
                        help="offline rehearsal: JSON with 'baseline' and optional 'readback' state blocks; no network")
    parser.add_argument("--output", type=Path,
                        help="evidence directory; default outputs/zero_flow_verification/site{site}_{timestamp}")
    parser.add_argument("--tick-timeout-seconds", type=float, default=90.0)
    parser.add_argument("--poll-seconds", type=float, default=2.0)
    parser.add_argument("--battery-tolerance-w", type=float, default=100.0)
    parser.add_argument("--grid-tolerance-w", type=float, default=100.0)
    parser.add_argument("--write-config", action="store_true",
                        help="live mode only: record the zero_flow_verified conclusion into the config file")
    args = parser.parse_args()

    cfg = load_runtime_config(args.config, args.mode)
    mode = str(cfg["mode"]).lower().replace("_", "-")
    live = mode == "live"
    site = str(args.site)
    if site not in cfg.get("stations", {}):
        raise SystemExit(f"site {site!r} not present in config stations")
    if args.snapshot and live:
        raise SystemExit("--snapshot is an offline rehearsal and cannot be combined with --mode live")
    if live and not args.authorization_note:
        raise SystemExit("live mode requires --authorization-note referencing the explicit authorization")
    if live:
        if args.reconciliation_state is None or not args.reconciliation_state.is_file():
            raise SystemExit("live mode requires an existing --reconciliation-state journal")
        blocked = pending_reconciliation_journals(ROOT, args.reconciliation_state)
        if blocked:
            raise SystemExit(f"live mode blocked by pending HA reconciliation sites: {blocked}")
    if args.write_config and not live:
        raise SystemExit("--write-config only applies to a live verdict; refusing in dry-run")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output = args.output or Path("outputs") / "zero_flow_verification" / f"site{site}_{stamp}"
    if (output / "report.json").exists():
        raise FileExistsError("choose a new output directory")
    output.mkdir(parents=True, exist_ok=True)

    report: dict = {
        "schema": "zero_flow_verification/v1",
        "site": site,
        "mode": mode,
        "authorization_note": args.authorization_note if live else None,
        "started_at": utcnow(),
        "writes_performed": 0,
        "verdict": None,
        "verdict_basis": "not_verified_dry_run",
        "status": "started",
        "config": str(args.config),
        "limits": ["dry-run judgement is informational only; no command was sent" if not live else
                   "single supervised write sequence; outside contest windows per R3"],
    }
    artifacts: dict[str, str] = {}

    def finish(status: str, exit_code: int) -> None:
        report["status"] = status
        report["finished_at"] = utcnow()
        report["artifacts"] = artifacts
        artifacts["report.json"] = write_json(output / "report.json", report)
        (output / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str),
                                            encoding="utf-8")
        print(json.dumps(report, indent=2, ensure_ascii=False, default=str))
        raise SystemExit(exit_code)

    ha: HAClient | None = None
    snapshot_readback: dict | None = None
    if args.snapshot:
        payload = json.loads(args.snapshot.read_text(encoding="utf-8-sig"))
        baseline = payload.get("baseline")
        if not isinstance(baseline, dict):
            raise SystemExit("snapshot must contain a 'baseline' state object")
        snapshot_readback = payload.get("readback") if isinstance(payload.get("readback"), dict) else None
        baseline_source = f"snapshot:{args.snapshot}"
    else:
        require_runtime_fields(cfg, write_enabled=live)
        token = exchange_ha_token(cfg)
        ha = HAClient(str(cfg["ha_base"]), token)
        suffix = str(cfg["stations"][site]["suffix"])
        baseline = read_verification_state(ha, suffix)
        baseline_source = "ha_read"
        fresh = state_is_fresh(baseline.get("device_time_last_changed"),
                               max_age_seconds=float(cfg.get("stale_after_seconds", 15.0)))
        report["baseline_fresh"] = bool(fresh)
        if not fresh:
            artifacts["baseline.json"] = write_json(output / "baseline.json", baseline)
            report["abort_reason"] = "baseline_stale_or_device_clock_not_fresh"
            finish("aborted", 2)
    report["baseline_source"] = baseline_source
    report["baseline_sha256"] = evidence_hash(baseline)
    artifacts["baseline.json"] = write_json(output / "baseline.json", baseline)
    if device_tick(baseline) is None:
        report["abort_reason"] = "baseline_device_time_unreadable"
        finish("aborted", 2)

    command = build_zero_flow_command()
    restore_plan = plan_restore(baseline)
    command_record = {
        "command": asdict(command),
        "planned_sequence": list(COMMAND_ENTITIES),
        "dry_run": not live,
        "sent": False,
        "sent_device_time": baseline.get("device_time"),
        "restore_plan": asdict(restore_plan),
    }
    if live and not restore_plan.restorable:
        artifacts["command.json"] = write_json(output / "command.json", command_record)
        report["abort_reason"] = f"restore_plan_unavailable:{restore_plan.reason}"
        finish("aborted", 2)

    suffix = str(cfg["stations"][site]["suffix"]) if not args.snapshot else "snapshot"
    if live:
        assert ha is not None
        print("WARNING: supervised HA write enabled by all safety gates; issuing charge=0 W", flush=True)
        try:
            ha.set_dispatch(suffix, command.grid_flow, command.power_w, live=True)
        except requests.RequestException as exc:
            # The write sequence failed part-way: restore the baseline mode
            # before anything else, then abort with the failure recorded.
            command_record["sent"] = False
            command_record["send_error"] = f"{type(exc).__name__}:{exc}"
            artifacts["command.json"] = write_json(output / "command.json", command_record)
            report["restore_after_send_failure"] = _attempt_restore(ha, suffix, restore_plan)
            report["writes_performed"] += report["restore_after_send_failure"]["writes"]
            report["abort_reason"] = f"command_send_failed:{type(exc).__name__}"
            finish("aborted", 2)
        command_record["sent"] = True
        command_record["issued_at"] = utcnow()
        report["writes_performed"] = 3  # mode select + grid_flow select + power set
    artifacts["command.json"] = write_json(output / "command.json", command_record)

    def read_state() -> dict:
        assert ha is not None
        return read_verification_state(ha, suffix)

    readback: dict | None = None
    readback_source = "none"
    if args.snapshot:
        readback = snapshot_readback or baseline
        readback_source = "snapshot_readback" if snapshot_readback else "snapshot_baseline_reused"
    else:
        try:
            readback = await_device_tick(read_state, since_device_time=baseline["device_time"],
                                         timeout_s=args.tick_timeout_seconds, poll_s=args.poll_seconds)
            readback_source = "polled_next_tick"
        except TimeoutError as exc:
            if live:
                # Never leave the device in the test mode without a judgement attempt.
                restore_result = _attempt_restore(ha, suffix, restore_plan)
                report["writes_performed"] += restore_result["writes"]
                report["restore_attempted_after_timeout"] = bool(restore_result["writes"])
                if restore_result["error"]:
                    report["restore_error_after_timeout"] = restore_result["error"]
                report["abort_reason"] = f"readback_timeout:{exc}"
                finish("aborted", 2)
            readback = read_state()
            readback_source = "immediate_reread_after_tick_timeout"
    report["readback_source"] = readback_source
    report["readback_sha256"] = evidence_hash(readback)
    artifacts["readback.json"] = write_json(output / "readback.json", readback)

    judgement = judge_zero_flow_state(readback, battery_tolerance_w=args.battery_tolerance_w,
                                      grid_tolerance_w=args.grid_tolerance_w)
    artifacts["judgement.json"] = write_json(output / "judgement.json", asdict(judgement))
    report["judgement"] = asdict(judgement)
    if live:
        report["verdict"] = bool(judgement.verified)
        report["verdict_basis"] = "live_readback"

    restore_record: dict = {"plan": asdict(restore_plan), "dry_run": not live,
                            "restored": None, "confirm_readback_sha256": None}
    if live:
        assert ha is not None
        restore_result = _attempt_restore(ha, suffix, restore_plan)
        report["writes_performed"] += restore_result["writes"]
        if restore_result["error"]:
            restore_record["restored"] = False
            restore_record["restore_error"] = restore_result["error"]
            artifacts["restore.json"] = write_json(output / "restore.json", restore_record)
            report["restore"] = restore_record
            report["abort_reason"] = "restore_send_failed_manual_check_required"
            finish("aborted", 2)
        try:
            confirm = await_device_tick(read_state, since_device_time=readback["device_time"],
                                        timeout_s=args.tick_timeout_seconds, poll_s=args.poll_seconds)
        except TimeoutError:
            confirm = read_state()
        restored = confirm_restored(confirm, restore_plan)
        restore_record["restored"] = bool(restored)
        restore_record["confirm_readback_sha256"] = evidence_hash(confirm)
        artifacts["restore_readback.json"] = write_json(output / "restore_readback.json", confirm)
        if not restored:
            artifacts["restore.json"] = write_json(output / "restore.json", restore_record)
            report["abort_reason"] = "mode_residue_restore_not_confirmed_manual_check_required"
            finish("aborted", 2)
    artifacts["restore.json"] = write_json(output / "restore.json", restore_record)
    report["restore"] = restore_record

    config_update: dict = {"applied": False}
    if live and args.write_config and report["verdict"] is not None:
        original = json.loads(Path(args.config).read_text(encoding="utf-8"))
        artifacts["config_backup.json"] = write_json(output / "config_backup.json", original)
        updated = apply_zero_flow_conclusion(original, site, bool(report["verdict"]))
        Path(args.config).write_text(json.dumps(updated, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        config_update = {"applied": True, "site": site,
                         "zero_flow_verified": bool(report["verdict"]),
                         "config": str(args.config)}
    elif live:
        config_update["reason"] = "pass --write-config to record the verdict into the config file"
    else:
        config_update["reason"] = "skipped_dry_run"
    report["config_update"] = config_update

    if not live:
        assert report["writes_performed"] == 0, "dry-run must never write"
    finish("completed", 0)


def _issue_restore(ha: HAClient, suffix: str, plan) -> int:
    """Send the restore sequence. Returns the number of HA service calls."""
    if plan.operating_mode == "self_consumption":
        ha.set_self_consumption(suffix, live=True)
        return 1
    ha.set_dispatch(suffix, str(plan.grid_flow), float(plan.power_w), live=True)
    return 3


def _attempt_restore(ha: HAClient, suffix: str, plan) -> dict:
    """Best-effort restore; failures are reported, never raised past this point."""
    if not plan.restorable:
        return {"writes": 0, "error": f"not_restorable:{plan.reason}"}
    try:
        return {"writes": _issue_restore(ha, suffix, plan), "error": None}
    except requests.RequestException as exc:
        return {"writes": 0, "error": f"{type(exc).__name__}:{exc}"}


if __name__ == "__main__":
    main()
