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

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
import argparse
import collections
import json
import math
import sys
import threading
import time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from energy_lab.config import exchange_ha_access, load_runtime_config, require_runtime_fields
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
                    batch["errors"][site].startswith(("ha_read_error:", "ha_snapshot_error:"))):
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


def _append_price_log(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")


def _station_solver_seconds(cfg: dict, site: str) -> float:
    """Per-station solver budget override; falls back to the global value.

    The round deadline (5 s hard cap) is enforced separately by
    ``solve_until``/``round_deadline``, so an override can never stretch the
    round — it only reallocates the serial budget between stations.
    """
    override = (cfg.get("stations", {}).get(site) or {}).get("solver_seconds")
    if override is None:
        return float(cfg.get("solver_seconds", 2.0))
    value = float(override)
    if not math.isfinite(value) or value <= 0:
        raise ValueError("invalid_station_solver_seconds")
    return value


def _v16_forecast_frame(carry: dict, site: str, state: dict, resolved) -> "pd.DataFrame | None":
    """Build a 96-step forecast frame for ``make_candidate`` from the frozen V16
    load forecaster. PV stays persistence (current observation); load uses the
    last 18 observed 15-min samples, refreshed every 4 rounds, with the final
    predicted step held flat beyond the 12-step model horizon (same policy as
    the offline replay runner). Returns None until enough history accumulated.
    """
    history = carry.setdefault("v16_history", {}).setdefault(site, collections.deque(maxlen=18))
    history.append(max(0.0, float(state["home_load"])))
    if len(history) < 18:
        return None
    generation = carry.get("round_generation", 0)
    cache = carry.setdefault("v16_cache", {}).get(site)
    if cache is None or generation - cache["generation"] >= 4:
        forecaster = carry.get("v16_forecaster")
        if forecaster is None:
            from energy_lab.load_forecast_v16 import LoadForecastV16
            forecaster = carry["v16_forecaster"] = LoadForecastV16()
        future = [max(0.0, float(v)) for v in forecaster.forecast_w(list(history))]
        cache = {"generation": generation, "future": future}
        carry["v16_cache"][site] = cache
    future = cache["future"]
    origin = pd.Timestamp(resolved)
    if origin.tzinfo is None:
        origin = origin.tz_localize("UTC")
    times = pd.date_range(origin, periods=96, freq="15min")
    loads = (future + [future[-1]] * 96)[:96]
    return pd.DataFrame({"time": times,
                         "pv": max(0.0, float(state["solar_power"])),
                         "load": loads})


def run_once_milp(cfg: dict, ha: HAClient, sim: SimulatorClient, *, write_enabled: bool,
                  carry: dict, audit_csv: Path | None = None,
                  reconciliation_store: ReconciliationStore | None = None,
                  delivery_executor: ThreadPoolExecutor | None = None,
                  price_log: Path | None = None,
                  load_forecast: str = "persistence") -> list[dict]:
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
    if price_log is not None and points:
        # Persist the full price window once per round (shared by all three
        # sites) so post-match analysis can reconstruct the complete contest
        # price series and compute the theoretical optimum at finer granularity.
        _append_price_log(price_log, {
            "received_at": _utcnow_iso(),
            "round_generation": generation,
            "api_current_start": batch["metadata"].get("api_current_start"),
            "api_current_end": batch["metadata"].get("api_current_end"),
            "selected_price_slot": batch["metadata"].get("selected_price_slot"),
            "points": points,
        })
    # A delivery halt is a shared HA-path concern, not a permanent judgement on
    # the unaffected sites. Observe a couple of clean acquisition rounds (every
    # site read fresh, no read errors) and then resume writes to those sites.
    # The site whose delivery was uncertain stays write-quarantined in
    # ``delivery_unknown`` until explicit operator reconciliation.
    if carry.get("global_delivery_halt"):
        stations = set(cfg["stations"])
        ha_healthy = (set(batch["states"]) == stations
                      and all(not batch["errors"].get(site) for site in stations))
        clean = carry.get("halt_clean_observations", 0)
        clean = clean + 1 if ha_healthy else 0
        carry["halt_clean_observations"] = clean
        if clean >= 2:
            carry["global_delivery_halt"] = False
            carry["halt_clean_observations"] = 0
    results_by_site: dict = {}
    audit_rows: list[dict] = []
    plans: list[dict] = []
    for site, item in sorted(cfg["stations"].items(), key=lambda pair: str(pair[0])):
        suffix = str(item["suffix"])
        try:
            if site not in batch["states"] and (
                    site not in batch["errors"] or
                    batch["errors"][site].startswith(("ha_read_error:", "ha_snapshot_error:"))):
                results_by_site[site] = {"site": site, "status": "station_error",
                                         "error": batch["errors"].get(site), "writes_performed": 0}
                continue
            state = batch["states"].get(site, {})
            stamp = state.get("device_time")
            freshness = state.get("device_time_last_changed")
            if (batch["errors"].get(site) == "stale_or_unavailable" or
                    not batch["errors"].get(site) and
                    (not stamp or not freshness or "unavailable" in str(stamp).lower())):
                results_by_site[site] = {"site": site, "status": "stale_or_unavailable", "device_time": stamp}
                continue
            if not batch["errors"].get(site) and not state_is_fresh(freshness, max_age_seconds=float(cfg.get("stale_after_seconds", 15.))):
                results_by_site[site] = {"site": site, "status": "stale_or_unavailable", "device_time": stamp}
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
                results_by_site[site] = {"site": site, "status": "reconciliation_required",
                                         "device_time": stamp, "write_enabled": False,
                                         "writes_performed": 0, "optimizer_attributed": False,
                                         "attribution": "non-optimizer-attributed",
                                         "reason": "previous_delivery_unknown",
                                         "read_only_state_observed": True,
                                         "previous_delivery": carry["delivery_unknown"][site]}
                continue
            if write_enabled and carry.get("global_delivery_halt"):
                results_by_site[site] = {"site": site, "status": "write_suspended",
                                         "device_time": stamp, "write_enabled": False,
                                         "writes_performed": 0, "optimizer_attributed": False,
                                         "reason": "another_site_delivery_failed_in_this_runner",
                                         "read_only_state_observed": True}
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
                    forecast_df = None
                    forecast_mode = None
                    if load_forecast == "v16":
                        forecast_df = _v16_forecast_frame(carry, site, state, resolved)
                        if forecast_df is not None:
                            forecast_mode = "v16"
                    candidate, deadline_error, solve_elapsed = solve_until(
                        round_deadline, lambda: make_candidate(
                            {**state, "device_time": resolved.isoformat()}, points,
                            solver_seconds=_station_solver_seconds(cfg, site),
                            capacity=float(cfg["capacity_kwh"]), max_power=float(cfg["max_power_w"]),
                            soc_min=float(cfg["soc_min"]), soc_max=float(cfg["soc_max"]),
                            forecast=forecast_df, forecast_mode=forecast_mode,
                            idle_candidate="zero_charge_unverified" if verified else "legacy_hold"))
                    if deadline_error:
                        candidate = {"status": "blocked", "solver_status": "not_run",
                                     "device_time": stamp, "fallback": deadline_error}
                except Exception as exc:
                    # Keep failures outside the explicitly classified price
                    # policy on the existing fail-closed path.
                    results_by_site[site] = {"site": site, "status": "candidate_error",
                                             "device_time": stamp, "error": str(exc),
                                             "write_enabled": False}
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
            plans.append({"site": site, "suffix": suffix, "candidate": candidate,
                          "verified": verified, "issued_at": issued_at,
                          "state": state, "stamp": stamp, "resolved": resolved,
                          "previous_verdict": previous_verdict, "guard": guard,
                          "safety_error": safety_error, "price_error": price_error,
                          "solve_elapsed": solve_elapsed,
                          "live": write_enabled and safety_error is None and
                              candidate.get("fallback") not in (
                                  "solver_deadline_exceeded",
                                  "previous_solver_still_running",
                                  "round_deadline_exceeded")})
        except Exception as exc:  # isolate one failed station from the other two
            results_by_site[site] = {"site": site, "status": "station_error", "error": str(exc)}

    # Phase 2 (parallel): deliver each site's candidate concurrently. The write
    # path (fail-closed journal, SOP mode-gap) is per-site; workers never share
    # the read-path ``ha.session`` (HAClient hands each thread its own Session).
    def _deliver(plan: dict):
        try:
            record = execute_candidate(
                plan["candidate"], suffix=plan["suffix"], ha=ha, site=plan["site"],
                zero_flow_verified=plan["verified"],
                reconciliation_store=reconciliation_store, deadline=round_deadline,
                current=lambda: carry.get("round_generation") == generation,
                live=plan["live"], readback_state=None,
                issued_at=plan["issued_at"], now=plan["issued_at"])
            return plan, record, None
        except Exception as exc:
            return plan, None, exc

    deliveries: list[tuple] = []
    if plans:
        if delivery_executor is not None:
            # Reused across rounds so worker threads (and their thread-local
            # HA Sessions) keep their connections warm via keep-alive.
            futures = [delivery_executor.submit(_deliver, p) for p in plans]
            deliveries = [f.result() for f in futures]
        else:
            with ThreadPoolExecutor(max_workers=len(plans), thread_name_prefix="delivery") as ex:
                futures = [ex.submit(_deliver, p) for p in plans]
                deliveries = [f.result() for f in futures]

    # Phase 3 (serial, original site order): fold results back into the audit
    # stream and the shared ``carry`` state (previous + halt + quarantine).
    for plan, record, exc in deliveries:
        site = plan["site"]
        if exc is not None:
            results_by_site[site] = {"site": site, "status": "station_error", "error": str(exc)}
            continue
        if carry.get("round_generation") == generation:
            carry["previous"][site] = {"candidate": plan["candidate"], "record": record,
                                       "issued_at": plan["issued_at"]}
            if record["requires_reconciliation"]:
                # One uncertain POST is a shared HA-path incident. Keep
                # observing, but send no more commands from this runner; the
                # next round's other sites halt until two clean reads.
                carry["global_delivery_halt"] = True
                carry.setdefault("delivery_unknown", {})[site] = (
                    reconciliation_store.pending()[site] if reconciliation_store is not None
                    else {"device_time": plan["stamp"], "delivery_steps": record["delivery_steps"],
                          "reason": record["reason"]})
        row = {"received_at": _utcnow_iso(), "site": site,
               "observed_soc": plan["state"].get("soc"), "observed_pv": plan["state"].get("solar_power"),
               "observed_load": plan["state"].get("home_load"), "write_enabled": write_enabled,
               **record,
               "ha_device_time_raw": plan["stamp"],
               "ha_entity_diagnostics": plan["state"].get("entity_diagnostics"),
               "ha_missing_entities": plan["state"].get("missing_entities"),
               "resolved_device_time": None if plan["resolved"] is None else plan["resolved"].isoformat(),
               **batch["metadata"],
               "alignment_attempts": batch["alignment_attempts"],
               "alignment_rejection_reason": plan["price_error"],
               "solve_elapsed_seconds": plan["solve_elapsed"],
               "round_deadline_seconds": round_limit,
               "round_generation": generation,
               "execution_safety_rejection": plan["safety_error"] or plan["candidate"].get("fallback") if
                   plan["candidate"].get("fallback") in ("solver_deadline_exceeded",
                                                         "previous_solver_still_running",
                                                         "round_deadline_exceeded") else plan["safety_error"],
               "attribution": ("optimizer-attributed" if record["optimizer_attributed"] else
                               "non-optimizer-attributed"),
               "previous_readback_verdict": plan["previous_verdict"],
               "guard_action": None if plan["guard"] is None else plan["guard"]["guard_action"],
               "guard_reason": None if plan["guard"] is None else plan["guard"]["reason"],
               "soc_divergence_percent": None if plan["guard"] is None else plan["guard"]["soc_divergence_percent"]}
        audit_rows.append(row)
        results_by_site[site] = row

    results = [results_by_site[site] for site in sorted(results_by_site, key=str)]
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
    parser.add_argument("--load-forecast", choices=("persistence", "v16"), default="persistence",
                        help="milp strategy only: v16 feeds the frozen neural load forecaster "
                             "into the optimizer horizon (PV stays persistence)")
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
    access = exchange_ha_access(cfg)
    ha = HAClient(str(cfg["ha_base"]), access["access_token"], proxy_mode=cfg["proxy_mode"])
    refresh_at = (time.monotonic() + min(access["expires_in"] * .6, 1200)
                  if access["expires_in"] is not None else None)
    sim = SimulatorClient(str(cfg["sim_base"]), str(cfg["sim_username"]),
                          str(cfg["sim_password"]), proxy_mode=cfg["proxy_mode"])
    print(f"mode={mode}; write_enabled={write_enabled}; strategy={args.strategy}; "
          f"proxy_mode={cfg['proxy_mode']}; config={cfg['source']}")
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
    # Reuse one executor across rounds so the delivery worker threads (and their
    # thread-local HA Sessions) keep their connections warm via keep-alive.
    delivery_executor = (ThreadPoolExecutor(max_workers=max(1, len(cfg["stations"])),
                                            thread_name_prefix="delivery")
                         if args.strategy == "milp" else None)
    while True:
        loop_started = time.monotonic()
        if refresh_at is not None and loop_started >= refresh_at:
            try:
                access = exchange_ha_access(cfg)
                ha.replace_token(access["access_token"])
                refresh_at = time.monotonic() + min(access["expires_in"] * .6, 1200)
                print("HA access token refreshed", flush=True)
            except Exception as exc:
                carry["global_delivery_halt"] = True
                print(f"HA token refresh failed; writes halted: {type(exc).__name__}", flush=True)
        if args.strategy == "milp":
            tick_results = run_once_milp(cfg, ha, sim, write_enabled=write_enabled,
                                         carry=carry, audit_csv=args.audit_csv,
                                         reconciliation_store=reconciliation_store,
                                         delivery_executor=delivery_executor,
                                         load_forecast=args.load_forecast)
        else:
            tick_results = run_once(cfg, ha, sim, write_enabled=write_enabled)
        for result in tick_results:
            print(result, flush=True)
        if args.once:
            return
        time.sleep(max(0.1, float(cfg["poll_seconds"]) - (time.monotonic() - loop_started)))


if __name__ == "__main__":
    main()
