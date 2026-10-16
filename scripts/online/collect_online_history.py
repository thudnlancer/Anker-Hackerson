"""Periodic read-only collector for HA power history and API weather fields.

B4.1: every instance runs under a --node-id and writes per-node shard files
(site_{site}.{node}.jsonl) plus an atomic heartbeat per cycle, so redundant
collectors never share a write target and the monitor can detect stalls.
After --max-consecutive-errors failed cycles the process exits non-zero and
leaves restart to the watchdog (crash-loop supervision lives there).

With --include-prices each tick also carries the hourly import/export price
covering its device hour (contest price API serves today/tomorrow only, so
prices must be captured live), and --price-log keeps a deduplicated raw copy
of every price window for later audit. A manifest.json records the epoch,
stations, timezone source, collector version and code revision.
"""

from pathlib import Path
from time import monotonic, sleep
from time import perf_counter
import argparse
import json
import os
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from energy_lab.online_contract import (append_observation, normalize_station_observation,
                                        normalize_weather_payload, infer_payload_timezone)
from scripts.online.run_shadow_dry_run import fetch_snapshot

COLLECTOR_VERSION = "collector/1.3-price-capture"


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


def price_hour_lookup(prices: dict) -> dict:
    """Hour-start -> (import_price, export_price) from the today/tomorrow windows."""
    import pandas as pd
    lookup = {}
    for day in ("today", "tomorrow"):
        for point in (prices.get(day) or {}).get("points", []):
            try:
                hour = pd.Timestamp(point["start"])
                lookup[hour] = (float(point["import_price"]), float(point["export_price"]))
            except (KeyError, TypeError, ValueError):
                continue
    return lookup


def attach_tick_price(observation: dict, lookup: dict) -> None:
    """Map a tick to the hourly price covering its device hour.

    A tick is never rejected for a missing price: the columns stay None and
    the reason is recorded, mirroring the weather degradation contract.
    """
    import pandas as pd
    observation["import_price"] = None
    observation["export_price"] = None
    observation["price_status"] = "missing"
    observation["price_missing_reason"] = None
    if not lookup:
        observation["price_missing_reason"] = "price window not captured"
        return
    try:
        hour = pd.Timestamp(observation["device_time"]).floor("h")
    except (ValueError, TypeError) as exc:
        observation["price_missing_reason"] = f"{type(exc).__name__}: {str(exc)[:200]}"
        return
    found = lookup.get(hour)
    if found is None:
        observation["price_missing_reason"] = f"no price point for device hour {hour.isoformat()}"
        return
    observation["import_price"], observation["export_price"] = found
    observation["price_status"] = "ok"


def append_price_window(path: Path, snapshot: dict, state: dict) -> bool:
    """Append the raw price window only when its content changed (dedup)."""
    prices = snapshot.get("prices")
    if not prices:
        return False
    blob = json.dumps(prices, sort_keys=True, ensure_ascii=False)
    if blob == state.get("last_blob"):
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"received_at": utcnow(), "prices": prices}, ensure_ascii=False) + "\n")
    state["last_blob"] = blob
    return True


def code_revision() -> str | None:
    try:
        out = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, timeout=10,
                             cwd=Path(__file__).resolve().parents[2])
        return out.stdout.decode().strip() or None if out.returncode == 0 else None
    except Exception:
        return None


def write_manifest(cache_dir: Path, *, node_id: str, epoch_id: str, started_at: str,
                   device_timezone: str | None, configured_timezone: str | None,
                   include_prices: bool, price_log: Path | None) -> Path:
    cache_dir.mkdir(parents=True, exist_ok=True)
    payload = {"node_id": node_id, "epoch_id": epoch_id, "started_at": started_at,
               "collector_version": COLLECTOR_VERSION, "code_sha256": code_revision(),
               "device_timezone": device_timezone, "configured_timezone": configured_timezone,
               "include_prices": include_prices,
               "price_log": str(price_log) if price_log else None,
               "shard_pattern": f"site_{{site}}.{node_id}.jsonl"}
    destination = cache_dir / "manifest.json"
    destination.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return destination


def capture(snapshot: dict, cache_dir: Path, device_timezone: str | None = None,
            node_id: str | None = None, diagnostics: dict | None = None,
            configured_timezone: str | None = None, epoch_id: str | None = None,
            with_prices: bool = False, price_history: dict | None = None) -> int:
    # HA state is a hard contract.  Weather is an optional forecast input: a
    # malformed/missing forecast must not discard an otherwise valid HA tick.
    prepared = []
    rejected = {}
    lookup = price_hour_lookup(snapshot.get("prices") or {}) if with_prices else {}
    if with_prices and price_history:
        # Day rollover: the API window is today/tomorrow only, so the last hour
        # of the previous device day would otherwise lose its price.  Hours are
        # never republished with different values, so carrying them forward is safe.
        for hour, value in price_history.items():
            lookup.setdefault(hour, value)
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
        if with_prices:
            attach_tick_price(observation, lookup)
        prepared.append(observation)

    if diagnostics is not None:
        diagnostics.update({"rejected_stations": rejected,
            "weather_missing": {row["site"]: row["weather_missing_reason"]
                                for row in prepared if row["weather_status"] == "missing"}})
        if with_prices:
            diagnostics["price_missing"] = {row["site"]: row["price_missing_reason"]
                                            for row in prepared if row["price_status"] != "ok"}
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
                    diagnostics: dict | None = None, epoch_id: str | None = None,
                    stall: dict | None = None) -> Path:
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
    payload["device_time_stall"] = stall
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
    parser.add_argument("--include-prices", action="store_true",
                        help="fetch the contest price window and attach hourly import/export prices to each tick")
    parser.add_argument("--price-log", type=Path,
                        help="append every changed raw price window to this JSONL file (requires --include-prices)")
    parser.add_argument("--health-dir", type=Path, default=Path("outputs/collector_health"))
    parser.add_argument("--max-consecutive-errors", type=int, default=12,
                        help="exit non-zero after this many failed cycles; 0 disables")
    parser.add_argument("--stall-alert-seconds", type=float, default=300.0,
                        help="flag device_time_stall.alert in the heartbeat when the max device "
                             "clock has not advanced for this many wall-clock seconds "
                             "(epoch resets/backward jumps count as advancement)")
    args = parser.parse_args()
    if args.interval_seconds <= 0 or args.iterations < 0:
        parser.error("interval-seconds must be positive and iterations nonnegative")
    if args.max_consecutive_errors < 0:
        parser.error("max-consecutive-errors must be nonnegative")
    if args.stall_alert_seconds < 0:
        parser.error("stall-alert-seconds must be nonnegative")
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
    if args.price_log and not args.include_prices:
        parser.error("--price-log requires --include-prices")
    write_manifest(args.cache_dir, node_id=args.node_id, epoch_id=epoch_id,
                   started_at=started_at, device_timezone=args.device_timezone,
                   configured_timezone=confirmed_timezone,
                   include_prices=args.include_prices, price_log=args.price_log)
    price_state: dict = {}
    price_history: dict = {}
    iteration = 0
    consecutive_errors = 0
    last_success_at: str | None = None
    last_device_time_seen: str | None = None
    device_time_last_change = monotonic()
    while args.iterations == 0 or iteration < args.iterations:
        cycle_started = monotonic()
        started = perf_counter()
        snapshot = None
        error_type = None
        error_detail = None
        diagnostics = {}
        try:
            snapshot = (json.loads(args.snapshot.read_text(encoding="utf-8")) if args.snapshot
                        else fetch_snapshot(args.config, include_prices=args.include_prices))
            count = capture(snapshot, args.cache_dir, args.device_timezone, node_id=args.node_id,
                            diagnostics=diagnostics, configured_timezone=confirmed_timezone,
                            epoch_id=epoch_id, with_prices=args.include_prices,
                            price_history=price_history)
            if args.include_prices:
                price_history.update(price_hour_lookup(snapshot.get("prices") or {}))
                if args.price_log:
                    append_price_window(args.price_log, snapshot, price_state)
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
        current_max = max((str(value) for value in device_times.values() if value), default=None)
        if current_max is not None and current_max != last_device_time_seen:
            last_device_time_seen = current_max
            device_time_last_change = monotonic()
        stall_seconds = (monotonic() - device_time_last_change) if last_device_time_seen else None
        stall = {"seconds_since_advance": (round(stall_seconds, 1) if stall_seconds is not None else None),
                 "alert": bool(stall_seconds is not None and stall_seconds >= args.stall_alert_seconds),
                 "threshold_seconds": args.stall_alert_seconds,
                 "device_time_max": last_device_time_seen}
        write_heartbeat(args.health_dir, args.node_id, pid=os.getpid(), started_at=started_at,
                        cycle=iteration + 1, last_success_at=last_success_at,
                        consecutive_errors=consecutive_errors,
                        error=(f"{error_type}:{error_detail}" if error_type else None),
                        device_times=device_times, cache_dir=args.cache_dir, diagnostics=diagnostics,
                        epoch_id=epoch_id, stall=stall)
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
