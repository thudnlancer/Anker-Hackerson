"""Offline experiment: verify bounded history gaps degrade instead of failing.

Synthetic weather is derived from recent observations only to exercise the
gating code path. It is not a forecast-quality result.
"""

from pathlib import Path
import argparse
import json
import sys

import joblib
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
from energy_lab.online_contract import OnlineContractError, hourly_observation_history, load_observation_history
from energy_lab.online_inference import predict_online_power
from run_online_inference_dry_run import tail_is_contiguous


def build_synthetic_weather(history: pd.DataFrame, horizon: int = 24) -> dict:
    hourly = hourly_observation_history(history, minimum_hours=24, allow_gaps=True)
    last = hourly["time"].max()
    template = hourly.tail(24)
    points = []
    for index, stamp in enumerate(pd.date_range(last + pd.Timedelta(hours=1), periods=horizon, freq="h")):
        source = template.iloc[index % len(template)]
        points.append({"timestamp": stamp.isoformat(), "condition": "synthetic_replay",
                       "temperature_c": float(source["temperature_c"]),
                       "cloud_cover_percent": float(source["cloud_cover_percent"]),
                       "solar_irradiance_wm2": 0.0})
    return {"provenance": "synthetic_replay_of_observed_weather",
            "today": {"points": points[:24]}, "tomorrow": {"points": points[24:]}}


def drop_hours(history: pd.DataFrame, stamps: set) -> pd.DataFrame:
    return history[~history["time"].dt.floor("h").isin(stamps)].reset_index(drop=True)


def evaluate(cache_dir: Path, site: str, models, *, keep_stamps, minimum_hours,
             max_missing_ratio, max_gap_hours, max_stale_hours) -> dict:
    history = load_observation_history(cache_dir / f"site_{site}.jsonl")
    tail = history["time"].max()
    trimmed = drop_hours(history, keep_stamps) if keep_stamps is not None else history
    try:
        hourly = hourly_observation_history(trimmed, minimum_hours=minimum_hours, allow_gaps=True,
                                            max_missing_ratio=max_missing_ratio, max_gap_hours=max_gap_hours)
    except OnlineContractError as exc:
        return {"status": "model_unready", "error_type": type(exc).__name__, "error": str(exc),
                "fallback": "api_first_current_pv_load_persistence"}
    quality = hourly.attrs.get("quality", {}).get(site, {})
    contiguous, tail_reason = tail_is_contiguous(hourly, str(tail), "UTC", max_stale_hours)
    if not contiguous:
        return {"status": "model_unready", "reason": tail_reason,
                "fallback": "api_first_current_pv_load_persistence"}
    weather = build_synthetic_weather(history)
    try:
        forecast = predict_online_power(trimmed, weather, models["pv"], models["load"],
                                        horizon=24, minimum_history_hours=minimum_hours,
                                        allow_gaps=True, max_missing_ratio=max_missing_ratio,
                                        max_gap_hours=max_gap_hours)
    except OnlineContractError as exc:
        return {"status": "model_unready", "error_type": type(exc).__name__, "error": str(exc),
                "fallback": "api_first_current_pv_load_persistence", "history_quality": quality}
    full = (len(hourly) >= 168 and float(quality.get("coverage_ratio", 0.0)) >= 1.0
            and tail_reason == "contiguous")
    return {"status": "forecast_ready" if full else "forecast_degraded",
            "history_hours": int(len(hourly)), "history_quality": quality,
            "tail_status": tail_reason, "forecast_points": int(len(forecast)),
            "synthetic_weather": True}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache-dir", type=Path, required=True)
    parser.add_argument("--model-dir", type=Path, default=Path("models/online_api"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--sites", default="1,2,3")
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(f"Use a new output directory: {args.output}")
    models = {target: joblib.load(args.model_dir / f"shared_{target}.joblib") for target in ("pv", "load")}
    sites = [x.strip() for x in args.sites.split(",") if x.strip()]
    common = {"models": models, "minimum_hours": 168, "max_missing_ratio": 0.05,
              "max_gap_hours": 3, "max_stale_hours": 3.0}
    report = {"mode": "offline_gap_tolerance_experiment", "writes_performed": 0,
              "weather_provenance": "synthetic_replay_of_observed_weather",
              "scoreable_result": False, "cases": {}}
    for site in sites:
        history = load_observation_history(args.cache_dir / f"site_{site}.jsonl")
        hourly_index = pd.date_range(history["time"].max() - pd.Timedelta(hours=72), periods=73, freq="h")
        cases = {"clean_history": None,
                 "two_isolated_missing_hours": {hourly_index[-8], hourly_index[-40]},
                 "six_hour_block_missing": set(hourly_index[-30:-24])}
        report["cases"][site] = {
            name: evaluate(args.cache_dir, site, keep_stamps=stamps, **common)
            for name, stamps in cases.items()
        }
    report["expected"] = {"clean_history": "forecast_ready",
                           "two_isolated_missing_hours": "forecast_degraded",
                           "six_hour_block_missing": "model_unready"}
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
