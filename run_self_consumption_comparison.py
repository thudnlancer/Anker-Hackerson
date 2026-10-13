"""Add causal self-consumption to an already completed, unchanged MILP run."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
from energy_lab.backtest import load_csv
from energy_lab.validation import self_use_trace, audit_trace, metrics, compare


def run(source, data, output):
    if (output / "summary.json").exists():
        raise FileExistsError("Use a new output directory")
    output.mkdir(parents=True, exist_ok=True)
    report = json.loads((source / "strategy_comparison_summary.json").read_text(encoding="utf-8"))
    manifest = json.loads((source / "run_manifest.json").read_text(encoding="utf-8"))
    assert report["sites"] == 100 and report["solver"] == "milp"
    assert report["step_hours"] == 1 and report["horizon_hours"] == 24
    assert report["terminal_price_eur_per_kwh"] == .1 and report["degradation_cost_eur_per_kwh"] == .02
    assert report["charge_efficiency"] == .95 and report["train_fraction"] == .8
    assert all(p["initial_soc_percent"] == 50 and p["soc_min_percent"] == 10 and
               p["capacity_kwh"] == 14 and p["max_power_w"] == 5000 for p in report["policies"])
    archived = pd.read_csv(source / "strategy_comparison_trace.csv", dtype={"site": str},
                           parse_dates=["time"], float_precision="round_trip")
    baselines, audits = [], []
    for site in report["evaluated_site_ids"]:
        path = data / f"{site}.csv"
        assert hashlib.sha256(path.read_bytes()).hexdigest() == manifest["data_sha256"][str(path.resolve())]
        raw = load_csv(path)
        baseline = self_use_trace(raw, site, int(len(raw)*.8))
        prior = archived[archived.site.eq(site)]
        assert set(prior.time) == set(baseline.time) and len(prior) == 2*len(baseline)
        assert prior[prior.policy.ne("rule")].lp_status.eq("optimal").all()
        audits.append(audit_trace(pd.concat([prior, baseline], ignore_index=True), raw))
        baselines.append(baseline)
    baseline_trace = pd.concat(baselines, ignore_index=True)
    trace = pd.concat([archived, baseline_trace], ignore_index=True)
    per_site = metrics(trace)
    aggregate = per_site.groupby("policy").sum(numeric_only=True).drop(columns="final_soc")
    result = {"passed": True, "source": str(source),
              "source_trace_sha256": hashlib.sha256((source / "strategy_comparison_trace.csv").read_bytes()).hexdigest(),
              "source_model_hash": manifest["model_hash"],
              "comparison": compare(per_site, "forecast_milp_persistence"),
              "totals": aggregate.to_dict("index"), "audits": audits,
              "limits": "Reuses audited MILP trajectories; new baseline only. Self-use may export excess PV when full. No curtailment, no zero-export guarantee. Internal costs only."}
    baseline_trace.to_csv(output / "self_consumption_trace.csv", index=False)
    per_site.to_csv(output / "per_site_metrics.csv")
    (output / "summary.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps({"comparison": result["comparison"], "totals": result["totals"]}, indent=2))


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--source", type=Path, default=Path("outputs/milp_100site_parallel8"))
    p.add_argument("--data", type=Path, default=Path("forecast_data"))
    p.add_argument("--output", type=Path, default=Path("outputs/self_consumption_100site"))
    a = p.parse_args()
    run(a.source, a.data, a.output)
