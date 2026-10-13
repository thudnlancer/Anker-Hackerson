"""Build strategy power paths with API-first precedence and explicit fallbacks."""

from __future__ import annotations

from dataclasses import dataclass
import numpy as np
import pandas as pd

from .online_contract import normalize_weather_payload
from .system_model import pv_from_irradiance


@dataclass(frozen=True)
class ApiPowerPath:
    frame: pd.DataFrame
    mode: str
    assumptions: tuple[str, ...]


def build_api_power_path(state: dict, weather_payload: dict, device_time, *,
                         calibration: dict | None = None, horizon_steps: int = 96) -> ApiPowerPath:
    """Create quarter-hour PV/load inputs for the optimizer.

    Future weather is authoritative when available. PV conversion requires an
    explicit calibration learned from matching API irradiance and HA PV values;
    without it, current PV is held conservatively. Future load is never
    invented from weather and defaults to current HA load persistence.
    """
    if horizon_steps < 1:
        raise ValueError("horizon_steps must be positive")
    tick = pd.Timestamp(device_time)
    if tick.tzinfo is None:
        raise ValueError("device_time must include timezone")
    # Weather payloads are normalized to UTC; use the same coordinate for all
    # horizon indexing even when HA reports a non-UTC device timezone.
    tick = tick.tz_convert("UTC")
    weather = normalize_weather_payload(weather_payload)
    expected_hours = pd.date_range(tick.floor("h"), periods=25 if tick.minute else 24, freq="h", tz="UTC")
    weather = weather.set_index("time").reindex(expected_hours)
    if weather[["temperature_c", "cloud_cover_percent", "solar_irradiance_wm2"]].isna().any().any():
        raise ValueError("weather API does not cover the complete device horizon")
    times = pd.date_range(tick, periods=horizon_steps, freq="15min", tz="UTC")
    hourly = weather.loc[times.floor("h")]
    current_pv = float(state["solar_power"])
    current_load = float(state["home_load"])
    if current_pv < 0 or current_load < 0 or not np.isfinite([current_pv, current_load]).all():
        raise ValueError("invalid current HA power")
    assumptions: list[str] = ["future_load=current_HA_load_persistence"]
    if calibration is None:
        pv = np.full(horizon_steps, current_pv, dtype=float)
        mode = "api_weather_uncalibrated_current_pv_persistence_current_load_persistence"
        assumptions.append("future_pv=current_HA_pv_persistence_without_calibration")
    else:
        pv = np.asarray([pv_from_irradiance(value, calibration) for value in hourly["solar_irradiance_wm2"]], dtype=float)
        mode = "api_weather_calibrated_pv_current_load_persistence"
        assumptions.append("future_pv=API_irradiance_calibrated_to_HA_pv")
    frame = pd.DataFrame({"time": times, "pv": pv, "load": np.full(horizon_steps, current_load),
                          "temperature_c": hourly["temperature_c"].to_numpy(),
                          "cloud_cover_percent": hourly["cloud_cover_percent"].to_numpy(),
                          "solar_irradiance_wm2": hourly["solar_irradiance_wm2"].to_numpy()})
    frame.loc[0, ["pv", "load"]] = [current_pv, current_load]
    return ApiPowerPath(frame=frame, mode=mode, assumptions=tuple(assumptions))
