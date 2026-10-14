"""Explicit contracts for the online weather API and HA observations.

The competition API exposes fewer weather fields than the historical training
CSV.  This module keeps that mismatch visible instead of silently fabricating
features or treating a forecast as an observed target.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
import hashlib
import json

import numpy as np
import pandas as pd


ONLINE_WEATHER_FIELDS = (
    "temperature_c",
    "cloud_cover_percent",
    "solar_irradiance_wm2",
    "condition",
)
TARGET_FIELDS = ("pv", "load")
STATE_FIELDS = ("soc", "solar_power", "home_load", "device_time")

# contest/history contains the same 28 observed feature columns as the
# training CSV (without prices), plus a point timestamp.  Keep this contract
# separate from the HA tick cache: history has no SOC or control outcome.
CONTEST_HISTORY_FIELDS = (
    "pv_power_avg_w", "temperature_c", "relative_humidity_percent",
    "dew_point_c", "apparent_temperature_c", "precipitation_mm", "rain_mm",
    "snowfall_mm", "snow_depth_cm", "pressure_msl_hpa", "surface_pressure_hpa",
    "cloud_cover_percent", "cloud_cover_low_percent", "cloud_cover_mid_percent",
    "cloud_cover_high_percent", "wind_speed_10m_ms", "wind_speed_100m_ms",
    "wind_gusts_10m_ms", "shortwave_radiation_wm2", "direct_radiation_wm2",
    "diffuse_radiation_wm2", "direct_normal_irradiance_wm2",
    "terrestrial_radiation_wm2", "sunshine_duration_s", "solar_elevation_deg",
    "solar_azimuth_deg", "load_power_avg_w", "is_day",
)


class OnlineContractError(ValueError):
    """Raised when an online payload cannot be used without assumptions."""


def normalize_contest_history(payload: dict[str, Any], *, requested_site: int | None = None,
                              expected_days: int = 7) -> tuple[dict[str, Any], pd.DataFrame]:
    """Validate and normalize one ``/api/contest/history`` response.

    The endpoint is an optional warm-start source.  This function is strict
    about a payload that *is* supplied: it rejects missing fields, duplicate
    or gapped hourly points, wrong site metadata, and non-finite values rather
    than filling or aliasing fields.  Naive point timestamps are preserved as
    device-clock values; the caller must align them with the API/HA clock
    before combining them with timezone-aware data.
    """
    if not isinstance(payload, dict):
        raise OnlineContractError("contest history payload must be an object")
    site = payload.get("site")
    try:
        site_int = int(site)
    except (TypeError, ValueError) as exc:
        raise OnlineContractError("contest history site is invalid") from exc
    if site_int not in (1, 2, 3):
        raise OnlineContractError("contest history site must be 1, 2 or 3")
    if requested_site is not None and site_int != int(requested_site):
        raise OnlineContractError(f"contest history site mismatch: requested {requested_site}, got {site_int}")
    if payload.get("days") != expected_days:
        raise OnlineContractError(f"contest history days must be {expected_days}")
    if payload.get("interval_hours") != 1:
        raise OnlineContractError("contest history interval_hours must be 1")
    points = payload.get("points")
    if not isinstance(points, list) or not points:
        raise OnlineContractError("contest history points are missing")
    expected_points = expected_days * 24
    if len(points) != expected_points:
        raise OnlineContractError(f"contest history requires {expected_points} points; got {len(points)}")

    rows: list[dict[str, Any]] = []
    for index, point in enumerate(points):
        if not isinstance(point, dict):
            raise OnlineContractError(f"contest history point {index} is not an object")
        missing = [field for field in ("time", *CONTEST_HISTORY_FIELDS) if field not in point]
        if missing:
            raise OnlineContractError(f"contest history point {index} missing fields: {', '.join(missing)}")
        try:
            timestamp = pd.Timestamp(point["time"])
        except Exception as exc:
            raise OnlineContractError(f"contest history point {index} time is invalid") from exc
        row: dict[str, Any] = {"time": timestamp}
        for field in CONTEST_HISTORY_FIELDS:
            value = point[field]
            if field == "is_day":
                if isinstance(value, bool):
                    row[field] = value
                elif value in (0, 1, 0.0, 1.0):
                    row[field] = bool(value)
                else:
                    raise OnlineContractError(f"contest history point {index} is_day is invalid")
                continue
            try:
                number = float(value)
            except (TypeError, ValueError) as exc:
                raise OnlineContractError(f"contest history point {index} {field} is invalid") from exc
            if not np.isfinite(number):
                raise OnlineContractError(f"contest history point {index} {field} is not finite")
            if field not in ("solar_elevation_deg", "solar_azimuth_deg") and number < 0:
                raise OnlineContractError(f"contest history point {index} {field} is negative")
            row[field] = number
        rows.append(row)

    frame = pd.DataFrame(rows)
    if frame["time"].duplicated().any():
        raise OnlineContractError("contest history contains duplicate timestamps")
    try:
        delta = frame["time"].diff().dropna().dt.total_seconds()
    except Exception as exc:
        raise OnlineContractError("contest history timestamps use mixed timezone formats") from exc
    if not delta.eq(3600).all():
        raise OnlineContractError("contest history timestamps are not continuous hourly points")
    start_time = payload.get("start_time")
    if start_time is not None and pd.Timestamp(start_time) != frame.iloc[0]["time"]:
        raise OnlineContractError("contest history start_time does not match first point")
    normalized = dict(payload)
    normalized["site"] = site_int
    normalized["points"] = [{key: (value.isoformat() if isinstance(value, pd.Timestamp) else value)
                              for key, value in row.items()} for row in rows]
    return normalized, frame


def cache_contest_history(cache_dir: Path, payload: dict[str, Any], *, requested_site: int | None = None) -> Path:
    """Validate and persist a token-free history snapshot with an audit hash."""
    normalized, frame = normalize_contest_history(payload, requested_site=requested_site)
    cache_dir.mkdir(parents=True, exist_ok=True)
    site = int(normalized["site"])
    points_json = json.dumps(normalized["points"], ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    metadata = {
        "site": site,
        "days": normalized["days"],
        "interval_hours": normalized["interval_hours"],
        "start_time": str(normalized.get("start_time", frame.iloc[0]["time"])),
        "end_time": frame.iloc[-1]["time"].isoformat(),
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "field_hash_sha256": hashlib.sha256(
            json.dumps(list(CONTEST_HISTORY_FIELDS), separators=(",", ":")).encode("utf-8")
        ).hexdigest(),
        "points_sha256": hashlib.sha256(points_json.encode("utf-8")).hexdigest(),
        "points": normalized["points"],
    }
    destination = cache_dir / f"contest_history_site_{site}.json"
    destination.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    return destination


def _timestamp(value: Any, *, field: str) -> pd.Timestamp:
    try:
        stamp = pd.Timestamp(value)
    except Exception as exc:  # pragma: no cover - pandas error wording varies
        raise OnlineContractError(f"invalid {field} timestamp") from exc
    if stamp.tzinfo is None:
        raise OnlineContractError(f"{field} timestamp must include timezone")
    return stamp.tz_convert("UTC")


def infer_payload_timezone(payload: dict[str, Any]) -> str | None:
    """Return the timezone carried by a raw API timestamp, if present."""
    for day in ("today", "tomorrow"):
        block = payload.get(day) if isinstance(payload, dict) else None
        points = block.get("points") if isinstance(block, dict) else None
        if not isinstance(points, list):
            continue
        for point in points:
            if not isinstance(point, dict):
                continue
            raw = point.get("timestamp", point.get("start"))
            if raw is None:
                continue
            stamp = pd.Timestamp(raw)
            if stamp.tzinfo is not None:
                return str(stamp.tz)
    return None


def normalize_weather_payload(payload: dict[str, Any], *, days: Iterable[str] = ("today", "tomorrow")) -> pd.DataFrame:
    """Normalize dated API weather blocks without inventing missing fields."""
    rows: list[dict[str, Any]] = []
    for day in days:
        block = payload.get(day)
        if block is None:
            continue
        points = block.get("points") if isinstance(block, dict) else None
        if not isinstance(points, list):
            raise OnlineContractError(f"weather {day} points missing")
        for index, point in enumerate(points):
            if not isinstance(point, dict):
                raise OnlineContractError(f"weather {day}[{index}] is not an object")
            source_time = point.get("timestamp", point.get("start"))
            if source_time is None:
                raise OnlineContractError(f"weather {day}[{index}] timestamp missing")
            row: dict[str, Any] = {"time": _timestamp(source_time, field=f"weather {day}[{index}]")}
            missing = [field for field in ONLINE_WEATHER_FIELDS if field not in point]
            if missing:
                raise OnlineContractError(f"weather {day}[{index}] missing fields: {', '.join(missing)}")
            for field in ONLINE_WEATHER_FIELDS:
                value = point[field]
                if field == "condition":
                    if not isinstance(value, str) or not value.strip():
                        raise OnlineContractError(f"weather {day}[{index}] condition invalid")
                    row[field] = value.strip()
                else:
                    try:
                        number = float(value)
                    except (TypeError, ValueError) as exc:
                        raise OnlineContractError(f"weather {day}[{index}] {field} invalid") from exc
                    if not np.isfinite(number) or (field != "temperature_c" and number < 0):
                        raise OnlineContractError(f"weather {day}[{index}] {field} out of range")
                    row[field] = number
            rows.append(row)
    if not rows:
        raise OnlineContractError("weather payload has no points")
    result = pd.DataFrame(rows).sort_values("time").drop_duplicates("time").reset_index(drop=True)
    if result["time"].diff().dropna().dt.total_seconds().le(0).any():
        raise OnlineContractError("weather timestamps are not strictly increasing")
    return result


def normalize_station_observation(site: str, state: dict[str, Any], received_at: Any, *, device_timezone: str | None = None) -> dict[str, Any]:
    """Convert one HA snapshot into a cache-safe, model-input observation."""
    if not str(site).strip():
        raise OnlineContractError("site is required")
    received = _timestamp(received_at, field="received_at")
    missing = [field for field in STATE_FIELDS if state.get(field) in (None, "", "unknown", "unavailable")]
    if missing:
        raise OnlineContractError(f"station {site} missing state fields: {', '.join(missing)}")
    numbers: dict[str, float] = {}
    for field in ("soc", "solar_power", "home_load"):
        try:
            value = float(state[field])
        except (TypeError, ValueError) as exc:
            raise OnlineContractError(f"station {site} {field} invalid") from exc
        if not np.isfinite(value) or value < 0 or (field == "soc" and value > 100):
            raise OnlineContractError(f"station {site} {field} out of range")
        numbers[field] = value
    raw_device_time = pd.Timestamp(state["device_time"])
    if raw_device_time.tzinfo is None:
        if not device_timezone:
            raise OnlineContractError(f"station {site} device_time timezone is required")
        raw_device_time = raw_device_time.tz_localize(device_timezone)
    device_time = _timestamp(raw_device_time, field=f"station {site} device_time")
    return {
        "site": str(site),
        "received_at": received.isoformat(),
        "device_time": device_time.isoformat(),
        "soc": numbers["soc"],
        "pv": numbers["solar_power"],
        "load": numbers["home_load"],
        "source": "home_assistant_read_only_snapshot",
        "device_timezone_assumption": device_timezone if pd.Timestamp(state["device_time"]).tzinfo is None else None,
    }


def append_observation(cache_dir: Path, observation: dict[str, Any], *, node_id: str | None = None) -> Path:
    """Append one validated observation to its station JSONL cache.

    With ``node_id`` the shard file is ``site_{site}.{node_id}.jsonl`` so
    redundant collectors (B4) never share a write target; without it the
    legacy ``site_{site}.jsonl`` name is kept.
    """
    required = ("site", "received_at", "device_time", "soc", "pv", "load", "source")
    if any(key not in observation for key in required):
        raise OnlineContractError("observation is not normalized")
    if node_id is not None and not str(node_id).replace("_", "").replace("-", "").isalnum():
        raise OnlineContractError(f"node_id is not filename-safe: {node_id!r}")
    cache_dir.mkdir(parents=True, exist_ok=True)
    suffix = f".{node_id}" if node_id else ""
    destination = cache_dir / f"site_{observation['site']}{suffix}.jsonl"
    with destination.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(observation, ensure_ascii=False, allow_nan=False) + "\n")
    return destination


def load_observation_history(path: Path) -> pd.DataFrame:
    """Read a station cache and return a deduplicated, time-ordered frame."""
    if not path.exists():
        return pd.DataFrame(columns=["time", "received_at", "soc", "pv", "load", "site"])
    rows = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        try:
            item = json.loads(line)
        except json.JSONDecodeError as exc:
            raise OnlineContractError(f"invalid cache JSON at line {line_number}") from exc
        normalized = normalize_station_observation(item["site"], {
            "soc": item["soc"], "solar_power": item["pv"], "home_load": item["load"],
            "device_time": item["device_time"],
        }, item["received_at"], device_timezone=item.get("device_timezone_assumption"))
        row = {"time": pd.Timestamp(normalized["device_time"]),
                     "received_at": pd.Timestamp(normalized["received_at"]),
                     "soc": normalized["soc"], "pv": normalized["pv"],
                     "load": normalized["load"], "site": normalized["site"]}
        for field in ("temperature_c", "cloud_cover_percent", "solar_irradiance_wm2"):
            if field in item and item[field] is not None:
                row[field] = float(item[field])
            elif field in item:
                row[field] = np.nan
        rows.append(row)
    result = pd.DataFrame(rows).sort_values("time").drop_duplicates(["site", "time"], keep="last").reset_index(drop=True)
    return result


def cached_pv_calibration(history: pd.DataFrame, *, minimum_device_hours: int = 3) -> dict[str, Any]:
    """Fit only observed API-irradiance/HA-PV pairs; otherwise require persistence.

    This is an input-readiness gate, not an independent weather-forecast score.
    Multiple ticks in one device hour count as one sample.
    """
    if minimum_device_hours < 1:
        raise ValueError("minimum_device_hours must be positive")
    required = {"time", "pv", "solar_irradiance_wm2"}
    if not required.issubset(history.columns):
        return {"mode": "current_pv_persistence", "reason": "missing_calibration_fields",
                "eligible_device_hours": 0, "calibration": None}
    frame = history[["time", "pv", "solar_irradiance_wm2"]].copy()
    frame["time"] = pd.to_datetime(frame["time"], utc=True)
    frame["pv"] = pd.to_numeric(frame["pv"], errors="coerce")
    frame["solar_irradiance_wm2"] = pd.to_numeric(frame["solar_irradiance_wm2"], errors="coerce")
    frame = frame.replace([np.inf, -np.inf], np.nan).dropna()
    frame = frame[(frame["solar_irradiance_wm2"] > 25) & (frame["pv"] >= 0)]
    frame["hour"] = frame["time"].dt.floor("h")
    hourly = frame.groupby("hour")[["pv", "solar_irradiance_wm2"]].median()
    eligible = len(hourly)
    if eligible < minimum_device_hours:
        return {"mode": "current_pv_persistence", "reason": "insufficient_valid_daylight_device_hours",
                "eligible_device_hours": eligible, "calibration": None}
    from .system_model import calibrate_pv_scale
    calibration = calibrate_pv_scale(hourly["solar_irradiance_wm2"], hourly["pv"])
    return {"mode": "api_irradiance_calibrated_pv", "reason": None,
            "eligible_device_hours": eligible, "calibration": calibration}


def merge_observation_history(path: Path) -> pd.DataFrame:
    """B4 merge view: read every cache shard under ``path`` and deduplicate.

    Accepts either a single legacy cache file (delegates to
    ``load_observation_history``) or a cache directory containing both legacy
    ``site_{site}.jsonl`` files and per-node ``site_{site}.{node}.jsonl``
    shards. Duplicate ``(site, device_time)`` rows keep the one with the
    newest ``received_at``; ties resolve deterministically toward the
    alphabetically later shard file.
    """
    if not Path(path).is_dir():
        return load_observation_history(Path(path))
    frames = []
    # Remote shards are copied into a dedicated child directory; do not glob
    # recursively through unrelated artifacts or temporary files.
    shards = sorted(Path(path).glob("site_*.jsonl"))
    remote_ingest = Path(path) / "remote_ingest"
    if remote_ingest.is_dir():
        shards += sorted(remote_ingest.glob("site_*.jsonl"))
    for order, shard in enumerate(shards):
        frame = load_observation_history(shard)
        if frame.empty:
            continue
        frame["_received"] = pd.to_datetime(frame["received_at"], utc=True)
        frame["_order"] = order
        frames.append(frame)
    if not frames:
        return pd.DataFrame(columns=["time", "received_at", "soc", "pv", "load", "site"])
    merged = pd.concat(frames, ignore_index=True)
    merged = (merged.sort_values(["_received", "_order"])
                    .drop_duplicates(["site", "time"], keep="last")
                    .sort_values("time")
                    .drop(columns=["_received", "_order"])
                    .reset_index(drop=True))
    return merged


def hourly_observation_history(history: pd.DataFrame, *, minimum_hours: int = 168,
                               allow_gaps: bool = False,
                               max_missing_ratio: float = 0.0,
                               max_gap_hours: int = 0) -> pd.DataFrame:
    """Aggregate device ticks to hourly means with optional bounded gap tolerance.

    Strict mode preserves the original contract. Tolerant mode reindexes the
    latest time window to a real hourly grid, leaving missing measurements as
    NaN so lag positions are not silently shifted.
    """
    if minimum_hours <= 0 or not 0 <= max_missing_ratio < 1 or max_gap_hours < 0:
        raise ValueError("invalid hourly history tolerance")
    required = {"time", "pv", "load", "soc", "site"}
    if not required.issubset(history.columns):
        raise OnlineContractError(f"history missing columns: {sorted(required - set(history.columns))}")
    if history.empty:
        raise OnlineContractError("observation history is empty")
    frame = history.copy()
    frame["time"] = pd.to_datetime(frame["time"], utc=True)
    frame["hour"] = frame["time"].dt.floor("h")
    aggregations = {"pv": ("pv", "mean"), "load": ("load", "mean"), "soc": ("soc", "last")}
    for field in ("temperature_c", "cloud_cover_percent"):
        if field in frame.columns:
            aggregations[field] = (field, "mean")
    result = (frame.groupby(["site", "hour"], as_index=False)
              .agg(**aggregations)
              .rename(columns={"hour": "time"})
              .sort_values(["site", "time"]).reset_index(drop=True))
    quality: dict[str, dict[str, float | int]] = {}
    rebuilt: list[pd.DataFrame] = []
    for site, group in result.groupby("site"):
        if len(group) < minimum_hours:
            span_hours = ((group["time"].max() - group["time"].min()).total_seconds() / 3600.0
                          + 1 if not group.empty else 0)
            if not allow_gaps or span_hours < minimum_hours:
                raise OnlineContractError(f"station {site} requires {minimum_hours} hourly observations; got {len(group)}")
        end = group["time"].max()
        start = end - pd.Timedelta(hours=minimum_hours - 1)
        tail = group[(group["time"] >= start) & (group["time"] <= end)].copy()
        expected = pd.date_range(start, end, freq="h", tz="UTC")
        indexed = tail.set_index("time").reindex(expected)
        missing_ratio = float(indexed["pv"].isna().mean())
        deltas = tail["time"].sort_values().diff().dropna().dt.total_seconds().div(3600)
        largest_gap = float(max(0.0, (deltas.max() if not deltas.empty else 1.0) - 1.0))
        quality[str(site)] = {"coverage_ratio": 1.0 - missing_ratio,
                              "missing_hours": int(indexed["pv"].isna().sum()),
                              "max_gap_hours": largest_gap}
        if (not allow_gaps and (missing_ratio > 0 or largest_gap > 0)):
            raise OnlineContractError(f"station {site} history has gaps in the latest {minimum_hours} hours")
        if allow_gaps and (missing_ratio > max_missing_ratio or largest_gap > max_gap_hours):
            raise OnlineContractError(f"station {site} history gaps exceed tolerance")
        if not allow_gaps:
            missing_weather = [field for field in ("temperature_c", "cloud_cover_percent")
                               if field not in group or group[field].isna().any()]
            if missing_weather:
                raise OnlineContractError(f"station {site} history missing weather fields: {', '.join(missing_weather)}")
        indexed["site"] = str(site)
        indexed.index.name = "time"
        rebuilt.append(indexed.reset_index())
    output = (result if not allow_gaps else
              pd.concat(rebuilt, ignore_index=True).sort_values(["site", "time"]).reset_index(drop=True))
    output.attrs["quality"] = quality
    return output


def model_compatibility(required_features: Iterable[str], available_weather: Iterable[str]) -> dict[str, Any]:
    """Return a machine-readable compatibility decision; no feature aliases."""
    required = set(required_features)
    available = set(available_weather)
    missing = sorted(required - available)
    return {"compatible": not missing, "required": sorted(required),
            "available": sorted(available), "missing": missing,
            "decision": "load_model" if not missing else "reject_until_retrained"}
