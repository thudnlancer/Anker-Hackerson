"""Check whether cached HA observations are sufficient for online inference."""

from pathlib import Path
import argparse
import json
import sys

import joblib

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
from energy_lab.online_contract import OnlineContractError, hourly_observation_history, merge_observation_history, model_compatibility
from energy_lab.forecast import WEATHER_FEATURES

FULL_HISTORY_HOURS = 168
DEGRADED_MINIMUM_HOURS = 24


def check(cache_dir: Path, model_dir: Path, sites: list[str]) -> dict:
    models = {}
    for target in ("pv", "load"):
        path = model_dir / f"shared_{target}.joblib"
        if not path.exists():
            return {"ready": False, "degraded_model_usable": False,
                    "strategy_ready": True, "reason": f"missing_model_{target}",
                    "policy": "API-first strategy remains runnable; use current PV/load persistence fallback"}
        model = joblib.load(path)
        models[target] = {"path": str(path), "features": list(model.features)}
    weather_available = {"temperature_c", "cloud_cover_percent"}
    compatibility = {target: model_compatibility(
        (feature for feature in item["features"] if feature in WEATHER_FEATURES), weather_available
    ) for target, item in models.items()}
    stations = {}
    merged = merge_observation_history(cache_dir)  # B4: legacy files + per-node shards
    for site in sites:
        history = merged[merged.site == site].reset_index(drop=True) if not merged.empty else merged
        degraded_error = None
        try:
            hourly = hourly_observation_history(history)
            unique_hours = int(hourly[hourly.site == site]["time"].nunique())
            history_error = None
        except OnlineContractError as exc:
            unique_hours = int(history["time"].dt.floor("h").nunique()) if not history.empty else 0
            history_error = str(exc)
        quality = {}
        try:
            degraded = hourly_observation_history(history, minimum_hours=DEGRADED_MINIMUM_HOURS,
                                                  allow_gaps=True)
            quality = degraded.attrs.get("quality", {}).get(site, {})
        except OnlineContractError as exc:
            degraded_error = str(exc)
        history_ready = (history_error is None and unique_hours >= FULL_HISTORY_HOURS
                         and float(quality.get("coverage_ratio", 0.0)) >= 1.0)
        degraded_ready = degraded_error is None and unique_hours >= DEGRADED_MINIMUM_HOURS
        stations[site] = {"observations": int(len(history)), "unique_device_hours": unique_hours,
                          "minimum_required": FULL_HISTORY_HOURS, "degraded_minimum": DEGRADED_MINIMUM_HOURS,
                          "history_ready": history_ready,
                          "coverage_ratio": quality.get("coverage_ratio"),
                          "max_gap_hours": quality.get("max_gap_hours"),
                          "model_tier": ("full_168h" if history_ready else
                                          "degraded_minimum" if degraded_ready else "unready")}
        if history_error:
            stations[site]["history_error"] = history_error
        if degraded_error and not history_error:
            stations[site]["degraded_history_error"] = degraded_error
    ready = all(item["history_ready"] for item in stations.values()) and all(item["compatible"] for item in compatibility.values())
    any_model_usable = all(item["model_tier"] != "unready" for item in stations.values())
    return {"ready": ready, "degraded_model_usable": any_model_usable,
            "strategy_ready": True,
            "reason": "ready" if ready else "degraded_model_available" if any_model_usable else "history_or_model_contract_incomplete",
            "models": models, "compatibility": compatibility, "stations": stations,
            "policy": "API-first strategy remains runnable; use full model only when ready=true, degraded model when degraded_model_usable=true, otherwise use persistence fallback"}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache-dir", type=Path, default=Path("data/online_history"))
    parser.add_argument("--model-dir", type=Path, default=Path("models/online_api"))
    parser.add_argument("--sites", default="1,2,3")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = check(args.cache_dir, args.model_dir, [x.strip() for x in args.sites.split(",") if x.strip()])
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not report["ready"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
