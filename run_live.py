"""Run the online policy locally or on the contest machine.

The default is a read-only dry-run. HA writes require an explicit ``--mode live``
and all three safety flags in the selected configuration.
"""

from __future__ import annotations

from pathlib import Path
import argparse
import math
import sys
import time
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
from energy_lab.config import exchange_ha_token, load_runtime_config, require_runtime_fields
from energy_lab.ha_client import HAClient
from energy_lab.model import dispatch
from energy_lab.sim_client import SimulatorClient
from energy_lab.shadow import daily_points
from energy_lab.interface_contract import map_battery_action, state_is_fresh


def numeric(value: object) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float("nan")


def price_points(response: object) -> list[dict]:
    if isinstance(response, list):
        return response
    if isinstance(response, dict):
        if isinstance(response.get("points"), list):
            return response["points"]
        for key in ("today", "prices", "data"):
            value = response.get(key)
            if isinstance(value, dict) and isinstance(value.get("points"), list):
                return value["points"]
            if isinstance(value, list):
                return value
    return []


def price_window(response: object) -> list[dict]:
    """Flatten explicit today/tomorrow price blocks; never wrap one day."""
    if not isinstance(response, dict) or not any(key in response for key in ("today", "tomorrow")):
        points = price_points(response)
        if len(points) < 24:
            raise RuntimeError(f"simulator returned only {len(points)} price points; need explicit today/tomorrow data")
        return points
    return daily_points(response)


def current_price_and_future(points: list[dict], device_time: object) -> tuple[dict, list[float]]:
    stamp = pd.Timestamp(device_time)
    if stamp.tzinfo is None:
        api_stamp = next((pd.Timestamp(p["time"]) for p in points if pd.Timestamp(p["time"]).tzinfo is not None), None)
        if api_stamp is None:
            raise ValueError("price API timestamps have no timezone")
        stamp = stamp.tz_localize(api_stamp.tz)
    frame = pd.DataFrame(points)
    frame["time"] = pd.to_datetime(frame["time"], utc=True)
    wanted = stamp.tz_convert("UTC").floor("h")
    row = frame.loc[frame["time"] == wanted]
    if row.empty:
        raise ValueError("price API has no current device-time slot")
    pos = int(row.index[0])
    future = frame.iloc[pos + 1:]["import_price"].astype(float).tolist()
    return row.iloc[0].to_dict(), future


def run_once(cfg: dict, ha: HAClient, sim: SimulatorClient, *, write_enabled: bool) -> list[dict]:
    prices = price_window(sim.prices())
    results: list[dict] = []
    for site, item in sorted(cfg["stations"].items(), key=lambda pair: str(pair[0])):
        suffix = str(item["suffix"])
        try:
            state = ha.station_state(suffix)
            stamp = state.get("device_time")
            freshness = state.get("device_time_last_changed")
            if not stamp or not freshness or "unavailable" in str(stamp).lower():
                results.append({"site": site, "status": "stale_or_unavailable", "device_time": stamp})
                continue
            if not state_is_fresh(freshness, max_age_seconds=float(cfg.get("stale_after_seconds", 15.))):
                results.append({"site": site, "status": "stale_or_unavailable", "device_time": stamp})
                continue
            current, future = current_price_and_future(prices, stamp)
            solar_w, load_w, soc = numeric(state.get("solar_power")), numeric(state.get("home_load")), numeric(state.get("soc"))
            flow, watts, reason = dispatch(
                numeric(current.get("import_price")),
                numeric(current.get("export_price")),
                solar_w, load_w, soc,
                future,
                float(cfg["capacity_kwh"]),
                float(cfg["max_power_w"]),
            )
            finite_watts = watts if math.isfinite(watts) else 0.0
            # No zero_flow_verified site exists yet (Q1): planned idle steps are
            # executed as an explicit self_consumption mode switch, never via a
            # silent dispatch mapping.
            mapping = map_battery_action(flow, finite_watts, solar_w, load_w,
                                         limit_w=float(cfg["max_power_w"]),
                                         idle_mode="self_consumption" if flow in ("idle", "hold") else None)
            representable, command_reason = mapping.representable, mapping.reason
            command_flow = mapping.flow or flow
            command_watts = float(mapping.target_w or 0.0)
            if not representable:
                results.append({"site": site, "status": "unrepresentable_command",
                                "device_time": stamp, "flow": flow, "power_w": finite_watts,
                                "reason": command_reason, "write_enabled": False})
                continue
            record = {
                "site": site,
                "device_time": stamp,
                "flow": flow,
                "power_w": finite_watts,
                "command_flow": command_flow,
                "command_power_w": command_watts,
                "soc": state.get("soc"),
                "reason": reason,
                "write_enabled": write_enabled,
            }
            if write_enabled:
                if command_flow == "self_consumption":
                    ha.set_self_consumption(suffix, live=True)
                else:
                    ha.set_dispatch(suffix, command_flow, command_watts, live=True)
            results.append(record)
        except Exception as exc:  # isolate one failed station from the other two
            results.append({"site": site, "status": "station_error", "error": str(exc)})
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="Online storage policy runner")
    parser.add_argument("--config", type=Path, default=Path("config.json"))
    parser.add_argument("--mode", choices=("dry-run", "live"), default=None)
    parser.add_argument("--once", action="store_true", help="run one tick and exit")
    args = parser.parse_args()
    cfg = load_runtime_config(args.config, args.mode)
    mode = str(cfg["mode"]).lower().replace("_", "-")
    write_enabled = mode == "live"
    require_runtime_fields(cfg, write_enabled=write_enabled)
    token = exchange_ha_token(cfg)
    ha = HAClient(str(cfg["ha_base"]), token)
    sim = SimulatorClient(str(cfg["sim_base"]), str(cfg["sim_username"]), str(cfg["sim_password"]))
    print(f"mode={mode}; write_enabled={write_enabled}; config={cfg['source']}")
    if write_enabled:
        print("WARNING: HA write mode is enabled by all safety gates")
    while True:
        for result in run_once(cfg, ha, sim, write_enabled=write_enabled):
            print(result, flush=True)
        if args.once:
            return
        time.sleep(max(5.0, float(cfg["poll_seconds"])))


if __name__ == "__main__":
    main()
