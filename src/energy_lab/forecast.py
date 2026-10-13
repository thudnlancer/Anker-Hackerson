from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
import json

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.metrics import mean_absolute_error, mean_squared_error


WEATHER_FEATURES = (
    "temperature_c", "relative_humidity_percent", "dew_point_c", "apparent_temperature_c",
    "precipitation_mm", "rain_mm", "snowfall_mm", "snow_depth_cm", "pressure_msl_hpa",
    "surface_pressure_hpa", "cloud_cover_percent", "cloud_cover_low_percent",
    "cloud_cover_mid_percent", "cloud_cover_high_percent", "wind_speed_10m_ms",
    "wind_speed_100m_ms", "wind_gusts_10m_ms", "shortwave_radiation_wm2",
    "direct_radiation_wm2", "diffuse_radiation_wm2", "direct_normal_irradiance_wm2",
    "terrestrial_radiation_wm2", "sunshine_duration_s", "solar_elevation_deg",
    "solar_azimuth_deg", "is_day",
)


def _find(columns: Iterable[str], names: tuple[str, ...]) -> str | None:
    lower = {str(c).lower(): c for c in columns}
    for name in names:
        if name.lower() in lower:
            return lower[name.lower()]
    return None


def load_weather_csv(path: Path) -> pd.DataFrame:
    raw = pd.read_csv(path)
    time_col = _find(raw.columns, ("local_hour_time", "time", "timestamp", "datetime", "date"))
    pv_col = _find(raw.columns, ("pv_power_avg_w", "pv_power", "pv_power_w", "solar_power"))
    load_col = _find(raw.columns, ("load_power_avg_w", "home_load", "load_power", "load_power_w"))
    missing = [name for name, value in (("time", time_col), ("pv", pv_col), ("load", load_col)) if value is None]
    if missing:
        raise ValueError(f"{path}: missing {missing}; columns={list(raw.columns)}")
    out = pd.DataFrame({
        "time": pd.to_datetime(raw[time_col]),
        "pv": pd.to_numeric(raw[pv_col], errors="coerce"),
        "load": pd.to_numeric(raw[load_col], errors="coerce"),
    })
    for col in WEATHER_FEATURES:
        if col in raw.columns:
            out[col] = pd.to_numeric(raw[col], errors="coerce")
    out = out.sort_values("time").drop_duplicates("time").reset_index(drop=True)
    if out["time"].diff().dropna().dt.total_seconds().ne(3600).any():
        raise ValueError(f"{path}: expected hourly data without gaps")
    return out


def _feature_frame(frame: pd.DataFrame, target: str, values: pd.Series | None = None) -> pd.DataFrame:
    result = pd.DataFrame(index=frame.index)
    t = pd.to_datetime(frame["time"])
    result["hour"] = t.dt.hour
    result["hour_sin"] = np.sin(2 * np.pi * t.dt.hour / 24)
    result["hour_cos"] = np.cos(2 * np.pi * t.dt.hour / 24)
    result["day_of_year_sin"] = np.sin(2 * np.pi * t.dt.dayofyear / 365.25)
    result["day_of_year_cos"] = np.cos(2 * np.pi * t.dt.dayofyear / 365.25)
    result["day_of_week"] = t.dt.dayofweek
    for col in WEATHER_FEATURES:
        if col in frame.columns:
            result[col] = pd.to_numeric(frame[col], errors="coerce")
    source = values if values is not None else frame[target]
    result["lag_1"] = source.shift(1)
    result["lag_24"] = source.shift(24)
    result["lag_168"] = source.shift(168)
    result["rolling_24"] = source.shift(1).rolling(24, min_periods=6).mean()
    return result


@dataclass
class ForecastModel:
    target: str
    features: list[str]
    model: HistGradientBoostingRegressor
    clip_min: float
    clip_max: float

    def predict(self, history: pd.DataFrame, future: pd.DataFrame) -> pd.DataFrame:
        history = history.reset_index(drop=True)
        future = future.reset_index(drop=True)
        values = pd.to_numeric(history[self.target], errors="coerce").tolist()
        predictions: list[float] = []
        for i in range(len(future)):
            timestamp = pd.Timestamp(future.loc[i, "time"])
            row: dict[str, float] = {
                "hour": float(timestamp.hour),
                "hour_sin": float(np.sin(2 * np.pi * timestamp.hour / 24)),
                "hour_cos": float(np.cos(2 * np.pi * timestamp.hour / 24)),
                "day_of_year_sin": float(np.sin(2 * np.pi * timestamp.dayofyear / 365.25)),
                "day_of_year_cos": float(np.cos(2 * np.pi * timestamp.dayofyear / 365.25)),
                "day_of_week": float(timestamp.dayofweek),
            }
            for col in WEATHER_FEATURES:
                if col in future.columns:
                    row[col] = pd.to_numeric(pd.Series([future.loc[i, col]]), errors="coerce").iloc[0]
            position = len(values)
            for lag in (1, 24, 168):
                row[f"lag_{lag}"] = values[position - lag] if position >= lag else np.nan
            prior = values[max(0, position - 24) : position]
            finite_prior = [float(value) for value in prior if pd.notna(value)]
            row["rolling_24"] = float(np.mean(finite_prior)) if len(finite_prior) >= 6 else np.nan
            x = pd.DataFrame([row]).reindex(columns=self.features).fillna(0.0)
            value = float(self.model.predict(x)[0])
            value = float(np.clip(value, self.clip_min, self.clip_max))
            predictions.append(value)
            values.append(value)
        return pd.DataFrame({"time": future["time"].to_numpy(), "prediction": predictions})


def fit_model(frame: pd.DataFrame, target: str, train_fraction: float = 0.8) -> tuple[ForecastModel, dict]:
    if target not in ("pv", "load"):
        raise ValueError("target must be pv or load")
    n_train = max(1, int(len(frame) * train_fraction))
    features = _feature_frame(frame, target)
    usable = features.notna().all(axis=1)
    train_rows = usable & (features.index < n_train)
    test_rows = usable & (features.index >= n_train)
    if train_rows.sum() < 200 or test_rows.sum() < 24:
        raise ValueError(f"not enough hourly rows for temporal split: train={train_rows.sum()}, test={test_rows.sum()}")
    x_train = features.loc[train_rows]
    y_train = frame.loc[train_rows, target]
    x_test = features.loc[test_rows]
    y_test = frame.loc[test_rows, target]
    model = HistGradientBoostingRegressor(
        max_iter=100, learning_rate=0.08, max_leaf_nodes=15,
        l2_regularization=2.0, early_stopping=True, validation_fraction=0.1,
        n_iter_no_change=8, random_state=42,
    )
    model.fit(x_train, y_train)
    pred = np.clip(model.predict(x_test), 0, None)
    naive = frame[target].shift(24).loc[test_rows].to_numpy()
    metrics = {
        "target": target,
        "rows": int(len(frame)),
        "train_rows": int(train_rows.sum()),
        "test_rows": int(test_rows.sum()),
        "mae_w": float(mean_absolute_error(y_test, pred)),
        "rmse_w": float(mean_squared_error(y_test, pred) ** 0.5),
        "naive_24h_mae_w": float(mean_absolute_error(y_test, naive)),
        "naive_24h_rmse_w": float(mean_squared_error(y_test, naive) ** 0.5),
        "validation_semantics": "one-step-ahead; prior observed values are available at each hourly replan",
        "model_iterations": int(model.n_iter_),
        "weather_features": [c for c in WEATHER_FEATURES if c in frame.columns],
    }
    return ForecastModel(target, list(features.columns), model, 0.0, float(max(frame[target].max() * 1.1, 1.0))), metrics


def fit_shared_model(frames: list[pd.DataFrame], target: str, train_fraction: float = 0.8) -> tuple[ForecastModel, dict]:
    """Fit one shared model, but create lags and temporal split independently per site."""
    feature_parts: list[pd.DataFrame] = []
    train_x, train_y, test_x, test_y, naive_values = [], [], [], [], []
    max_value = 1.0
    for frame in frames:
        features = _feature_frame(frame, target)
        n_train = max(1, int(len(frame) * train_fraction))
        usable = features.notna().all(axis=1)
        train_rows = usable & (features.index < n_train)
        test_rows = usable & (features.index >= n_train)
        if train_rows.sum() < 200 or test_rows.sum() < 24:
            raise ValueError(f"not enough rows for site temporal split: train={train_rows.sum()}, test={test_rows.sum()}")
        train_x.append(features.loc[train_rows]); train_y.append(frame.loc[train_rows, target])
        test_x.append(features.loc[test_rows]); test_y.append(frame.loc[test_rows, target])
        naive_values.append(frame[target].shift(24).loc[test_rows])
        max_value = max(max_value, float(frame[target].max() * 1.1))
    x_train = pd.concat(train_x); y_train = pd.concat(train_y)
    x_test = pd.concat(test_x); y_test = pd.concat(test_y)
    model = HistGradientBoostingRegressor(
        max_iter=100, learning_rate=0.08, max_leaf_nodes=15,
        l2_regularization=2.0, early_stopping=True, validation_fraction=0.1,
        n_iter_no_change=8, random_state=42,
    )
    model.fit(x_train, y_train)
    pred = np.clip(model.predict(x_test), 0, None)
    naive = pd.concat(naive_values).to_numpy()
    metrics = {
        "target": target, "sites": len(frames), "rows": int(sum(len(f) for f in frames)),
        "train_rows": int(len(y_train)), "test_rows": int(len(y_test)),
        "mae_w": float(mean_absolute_error(y_test, pred)),
        "rmse_w": float(mean_squared_error(y_test, pred) ** 0.5),
        "naive_24h_mae_w": float(mean_absolute_error(y_test, naive)),
        "naive_24h_rmse_w": float(mean_squared_error(y_test, naive) ** 0.5),
        "validation_semantics": "per-site temporal 80/20 split; one-step-ahead with observed history",
        "model_iterations": int(model.n_iter_),
        "weather_features": [c for c in WEATHER_FEATURES if c in frames[0].columns],
    }
    return ForecastModel(target, list(x_train.columns), model, 0.0, max_value), metrics


def fit_shared_model_full(frames: list[pd.DataFrame], target: str) -> ForecastModel:
    """Fit a shared model on all supplied training rows without an internal holdout."""
    if target not in ("pv", "load"):
        raise ValueError("target must be pv or load")
    feature_parts, target_parts = [], []
    max_value = 1.0
    for frame in frames:
        features = _feature_frame(frame, target)
        usable = features.notna().all(axis=1) & pd.to_numeric(frame[target], errors="coerce").notna()
        feature_parts.append(features.loc[usable])
        target_parts.append(frame.loc[usable, target])
        max_value = max(max_value, float(frame[target].max() * 1.1))
    x_train = pd.concat(feature_parts, ignore_index=True)
    y_train = pd.concat(target_parts, ignore_index=True)
    model = HistGradientBoostingRegressor(
        max_iter=100, learning_rate=0.08, max_leaf_nodes=15,
        l2_regularization=2.0, early_stopping=True, validation_fraction=0.1,
        n_iter_no_change=8, random_state=42,
    )
    model.fit(x_train, y_train)
    return ForecastModel(target, list(x_train.columns), model, 0.0, max_value)


def train_folder(folder: Path, model_dir: Path, metrics_path: Path) -> dict:
    files = sorted(folder.rglob("*.csv"))
    if not files:
        raise FileNotFoundError(f"no CSV files under {folder}")
    frames = [load_weather_csv(path) for path in files]
    model_dir.mkdir(parents=True, exist_ok=True)
    metrics = {}
    for target in ("pv", "load"):
        model, report = fit_shared_model(frames, target)
        joblib.dump(model, model_dir / f"shared_{target}.joblib")
        metrics[target] = report
    output = {
        "sites": len(files),
        "rows": sum(len(frame) for frame in frames),
        "model_scope": "shared across sites; lags and temporal split are computed independently per site",
        "models": {target: f"shared_{target}.joblib" for target in metrics},
        "metrics": metrics,
    }
    metrics_path.parent.mkdir(parents=True, exist_ok=True)
    metrics_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    return output
