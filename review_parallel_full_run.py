"""Audit full parallel MILP, compare fixed-model benchmark and archived LP."""
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from audit_milp_batch import audit


def main():
    output = Path("outputs/milp_100site_parallel8")
    audit(output, 100)
    trace_path = output / "strategy_comparison_trace.csv"
    trace = pd.read_csv(trace_path, dtype={"site": str}, float_precision="round_trip", parse_dates=["time"])
    report = json.loads((output / "strategy_comparison_summary.json").read_text(encoding="utf-8"))
    manifest = json.loads((output / "run_manifest.json").read_text(encoding="utf-8"))
    benchmark_dir = Path("outputs/parallel_benchmark_8site")
    benchmark_report = json.loads((benchmark_dir / "summary.json").read_text(encoding="utf-8"))
    assert manifest["model_hash"] == benchmark_report["model_hash"]
    benchmark = pd.read_csv(benchmark_dir / "workers_4_trace.csv", dtype={"site": str},
                            float_precision="round_trip", parse_dates=["time"])
    pd.testing.assert_frame_equal(trace[trace.site.astype(int).le(8)].reset_index(drop=True), benchmark, check_exact=True)
    assert len(list((output / "site_results").glob("*.json"))) == 100
    lp_dir = Path("outputs/lp_100site_same_model_parallel8")
    lp_report = json.loads((lp_dir / "strategy_comparison_summary.json").read_text(encoding="utf-8"))
    lp_manifest = json.loads((lp_dir / "run_manifest.json").read_text(encoding="utf-8"))
    assert lp_manifest["model_hash"] == manifest["model_hash"]
    assert lp_manifest["data_sha256"] == manifest["data_sha256"]
    assert report["forecast_metrics"] == lp_report["forecast_metrics"]
    lp = pd.read_csv(lp_dir / "strategy_comparison_trace.csv", dtype={"site": str}, parse_dates=["time"], float_precision="round_trip")
    assert len(lp) == len(trace) and not lp.duplicated(["site", "time", "policy"]).any()
    assert lp.groupby(["site", "policy"]).size().eq(442).all()
    assert lp.loc[lp.policy.ne("rule"), "lp_status"].eq("optimal").all()
    archived = pd.read_csv("outputs/grid_100site_soc10_deg002_persistence_corrected/strategy_comparison_trace.csv",
                           dtype={"site": str}, parse_dates=["time"], float_precision="round_trip")
    pd.testing.assert_frame_equal(lp, archived, check_exact=False, rtol=1e-12, atol=1e-9)
    pd.testing.assert_frame_equal(trace[trace.policy.eq("rule")].reset_index(drop=True),
                                  lp[lp.policy.eq("rule")].reset_index(drop=True), check_exact=False, rtol=1e-12, atol=1e-9)
    joined = lp[lp.policy.ne("rule")].merge(trace[trace.policy.ne("rule")], on=["site", "time"],
                                           suffixes=("_lp", "_milp"), validate="one_to_one")
    def signed(suffix):
        return joined[f"power_w_{suffix}"] * joined[f"flow_{suffix}"].map({"charge": 1, "discharge": -1, "hold": 0, "idle": 0})
    per_site = trace.groupby(["site", "policy"]).agg(cost_eur=("cost_eur", "sum"),
              final_soc=("soc_after", "last"), throughput_kwh=("throughput_kwh", "sum"))
    per_site["settled_cost_eur"] = per_site.cost_eur - per_site.final_soc * .14 * .1
    per_site["cost_with_wear_eur"] = per_site.settled_cost_eur + per_site.throughput_kwh * .02
    rule = per_site.xs("rule", level="policy")
    optimized = per_site.xs("forecast_milp_persistence", level="policy")
    savings = rule - optimized
    per_site.to_csv(output / "per_site_metrics.csv")
    savings.to_csv(output / "per_site_savings.csv")
    result = {
        "passed": True,
        "model_matches_parallel_benchmark": True,
        "first_8_sites_exact_trace_match": True,
        "lp_milp_model_and_data_hashes_match": True,
        "forecast_metrics_match_same_model_lp": True,
        "new_lp_matches_archived_corrected_lp_within_1e9_tolerance": True,
        "rule_trace_matches_corrected_lp_baseline": True,
        "trace_sha256": hashlib.sha256(trace_path.read_bytes()).hexdigest(),
        "signed_action_differences_vs_lp_above_0p001_w": int((signed("lp")-signed("milp")).abs().gt(.001).sum()),
        "flow_differences_vs_lp": int(joined.flow_lp.ne(joined.flow_milp).sum()),
        "total_operating_cost_difference_milp_minus_lp_eur": float(joined.cost_eur_milp.sum()-joined.cost_eur_lp.sum()),
        "cost_savings_with_wear_vs_rule_eur": float(savings.cost_with_wear_eur.sum()),
        "positive_sites_including_wear": int(savings.cost_with_wear_eur.gt(1e-8).sum()),
        "minimum_site_saving_including_wear_eur": float(savings.cost_with_wear_eur.min()),
        "median_site_saving_including_wear_eur": float(savings.cost_with_wear_eur.median()),
        "timing": report["timing"],
        "lp_timing": lp_report["timing"],
        "limits": "Same model and input hashes verified. Different optimal plans and forecast errors can yield different rolling realized costs; does not establish MILP economic superiority. Internal costs, not official scores."
    }
    (output / "paired_review.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
