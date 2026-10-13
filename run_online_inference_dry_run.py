"""Run gated API-compatible forecasts from a frozen observation cache.

This runner is deliberately offline with respect to HA writes: it consumes a
saved read-only weather snapshot and writes only forecast artifacts.
"""

from pathlib import Path
import argparse
import json
import sys

import joblib
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
from energy_lab.online_contract import (hourly_observation_history, load_observation_history,
                                        OnlineContractError, infer_payload_timezone)
from energy_lab.online_inference import predict_online_power


def history_at_snapshot(history, device_time, device_timezone=None):
    tick = pd.Timestamp(device_time)
    if pd.isna(tick):
        raise OnlineContractError("invalid_device_time")
    if tick.tzinfo is None:
        if not device_timezone:
            raise OnlineContractError("device_time_without_timezone")
        tick = tick.tz_localize(device_timezone)
    return history[history["time"] <= tick.tz_convert("UTC")].copy()


def select_history_window(history, minimum_hours=24, max_missing_ratio=0.05,
                          max_gap_hours=3):
    for hours in sorted({168, minimum_hours}, reverse=True):
        try:
            hourly = hourly_observation_history(history, minimum_hours=hours,
                                                allow_gaps=True,
                                                max_missing_ratio=max_missing_ratio,
                                                max_gap_hours=max_gap_hours)
            return hours, hourly
        except OnlineContractError:
            if hours == minimum_hours:
                raise


def tail_is_contiguous(hourly: pd.DataFrame, device_time: str,
                       device_timezone: str | None = None,
                       max_stale_hours: float = 0.0) -> tuple[bool, str]:
    """Return whether the cache tail is close enough to the current device clock.

    ``max_stale_hours`` allows a short collection outage without disabling the
    model; the caller downgrades such forecasts instead of trusting them fully.
    """
    tick = pd.Timestamp(device_time)
    if tick.tzinfo is None:
        if not device_timezone:
            return False, "device_time_without_timezone"
        tick = tick.tz_localize(device_timezone)
    tick = tick.tz_convert("UTC")
    last = pd.Timestamp(hourly["time"].max())
    gap_hours = (tick.floor("h") - last).total_seconds() / 3600.0
    if gap_hours < 0:
        return False, f"cache_tail_ahead_of_device_time_{gap_hours:.2f}h"
    if gap_hours > 1.0 + max_stale_hours:
        return False, f"cache_tail_stale_by_{gap_hours:.2f}h"
    if gap_hours > 1.0:
        return True, f"stale_within_tolerance_{gap_hours:.2f}h"
    return True, "contiguous"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache-dir", type=Path, required=True)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--model-dir", type=Path, default=Path("models/online_api"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--horizon", type=int, default=24)
    parser.add_argument("--minimum-history-hours", type=int, default=24,
                        help="degraded fallback threshold; runner first attempts the full 168-hour tier")
    parser.add_argument("--max-stale-hours", type=float, default=3.0,
                        help="device hours of cache staleness tolerated as a degraded forecast")
    parser.add_argument("--max-missing-ratio", type=float, default=0.05,
                        help="maximum missing hourly ratio in the model history window")
    parser.add_argument("--max-gap-hours", type=int, default=3,
                        help="maximum single missing-hour run in tolerant history")
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(f"Use a new output directory: {args.output}")
    payload = json.loads(args.snapshot.read_text(encoding="utf-8"))
    pv_model = joblib.load(args.model_dir / "shared_pv.joblib")
    load_model = joblib.load(args.model_dir / "shared_load.joblib")
    report = {"mode": "online_inference_dry_run", "writes_performed": 0,
              "control_actions_generated": False, "sites": {}}
    args.output.mkdir(parents=True, exist_ok=True)
    for site in sorted(payload["stations"], key=str):
        history = load_observation_history(args.cache_dir / f"site_{site}.jsonl")
        device_time = payload["stations"][site]["state"].get("device_time")
        try:
            # The collector may advance while this snapshot is being fetched.
            # Never let observations newer than the snapshot clock leak into
            # the feature window; trim each station to the snapshot boundary.
            device_timezone = infer_payload_timezone(payload["stations"][site]["weather"])
            history = history_at_snapshot(history, device_time, device_timezone)
            # Prefer the full feature window whenever available. If a live
            # cache is still warming up, fall back to the degraded minimum so
            # the API-first strategy remains operational.
            selected_minimum, hourly = select_history_window(
                history, args.minimum_history_hours, args.max_missing_ratio, args.max_gap_hours)
            device_timezone = infer_payload_timezone(payload["stations"][site]["weather"])
            contiguous, reason = tail_is_contiguous(hourly, device_time, device_timezone,
                                                     args.max_stale_hours)
            if not contiguous:
                report["sites"][site] = {"status": "model_unready",
                                         "reason": reason,
                                         "history_observations": int(len(history)),
                                         "device_time": device_time,
                                         "cache_tail": str(hourly["time"].max()),
                                         "fallback": "api_first_current_pv_load_persistence"}
                continue
            forecast = predict_online_power(history, payload["stations"][site]["weather"],
                                            pv_model, load_model, horizon=args.horizon,
                                            minimum_history_hours=selected_minimum,
                                            allow_gaps=True,
                                            max_missing_ratio=args.max_missing_ratio,
                                            max_gap_hours=args.max_gap_hours)
            forecast.to_json(args.output / f"site_{site}_forecast.json", orient="records",
                             date_format="iso", indent=2)
            site_quality = hourly.attrs.get("quality", {}).get(str(site), {})
            full_quality = (selected_minimum >= 168
                            and len(hourly) >= 168 and reason == "contiguous"
                            and float(site_quality.get("coverage_ratio", 0.0)) >= 1.0)
            report["sites"][site] = {"status": "forecast_ready" if full_quality else "forecast_degraded",
                                     "history_observations": int(len(history)),
                                     "history_hours": int(len(hourly)),
                                     "quality_tier": "full_168h" if full_quality else "degraded_minimum",
                                     "history_quality": site_quality,
                                     "tail_status": reason,
                                     "forecast_points": int(len(forecast)),
                                     "forecast_file": f"site_{site}_forecast.json"}
        except (OnlineContractError, ValueError, KeyError) as exc:
            report["sites"][site] = {"status": "model_unready", "error_type": type(exc).__name__,
                                     "error": str(exc),
                                     "fallback": "api_first_current_pv_load_persistence"}
    statuses = {item["status"] for item in report["sites"].values()}
    report["status"] = ("completed" if statuses == {"forecast_ready"}
                         else "degraded" if statuses & {"forecast_ready", "forecast_degraded"}
                         else "model_unready")
    (args.output / "result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report["status"] == "model_unready":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
