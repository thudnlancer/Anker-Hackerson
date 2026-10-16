"""Periodic read-only collector for HA power history and API weather fields.

B4.1: every instance runs under a --node-id and writes per-node shard files
(site_{site}.{node}.jsonl) plus an atomic heartbeat per cycle, so redundant
collectors never share a write target and the monitor can detect stalls.
After --max-consecutive-errors failed cycles the process exits non-zero and
leaves restart to the watchdog (crash-loop supervision lives there).
"""

from pathlib import Path
from time import monotonic, sleep
from time import perf_counter
import argparse
import json
import os
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from energy_lab.online_contract import (append_observation, normalize_station_observation,
                                        normalize_weather_payload, infer_payload_timezone)
from scripts.online.run_shadow_dry_run import fetch_snapshot

COLLECTOR_VERSION = "collector/1.2-weather-degraded"


def configured_device_timezone(path: Path) -> str | None:
    """Only an explicit device-clock coordinate is trusted, not a local/UI timezone."""
    # An offline snapshot does not need a local credential/config file. Live
    # polling still requires the file when fetch_snapshot opens it.
    if not path.exists():
        return None
    raw = json.loads(path.read_text(encoding="utf-8"))
    value = (raw.get("local") or {}).get("device_timezone") or raw.get("device_timezone")
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError("configured device_timezone must be a nonempty timezone string")
    return value.strip()


def last_cached_observation(path: Path) -> dict | None:
    """Read only the last JSONL row; cache size must not slow each poll."""
    if not path.exists():
        return None
    with path.open("rb") as handle:
        handle.seek(0, os.SEEK_END)
        end = handle.tell()
        if not end:
            return None
        position = end - 1
        while position >= 0:
            handle.seek(position)
            if handle.read(1) not in (b"\r", b"\n"):
                break
            position -= 1
        last = position
        while position >= 0:
            handle.seek(position)
            if handle.read(1) == b"\n":
                break
            position -= 1
        handle.seek(position + 1)
        try:
            return json.loads(handle.read(last - position).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None


def utcnow() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def capture(snapshot: dict, cache_dir: Path, device_timezone: str | None = None,
            node_id: str | None = None, diagnostics: dict | None = None,
            configured_timezone: str | None = None, epoch_id: str | None = None) -> int:
    # HA state is a hard contract.  Weather is an optional forecast input: a
    # malformed/missing forecast must not discard an otherwise valid HA tick.
    prepared = []
    rejected = {}
    for site, item in snapshot["stations"].items():
        try:
            timezone_name = device_timezone or configured_timezone
            timezone_source = ("cli" if device_timezone else "confirmed_config" if configured_timezone else "api_timestamp")
            if not timezone_name:
                try:
                    timezone_name = infer_payload_timezone(item.get("weather", {}))
                except (ValueError, TypeError, AttributeError):
                    timezone_name = None
            observation = normalize_station_observation(site, item["state"], item["received_at"], device_timezone=timezone_name)
            if epoch_id:
                observation["epoch_id"] = epoch_id
            observation["device_timezone_source"] = (timezone_source if observation["device_timezone_assumption"] else "ha_timestamp")
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            rejected[str(site)] = f"{type(exc).__name__}: {str(exc)[:200]}"
            continue
        for field in ("temperature_c", "cloud_cover_percent", "solar_irradiance_wm2"):
            observation[field] = None
        observation["weather_status"] = "missing"
        observation["weather_missing_reason"] = None
        observation["irradiance_source"] = None
        try:
            weather_payload = item.get("weather", {})
            weather = normalize_weather_payload(weather_payload)
            hour = observation["device_time"][:13]
            matches = weather[weather["time"].dt.strftime("%Y-%m-%dT%H") == hour]
            if matches.empty:
                raise ValueError(f"no forecast point for device hour {hour}")
            point = matches.iloc[0]
            observation["temperature_c"] = float(point["temperature_c"])
            observation["cloud_cover_percent"] = float(point["cloud_cover_percent"])
            observation["solar_irradiance_wm2"] = float(point["solar_irradiance_wm2"])
            observation["weather_status"] = "valid"
            observation["irradiance_source"] = "api_weather_at_HA_device_hour"
        except Exception as exc:
            # Deliberately do not carry forward or interpolate weather values.
            observation["weather_missing_reason"] = f"{type(exc).__name__}: {str(exc)[:200]}"
        prepared.append(observation)

    if diagnostics is not None:
        diagnostics.update({"rejected_stations": rejected,
            "weather_missing": {row["site"]: row["weather_missing_reason"]
                                for row in prepared if row["weather_status"] == "missing"}})
    if not prepared:
        raise ValueError(f"no valid HA stations: {rejected}")

    written = 0
    for observation in prepared:
        suffix = f".{node_id}" if node_id else ""
        destination = cache_dir / f"site_{observation['site']}{suffix}.jsonl"
        previous = last_cached_observation(destination)
        comparable = lambda row: {k: v for k, v in row.items() if k != "received_at"}
        duplicate = previous is not None and comparable(previous) == comparable(observation)
        if not duplicate:
            append_observation(cache_dir, observation, node_id=node_id)
            written += 1
    return written


def write_heartbeat(health_dir: Path, node_id: str, *, pid: int, started_at: str,
                    cycle: int, last_success_at: str | None, consecutive_errors: int,
                    error: str | None, device_times: dict, cache_dir: Path,
                    diagnostics: dict | None = None, epoch_id: str | None = None) -> Path:
    """Atomically replace the per-node heartbeat file (tmp + os.replace)."""
    health_dir.mkdir(parents=True, exist_ok=True)
    cache_bytes = {}
    for shard in sorted(cache_dir.glob(f"site_*.{node_id}.jsonl")) if cache_dir.exists() else []:
        site = shard.name.split(".")[0].removeprefix("site_")
        cache_bytes[site] = shard.stat().st_size
    payload = {"node_id": node_id, "pid": pid, "version": COLLECTOR_VERSION,
               "started_at": started_at, "last_cycle_at": utcnow(), "cycle": cycle,
               "last_success_at": last_success_at,
               "consecutive_errors": consecutive_errors,
               "last_error": error, "device_times": device_times,
               "cache_bytes": cache_bytes, "writes_performed": 0}
    payload["station_diagnostics"] = diagnostics or {}
    payload["epoch_id"] = epoch_id
    tmp = health_dir / f"heartbeat_{node_id}.json.tmp"
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    destination = health_dir / f"heartbeat_{node_id}.json"
    os.replace(tmp, destination)
    return destination


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect online observations without HA writes")
    parser.add_argument("--config", type=Path, default=Path("config/local/local_online_config.json"))
    parser.add_argument("--cache-dir", type=Path, default=Path("data/online_history"))
    parser.add_argument("--snapshot", type=Path, help="offline snapshot replay; no network")
    parser.add_argument("--device-timezone", help="explicit HA device-clock timezone; overrides local.device_timezone, then API timestamp")
    parser.add_argument("--interval-seconds", type=float, default=5,
                        help="target wall-clock interval; use a short interval for accelerated device time")
    parser.add_argument("--iterations", type=int, default=1, help="use 0 to run until interrupted")
    parser.add_argument("--node-id", default="nodeA",
                        help="shard/heartbeat identity of this collector instance (B4)")
    parser.add_argument("--epoch-id", help="independent simulator epoch/cache identity")
    parser.add_argument("--health-dir", type=Path, default=Path("outputs/collector_health"))
    parser.add_argument("--max-consecutive-errors", type=int, default=12,
                        help="exit non-zero after this many failed cycles; 0 disables")
    args = parser.parse_args()
    if args.interval_seconds <= 0 or args.iterations < 0:
        parser.error("interval-seconds must be positive and iterations nonnegative")
    if args.max_consecutive_errors < 0:
        parser.error("max-consecutive-errors must be nonnegative")
    if not args.node_id.replace("_", "").replace("-", "").isalnum():
        parser.error("node-id must be filename-safe")
    if args.epoch_id is not None and (not args.epoch_id.strip() or
            not args.epoch_id.replace("_", "").replace("-", "").isalnum()):
        parser.error("epoch-id must be nonempty and filename-safe")

    started_at = utcnow()
    epoch_id = args.epoch_id or f"collector-{started_at}"
    # Generic local.timezone is a display/location setting, not confirmed HA
    # device-clock evidence. Never silently use it for a naive HA timestamp.
    confirmed_timezone = configured_device_timezone(args.config)
    iteration = 0
    consecutive_errors = 0
    last_success_at: str | None = None
    while args.iterations == 0 or iteration < args.iterations:
        cycle_started = monotonic()
        started = perf_counter()
        snapshot = None
        error_type = None
        error_detail = None
        diagnostics = {}
        try:
            snapshot = (json.loads(args.snapshot.read_text(encoding="utf-8")) if args.snapshot
                        else fetch_snapshot(args.config, include_prices=False))
            count = capture(snapshot, args.cache_dir, args.device_timezone, node_id=args.node_id,
                            diagnostics=diagnostics, configured_timezone=confirmed_timezone,
                            epoch_id=epoch_id)
        except Exception as exc:
            count = 0
            error_type = type(exc).__name__
            error_detail = str(exc)[:240]
        consecutive_errors = consecutive_errors + 1 if error_type else 0
        if not error_type:
            last_success_at = utcnow()
        elapsed = perf_counter() - started
        device_times = ({site: item["state"].get("device_time")
                         for site, item in snapshot["stations"].items()} if snapshot else {})
        write_heartbeat(args.health_dir, args.node_id, pid=os.getpid(), started_at=started_at,
                        cycle=iteration + 1, last_success_at=last_success_at,
                        consecutive_errors=consecutive_errors,
                        error=(f"{error_type}:{error_detail}" if error_type else None),
                        device_times=device_times, cache_dir=args.cache_dir, diagnostics=diagnostics,
                        epoch_id=epoch_id)
        print(json.dumps({"iteration": iteration + 1, "node_id": args.node_id,
                          "observations_cached": count,
                          "writes_performed": 0, "cache_dir": str(args.cache_dir),
                          "cycle_seconds": round(elapsed, 3),
                          "target_interval_seconds": args.interval_seconds,
                          "over_interval": elapsed > args.interval_seconds,
                          "error_type": error_type,
                          "error_detail": error_detail,
                          "consecutive_errors": consecutive_errors,
                          "station_diagnostics": diagnostics,
                          "device_times": device_times,
                          "device_times_aligned": (len(set(map(str, device_times.values()))) <= 1
                                                    if device_times else False)}, ensure_ascii=False), flush=True)
        iteration += 1
        if args.max_consecutive_errors and consecutive_errors >= args.max_consecutive_errors:
            print(json.dumps({"fatal": "max_consecutive_errors_reached",
                              "consecutive_errors": consecutive_errors,
                              "node_id": args.node_id}, ensure_ascii=False), flush=True)
            raise SystemExit(2)
        if args.iterations and iteration >= args.iterations:
            break
        sleep(max(0.0, args.interval_seconds - (monotonic() - cycle_started)))


if __name__ == "__main__":
    main()
