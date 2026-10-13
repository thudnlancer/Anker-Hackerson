"""Create explicit synthetic observations for the shared-strategy smoke test."""
import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    device_day = datetime(2031, 1, 1, tzinfo=timezone.utc)
    wall_time = "2026-10-13T08:00:05+00:00"
    prices = {}
    for offset, name in enumerate(("today", "tomorrow")):
        day = device_day + timedelta(days=offset)
        prices[name] = {"date": day.date().isoformat(), "points": [
            {"start": (day + timedelta(hours=hour)).isoformat(),
             "import_price": .05 if hour == 0 else .08 if hour < 6 else .35,
             "export_price": .04} for hour in range(24)]}
    stations = {}
    for site, soc, pv, load in (("1", 40, 0, 0), ("2", 80, 500, 500), ("3", 100, 1000, 1000)):
        stations[site] = {"received_at": wall_time,
            "state": {"soc": soc, "solar_power": pv, "home_load": load,
                      "device_time": device_day.isoformat(),
                      "device_time_last_changed": "2026-10-13T08:00:04+00:00"},
            "weather": {"unavailable": "synthetic_fixture_without_weather"}}
    snapshot = {"input_kind": "synthetic", "received_at": wall_time, "prices": prices,
                "stations": stations,
                "note": "人工观测与价格，仅用于同仓策略接入测试；设备时钟与采集墙钟刻意分开。"}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(snapshot, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")
    print(str(args.output))


if __name__ == "__main__":
    main()
