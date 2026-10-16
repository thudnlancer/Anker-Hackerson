"""Online device-clock alignment using an explicit, bracketed API current slot.

No wall clock, price-value matching, array positions, or inferred IANA timezone.
"""
from __future__ import annotations

import math
import time
import requests
from concurrent.futures import ThreadPoolExecutor
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
import pandas as pd


def slot(point: dict) -> tuple[pd.Timestamp, pd.Timestamp]:
    if not isinstance(point, dict):
        raise ValueError("current_slot_missing_or_invalid")
    try:
        start, end = pd.Timestamp(point["start"]), pd.Timestamp(point["end"])
        if pd.isna(start) or pd.isna(end) or start.tzinfo is None or end.tzinfo is None:
            raise ValueError("timezone_required")
        if start != start.floor("h") or end - start != pd.Timedelta(hours=1):
            raise ValueError("not_hourly")
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise ValueError("current_slot_missing_or_invalid") from exc
    return start, end


def anchor_prices(points: list[dict], before: dict, after: dict) -> tuple[pd.Timestamp, dict]:
    start, end = slot(before)
    if slot(after) != (start, end):
        raise ValueError("current_slot_changed_during_read")
    slots = []
    for point in points:
        # Normalized daily_points retain end, but use time instead of start.
        slots.append(slot({**point, "start": point.get("time", point.get("start"))}))
    starts = [s for s, _ in slots]
    if len(set(starts)) != len(starts):
        raise ValueError("duplicate_price_slot")
    ordered = sorted(slots)
    if any(left[1] != right[0] for left, right in zip(ordered, ordered[1:])):
        raise ValueError("missing_or_noncontinuous_price_slot")
    matches = [p for p, pair in zip(points, slots) if pair == (start, end)]
    if len(matches) != 1:
        raise ValueError("current_slot_not_unique_in_price_window")
    # Prices are consistency checks only, never locators.
    for field in ("import_price", "export_price"):
        try:
            values = [float(p[field]) for p in (before, after, matches[0])]
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise ValueError(f"missing_price_data:{field}") from exc
        if not all(math.isfinite(v) for v in values):
            raise ValueError(f"non_finite_price:{field}")
        if not (values[0] == values[1] == values[2]):
            raise ValueError(f"current_slot_price_conflict:{field}")
    return start, {"api_current_start": before["start"], "api_current_end": before["end"],
                   "selected_price_slot": matches[0].get("time", matches[0].get("start")),
                   "alignment_source": "api_current_slot"}


def anchor_device_time(raw: object, start: pd.Timestamp,
                       device_timezone: str | None = None) -> pd.Timestamp:
    try:
        tick = pd.Timestamp(raw)
        if pd.isna(tick):
            raise ValueError("invalid")
        # Compare the API's literal local date/hour labels, not an invented zone.
        if tick.strftime("%Y-%m-%d %H") != start.strftime("%Y-%m-%d %H"):
            raise ValueError("device_api_date_hour_conflict")
        resolved = start + (tick - tick.floor("h"))
        if tick.tzinfo is not None and tick != resolved:
            raise ValueError("device_api_offset_conflict")
        if device_timezone:
            try:
                ZoneInfo(device_timezone)
            except (ZoneInfoNotFoundError, ValueError, TypeError) as exc:
                raise ValueError("device_time_misaligned:invalid_device_timezone") from exc
            configured = tick.tz_localize(device_timezone) if tick.tzinfo is None else tick.tz_convert(device_timezone)
            if configured != resolved:
                raise ValueError("explicit_timezone_api_conflict")
        return resolved
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise ValueError(str(exc) or "invalid_device_time") from exc


def _retryable(error):
    """Explicit allowlist: malformed inputs/configuration are never retried."""
    return any(token in str(error) for token in (
        "current_slot_changed_during_read", "device_api_date_hour_conflict",
        "current_slot_price_conflict", "temporary", "Timeout", "ConnectionError"))


def read_anchored_tick(cfg, ha, sim, price_window, *, price_validator=None, max_attempts=3):
    """One acquisition-round deadline, transient-only retries, isolated site faults."""
    from energy_lab.interface_contract import state_is_fresh
    from energy_lab.read_budget import ACTIVE_READ_BUDGET, ReadBudget

    started = time.monotonic()
    points, states, errors, times, metadata = [], {}, {}, {}, {}
    attempt, operations = 0, []
    # Acquisition-only deadline. Three serial MILPs can consume 3 x the
    # per-site solver limit; never pretend one 2 s reserve covers them all.
    try:
        poll = float(cfg.get("poll_seconds", 5))
        solver = float(cfg.get("solver_seconds", 2))
        seconds = float(cfg.get("alignment_budget_seconds", min(2.5, poll - solver - .25)))
        if not math.isfinite(seconds) or seconds <= 0 or seconds >= poll:
            raise ValueError("invalid_alignment_budget")
        config_error = None
        if cfg.get("device_timezone"):
            try:
                ZoneInfo(cfg["device_timezone"])
            except (ZoneInfoNotFoundError, ValueError, TypeError):
                config_error = "device_time_misaligned:invalid_device_timezone"
        stale_seconds = float(cfg.get("stale_after_seconds", 15))
        if not math.isfinite(stale_seconds) or stale_seconds <= 0:
            raise ValueError("invalid_stale_after_seconds")
    except Exception as exc:
        return {"points": [], "states": {}, "times": {},
                "errors": {s: f"invalid_anchor_config:{type(exc).__name__}:{exc}" for s in cfg["stations"]},
                "metadata": {"alignment_elapsed_seconds": time.monotonic() - started,
                             "alignment_stop_reason": "invalid_anchor_config",
                             "alignment_requests": [], "alignment_operations": []},
                "alignment_attempts": 0}
    budget = ReadBudget(seconds, started=started)
    token = ACTIVE_READ_BUDGET.set(budget)
    permanent = {}
    stop_reason = "accepted"

    def read(label, operation):
        begin = time.monotonic()
        row = {"operation": label, "attempt": attempt,
               "started_offset_seconds": begin - started}
        try:
            budget.remaining()
            value = operation()
            budget.remaining()
            row["status"] = "ok"
            return value
        except Exception as exc:
            row.update(status="error", error_type=type(exc).__name__)
            raise
        finally:
            row["elapsed_seconds"] = time.monotonic() - begin
            operations.append(row)

    try:
      for attempt in range(1, max_attempts + 1):
        if attempt > 1 and time.monotonic() - started >= seconds:
            attempt -= 1
            stop_reason = "deadline_exceeded"
            break
        times, errors = {}, dict(permanent)
        if config_error:
            errors = {s: config_error for s in cfg["stations"]}
            stop_reason = "deterministic_rejection"
            break
        # Parallel acquisition: overlap the simulator read (current.before then
        # prices, one shared SimulatorClient Session) with the HA snapshot read
        # (a separate HAClient Session). The two branches never race a single
        # Session, but their network round-trips overlap. Each branch keeps its
        # own error so the per-source rejection/retry semantics are preserved.
        def _read_sim():
            before_value = read("sim.current.before", sim.current_price)
            slot(before_value)  # Invalid payload is deterministic.
            points_value = price_window(read("sim.prices", sim.prices))
            return before_value, points_value

        def _read_snapshot():
            return read("ha.snapshot", ha.snapshot)

        need_snapshot = hasattr(ha, "snapshot") and len(permanent) < len(cfg["stations"])
        sim_outcome = None
        sim_error = None
        snapshot_outcome = None
        snapshot_error = None
        with ThreadPoolExecutor(max_workers=2) as _executor:
            sim_future = _executor.submit(_read_sim)
            snapshot_future = _executor.submit(_read_snapshot) if need_snapshot else None
            try:
                sim_outcome = sim_future.result()
            except Exception as exc:
                sim_error = exc
            if snapshot_future is not None:
                try:
                    snapshot_outcome = snapshot_future.result()
                except Exception as exc:
                    snapshot_error = exc

        if sim_error is not None:
            reason = f"price_anchor_rejected:{sim_error}"
            errors.update({s: reason for s in cfg["stations"] if s not in permanent})
            if _retryable(reason) or isinstance(sim_error, (TimeoutError, ConnectionError,
                                                            requests.exceptions.Timeout,
                                                            requests.exceptions.ConnectionError)):
                if time.monotonic() - started >= seconds:
                    stop_reason = "deadline_exceeded"
                    break
                stop_reason = "transient_attempt_limit"
                continue
            stop_reason = "deterministic_rejection"
            if time.monotonic() - started < seconds:
                # The parallel HA read (if it succeeded) doubles as the
                # bad-price diagnostic snapshot; otherwise fall back per-station.
                bad_price_snapshot = snapshot_outcome if snapshot_error is None else None
                for site, item in cfg["stations"].items():
                    try:
                        if bad_price_snapshot is not None:
                            states[site] = ha.station_from_snapshot(str(item["suffix"]), bad_price_snapshot)
                        elif not hasattr(ha, "snapshot"):
                            states[site] = read(f"ha.station.{site}",
                                                lambda: ha.station_state(str(item["suffix"])))
                    except Exception as ha_exc:
                        errors[site] = f"ha_read_error:{type(ha_exc).__name__}:{ha_exc}"
            break

        before, points = sim_outcome
        if isinstance(before, dict):
            metadata = {"api_current_start": before.get("start"), "api_current_end": before.get("end"),
                        "selected_price_slot": None, "alignment_source": None}
        snapshot = None
        if need_snapshot:
            if snapshot_error is not None:
                reason = f"ha_snapshot_error:{type(snapshot_error).__name__}:{snapshot_error}"
                errors.update({s: reason for s in cfg["stations"] if s not in permanent})
                if not _retryable(reason):
                    permanent.update({s: reason for s in cfg["stations"] if s not in permanent})
                stop_reason = "transient_attempt_limit" if _retryable(reason) else "deterministic_rejection"
                if not _retryable(reason):
                    break
                continue
            snapshot = snapshot_outcome
        for site, item in cfg["stations"].items():
            if site in permanent:
                continue
            try:
                states[site] = (ha.station_from_snapshot(str(item["suffix"]), snapshot)
                                if snapshot is not None else read(
                                    f"ha.station.{site}",
                                    lambda: ha.station_state(str(item["suffix"]))))
                for diagnostic in states[site].get("entity_diagnostics", {}).values():
                    diagnostic["fresh"] = bool(diagnostic.get("last_changed") and state_is_fresh(
                        diagnostic["last_changed"], max_age_seconds=stale_seconds))
                if states[site].get("missing_entities"):
                    permanent[site] = errors[site] = "missing_or_duplicate_ha_entities:" + ",".join(
                        states[site]["missing_entities"])
                    continue
                if not state_is_fresh(states[site].get("device_time_last_changed"),
                                      max_age_seconds=stale_seconds):
                    permanent[site] = errors[site] = "stale_or_unavailable"
            except Exception as exc:
                errors[site] = f"ha_read_error:{type(exc).__name__}:{exc}"
                if not _retryable(errors[site]):
                    permanent[site] = errors[site]
        if len(permanent) == len(cfg["stations"]):
            stop_reason = "deterministic_rejection"
            break
        try:
            after = read("sim.current.after", sim.current_price)
            try:
                start, metadata = anchor_prices(points, before, after)
            except Exception as exc:
                if "current_slot_changed_during_read" not in str(exc):
                    raise
                # Hour-boundary straddle: `after` landed in the next hour because the
                # simulator's continuous clock crossed the boundary mid-read, while the
                # device's own discrete clock (device_time, read in between) is still in
                # `before`'s hour. Accept only a forward single-hour crossing; the
                # per-site anchor_device_time check below still enforces device-vs-slot
                # hour consistency, so a genuine misalignment is still rejected/retried.
                b_start, b_end = slot(before)
                if slot(after) != (b_start + pd.Timedelta(hours=1), b_end + pd.Timedelta(hours=1)):
                    raise
                start, metadata = anchor_prices(points, before, before)
            for site, state in states.items():
                if site in errors:
                    continue
                try:
                    if not state_is_fresh(state.get("device_time_last_changed"),
                                          max_age_seconds=float(cfg.get("stale_after_seconds", 15))):
                        raise ValueError("stale_or_unavailable")
                    resolved = anchor_device_time(state.get("device_time"), start,
                                                  cfg.get("device_timezone"))
                    if price_validator:
                        error = price_validator(points, resolved)
                        if error:
                            raise ValueError(error)
                    times[site] = resolved
                except Exception as exc:
                    errors[site] = str(exc)
                    if not _retryable(errors[site]):
                        permanent[site] = errors[site]
        except Exception as exc:
            times = {}
            errors.update({site: f"price_anchor_rejected:{exc}"
                           for site in cfg["stations"] if site not in permanent})
        if not any(_retryable(error) for s, error in errors.items() if s not in permanent):
            stop_reason = "accepted" if not errors else "deterministic_rejection"
            break
        stop_reason = "transient_attempt_limit"
    finally:
        ACTIVE_READ_BUDGET.reset(token)
    elapsed = time.monotonic() - started
    if elapsed >= seconds:
        times = {}
        errors.update({s: "alignment_deadline_exceeded" for s in cfg["stations"] if s not in permanent})
        stop_reason = "deadline_exceeded"
    metadata.update(alignment_budget_seconds=seconds, alignment_elapsed_seconds=elapsed,
                    serial_solver_worst_seconds=solver * len(cfg["stations"]),
                    serial_tick_budget_floor_seconds=seconds + solver * len(cfg["stations"]),
                    serial_tick_fits_poll=(seconds + solver * len(cfg["stations"]) <= poll),
                    alignment_stop_reason=stop_reason, alignment_requests=budget.requests,
                    alignment_operations=operations)
    return {"points": points, "states": states, "errors": errors, "times": times,
            "metadata": metadata, "alignment_attempts": attempt}
