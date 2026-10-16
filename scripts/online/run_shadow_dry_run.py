"""Fetch public forecasts and HA snapshots, then generate non-executed candidates.

Only authentication POSTs and read-only GETs are available here. No HA service API.
"""
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
import argparse
import json
import sys
import hashlib
import re
from concurrent.futures import ThreadPoolExecutor

import joblib

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from energy_lab.config import load_runtime_config, require_runtime_fields, exchange_ha_token
from energy_lab.shadow import evaluate_snapshot
from energy_lab.forecast import WEATHER_FEATURES


def utcnow():
    return datetime.now(timezone.utc).isoformat()


def fetch_snapshot(config, *, include_prices=True):
    cfg = load_runtime_config(config, mode_override="dry-run")
    return fetch_snapshot_from_config(cfg, include_prices=include_prices)


def fetch_snapshot_from_config(cfg, *, include_prices=True):
    """Collect from a normalized in-memory config without persisting credentials.

    This entry point shares the same read-only endpoint allowlist as the file
    wrapper. Configuration mode and write flags cannot enable control calls.
    """
    require_runtime_fields(cfg, write_enabled=False)
    token = exchange_ha_token(cfg)
    session = requests.Session()
    session.trust_env = False
    response = session.post(str(cfg["sim_base"]).rstrip("/")+"/api/auth/login",
                            json={"username": cfg["sim_username"], "password": cfg["sim_password"]}, timeout=10)
    response.raise_for_status()
    sim_token = response.json()["token"]

    def get(base, path, credential, **params):
        # Narrow allowlist ensures this runner cannot fetch device internals via simulator.
        if path not in ("/api/states", "/api/price", "/api/weather/today", "/api/weather/tomorrow"):
            raise ValueError("read endpoint not allowed")
        r = session.get(str(base).rstrip("/")+path, headers={"Authorization": f"Bearer {credential}"},
                        params=params or None, timeout=10)
        r.raise_for_status()
        return r.json()

    snapshot = {"started_at": utcnow(), "stations": {}}
    if include_prices:
        snapshot["prices"] = get(cfg["sim_base"], "/api/price", sim_token)

    def fetch_weather(site, day):
        # Use an independent session per worker; requests.Session is not shared across threads.
        worker = requests.Session()
        worker.trust_env = False
        path = "/api/weather/" + day
        try:
            if path not in ("/api/weather/today", "/api/weather/tomorrow"):
                raise ValueError("read endpoint not allowed")
            response = worker.get(str(cfg["sim_base"]).rstrip("/") + path,
                                  headers={"Authorization": f"Bearer {sim_token}"},
                                  params={"site": int(site)}, timeout=10)
            response.raise_for_status()
            return response.json()
        except (requests.RequestException, ValueError, KeyError) as exc:
            return {"unavailable": type(exc).__name__}
        finally:
            worker.close()

    # Weather endpoints are independent. Fetch them concurrently so a short
    # device-tick collector is not delayed by six sequential forecast requests.
    weather = {}
    weather_jobs = {}
    with ThreadPoolExecutor(max_workers=max(1, len(cfg["stations"]) * 2)) as pool:
        for site in cfg["stations"]:
            weather[site] = {}
            for day in ("today", "tomorrow"):
                weather_jobs[(site, day)] = pool.submit(fetch_weather, site, day)
        # Fetch HA while forecast requests are in flight.
        states = get(cfg["ha_base"], "/api/states", token)
        for (site, day), future in weather_jobs.items():
            weather[site][day] = future.result()
    received_at = utcnow()
    entities = {s["entity_id"]: s for s in states}
    for site, spec in cfg["stations"].items():
        suffix = spec["suffix"]
        names = {"soc": "soc", "solar_power": "solar_power", "home_load": "home_load",
                 "device_time": "device_time", "battery_charging_power_w": "battery_charging_power",
                 "battery_discharging_power_w": "battery_discharging_power",
                 "grid_export_power_w": "grid_export_power", "grid_import_power_w": "grid_import_power"}
        state = {}
        for key, name in names.items():
            item = entities.get(f"sensor.{suffix}_{name}", {})
            state[key] = item.get("state")
            state[key+"_last_changed"] = item.get("last_changed")
        for key, entity in {"operating_mode": f"select.{suffix}_operating_mode_device_runs_by_command_in_third_party_controlled",
                            "grid_flow": f"select.{suffix}_grid_flow", "power_control": f"number.{suffix}_power_control"}.items():
            state[key] = entities.get(entity, {}).get("state")
        snapshot["stations"][site] = {"received_at": received_at, "state": state, "weather": weather[site]}
    snapshot["received_at"] = received_at
    snapshot["weather_provenance"] = "API today/tomorrow responses; reception time is not a forecast issuance timestamp"
    return snapshot


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--config", type=Path, default=Path("config/local/local_online_config.json"))
    p.add_argument("--snapshot", type=Path, help="offline replay; no network")
    p.add_argument("--device-timezone", help="optional override; otherwise infer the clock coordinate from API timestamps")
    p.add_argument("--model-directory", type=Path, default=Path("models"), help="inspect feature compatibility; does not invent missing history/features")
    p.add_argument("--replay-at-capture", action="store_true", help="offline only: evaluate freshness at recorded receipt")
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--explainability-run-id", help="explicitly export same-solve decision evidence to local JSONL; no HTTP submission")
    p.add_argument("--explainability-input-kind", choices=("mixed", "synthetic"),
                   help="mixed observations/assumptions or explicitly synthetic offline inputs (default: mixed)")
    a = p.parse_args()
    if a.replay_at_capture and not a.snapshot:
        p.error("--replay-at-capture requires --snapshot")
    if a.explainability_input_kind and not a.explainability_run_id:
        p.error("--explainability-input-kind requires --explainability-run-id")
    if a.explainability_run_id and not re.fullmatch(r"[A-Za-z0-9_.:-]{1,120}", a.explainability_run_id):
        p.error("--explainability-run-id must use 1-120 letters, digits, underscores, dots, colons or hyphens")
    if (a.output / "result.json").exists():
        raise FileExistsError("Use a new output directory")
    a.output.mkdir(parents=True, exist_ok=True)
    evidence_path = a.output / "explainability-decisions.jsonl" if a.explainability_run_id else None
    if evidence_path is not None and evidence_path.exists():
        raise FileExistsError("Use a new output directory for explainability evidence")
    evidence_stream, evidence_count = None, 0
    started = perf_counter()
    report = {"mode": "shadow_only", "writes_performed": 0, "started_at": utcnow()}
    try:
        snapshot = json.loads(a.snapshot.read_text(encoding="utf-8")) if a.snapshot else fetch_snapshot(a.config)
        (a.output / "snapshot.json").write_text(json.dumps(snapshot, indent=2, allow_nan=False), encoding="utf-8")
        replay_time = snapshot["received_at"] if a.replay_at_capture else None
        evidence_options = {}
        if evidence_path is not None:
            evidence_stream = evidence_path.open("x", encoding="utf-8")

            def evidence_sink(record):
                nonlocal evidence_count
                payload = json.dumps(record, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
                evidence_stream.write(payload + "\n")
                evidence_stream.flush()
                evidence_count += 1

            evidence_options = {"evidence_sink": evidence_sink,
                                "evidence_identity": {"run_id": a.explainability_run_id,
                                    "input_kind": a.explainability_input_kind or "mixed"}}
        report.update(evaluate_snapshot(snapshot, now=replay_time, device_timezone=a.device_timezone,
                                        **evidence_options))
        compatibility = {}
        for target in ("pv", "load"):
            model_path = a.model_directory / f"shared_{target}.joblib"
            if not model_path.exists():
                compatibility[target] = {"status": "model_missing"}
                continue
            model = joblib.load(model_path)
            required = set(model.features) & set(WEATHER_FEATURES)
            per_site = {}
            for site, item in snapshot["stations"].items():
                weather_points = [p for day in ("today", "tomorrow") for p in item["weather"].get(day, {}).get("points", [])]
                available = set.intersection(*(set(p) for p in weather_points)) if weather_points else set()
                per_site[site] = {"available_weather_fields": sorted(available),
                                  "missing_model_weather_fields": sorted(required-available),
                                  "observed_target_history_available": False}
            compatibility[target] = {"sha256": hashlib.sha256(model_path.read_bytes()).hexdigest(), "sites": per_site,
                                     "loaded_for_inference": False}
        report["model_compatibility"] = compatibility
        report["device_timezone_assumption"] = a.device_timezone
        report["device_time_basis"] = ("explicit_api_clock_override" if a.device_timezone
                                        else "inferred_from_api_timestamps")
        report["snapshot_saved"] = True
        report["replay_at_capture"] = a.replay_at_capture
        ready = sum(s["status"] == "candidate_only" for s in report["stations"].values())
        report["status"] = "completed" if ready == len(report["stations"]) else "partial" if ready else "blocked"
    except Exception as exc:
        # Avoid serializing request objects, URLs with query credentials, or auth responses.
        report.update(status="blocked", error_type=type(exc).__name__)
    finally:
        if evidence_stream is not None:
            evidence_stream.close()
    if evidence_path is not None:
        failures = sum(value.get("explainability_export", {}).get("status") == "failed"
                       for value in report.get("stations", {}).values())
        report["explainability_export"] = {"path": str(evidence_path), "run_id": a.explainability_run_id,
            "input_kind": a.explainability_input_kind or "mixed", "records_written": evidence_count,
            "failed_candidates": failures,
            "status": "failed" if failures and not evidence_count else "partial" if failures else "completed" if evidence_count else "empty"}
    report["elapsed_seconds"] = perf_counter()-started
    report["finished_at"] = utcnow()
    (a.output / "result.json").write_text(json.dumps(report, indent=2, allow_nan=False), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "model_compatibility"}, indent=2))
    if report["status"] == "blocked":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
