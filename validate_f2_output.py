"""Read-only F2 output validator; smoke mode never certifies a formal run."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
from energy_lab.backtest import load_csv
from energy_lab.validation import audit_trace

ARMS = {"self_consumption", "persistence", "irradiance", "hybrid"}
TRACE_FIELDS = {"site", "time", "arm", "solver_status", "accepted_incumbent", "mip_gap",
                "fallback", "fallback_reason", "optimizer_attributed", "attribution", "command_mode",
                "soc_before", "soc_after", "flow", "power_w", "grid_import_kwh",
                "grid_export_kwh", "cost_eur", "throughput_kwh"}
COUNT_FIELDS = ("fallback_steps", "accepted_incumbent_steps", "nonoptimal_solves",
                "optimizer_attributed_steps", "non_optimizer_attributed_steps")
PARAMETER_MAP = {"solver_time_limit": "solver_seconds", "capacity_kwh": "capacity_kwh",
                 "max_power_w": "max_power_w", "soc_min": "soc_min_percent",
                 "soc_max": "soc_max_percent", "charge_efficiency": "charge_efficiency",
                 "discharge_efficiency": "discharge_efficiency", "wear": "degradation_cost_eur_per_kwh",
                 "hold_mode": "successful_plan_idle"}


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def site_key(path: str) -> str:
    return Path(path.replace("\\", "/")).stem


def source_key(path: str) -> str:
    normalized = path.replace("\\", "/")
    marker = "src/energy_lab/"
    return normalized[normalized.index(marker):] if marker in normalized else Path(normalized).name


def bool_col(frame: pd.DataFrame, name: str) -> pd.Series:
    values = frame[name].astype(str).str.lower()
    if not values.isin(("true", "false")).all():
        raise ValueError(f"invalid boolean audit field {name}")
    return values.eq("true")


def validate(output: Path, *, manifest_path: Path, expected_manifest_sha256: str,
             data: Path, allow_smoke: bool = False) -> dict:
    actual_hash = digest(manifest_path)
    if actual_hash != expected_manifest_sha256.lower():
        raise ValueError("F1 manifest SHA-256 mismatch")
    f1 = json.loads(manifest_path.read_text(encoding="utf-8"))
    signature = f1["signature"]
    if not allow_smoke and not (f1.get("f2_authorized") is True and signature.get("f2_authorization") is True
                                and signature.get("integration_owner") and signature.get("signed_at")):
        raise ValueError("formal F2 requires signed F1 and separate F2 authorization")
    summary = json.loads((output / "summary.json").read_text(encoding="utf-8"))
    run = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    if run.get("f1_manifest_sha256") != actual_hash or summary.get("f1_manifest_sha256") != actual_hash:
        raise ValueError("output is not bound to the supplied F1 manifest SHA-256")
    if summary.get("official_score_claim") is not False or summary.get("writes_performed") != 0:
        raise ValueError("replay must have no HA writes and no official score claim")
    params, frozen = run["parameters"], f1["parameters"]
    if params.get("stage") != 4 or params.get("terminal_price_mode") != frozen["terminal_price"]["rule"]:
        raise ValueError("stage or terminal-price rule differs from F1")
    if params.get("f1_manifest_sha256") != actual_hash:
        raise ValueError("command arguments lack the F1 manifest hash")
    for run_key, frozen_key in PARAMETER_MAP.items():
        if params.get(run_key) != frozen[frozen_key]:
            raise ValueError(f"frozen parameter mismatch: {run_key}")
    expected_count = 1 if allow_smoke else 100
    if params.get("max_sites") != expected_count or run.get("hours") != 168:
        raise ValueError("site count or hours differs from validation mode")
    sites = summary.get("sites", [])
    if len(sites) != expected_count or len(run.get("data_sha256", {})) != expected_count:
        raise ValueError("summary/input site count mismatch")
    listed = f1["reproducibility"]["available_practice_dataset_sha256"]
    expected_data = {site_key(key): value for key, value in listed.items()}
    run_data = {site_key(key): value for key, value in run["data_sha256"].items()}
    if not allow_smoke and set(run_data) != set(expected_data):
        raise ValueError("formal F2 does not contain all frozen practice datasets")
    for site, recorded_hash in run_data.items():
        if site not in expected_data or recorded_hash != expected_data[site]:
            raise ValueError(f"site {site}: dataset hash differs from F1")
        if digest(data / f"{site}.csv") != recorded_hash:
            raise ValueError(f"site {site}: local input bytes differ from replay manifest")
    f1_sources = f1["reproducibility"]["source_sha256"]
    run_sources = {source_key(key): value for key, value in run.get("source_sha256", {}).items()}
    for source in ("run_contest_strategy_replay.py", "src/energy_lab/contest_replay.py",
                   "src/energy_lab/rolling.py", "src/energy_lab/model.py", "src/energy_lab/validation.py"):
        if run_sources.get(source) != f1_sources[source]:
            raise ValueError(f"replay source hash differs from F1: {source}")
    seen, total_counts, physical_rows = set(), {arm: {key: 0 for key in COUNT_FIELDS} for arm in ARMS}, 0
    for report in sites:
        site = str(report["site"])
        if site in seen or site not in run_data:
            raise ValueError(f"duplicate or unknown site: {site}")
        seen.add(site)
        if report.get("hours") != 168 or set(report.get("arms", {})) != ARMS:
            raise ValueError(f"site {site}: expected 168 hours and four exact arms")
        path = output / f"site_{site}_trace.csv"
        trace = pd.read_csv(path)
        if not TRACE_FIELDS.issubset(trace.columns) or len(trace) != 672:
            raise ValueError(f"site {site}: missing audit fields or not 672 trace rows")
        if trace.site.astype(str).ne(site).any() or set(trace.arm) != ARMS:
            raise ValueError(f"site {site}: trace identity/arms mismatch")
        trace["time"] = pd.to_datetime(trace.time, errors="coerce")
        if trace.time.isna().any():
            raise ValueError(f"site {site}: invalid trace time")
        reference_hours = None
        for arm, part in trace.groupby("arm"):
            if len(part) != 168 or part.time.nunique() != 168 or not part.time.sort_values().diff().dropna().eq(pd.Timedelta(hours=1)).all():
                raise ValueError(f"site {site}/{arm}: missing or duplicate device hours")
            current_hours = tuple(part.time.sort_values())
            if reference_hours is None:
                reference_hours = current_hours
            elif current_hours != reference_hours:
                raise ValueError(f"site {site}/{arm}: arms use different device-hour windows")
            fallback, incumbent, attributed = bool_col(part, "fallback"), bool_col(part, "accepted_incumbent"), bool_col(part, "optimizer_attributed")
            if (incumbent & (fallback | part.solver_status.ne("limit_reached"))).any():
                raise ValueError(f"site {site}/{arm}: invalid accepted incumbent")
            if (incumbent & ~attributed).any() or (fallback & attributed).any():
                raise ValueError(f"site {site}/{arm}: invalid optimizer attribution")
            if (fallback & part.fallback_reason.isna()).any() or (incumbent & part.nonoptimal_solves.lt(1)).any():
                raise ValueError(f"site {site}/{arm}: fallback reason/nonoptimal count invalid")
            expected_labels = np.where(attributed, "optimizer-attributed", "non-optimizer-attributed")
            if part.attribution.ne(expected_labels).any() or (fallback & part.command_mode.ne("self_consumption")).any():
                raise ValueError(f"site {site}/{arm}: attribution/fallback label mismatch")
            if arm == "self_consumption" and (attributed.any() or fallback.any()):
                raise ValueError(f"site {site}: baseline attribution invalid")
            gaps = pd.to_numeric(part.loc[incumbent, "mip_gap"], errors="coerce")
            if len(gaps) and ((~np.isfinite(gaps)).any() or (gaps < 0).any()):
                raise ValueError(f"site {site}/{arm}: incumbent gap missing/invalid")
            expected = {"fallback_steps": int(fallback.sum()), "accepted_incumbent_steps": int(incumbent.sum()),
                        "nonoptimal_solves": int(part.nonoptimal_solves.sum()), "optimizer_attributed_steps": int(attributed.sum()),
                        "non_optimizer_attributed_steps": int((~attributed).sum())}
            for key, value in expected.items():
                if report["arms"][arm].get(key) != value:
                    raise ValueError(f"site {site}/{arm}: summary {key} mismatch")
                total_counts[arm][key] += value
        raw = load_csv(data / f"{site}.csv")
        try:
            audit = audit_trace(trace.rename(columns={"arm": "policy"}), raw, soc_min=frozen["soc_min_percent"])
        except (AssertionError, ValueError, KeyError) as exc:
            raise ValueError(f"site {site}: independent physical audit failed") from exc
        if not report.get("physical_audit", {}).get("passed") or report["physical_audit"].get("rows") != 672:
            raise ValueError(f"site {site}: reported physical audit mismatch")
        site_summary = json.loads((output / f"site_{site}_summary.json").read_text(encoding="utf-8"))
        if site_summary != report:
            raise ValueError(f"site {site}: per-site and aggregate summary differ")
        physical_rows += audit["rows"]
    aggregate = summary.get("aggregate", {}).get("arms", {})
    if set(aggregate) != ARMS:
        raise ValueError("aggregate arms mismatch")
    for arm in ARMS:
        for key, value in total_counts[arm].items():
            if aggregate[arm].get(key) != value:
                raise ValueError(f"aggregate {arm}/{key} mismatch")
        for metric in ("operating_cost_eur", "settled_cost_eur", "throughput_kwh",
                       "internal_wear_eur", "settled_saving_eur"):
            expected = sum(float(site_report["arms"][arm][metric]) for site_report in sites)
            if not np.isclose(float(aggregate[arm].get(metric, np.nan)), expected, rtol=1e-9, atol=1e-8):
                raise ValueError(f"aggregate {arm}/{metric} mismatch")
    return {"valid": True, "mode": "single_site_smoke" if allow_smoke else "formal_f2", "sites": len(seen),
            "hours_per_site": 168, "arms": sorted(ARMS), "physical_audit_rows": physical_rows,
            "counts": total_counts, "manifest_sha256": actual_hash, "official_score_claim": False,
            "formal_result_authorized": not allow_smoke}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--expected-manifest-sha256", required=True)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--allow-smoke", action="store_true")
    args = parser.parse_args()
    result = validate(args.output, manifest_path=args.manifest, expected_manifest_sha256=args.expected_manifest_sha256,
                      data=args.data, allow_smoke=args.allow_smoke)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
