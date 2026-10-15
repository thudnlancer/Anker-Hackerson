"""Run the online policy locally or on the contest machine.

The default is a read-only dry-run. HA writes require an explicit ``--mode live``
and all three safety flags in the selected configuration.

``--strategy milp`` wires the HOLD_SEMANTICS_DESIGN step-4 chain per station:
snapshot -> shadow.make_candidate -> execute_candidate -> next-tick readback ->
audit log. A fourth safety switch applies there: only a site whose
``zero_flow_verified`` config flag is true may receive a live ``charge=0`` idle
command; an unverified site is refused without any silent downgrade. The
rule-based ``dispatch`` entry is kept unchanged for one release cycle (§7).
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import argparse
import math
import sys
import threading
import time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
from energy_lab.config import exchange_ha_token, load_runtime_config, require_runtime_fields
from energy_lab.execute import check_soc_divergence, execute_candidate
from energy_lab.ha_client import HAClient
from energy_lab.model import dispatch
from energy_lab.sim_client import SimulatorClient
from energy_lab.price_anchor import read_anchored_tick
from energy_lab.shadow import daily_points, make_candidate
from energy_lab.solve_schedule import solve_until
from energy_lab.reconciliation import ReconciliationStore
from energy_lab.interface_contract import (CommandMapping, map_battery_action, state_is_fresh,
                                           verify_readback, align_hourly_points)


def numeric(value: object) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float("nan")


def price_points(response: object) -> list[dict]:
    if isinstance(response, list):
        return response
    if isinstance(response, dict):
        if isinstance(response.get("points"), list):
            return response["points"]
        for key in ("today", "prices", "data"):
            value = response.get(key)
            if isinstance(value, dict) and isinstance(value.get("points"), list):
                return value["points"]
            if isinstance(value, list):
                return value
    return []


def price_window(response: object) -> list[dict]:
    """Flatten explicit today/tomorrow price blocks; never wrap one day."""
    if not isinstance(response, dict) or not any(key in response for key in ("today", "tomorrow")):
        points = price_points(response)
        if len(points) < 24:
            raise RuntimeError(f"simulator returned only {len(points)} price points; need explicit today/tomorrow data")
        return points
    return daily_points(response)


def validate_milp_price_window(points: list[dict], device_time: object,
                               device_timezone: str | None = None) -> str | None:
    """Validate the complete optimizer horizon before any solver call."""
    try:
        tick = pd.Timestamp(device_time)
        if pd.isna(tick):
            return "device_time_misaligned:invalid_device_time"
        if not device_timezone and tick.tzinfo is None:
            return "device_time_misaligned:missing_trusted_device_timezone"
        if device_timezone:
            ZoneInfo(device_timezone)
    except (ZoneInfoNotFoundError, ValueError, TypeError):
        return "device_time_misaligned:invalid_device_timezone"
    if not points:
        return "missing_price_data:empty_window"
    try:
        tick = pd.Timestamp(device_time)
        if pd.isna(tick):
            return "device_time_misaligned:invalid_device_time"
        if tick.tzinfo is None and device_timezone is not None:
            tick = tick.tz_localize(device_timezone)
        horizon = 24 if tick.minute == 0 and tick.second == 0 and tick.microsecond == 0 else 25
        aligned = align_hourly_points(points, tick, horizon=horizon)
    except (TypeError, ValueError, OverflowError) as exc:
        detail = str(exc)
        if "horizon incomplete" in detail or "empty price/weather horizon" in detail:
            try:
                timestamps = pd.to_datetime(
                    [point.get("time", point.get("start")) for point in points], utc=True)
                requested = tick.tz_convert("UTC").floor("h")
                if requested < timestamps.min() or requested > timestamps.max():
                    return "device_time_misaligned:outside_price_window"
            except (TypeError, ValueError, AttributeError):
                pass
            return "missing_price_data:incomplete_optimizer_horizon"
        return "device_time_misaligned:price_timestamp_alignment_failed"
    for field in ("import_price", "export_price"):
        if field not in aligned:
            return f"missing_price_data:missing_{field}"
        values = pd.to_numeric(aligned[field], errors="coerce").to_numpy(dtype=float)
        if any(value is None or value == "" for value in aligned[field]):
            return f"missing_price_data:missing_{field}"
        if not all(math.isfinite(value) for value in values):
            return f"non_finite_price:{field}"
        if field == "import_price" and (values <= 0).any():
            return "invalid_price_value:non_positive_import_price"
    return None


def current_price_and_future(points: list[dict], device_time: object,
                             device_timezone: str | None = None) -> tuple[dict, list[float]]:
    if not device_timezone and pd.Timestamp(device_time).tzinfo is None:
        raise ValueError("missing_trusted_device_timezone")
    if device_timezone:
        ZoneInfo(device_timezone)
    stamp = pd.Timestamp(device_time)
    if stamp.tzinfo is None:
        stamp = stamp.tz_localize(device_timezone)
    frame = pd.DataFrame(points)
    frame["time"] = pd.to_datetime(frame["time"], utc=True)
    wanted = stamp.tz_convert("UTC").floor("h")
    row = frame.loc[frame["time"] == wanted]
    if row.empty:
        raise ValueError("price API has no current device-time slot")
    pos = int(row.index[0])
    future = frame.iloc[pos + 1:]["import_price"].astype(float).tolist()
    return row.iloc[0].to_dict(), future


def run_once(cfg: dict, ha: HAClient, sim: SimulatorClient, *, write_enabled: bool) -> list[dict]:
    batch = read_anchored_tick(cfg, ha, sim, price_window,
                               price_validator=validate_milp_price_window)
    prices = batch["points"]
    results: list[dict] = []
    for site, item in sorted(cfg["stations"].items(), key=lambda pair: str(pair[0])):
        suffix = str(item["suffix"])
        try:
            if site not in batch["states"] and (
                    site not in batch["errors"] or
                    batch["errors"][site].startswith("ha_read_error:")):
                results.append({"site": site, "status": "station_error",
                                "error": batch["errors"].get(site), "writes_performed": 0})
                continue
            state = batch["states"].get(site, {})
            stamp = state.get("device_time")
            freshness = state.get("device_time_last_changed")
            if (batch["errors"].get(site) == "stale_or_unavailable" or
                    not batch["errors"].get(site) and
                    (not stamp or not freshness or "unavailable" in str(stamp).lower())):
                results.append({"site": site, "status": "stale_or_unavailable", "device_time": stamp})
                continue
            if not batch["errors"].get(site) and not state_is_fresh(freshness, max_age_seconds=float(cfg.get("stale_after_seconds", 15.))):
                results.append({"site": site, "status": "stale_or_unavailable", "device_time": stamp})
                continue
            resolved = batch["times"].get(site)
            price_error = batch["errors"].get(site) or validate_milp_price_window(prices, resolved)
            if price_error:
                results.append({"site": site, "status": "economic_action_blocked",
                                "device_time": stamp, "reason": price_error,
                                "optimizer_attributed": False,
                                "attribution": "non-optimizer-attributed",
                                "write_enabled": False, "writes_performed": 0})
                continue
            current, future = current_price_and_future(prices, resolved)
            solar_w, load_w, soc = numeric(state.get("solar_power")), numeric(state.get("home_load")), numeric(state.get("soc"))
            flow, watts, reason = dispatch(
                numeric(current.get("import_price")),
                numeric(current.get("export_price")),
                solar_w, load_w, soc,
                future,
                float(cfg["capacity_kwh"]),
                float(cfg["max_power_w"]),
            )
            finite_watts = watts if math.isfinite(watts) else 0.0
            # No zero_flow_verified site exists yet (Q1): planned idle steps are
            # executed as an explicit self_consumption mode switch, never via a
            # silent dispatch mapping.
            mapping = map_battery_action(flow, finite_watts, solar_w, load_w,
                                         limit_w=float(cfg["max_power_w"]),
                                         idle_mode="self_consumption" if flow in ("idle", "hold") else None)
            representable, command_reason = mapping.representable, mapping.reason
            command_flow = mapping.flow or flow
            command_watts = float(mapping.target_w or 0.0)
            if not representable:
                results.append({"site": site, "status": "unrepresentable_command",
                                "device_time": stamp, "flow": flow, "power_w": finite_watts,
                                "reason": command_reason, "write_enabled": False})
                continue
            record = {
                "site": site,
                "device_time": stamp,
                "flow": flow,
                "power_w": finite_watts,
                "command_flow": command_flow,
                "command_power_w": command_watts,
                "soc": state.get("soc"),
                "reason": reason,
                "write_enabled": write_enabled,
                "ha_device_time_raw": stamp,
                "resolved_device_time": str(resolved),
                **batch["metadata"],
            }
            if write_enabled:
                if command_flow == "self_consumption":
                    ha.set_self_consumption(suffix, live=True)
                else:
                    ha.set_dispatch(suffix, command_flow, command_watts, live=True)
            results.append(record)
        except Exception as exc:  # isolate one failed station from the other two
            results.append({"site": site, "status": "station_error", "error": str(exc)})
    return results


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _append_audit(path: Path, rows: list[dict]) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(path, mode="a", index=False, header=not path.exists(),
                              encoding="utf-8")


def run_once_milp(cfg: dict, ha: HAClient, sim: SimulatorClient, *, write_enabled: bool,
                  carry: dict, audit_csv: Path | None = None,
                  reconciliation_store: ReconciliationStore | None = None) -> list[dict]:
    """One step-4 chain tick per station. ``carry`` persists across ticks:
    ``carry["verified"]`` holds the in-memory site verification flags (seeded
    from config, revocable by the SOC guard); ``carry["previous"]`` keeps each
    site's last candidate/record for next-tick readback and divergence checks.
    """
    round_started = time.monotonic()
    round_limit = float(cfg.get("round_deadline_seconds", cfg.get("poll_seconds", 5.0)))
    if not math.isfinite(round_limit) or round_limit <= 0:
        raise ValueError("invalid_round_deadline_seconds")
    round_deadline = round_started + round_limit
    generation_lock = carry.setdefault("round_generation_lock", threading.Lock())
    with generation_lock:
        generation = carry.get("round_generation", 0) + 1
        carry["round_generation"] = generation
        if reconciliation_store is not None:
            # The disk journal is authoritative after restart or manual clear.
            carry["delivery_unknown"] = reconciliation_store.pending()
    batch = read_anchored_tick(cfg, ha, sim, price_window,
                               price_validator=validate_milp_price_window)
    points = batch["points"]
    results: list[dict] = []
    audit_rows: list[dict] = []
    for site, item in sorted(cfg["stations"].items(), key=lambda pair: str(pair[0])):
        suffix = str(item["suffix"])
        try:
            if site not in batch["states"] and (
                    site not in batch["errors"] or
                    batch["errors"][site].startswith("ha_read_error:")):
                results.append({"site": site, "status": "station_error",
                                "error": batch["errors"].get(site), "writes_performed": 0})
                continue
            state = batch["states"].get(site, {})
            stamp = state.get("device_time")
            freshness = state.get("device_time_last_changed")
            if (batch["errors"].get(site) == "stale_or_unavailable" or
                    not batch["errors"].get(site) and
                    (not stamp or not freshness or "unavailable" in str(stamp).lower())):
                results.append({"site": site, "status": "stale_or_unavailable", "device_time": stamp})
                continue
            if not batch["errors"].get(site) and not state_is_fresh(freshness, max_age_seconds=float(cfg.get("stale_after_seconds", 15.))):
                results.append({"site": site, "status": "stale_or_unavailable", "device_time": stamp})
                continue

            verified = carry.setdefault("verified", {}).setdefault(
                site, bool(item.get("zero_flow_verified", False)))
            soc_now = numeric(state.get("soc"))
            guard = None
            previous_verdict = None
            previous = carry.setdefault("previous", {}).get(site)
            if site in carry.setdefault("delivery_unknown", {}):
                # HA service calls are not atomic. A timeout/5xx may have
                # applied a prefix of the command; sensor state alone cannot
                # prove its mode/flow/value. Keep the site write-quarantined
                # until a separate, explicit reconciliation clears this flag.
                results.append({"site": site, "status": "reconciliation_required",
                                "device_time": stamp, "write_enabled": False,
                                "writes_performed": 0, "optimizer_attributed": False,
                                "attribution": "non-optimizer-attributed",
                                "reason": "previous_delivery_unknown",
                                "read_only_state_observed": True,
                                "previous_delivery": carry["delivery_unknown"][site]})
                continue
            if previous is not None:
                prev_candidate, prev_record = previous["candidate"], previous["record"]
                if prev_record["writes_performed"] and prev_record["command_mode"] in (
                        "dispatch", "zero_flow_command"):
                    # The previous command is verified against the state of this
                    # tick; a command is never marked verified synchronously.
                    check = verify_readback(
                        CommandMapping(True, prev_record["command_flow"], prev_record["target_w"],
                                       "executed_command"),
                        {**state, "device_time": batch["times"].get(site, stamp)},
                        sent_device_time=prev_candidate.get("device_time"),
                        issued_at=previous["issued_at"], now=_utcnow_iso())
                    previous_verdict = check.reason
                if prev_record["writes_performed"] and math.isfinite(soc_now):
                    # Step-3 guard: only a command that was actually sent can
                    # convict the device. Dry-run SOC movement is expected and
                    # never revokes anything.
                    guard = check_soc_divergence(
                        planned_flow=prev_record["planned_flow"],
                        planned_soc_percent=prev_candidate.get("planned_soc_after_15min"),
                        actual_soc_percent=soc_now,
                        zero_flow_verified=verified)
                    if guard["guard_action"] == "revoke_zero_flow_verified":
                        carry["verified"][site] = verified = False

            resolved = batch["times"].get(site)
            price_error = batch["errors"].get(site) or validate_milp_price_window(points, resolved)
            if price_error:
                candidate = {"status": "blocked", "solver_status": "not_run",
                             "device_time": stamp, "fallback": price_error}
                solve_elapsed = 0.0
            else:
                try:
                    candidate, deadline_error, solve_elapsed = solve_until(
                        round_deadline, lambda: make_candidate(
                            {**state, "device_time": resolved.isoformat()}, points,
                            solver_seconds=float(cfg.get("solver_seconds", 2.0)),
                            capacity=float(cfg["capacity_kwh"]), max_power=float(cfg["max_power_w"]),
                            soc_min=float(cfg["soc_min"]), soc_max=float(cfg["soc_max"]),
                            idle_candidate="zero_charge_unverified" if verified else "legacy_hold"))
                    if deadline_error:
                        candidate = {"status": "blocked", "solver_status": "not_run",
                                     "device_time": stamp, "fallback": deadline_error}
                except Exception as exc:
                    # Keep failures outside the explicitly classified price
                    # policy on the existing fail-closed path.
                    results.append({"site": site, "status": "candidate_error", "device_time": stamp,
                                    "error": str(exc), "write_enabled": False})
                    continue
            issued_at = _utcnow_iso()
            with generation_lock:
                safety_error = None
                if carry.get("round_generation") != generation:
                    safety_error = "superseded_round"
                elif time.monotonic() >= round_deadline:
                    safety_error = "round_deadline_exceeded"
                if safety_error:
                    candidate = {"status": "blocked", "solver_status": "not_run",
                                 "device_time": stamp, "fallback": safety_error}
                record = execute_candidate(candidate, suffix=suffix, ha=ha, site=site,
                                           zero_flow_verified=verified,
                                           reconciliation_store=reconciliation_store,
                                           deadline=round_deadline,
                                           current=lambda: carry.get("round_generation") == generation,
                                           live=write_enabled and safety_error is None and
                                                candidate.get("fallback") not in (
                                                    "solver_deadline_exceeded",
                                                    "previous_solver_still_running",
                                                    "round_deadline_exceeded"),
                                           readback_state=None, issued_at=issued_at, now=issued_at)
                if carry.get("round_generation") == generation:
                    carry["previous"][site] = {"candidate": candidate, "record": record,
                                               "issued_at": issued_at}
                    if record["requires_reconciliation"]:
                        carry["delivery_unknown"][site] = (
                            reconciliation_store.pending()[site] if reconciliation_store is not None
                            else {"device_time": stamp, "delivery_steps": record["delivery_steps"],
                                  "reason": record["reason"]})
            row = {"received_at": _utcnow_iso(), "site": site,
                   "observed_soc": state.get("soc"), "observed_pv": state.get("solar_power"),
                   "observed_load": state.get("home_load"), "write_enabled": write_enabled,
                   **record,
                   "ha_device_time_raw": stamp,
                   "ha_entity_diagnostics": state.get("entity_diagnostics"),
                   "ha_missing_entities": state.get("missing_entities"),
                   "resolved_device_time": None if resolved is None else resolved.isoformat(),
                   **batch["metadata"],
                   "alignment_attempts": batch["alignment_attempts"],
                   "alignment_rejection_reason": price_error,
                   "solve_elapsed_seconds": solve_elapsed,
                   "round_deadline_seconds": round_limit,
                   "round_generation": generation,
                   "execution_safety_rejection": safety_error or candidate.get("fallback") if
                       candidate.get("fallback") in ("solver_deadline_exceeded",
                                                     "previous_solver_still_running",
                                                     "round_deadline_exceeded") else safety_error,
                   "attribution": ("optimizer-attributed" if record["optimizer_attributed"] else
                                   "non-optimizer-attributed"),
                   "previous_readback_verdict": previous_verdict,
                   "guard_action": None if guard is None else guard["guard_action"],
                   "guard_reason": None if guard is None else guard["reason"],
                   "soc_divergence_percent": None if guard is None else guard["soc_divergence_percent"]}
            audit_rows.append(row)
            results.append(row)
        except Exception as exc:  # isolate one failed station from the other two
            results.append({"site": site, "status": "station_error", "error": str(exc)})
    round_elapsed = time.monotonic() - round_started
    for row in results:
        row["round_elapsed_seconds"] = round_elapsed
        row["serial_solver_worst_seconds"] = batch["metadata"].get("serial_solver_worst_seconds")
        row["serial_tick_budget_floor_seconds"] = batch["metadata"].get("serial_tick_budget_floor_seconds")
        row["serial_tick_fits_poll"] = batch["metadata"].get("serial_tick_fits_poll")
    for row in audit_rows:
        row["round_elapsed_seconds"] = round_elapsed
    if audit_csv is not None:
        _append_audit(Path(audit_csv), audit_rows)
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="Online storage policy runner")
    parser.add_argument("--config", type=Path, default=Path("config.json"))
    parser.add_argument("--mode", choices=("dry-run", "live"), default=None)
    parser.add_argument("--once", action="store_true", help="run one tick and exit")
    parser.add_argument("--strategy", choices=("dispatch", "milp"), default="dispatch",
                        help="dispatch: legacy rule entry; milp: step-4 shadow/execute chain")
    parser.add_argument("--audit-csv", type=Path, default=Path("outputs/run_live_audit.csv"),
                        help="milp strategy audit log, appended every tick")
    parser.add_argument("--reconciliation-state", type=Path,
                        default=Path("outputs/ha_reconciliation_state.json"),
                        help="durable live MILP delivery quarantine; preserve across restarts")
    args = parser.parse_args()
    cfg = load_runtime_config(args.config, args.mode)
    mode = str(cfg["mode"]).lower().replace("_", "-")
    write_enabled = mode == "live"
    require_runtime_fields(cfg, write_enabled=write_enabled)
    token = exchange_ha_token(cfg)
    ha = HAClient(str(cfg["ha_base"]), token)
    sim = SimulatorClient(str(cfg["sim_base"]), str(cfg["sim_username"]), str(cfg["sim_password"]))
    print(f"mode={mode}; write_enabled={write_enabled}; strategy={args.strategy}; config={cfg['source']}")
    if write_enabled:
        print("WARNING: HA write mode is enabled by all safety gates")
        if args.strategy == "milp":
            for site, item in sorted(cfg["stations"].items(), key=lambda pair: str(pair[0])):
                if not item.get("zero_flow_verified", False):
                    print(f"site {site}: zero_flow_verified missing/false; "
                          "live idle steps will be refused (fourth safety switch)")
    carry: dict = {}
    reconciliation_store = (ReconciliationStore(args.reconciliation_state)
                            if write_enabled and args.strategy == "milp" else None)
    while True:
        loop_started = time.monotonic()
        if args.strategy == "milp":
            tick_results = run_once_milp(cfg, ha, sim, write_enabled=write_enabled,
                                         carry=carry, audit_csv=args.audit_csv,
                                         reconciliation_store=reconciliation_store)
        else:
            tick_results = run_once(cfg, ha, sim, write_enabled=write_enabled)
        for result in tick_results:
            print(result, flush=True)
        if args.once:
            return
        time.sleep(max(0.1, float(cfg["poll_seconds"]) - (time.monotonic() - loop_started)))


if __name__ == "__main__":
    main()
