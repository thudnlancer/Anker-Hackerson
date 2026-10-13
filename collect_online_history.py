"""Periodic read-only collector for HA power history and API weather fields."""

from pathlib import Path
from time import monotonic, sleep
from time import perf_counter
import argparse
import json
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
from energy_lab.online_contract import (append_observation, normalize_station_observation,
                                        normalize_weather_payload, infer_payload_timezone)
from run_shadow_dry_run import fetch_snapshot


def capture(snapshot: dict, cache_dir: Path, device_timezone: str | None = None) -> int:
    # Validate and normalize the complete station snapshot before appending any
    # rows, so bad weather/time alignment cannot leave a partial station batch.
    prepared = []
    for site, item in snapshot["stations"].items():
        weather = normalize_weather_payload(item["weather"])
        timezone_name = device_timezone or infer_payload_timezone(item["weather"])
        observation = normalize_station_observation(site, item["state"], item["received_at"], device_timezone=timezone_name)
        hour = observation["device_time"][:13]
        matches = weather[weather["time"].dt.strftime("%Y-%m-%dT%H") == hour]
        if matches.empty:
            raise ValueError(f"site {site}: weather has no point for device hour {hour}")
        point = matches.iloc[0]
        observation["temperature_c"] = float(point["temperature_c"])
        observation["cloud_cover_percent"] = float(point["cloud_cover_percent"])
        observation["solar_irradiance_wm2"] = float(point["solar_irradiance_wm2"])
        observation["irradiance_source"] = "api_weather_at_HA_device_hour"
        prepared.append(observation)

    for observation in prepared:
        append_observation(cache_dir, observation)
    return len(prepared)


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect online observations without HA writes")
    parser.add_argument("--config", type=Path, default=Path("local_online_config.json"))
    parser.add_argument("--cache-dir", type=Path, default=Path("data/online_history"))
    parser.add_argument("--snapshot", type=Path, help="offline snapshot replay; no network")
    parser.add_argument("--device-timezone", help="optional override; otherwise infer from API timestamps")
    parser.add_argument("--interval-seconds", type=float, default=5,
                        help="target wall-clock interval; use a short interval for accelerated device time")
    parser.add_argument("--iterations", type=int, default=1, help="use 0 to run until interrupted")
    args = parser.parse_args()
    if args.interval_seconds <= 0 or args.iterations < 0:
        parser.error("interval-seconds must be positive and iterations nonnegative")
    iteration = 0
    while args.iterations == 0 or iteration < args.iterations:
        cycle_started = monotonic()
        started = perf_counter()
        snapshot = None
        error_type = None
        error_detail = None
        try:
            snapshot = (json.loads(args.snapshot.read_text(encoding="utf-8")) if args.snapshot
                        else fetch_snapshot(args.config, include_prices=False))
            count = capture(snapshot, args.cache_dir, args.device_timezone)
        except Exception as exc:
            count = 0
            error_type = type(exc).__name__
            error_detail = str(exc)[:240]
        elapsed = perf_counter() - started
        device_times = ({site: item["state"].get("device_time")
                         for site, item in snapshot["stations"].items()} if snapshot else {})
        print(json.dumps({"iteration": iteration + 1, "observations_cached": count,
                          "writes_performed": 0, "cache_dir": str(args.cache_dir),
                          "cycle_seconds": round(elapsed, 3),
                          "target_interval_seconds": args.interval_seconds,
                          "over_interval": elapsed > args.interval_seconds,
                          "error_type": error_type,
                          "error_detail": error_detail,
                          "device_times": device_times,
                          "device_times_aligned": (len(set(map(str, device_times.values()))) <= 1
                                                    if device_times else False)}, ensure_ascii=False), flush=True)
        iteration += 1
        if args.iterations and iteration >= args.iterations:
            break
        sleep(max(0.0, args.interval_seconds - (monotonic() - cycle_started)))


if __name__ == "__main__":
    main()
