"""Read-only snapshot to SOP-constrained optimization. No control client imported."""
from dataclasses import asdict
import numpy as np
import pandas as pd

from .interface_contract import align_hourly_points, map_battery_action, state_is_fresh
from .rolling import optimize_window
from .api_policy import build_api_power_path


def _api_timezone(points):
    """Get the timezone carried by an API timestamp, without using wall time."""
    for point in points:
        if not isinstance(point, dict):
            continue
        value = next((point[key] for key in ("time", "timestamp", "datetime", "start", "start_time")
                      if point.get(key) is not None), None)
        if value is None:
            continue
        stamp = pd.Timestamp(value)
        if stamp.tzinfo is not None:
            return stamp.tz
    return None


def _infer_api_timezone(prices, weather_payload):
    price_timezone = _api_timezone(prices)
    if price_timezone is not None:
        return price_timezone
    if isinstance(weather_payload, dict):
        weather_points = []
        for day in ("today", "tomorrow"):
            block = weather_payload.get(day)
            if isinstance(block, dict) and isinstance(block.get("points"), list):
                weather_points.extend(block["points"])
        return _api_timezone(weather_points)
    return None


def daily_points(payload):
    """Normalize explicit dates + hours or full timestamps; never infer list order."""
    if not isinstance(payload, dict):
        raise ValueError("expected dated daily payload")
    result = []
    for day in ("today", "tomorrow"):
        block = payload.get(day)
        if block is None:
            continue
        if not isinstance(block, dict) or not isinstance(block.get("points"), list):
            raise ValueError("daily points missing")
        for point in block["points"]:
            row = dict(point)
            full = next((row[k] for k in ("time", "timestamp", "datetime", "local_hour_time", "start") if k in row), None)
            if full is None:
                hour = row.get("hour")
                if "date" not in block or isinstance(hour, bool) or not isinstance(hour, (int, float)) or hour != int(hour) or not 0 <= hour <= 23:
                    raise ValueError("explicit date and numeric hour required")
                full = pd.Timestamp(block["date"]).normalize()+pd.Timedelta(hours=int(hour))
            row["time"] = str(full)
            result.append(row)
    if not result:
        raise ValueError("empty today/tomorrow data")
    return result


def validate_state(state, received_at, *, now, max_age=15.):
    if not state_is_fresh(received_at, now=now, max_age_seconds=max_age):
        raise ValueError("stale_snapshot")
    if not state_is_fresh(state.get("device_time_last_changed"), now=now, max_age_seconds=max_age):
        raise ValueError("stale_device_clock")
    if pd.isna(pd.Timestamp(state.get("device_time"))):
        raise ValueError("invalid_device_clock")
    values = np.array([float(state[k]) for k in ("soc", "solar_power", "home_load")])
    if not np.isfinite(values).all() or not 0 <= values[0] <= 100 or min(values[1:]) < 0:
        raise ValueError("invalid_device_observation")


def make_candidate(state, prices, *, forecast=None, forecast_mode=None, solver_seconds=1., capacity=14., max_power=5000., soc_min=0., soc_max=100., device_timezone=None, idle_candidate="legacy_hold", evidence_sink=None, evidence_identity=None, evidence_observed_at=None):
    """Quarter-hour synthetic discretization; only current power is observed."""
    if idle_candidate not in ("legacy_hold", "zero_charge_unverified"):
        raise ValueError("invalid idle candidate policy")
    tick = pd.Timestamp(state["device_time"])
    if tick.tzinfo is None and device_timezone is not None:
        tick = tick.tz_localize(device_timezone)
    # Cover the full 24 h from the next actual tick including its final partial hour.
    horizon_hours = 24 if tick.minute == 0 and tick.second == 0 else 25
    aligned = align_hourly_points(prices, tick, horizon=horizon_hours)
    numeric = aligned[["import_price", "export_price"]].to_numpy(dtype=float)
    if not np.isfinite(numeric).all() or (numeric[:, 0] <= 0).any():
        raise ValueError("invalid_price_window")
    # The first decision is stamped with actual device time, not rounded backwards.
    times = pd.Series(pd.date_range(tick, periods=96, freq="15min"))
    lookup = aligned.set_index("time").loc[times.dt.floor("h")]
    pv, load = float(state["solar_power"]), float(state["home_load"])
    pp, ll = np.full(96, pv), np.full(96, load)
    forecast_mode = forecast_mode or "current_power_persistence_diagnostic"
    if forecast is not None:
        forecast_indexed = forecast.copy()
        forecast_indexed["time"] = pd.to_datetime(forecast_indexed["time"], utc=True)
        forecast_indexed = forecast_indexed.set_index("time").sort_index()
        # API-first paths are already quarter-hourly. Legacy/model forecasts
        # may be hourly; support both without silently interpolating.
        if times.dt.tz is not None and times.iloc[-1] in forecast_indexed.index:
            future = forecast_indexed.reindex(times)
            f = future
        else:
            expected = pd.date_range(tick.floor("h"), periods=horizon_hours, freq="h", tz="UTC")
            future = forecast_indexed.reindex(expected)
            f = future.reindex(times, method="ffill")
        arrays = f[["pv", "load"]].to_numpy(dtype=float)
        if not np.isfinite(arrays).all() or (arrays < 0).any():
            raise ValueError("invalid_power_forecast")
        pp, ll = f.pv.to_numpy(copy=True), f.load.to_numpy(copy=True)
        pp[0], ll[0] = pv, load
        forecast_mode = forecast_mode or "api_power_path"
    result = optimize_window(times, pp, ll, lookup.import_price.to_numpy(), lookup.export_price.to_numpy(),
                             float(state["soc"]), capacity_kwh=capacity, max_power_w=max_power,
                             soc_min=soc_min, soc_max=soc_max, terminal_price=None, degradation_cost_eur_kwh=.02,
                             solver="milp", step_hours=.25, command_contract="sop",
                             time_limit_seconds=solver_seconds)
    if result.status != "optimal":
        # Software reserve is not a device reserve. Do not silently use self-use
        # here: it may discharge below the configured software minimum.
        return {"status": result.status, "candidate": None, "forecast_mode": forecast_mode,
                "fallback": "no_new_command_requires_supervised_policy", "writes_performed": 0}
    first = result.plan.iloc[0]
    power = float(first.charge_w if first.flow == "charge" else first.discharge_w if first.flow == "discharge" else 0.)
    candidate_flow = ("charge" if first.flow == "hold" and idle_candidate == "zero_charge_unverified"
                      else str(first.flow))
    mapping = map_battery_action(candidate_flow, max(0., power), pv, load, limit_w=max_power)
    if not mapping.representable:
        raise ValueError("optimizer_to_command_contract_mismatch")
    candidate = {"status": "candidate_only", "candidate": asdict(mapping), "forecast_mode": forecast_mode,
            "idle_candidate_policy": idle_candidate, "hardware_verified": False,
            "idle_model_consistent_under_zero_charge_assumption": idle_candidate == "zero_charge_unverified",
            "battery_flow": str(first.flow), "battery_power_w": power, "device_time": str(tick),
            "planned_soc_after_15min": float(first.soc_end), "solver_status": result.status,
            "horizon_steps": 96, "writes_performed": 0, "readback": "not_sent_no_acknowledgement"}
    if evidence_sink is not None:
        # Evidence is a sidecar: exporting must not change the chosen action.
        # No callback is called for blocked, failed, or unrepresentable solves.
        export_stage = "record"
        try:
            from .explainability_evidence import build_explainability_record
            record = build_explainability_record(
                identity=evidence_identity or {}, observed_at=evidence_observed_at,
                state=state, times=times, pv_w=pp, load_w=ll,
                import_price=lookup.import_price.to_numpy(), export_price=lookup.export_price.to_numpy(),
                result=result, mapping=mapping, forecast_mode=forecast_mode,
                capacity_kwh=capacity, max_power_w=max_power, soc_min=soc_min, soc_max=soc_max)
            export_stage = "sink"
            evidence_sink(record)
        except Exception as exc:
            # Error text can contain caller data; never expose arbitrary text.
            candidate["explainability_export"] = {"status": "failed", "error_type": type(exc).__name__}
            # Export validation codes are fixed strings defined by our own
            # exporter. Do not copy arbitrary sink error text into evidence.
            from .explainability_evidence import EvidenceExportError
            if export_stage == "record" and isinstance(exc, EvidenceExportError):
                candidate["explainability_export"]["failure_code"] = exc.code
    return candidate


def evaluate_snapshot(snapshot, *, now=None, max_age=15., solver_seconds=1., device_timezone=None, evidence_sink=None, evidence_identity=None, idle_candidate="legacy_hold"):
    now = pd.Timestamp.now(tz="UTC") if now is None else now
    results = {}
    prices = daily_points(snapshot["prices"])
    for site, item in snapshot["stations"].items():
        try:
            state = item["state"]
            validate_state(state, item["received_at"], now=now, max_age=max_age)
            forecast = None
            forecast_mode = None
            weather = item.get("weather")
            effective_timezone = device_timezone or _infer_api_timezone(prices, item.get("weather", {}))
            if isinstance(weather, dict) and not weather.get("unavailable"):
                try:
                    api_state = dict(state)
                    api_tick = pd.Timestamp(api_state["device_time"])
                    if api_tick.tzinfo is None:
                        effective_timezone = effective_timezone or _infer_api_timezone(prices, weather)
                        if effective_timezone is None:
                            raise ValueError("API timestamp timezone is unavailable for device clock alignment")
                        api_tick = api_tick.tz_localize(effective_timezone)
                    api_state["device_time"] = api_tick.isoformat()
                    api_path = build_api_power_path(api_state, weather, api_tick, horizon_steps=96)
                    forecast, forecast_mode = api_path.frame, api_path.mode
                except (ValueError, TypeError, KeyError) as exc:
                    # An incomplete or malformed API response must not be
                    # converted into fake zeros. Keep the explicit diagnostic
                    # persistence path and expose the reason in the result.
                    forecast_mode = f"api_fallback_current_power_persistence:{type(exc).__name__}"
            else:
                forecast_mode = "api_unavailable_current_power_persistence"
            evidence_options = {}
            if evidence_sink is not None:
                identity = dict(evidence_identity or {})
                identity["site_id"] = str(site)
                evidence_options = {"evidence_sink": evidence_sink, "evidence_identity": identity,
                                    "evidence_observed_at": item["received_at"]}
            result = make_candidate(state, prices, forecast=forecast, idle_candidate=idle_candidate,
                                    forecast_mode=forecast_mode,
                                    solver_seconds=solver_seconds, device_timezone=effective_timezone,
                                    **evidence_options)
            results[site] = result
        except (ValueError, TypeError, KeyError) as exc:
            results[site] = {"status": "blocked", "reason": str(exc), "writes_performed": 0}
    return {"mode": "shadow_only", "writes_performed": 0, "stations": results,
            "live_ready": False,
            "limits": "API-first path is candidate-only; uncalibrated PV/load use explicit persistence fallback; no device command was sent."}
