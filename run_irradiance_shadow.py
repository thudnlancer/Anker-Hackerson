"""Finite API/HA read-only paired calibration pilot; no HA service calls."""
from pathlib import Path
from time import monotonic, sleep
import argparse
import hashlib
import json
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
from run_shadow_dry_run import fetch_snapshot
from energy_lab.api_policy import build_api_power_path
from energy_lab.online_contract import normalize_weather_payload, infer_payload_timezone
from energy_lab.shadow import daily_points, make_candidate, validate_state
from energy_lab.system_model import calibrate_pv_scale, pv_from_irradiance


def causal_calibration(pairs, site, tick, received_at):
    """At least 3 distinct past daylight device hours; no current/future targets.

    This small-sample threshold exercises plumbing, NOT production readiness.
    Each device hour contributes one median pair to avoid tick oversampling.
    """
    eligible = [p for p in pairs if p["site"] == site
                and tick - pd.Timedelta(hours=168) <= pd.Timestamp(p["device_time"]) < tick
                and pd.Timestamp(p["received_at"]) < pd.Timestamp(received_at)]
    if not eligible:
        return None
    frame = pd.DataFrame(eligible)
    frame["hour"] = pd.to_datetime(frame.device_time, utc=True).dt.floor("h")
    frame = frame.groupby("hour")[["irradiance_wm2", "pv_w"]].median()
    daylight = frame[frame.irradiance_wm2 > 25]
    if len(daylight) < 3:
        return None
    result = calibrate_pv_scale(daylight.irradiance_wm2, daylight.pv_w)
    result.update(source="past_API_irradiance_and_HA_PV_pairs", device_hours=len(daylight),
                  last_history_hour=daylight.index[-1].isoformat(), production_validated=False)
    return result


def evaluate_pair_snapshot(snapshot, pairs, *, now=None, solver_seconds=2.):
    if not np.isfinite(solver_seconds) or solver_seconds <= 0:
        raise ValueError("positive finite solver budget required")
    results, additions = {}, []
    prices = daily_points(snapshot["prices"])
    for site, item in snapshot["stations"].items():
        try:
            state = item["state"]
            validate_state(state, item["received_at"], now=now or pd.Timestamp.now(tz="UTC"))
            weather = normalize_weather_payload(item["weather"])
            tick = pd.Timestamp(state["device_time"])
            if tick.tzinfo is None:
                zone = infer_payload_timezone(item["weather"])
                if not zone:
                    raise ValueError("missing_API_clock_timezone")
                tick = tick.tz_localize(zone)
            tick = tick.tz_convert("UTC")
            point = weather.set_index("time").loc[tick.floor("h")]
            irradiance = float(point.solar_irradiance_wm2)
            pv = float(state["solar_power"])
            calibration = causal_calibration(pairs, site, tick, item["received_at"])
            api_state = {**state, "device_time": tick.isoformat()}
            path = build_api_power_path(api_state, item["weather"], tick, calibration=calibration)
            candidate = make_candidate(api_state, prices, forecast=path.frame, forecast_mode=path.mode,
                                       solver_seconds=solver_seconds, idle_candidate="zero_charge_unverified")
            # Compare a calibration fitted BEFORE this observation with the new
            # concurrent pair, never score the current-observation override.
            nowcast = pv_from_irradiance(irradiance, calibration) if calibration else None
            result = {**candidate, "calibration": calibration,
                      "calibration_status": "pilot_past_pairs_only" if calibration else "insufficient_pairs_persistence",
                      "observed_pv_w": pv, "api_current_irradiance_wm2": irradiance,
                      "calibrated_nowcast_w": nowcast,
                      "concurrent_pair_abs_error_w": abs(nowcast-pv) if nowcast is not None else None,
                      "device_time": tick.isoformat(),
                      "weather_sha256": hashlib.sha256(json.dumps(item["weather"], sort_keys=True).encode()).hexdigest()}
            results[site] = result
            additions.append({"site": site, "device_time": tick.isoformat(), "received_at": item["received_at"],
                              "irradiance_wm2": irradiance, "pv_w": pv})
        except (ValueError, TypeError, KeyError) as exc:
            results[site] = {"status": "blocked", "error_type": type(exc).__name__, "writes_performed": 0}
    return results, additions


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("local_online_config.json"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--iterations", type=int, default=12)
    parser.add_argument("--interval-seconds", type=float, default=10.)
    parser.add_argument("--snapshots", type=Path, nargs="*", help="offline inputs, evaluated at recorded receipt")
    parser.add_argument("--history-result", type=Path, help="reuse previously saved real API/HA pairs; causal filtering still applies")
    parser.add_argument("--solver-seconds", type=float, default=2.,
                        help="per-station MILP budget for the read-only candidate; diagnostics only")
    args = parser.parse_args()
    if (not 1 <= args.iterations <= 24 or not np.isfinite(args.interval_seconds)
            or not 1 <= args.interval_seconds <= 30 or not np.isfinite(args.solver_seconds)
            or args.solver_seconds <= 0):
        parser.error("finite pilot requires 1..24 iterations, 1..30 second cadence, positive solver budget")
    args.output.mkdir(parents=True, exist_ok=False)
    pairs, cycles, errors = [], [], 0
    if args.history_result:
        saved = json.loads(args.history_result.read_text(encoding="utf-8"))
        if saved.get("mode") != "read_only_pilot" or saved.get("writes_performed") != 0:
            raise ValueError("history must be a read-only paired pilot result")
        for pair in saved["pairs"]:
            if (str(pair["site"]) not in ("1", "2", "3")
                    or not np.isfinite([pair["irradiance_wm2"], pair["pv_w"]]).all()
                    or min(pair["irradiance_wm2"], pair["pv_w"]) < 0
                    or pd.Timestamp(pair["device_time"]).tzinfo is None
                    or pd.Timestamp(pair["received_at"]).tzinfo is None):
                raise ValueError("invalid previous pair")
        pairs.extend(saved["pairs"])
    provenance = {"runner_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                  "history_result": str(args.history_result) if args.history_result else None,
                  "history_sha256": hashlib.sha256(args.history_result.read_bytes()).hexdigest() if args.history_result else None,
                  "seed_pairs": len(pairs), "offline_snapshot_replay": bool(args.snapshots),
                  "solver_seconds": args.solver_seconds,
                  "snapshot_sha256": ({str(path): hashlib.sha256(path.read_bytes()).hexdigest()
                                       for path in args.snapshots} if args.snapshots else {})}
    count = len(args.snapshots) if args.snapshots else args.iterations
    for i in range(count):
        started = monotonic()
        cycle = {"iteration": i+1, "writes_performed": 0}
        try:
            snapshot = (json.loads(args.snapshots[i].read_text(encoding="utf-8")) if args.snapshots
                        else fetch_snapshot(args.config))
            (args.output / f"snapshot_{i+1:03d}.json").write_text(json.dumps(snapshot, indent=2, allow_nan=False), encoding="utf-8")
            results, additions = evaluate_pair_snapshot(snapshot, pairs,
                                now=snapshot["received_at"] if args.snapshots else None,
                                solver_seconds=args.solver_seconds)
            pairs.extend(additions)
            cycle["stations"] = results
            errors = 0
        except Exception as exc:
            # No request object, auth response, endpoint credentials or raw
            # exception text is persisted in output.
            cycle.update(status="capture_failed", error_type=type(exc).__name__)
            errors += 1
        cycle["elapsed_seconds"] = monotonic()-started
        cycles.append(cycle)
        report = {"mode": "read_only_pilot", "writes_performed": 0, "live_ready": False,
                  "provenance": provenance,
                  "cycles": cycles, "pairs": pairs,
                  "limits": ["Calibration pilot, not accuracy acceptance or production training.",
                             "Concurrent irradiance/PV error is NOT future forecast MAE.",
                             "Reception timestamps are not forecast issuance timestamps.",
                             "Zero-charge idle is candidate-only, hardware response unverified.",
                             "Insufficient calibration never disables the current-power candidate path."]}
        (args.output / "result.json").write_text(json.dumps(report, indent=2, allow_nan=False), encoding="utf-8")
        print(json.dumps({"iteration": i+1, "pairs": len(pairs), "capture_error": cycle.get("error_type"),
                          "stations": {s: {"status": r["status"], "calibration": r.get("calibration_status")}
                                       for s, r in cycle.get("stations", {}).items()}}), flush=True)
        if errors >= 2:
            break
        if not args.snapshots and i+1 < count:
            sleep(max(0., args.interval_seconds-(monotonic()-started)))


if __name__ == "__main__":
    main()
