"""Build a same-input A5 attribution comparison from two offline replays."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def load_run(path: Path) -> tuple[dict, dict]:
    summary = json.loads((path / "summary.json").read_text(encoding="utf-8"))
    manifest = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
    return summary, manifest


def comparable_manifest(left: dict, right: dict) -> bool:
    def normalized(value):
        value = json.loads(json.dumps(value))
        value.get("parameters", {}).pop("output", None)
        value.get("parameters", {}).pop("hold_mode", None)
        return value
    return normalized(left) == normalized(right)


def build(old_dir: Path, new_dir: Path) -> dict:
    old, old_manifest = load_run(old_dir)
    new, new_manifest = load_run(new_dir)
    if not comparable_manifest(old_manifest, new_manifest):
        raise ValueError("replay manifests differ beyond hold_mode/output")
    arms = {}
    for arm in ("persistence", "irradiance"):
        old_arm = old["aggregate"]["arms"][arm]
        new_arm = new["aggregate"]["arms"][arm]
        arms[arm] = {
            "old_self_consumption_hold": {
                "settled_saving_eur": old_arm["settled_saving_eur"],
                "saving_settled_percent_estimate": old_arm["saving_settled_percent_estimate"],
                "fallback_steps": old_arm["fallback_steps"],
                "optimizer_attributed_steps": old_arm.get("optimizer_attributed_steps"),
                "non_optimizer_attributed_steps": old_arm.get("non_optimizer_attributed_steps"),
            },
            "new_zero_flow_hold": {
                "settled_saving_eur": new_arm["settled_saving_eur"],
                "saving_settled_percent_estimate": new_arm["saving_settled_percent_estimate"],
                "fallback_steps": new_arm["fallback_steps"],
                "optimizer_attributed_steps": new_arm.get("optimizer_attributed_steps"),
                "non_optimizer_attributed_steps": new_arm.get("non_optimizer_attributed_steps"),
            },
            "delta_zero_flow_minus_old_eur": new_arm["settled_saving_eur"] - old_arm["settled_saving_eur"],
            "delta_zero_flow_minus_old_percent_points": (
                new_arm["saving_settled_percent_estimate"] - old_arm["saving_settled_percent_estimate"]),
        }
    physical = {
        "old_self_consumption_hold": {
            "passed_sites": sum(site["physical_audit"]["passed"] for site in old["sites"]),
            "total_sites": len(old["sites"]),
            "audited_rows": sum(site["physical_audit"]["rows"] for site in old["sites"]),
            "max_energy_residual_kwh": max(site["physical_audit"]["max_energy_residual_kwh"] for site in old["sites"]),
            "max_grid_residual_kwh": max(site["physical_audit"]["max_grid_residual_kwh"] for site in old["sites"]),
            "max_cost_residual_eur": max(site["physical_audit"]["max_cost_residual_eur"] for site in old["sites"]),
        },
        "new_zero_flow_hold": {
            "passed_sites": sum(site["physical_audit"]["passed"] for site in new["sites"]),
            "total_sites": len(new["sites"]),
            "audited_rows": sum(site["physical_audit"]["rows"] for site in new["sites"]),
            "max_energy_residual_kwh": max(site["physical_audit"]["max_energy_residual_kwh"] for site in new["sites"]),
            "max_grid_residual_kwh": max(site["physical_audit"]["max_grid_residual_kwh"] for site in new["sites"]),
            "max_cost_residual_eur": max(site["physical_audit"]["max_cost_residual_eur"] for site in new["sites"]),
        },
    }
    return {
        "comparison": "A5_same_input_old_self_consumption_vs_new_zero_flow",
        "official_score_claim": False,
        "old_run": str(old_dir), "new_run": str(new_dir),
        "input_manifest_equivalent": True,
        "acceptance": {
            "attribution_report": {
                "passed": all("optimizer_attributed_steps" in new["aggregate"]["arms"][arm]
                               and "non_optimizer_attributed_steps" in new["aggregate"]["arms"][arm]
                               for arm in ("persistence", "irradiance")),
                "fallback_steps_reported": True,
            },
            "same_input_baseline_comparison": {
                "passed": old_manifest["data_sha256"] == new_manifest["data_sha256"]
                          and physical["old_self_consumption_hold"]["passed_sites"] == physical["old_self_consumption_hold"]["total_sites"]
                          and physical["new_zero_flow_hold"]["passed_sites"] == physical["new_zero_flow_hold"]["total_sites"],
                "includes_savings_fallback_attribution_and_physics": True,
            },
        },
        "sites": len(old["sites"]), "hours_per_site": old["sites"][0]["hours"],
        "arms": arms, "physical_audit": physical,
        "limits": [
            "Practice-site offline replay only; not official simulator settlement.",
            "Both runs use the same forecast_data hashes, 168-hour windows, 50% initial SOC, wear and 2s solver budget.",
            "Old self-consumption hold is non-optimizer-attributed; new zero-flow hold is optimizer-attributed only for successful plans.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--old", type=Path, required=True)
    parser.add_argument("--new", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = build(args.old, args.new)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
