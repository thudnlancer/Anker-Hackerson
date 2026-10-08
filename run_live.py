"""Run the online policy locally or on the contest machine.

The default is a read-only dry-run. HA writes require an explicit ``--mode live``
and all three safety flags in the selected configuration.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import argparse
import math
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
from energy_lab.config import exchange_ha_token, load_runtime_config, require_runtime_fields
from energy_lab.ha_client import HAClient
from energy_lab.model import dispatch
from energy_lab.sim_client import SimulatorClient


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


def run_once(cfg: dict, ha: HAClient, sim: SimulatorClient, *, write_enabled: bool) -> list[dict]:
    prices = price_points(sim.prices())
    if len(prices) < 24:
        raise RuntimeError(f"simulator returned only {len(prices)} price points; need at least 24")
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
            stamp_text = str(stamp).replace("T", " ")[:19]
            hour = datetime.strptime(stamp_text, "%Y-%m-%d %H:%M:%S").hour
            current = prices[hour % len(prices)]
            future = [numeric(p.get("import_price")) for p in prices[hour + 1 :]]
            if not future:
                future = [numeric(p.get("import_price")) for p in prices[:hour]]
            flow, watts, reason = dispatch(
                numeric(current.get("import_price")),
                numeric(current.get("export_price")),
                numeric(state.get("solar_power")),
                numeric(state.get("home_load")),
                numeric(state.get("soc")),
                future,
                float(cfg["capacity_kwh"]),
                float(cfg["max_power_w"]),
            )
            finite_watts = watts if math.isfinite(watts) else 0.0
            record = {
                "site": site,
                "device_time": stamp,
                "flow": flow,
                "power_w": finite_watts,
                "soc": state.get("soc"),
                "reason": reason,
                "write_enabled": write_enabled,
            }
            if write_enabled:
                ha.set_dispatch(suffix, flow, finite_watts, live=True)
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
