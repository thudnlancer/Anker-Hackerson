"""Offline, same-snapshot candidate comparison; no network or HA client.

The model arm is diagnostic only, not an API-first production replacement.
No realized savings or forecast-accuracy claim can be made from one snapshot.
"""
import argparse
import json
from pathlib import Path
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from energy_lab.api_policy import build_api_power_path
from energy_lab.online_contract import infer_payload_timezone
from energy_lab.shadow import daily_points, make_candidate, validate_state


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--forecasts", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("Use a new output directory")
    snapshot = json.loads(args.snapshot.read_text(encoding="utf-8"))
    readiness = json.loads((args.forecasts / "result.json").read_text(encoding="utf-8"))
    prices = daily_points(snapshot["prices"])
    report = {"mode": "offline_same_snapshot_diagnostic", "writes_performed": 0,
              "replay_at_capture": True, "stations": {},
              "limits": ["Not a realized savings or accuracy evaluation.",
                         "Model PV/load are diagnostic; API irradiance calibration remains the primary design.",
                         "Checks cover candidate/first-step constraints, not executed device behavior."]}
    args.output.mkdir(parents=True)
    for site, item in snapshot["stations"].items():
        try:
            state = item["state"]
            validate_state(state, item["received_at"], now=snapshot["received_at"])
            if readiness["sites"][site]["status"] != "forecast_ready":
                raise ValueError("comparison requires full-tier ready forecasts")
            tick = pd.Timestamp(state["device_time"])
            zone = infer_payload_timezone(item["weather"])
            if tick.tzinfo is None:
                if not zone:
                    raise ValueError("missing device timezone")
                tick = tick.tz_localize(zone)
            tick = tick.tz_convert("UTC")
            baseline = build_api_power_path(state, item["weather"], tick)
            path = baseline.frame.copy()
            hourly = pd.DataFrame(json.loads((args.forecasts / f"site_{site}_forecast.json").read_text()))
            hourly["time"] = pd.to_datetime(hourly["time"], utc=True)
            expected = pd.date_range(tick.floor("h") + pd.Timedelta(hours=1), periods=24, freq="h")
            if not pd.DatetimeIndex(hourly.time).equals(expected):
                raise ValueError("forecast does not match snapshot future hours")
            # Current partial hour uses observed HA power. Future hourly values
            # are held within their own hour, never shifted or interpolated.
            future = path.time.dt.floor("h") > tick.floor("h")
            path.loc[future, ["pv", "load"]] = hourly.set_index("time").loc[
                path.loc[future, "time"].dt.floor("h"), ["pv", "load"]].to_numpy()
            values = path[["pv", "load"]].to_numpy()
            if not np.isfinite(values).all() or (values < 0).any():
                raise ValueError("invalid diagnostic model path")
            arms = {}
            for name, frame, mode in (
                ("persistence", baseline.frame, baseline.mode),
                ("auxiliary_model", path, "diagnostic_only_full_168h_model"),
            ):
                result = make_candidate(state, prices, forecast=frame, forecast_mode=mode,
                                        device_timezone=zone, solver_seconds=5.)
                passed = (result["status"] == "candidate_only"
                          and result["candidate"]["representable"]
                          and 0 <= result["planned_soc_after_15min"] <= 100
                          and 0 <= result["battery_power_w"] <= 5000 + 1e-6
                          and -1e-6 <= result["candidate"]["target_w"] <= 5000 + 1e-6)
                arms[name] = {**result, "first_step_checks_passed": bool(passed)}
                frame.to_csv(args.output / f"site_{site}_{name}_inputs.csv", index=False)
            report["stations"][site] = arms
        except (ValueError, KeyError, TypeError) as exc:
            report["stations"][site] = {"error": str(exc)}
    report["passed"] = bool(report["stations"]) and all(
        "error" not in arms and all(r["first_step_checks_passed"] for r in arms.values())
        for arms in report["stations"].values())
    (args.output / "result.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    if not report["passed"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
