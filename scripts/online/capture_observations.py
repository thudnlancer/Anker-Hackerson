"""Read-only HA snapshot capture for the online target-history cache."""

from pathlib import Path
import argparse
import json
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from energy_lab.online_contract import (append_observation, normalize_station_observation,
                                        normalize_weather_payload, infer_payload_timezone)
from scripts.online.run_shadow_dry_run import fetch_snapshot


def main() -> None:
    parser = argparse.ArgumentParser(description="Capture HA observations without sending controls")
    parser.add_argument("--config", type=Path, default=Path("config/local/local_online_config.json"))
    parser.add_argument("--cache-dir", type=Path, default=Path("data/online_history"))
    parser.add_argument("--snapshot", type=Path, help="use an existing shadow snapshot; no network")
    parser.add_argument("--device-timezone", help="optional override; otherwise infer from API timestamps")
    args = parser.parse_args()
    snapshot = json.loads(args.snapshot.read_text(encoding="utf-8")) if args.snapshot else fetch_snapshot(args.config)
    written = []
    for site, item in snapshot["stations"].items():
        weather = normalize_weather_payload(item["weather"])
        timezone_name = args.device_timezone or infer_payload_timezone(item["weather"])
        observation = normalize_station_observation(site, item["state"], item["received_at"], device_timezone=timezone_name)
        observation_hour = observation["device_time"][:13] + ":00:00+00:00"
        matching = weather[weather["time"].dt.strftime("%Y-%m-%dT%H:%M:%S%z") == observation_hour.replace("+00:00", "+0000")]
        if matching.empty:
            raise ValueError(f"site {site}: no weather point for observed device hour {observation_hour}")
        point = matching.iloc[0]
        observation["temperature_c"] = float(point["temperature_c"])
        observation["cloud_cover_percent"] = float(point["cloud_cover_percent"])
        written.append(str(append_observation(args.cache_dir, observation)))
    print(json.dumps({"writes_performed": 0, "observations_cached": len(written),
                      "cache_files": sorted(set(written)), "source": "read_only_snapshot"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
