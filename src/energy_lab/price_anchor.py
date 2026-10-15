"""Online device-clock alignment using an explicit, bracketed API current slot.

No wall clock, price-value matching, array positions, or inferred IANA timezone.
"""
from __future__ import annotations

import math
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


def read_anchored_tick(cfg, ha, sim, price_window, *, price_validator=None, max_attempts=3):
    """Bounded full rereads; per-site faults remain isolated in the final batch."""
    from energy_lab.interface_contract import state_is_fresh

    for attempt in range(1, max_attempts + 1):
        points, states, errors, times, metadata = [], {}, {}, {}, {}
        payload_error = None
        try:
            before = sim.current_price()
            if isinstance(before, dict):
                metadata = {"api_current_start": before.get("start"), "api_current_end": before.get("end"),
                            "selected_price_slot": None, "alignment_source": None}
            points = price_window(sim.prices())
        except Exception as exc:
            payload_error = str(exc)
        for site, item in cfg["stations"].items():
            try:
                states[site] = ha.station_state(str(item["suffix"]))
            except Exception as exc:
                errors[site] = f"ha_read_error:{type(exc).__name__}"
        try:
            after = sim.current_price()
            if payload_error:
                raise ValueError(payload_error)
            start, metadata = anchor_prices(points, before, after)
            for site, state in states.items():
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
        except Exception as exc:
            errors = {site: f"price_anchor_rejected:{exc}" for site in cfg["stations"]}
        if not errors:
            break
    return {"points": points, "states": states, "errors": errors, "times": times,
            "metadata": metadata, "alignment_attempts": attempt}
