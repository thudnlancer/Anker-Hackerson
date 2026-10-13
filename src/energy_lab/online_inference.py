"""Gated online forecast assembly for the API-compatible models."""

from __future__ import annotations

import pandas as pd

from .forecast import ForecastModel
from .online_contract import OnlineContractError, hourly_observation_history, normalize_weather_payload


def predict_online_power(history: pd.DataFrame, weather_payload: dict, pv_model: ForecastModel,
                         load_model: ForecastModel, *, horizon: int = 24,
                         minimum_history_hours: int = 168,
                         allow_gaps: bool = False,
                         max_missing_ratio: float = 0.05,
                         max_gap_hours: int = 3) -> pd.DataFrame:
    """Predict hourly PV/load with an explicit history-quality threshold.

    The full 168-hour window is the preferred quality tier. A lower threshold
    can be used for an operational degraded forecast; API-first control must
    remain available when even that threshold is not met.
    """
    if minimum_history_hours <= 0:
        raise ValueError("minimum_history_hours must be positive")
    hourly = hourly_observation_history(history, minimum_hours=minimum_history_hours,
                                         allow_gaps=allow_gaps,
                                         max_missing_ratio=max_missing_ratio,
                                         max_gap_hours=max_gap_hours)
    if hourly["site"].nunique() != 1:
        raise OnlineContractError("online prediction requires one station at a time")
    weather = normalize_weather_payload(weather_payload)
    last = hourly["time"].max()
    expected = pd.date_range(last + pd.Timedelta(hours=1), periods=horizon, freq="h", tz="UTC")
    future = weather.set_index("time").reindex(expected).reset_index(names="time")
    if future[["temperature_c", "cloud_cover_percent"]].isna().any().any():
        raise OnlineContractError("weather forecast does not cover the complete hourly horizon")
    required_history = ["time", "pv", "load", "temperature_c", "cloud_cover_percent"]
    if any(field not in hourly.columns for field in required_history):
        raise OnlineContractError("history missing model weather fields")
    model_history = hourly[required_history].copy()
    pv = pv_model.predict(model_history, future)["prediction"]
    load = load_model.predict(model_history, future)["prediction"]
    return pd.DataFrame({"time": expected, "pv": pv.to_numpy(), "load": load.to_numpy()})
