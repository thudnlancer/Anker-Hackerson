"""Build the complete, unsigned F1 candidate parameter/evidence manifest."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def hashes(paths: list[Path]) -> dict[str, str]:
    return {path.relative_to(ROOT).as_posix(): sha256(path)
            for path in sorted(paths, key=lambda item: item.as_posix())}


def read_json(relative: str):
    return json.loads((ROOT / relative).read_text(encoding="utf-8"))


def arm_stats(relative: str) -> dict:
    summary = read_json(f"{relative}/summary.json")
    arms = summary["aggregate"]["arms"]
    return {
        "output": relative,
        "k_eur": summary["aggregate"]["k_eur"],
        "savings_percent": {arm: arms[arm]["saving_settled_percent_estimate"]
                            for arm in ("persistence", "irradiance", "hybrid")},
        "savings_eur": {arm: arms[arm]["settled_saving_eur"]
                        for arm in ("persistence", "irradiance", "hybrid")},
        "fallback_steps": {arm: arms[arm]["fallback_steps"]
                           for arm in ("persistence", "irradiance", "hybrid")},
        "nonoptimal_solves": {arm: arms[arm]["nonoptimal_solves"]
                              for arm in ("persistence", "irradiance", "hybrid")},
        "optimizer_attributed_steps": {arm: arms[arm]["optimizer_attributed_steps"]
                                       for arm in ("persistence", "irradiance", "hybrid")},
        "non_optimizer_attributed_steps": {arm: arms[arm]["non_optimizer_attributed_steps"]
                                           for arm in ("persistence", "irradiance", "hybrid")},
        "positive_sites": {arm: arms[arm]["positive_sites"]
                           for arm in ("persistence", "irradiance", "hybrid")},
        "physical_audit_passed_sites": sum(
            site["physical_audit"]["passed"] for site in summary["sites"]),
        "site_count": len(summary["sites"]),
        "official_score_claim": summary["official_score_claim"],
    }


def main() -> None:
    # Keep the selectable W1 manifest scoped to the F1 runner and its model
    # dependencies. Shared collector/calibration work in the same worktree is
    # intentionally outside this submission and must not alter this hash set.
    source_files = [ROOT / name for name in (
        "run_contest_strategy_replay.py", "prepare_f2_command.py", "validate_f2_output.py",
        "run_live.py", "build_f1_manifest.py",
        "src/energy_lab/backtest.py", "src/energy_lab/contest_replay.py",
        "src/energy_lab/execute.py", "src/energy_lab/interface_contract.py",
        "src/energy_lab/model.py", "src/energy_lab/rolling.py",
        "src/energy_lab/shadow.py", "src/energy_lab/validation.py",
    )]
    data_files = list((ROOT / "forecast_data").glob("*.csv"))
    d2 = read_json("outputs/irradiance_d2_expansion_20261013/final_bootstrap_all20.json")
    readiness = read_json("outputs/w1_readiness_actual_20261014/readiness.json")
    replay_dirs = {
        "a5_eval_start_mean": "outputs/f1_terminal_a5_v2_20261014",
        "current_visible_mean": "outputs/f1_terminal_current_v2_20261014",
        "zero": "outputs/f1_terminal_zero_v2_20261014",
    }
    replay = {mode: arm_stats(directory) for mode, directory in replay_dirs.items()}
    artifacts = [ROOT / "outputs/w1_readiness_actual_20261014/config_snapshot.json",
                 ROOT / "outputs/w1_readiness_actual_20261014/readiness.json",
                 ROOT / "outputs/w1_c4_verification_20261014_1/verification_report.json",
                 ROOT / "outputs/w1_a5_compare_20261014_1/a5_same_input_comparison.json",
                 ROOT / "outputs/irradiance_d2_expansion_20261013/final_bootstrap_all20.json",
                 ROOT / "F1_PARAMETER_FREEZE_CHECKLIST.md",
                 ROOT / "F1_TERMINAL_PV_EVIDENCE.md",
                 ROOT / "F1_ONLINE_PRICE_ANOMALY_EVIDENCE.md",
                 ROOT / "tests/test_run_live_chain.py",
                 ROOT / "tests/test_contest_replay.py",
                 ROOT / "tests/test_rolling_milp.py",
                 ROOT / "tests/test_prepare_f2_command.py",
                 ROOT / "tests/test_validate_f2_output.py",
                 *(ROOT / directory / filename
                   for directory in replay_dirs.values()
                   for filename in ("manifest.json", "summary.json"))]
    terminal = {
        "sites": 5,
        "hours_per_site": 168,
        "stage": 4,
        "hold_mode": "zero_flow",
        "wear_eur_per_kwh": 0.02,
        "solver_seconds": 2.0,
        "runs": replay,
        "conclusion": "Use current API-visible import-price mean as the causal optimizer terminal price; A5 evaluation-start mean reuses past prices in later ticks. Keep contest-window inventory settlement valuation distinct from optimizer terminal estimate.",
    }
    pv = {
        "d2_bootstrap": "outputs/irradiance_d2_expansion_20261013/final_bootstrap_all20.json",
        "lead_1h": d2["leads"]["1"],
        "lead_2h": d2["leads"]["2"],
        "lead_4h": d2["leads"]["4"],
        "strategy_replay": {
            "current_visible_hybrid": replay["current_visible_mean"]["savings_percent"]["hybrid"],
            "current_visible_hybrid_positive_sites": replay["current_visible_mean"]["positive_sites"]["hybrid"],
            "limitation": "Five practice sites and future observed shortwave proxy, not archived API forecasts or official scoring.",
        },
        "candidate_rule": "Current HA PV observation wins at the current point; persistence at +1 device hour; calibrated forecast irradiance at >=2 device hours using prior 168h and >=3 daylight samples; otherwise persistence for the full forecast.",
    }
    config_snapshot = read_json("outputs/w1_readiness_actual_20261014/config_snapshot.json")
    selected_inputs = read_json(f"{replay_dirs['current_visible_mean']}/manifest.json")["data_sha256"]
    manifest = {
        "manifest_version": "f1-final-proposal-20261015-2",
        "status": "final_proposal_pending_integration_signature",
        "official_score_claim": False,
        "f2_authorized": False,
        "scope": "Final F1 parameter proposal, unsigned and locked for integration-owner review; no formal 100-site F2 run.",
        "parameters": {
            "solver_seconds": 2.0,
            "mip_rel_gap": 1e-9,
            "capacity_kwh": 14.0,
            "max_power_w": 5000.0,
            "soc_min_percent": 0.0,
            "soc_max_percent": 100.0,
            "poll_seconds": 5.0,
            "charge_efficiency": 0.95,
            "discharge_efficiency": 1.0,
            "efficiency_basis": "SOP and current energy model convention: 5% charging-side loss, no second discharge-side loss; not measured hardware efficiency. Official contradictory parameters require a new manifest and C4/A5/F1 review.",
            "degradation_cost_eur_per_kwh": 0.02,
            "terminal_price": {
                "rule": "current_visible_mean",
                "definition": "Arithmetic mean of import prices in the API-visible horizon at the current decision tick; explicitly passed to optimizer.",
                "missing_price": "If any price required in the current optimizer horizon is missing, non-finite or not device-time aligned, skip optimization for that tick; explicitly switch to self_consumption, record non-optimizer-attributed fallback and reason missing_or_invalid_price. Do not fill zero, reuse stale prices or invoke optimizer terminal-price default.",
                "settlement_inventory_value": "Report separately using the contest evaluation-window settlement price; do not conflate with optimizer terminal estimate.",
            },
            "successful_plan_idle": "zero_flow",
            "failure_fallback_idle": "self_consumption",
            "failure_fallback_attribution": "non-optimizer-attributed",
            "time_limit_incumbent": "Accept only rolling.py independently verified feasible limit_reached incumbent; preserve solver_status=limit_reached, mip_gap and accepted_incumbent=true. Reject unverified or infeasible plans to self_consumption.",
            "online_price_anomaly_acceptance": {
                "evidence": "F1_ONLINE_PRICE_ANOMALY_EVIDENCE.md",
                "checks": ["missing", "non_finite", "device_time_misaligned"],
                "policy": "Skip optimizer for this tick; explicitly switch to self_consumption; audit reason and non-optimizer-attributed; never impute a price.",
                "scope_limit": "Mocked online runner/HA client chain, not remote contest-machine acceptance.",
            },
            "pv_forecast": {
                "status": "offline_f2_candidate_pending_integration_approval",
                "current_point": "Current HA PV measurement is authoritative.",
                "lead_1_device_hour": "persistence",
                "lead_ge_2_device_hours": "causal 168-hour irradiance calibration",
                "minimum_daylight_samples": 3,
                "calibration_failure": "persistence for the full forecast horizon",
                "offline_f2_scope": "Hybrid rule is a diagnostic F2 offline candidate only; it uses observed shortwave proxy rather than archived API forecasts.",
                "online_scope": "Use persistence-only until D3 validates real API forecast field, device-time alignment, quality, missing and delayed updates; no online hybrid activation from this manifest.",
            },
            "offline_time_step": "1 hour",
            "online_time_step": "Use official API/device granularity; not frozen by the offline hourly study.",
        },
        "readiness": {
            "config_snapshot": "outputs/w1_readiness_actual_20261014/config_snapshot.json",
            "config_snapshot_sha256": sha256(ROOT / "outputs/w1_readiness_actual_20261014/config_snapshot.json"),
            "readiness_report": "outputs/w1_readiness_actual_20261014/readiness.json",
            "control_config_ready": readiness["control_config"]["ready"],
            "overall_ready": readiness["ready"],
            "observations_per_station_device_hours": {
                station: info["unique_device_hours"] for station, info in readiness["stations"].items()},
            "note": "Control configuration check passes; full online readiness is false because stations have 30 device-hours versus 168 required. This is not a prerequisite for offline F2.",
        },
        "evidence": {
            "a5_same_input": "outputs/w1_a5_compare_20261014_1/a5_same_input_comparison.json",
            "a5_report": "A5_ATTRIBUTION_AND_BASELINE_REPORT.md",
            "c4_report": "outputs/w1_c4_verification_20261014_1/verification_report.json",
            "terminal_price_sensitivity": terminal,
            "pv_rule_evidence": pv,
            "limitations": [
                "Practice-site offline replay, not official settlement.",
                "Future observed shortwave radiation is only a proxy for issued API weather forecasts.",
                "Five-site strategy evidence is directional and not a formal 100-site result.",
                "PV hybrid is implemented in the offline diagnostic arm; online API forecast integration remains unverified.",
                "Existing five-site terminal/PV artifacts were produced before incumbent-alignment source changes; they remain frozen historical diagnostics and are not F2 results. Recompute after F1 signature before comparing new policy outcomes.",
            ],
        },
        "parameter_provenance": {
            "solver_seconds": "C4 72 frozen cases, 72/72 emitted and zero physics violations",
            "capacity_kwh": "current replay/model device capacity convention; explicitly passed for F2",
            "max_power_w": "current replay/model device power limit convention; explicitly passed for F2",
            "mip_rel_gap": "rolling.py MILP option; no relaxation from 1e-9",
            "soc_min_percent": "C3 and contest SOP 0-100% physical interval",
            "soc_max_percent": "contest SOP 0-100% physical interval",
            "poll_seconds": "actual online control-config snapshot, 5s > 2s solver budget",
            "charge_efficiency": "rolling.py and PhysicalParameters charging-side energy equation; model convention, not hardware measurement",
            "discharge_efficiency": "PhysicalParameters and replay model, no second discharge-side loss; model convention",
            "degradation_cost_eur_per_kwh": "A5 replay manifest wear=0.02 explicitly passed to solver and accounting; rolling.py generic default 0.002 is not used for F2",
            "terminal_price": "F1 five-site paired terminal sensitivity; current-visible mean is causal and agrees with rolling.py default only when optimizer horizon equals the visible price window",
            "successful_plan_idle": "A5 zero-flow same-input comparison and B1/B2 verified device semantics",
            "failure_fallback_idle": "A2 execution decision table and A5 non-optimizer attribution",
            "pv_forecast": "D2 lead-error bootstrap plus five-site offline proxy strategy replay; not online API forecast validation",
            "offline_time_step": "current practice dataset and replay implementation use hourly rows",
            "online_time_step": "official API/device granularity remains authoritative and is not inferred from offline data",
        },
        "reproducibility": {
            "replay_command_template": "prepare_f2_command.py --manifest outputs/f1_parameter_manifest_20261014.json --expected-sha256 <signed-manifest-sha256> --data forecast_data --output <new-dir> --workers 2; gate prints an explicit stage-4 command with all frozen parameters; it does not start replay",
            "source_sha256": hashes(source_files),
            "available_practice_dataset_count": len(data_files),
            "available_practice_dataset_sha256": hashes(data_files),
            "diagnostic_replay_input_count": len(selected_inputs),
            "diagnostic_replay_input_sha256": selected_inputs,
            "evidence_artifact_sha256": hashes(artifacts),
            "no_model_training": True,
        },
        "signature": {
            "a5_1_accepted": True,
            "a5_2_accepted": True,
            "c4_accepted": True,
            "acceptance_basis": "PROJECT_STATUS_AND_WORKSTREAMS.md 2026-10-14 integration-owner A5/C4 offline evidence signoff",
            "integration_owner": "",
            "signed_at": "",
            "approved_unsigned_manifest_sha256": "",
            "terminal_price_rule_approved": False,
            "pv_scope_approved": False,
            "f2_authorization": False,
        },
    }
    target = ROOT / "outputs/f1_parameter_manifest_20261014.json"
    target.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {target.relative_to(ROOT)} with {len(data_files)} inputs and {len(artifacts)} evidence artifacts")


if __name__ == "__main__":
    main()
