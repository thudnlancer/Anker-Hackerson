"""Independently recompute paired replay accounting from saved step traces."""
from pathlib import Path
import argparse
import hashlib
import json
import numpy as np
import pandas as pd


def audit(root):
    summary = json.loads((root / "summary.json").read_text())
    manifest = json.loads((root / "manifest.json").read_text())
    parameters = manifest["parameters"]
    counts = 0
    max_error = 0.
    nonoptimal_by_arm = {}
    for site in summary["sites"]:
        trace = pd.read_csv(root / f"site_{site['site']}_trace.csv")
        assert set(trace.arm) == set(site["arms"])
        assert len(trace) == len(site["arms"]) * parameters["eval_hours"]
        for arm, part in trace.groupby("arm"):
            nonoptimal = int(part.solver_status.ne("optimal").sum())
            assert nonoptimal == site["arms"][arm]["nonoptimal_steps"]
            nonoptimal_by_arm[arm] = nonoptimal_by_arm.get(arm, 0) + nonoptimal
            assert len(part) == parameters["eval_hours"]
            assert pd.to_datetime(part.time).diff().dropna().eq(pd.Timedelta(hours=1)).all()
            assert abs(float(part.soc_before.iloc[0]) - 50.) < 1e-8
            assert np.allclose(part.soc_before.iloc[1:], part.soc_after.iloc[:-1], atol=1e-8)
            charge = np.where(part.flow == "charge", part.power_w, 0.)
            discharge = np.where(part.flow == "discharge", part.power_w, 0.)
            energy = (part.soc_after - part.soc_before) * 14 / 100
            residual = np.abs(energy - (charge * .95 - discharge) / 1000)
            max_error = max(max_error, float(residual.max()))
            assert (residual < 1e-6).all()
            net = (part.load_w + charge - part.pv_w - discharge) / 1000
            imports, exports = np.maximum(net, 0), np.maximum(-net, 0)
            assert np.allclose(imports, part.grid_import_kwh, atol=1e-8)
            assert np.allclose(exports, part.grid_export_kwh, atol=1e-8)
            costs = imports * part.buy - exports * part.sell
            assert np.allclose(costs, part.cost_eur, atol=1e-8)
            settled = costs.sum() - part.soc_after.iloc[-1] / 100 * 14 * part.buy.mean()
            assert abs(settled - site["arms"][arm]["settled_cost_eur"]) < 1e-6
            if "comparisons_vs_persistence" in site and arm != "persistence":
                comparison = site["comparisons_vs_persistence"][arm]
                assert abs(comparison["settled_saving_eur"] - comparison["operating_saving_eur"]
                           - comparison["inventory_contribution_eur"]) < 1e-6
                assert abs(comparison["settled_saving_after_degradation_eur"] - comparison["settled_saving_eur"]
                           + comparison["extra_degradation_eur"]) < 1e-6
            assert part.soc_after.between(parameters["soc_min"]-1e-6, 100+1e-6).all()
            assert part.power_w.between(-1e-6, 5000+1e-6).all()
            assert part.representable.all()
        counts += len(trace)
    hashes_match = all(hashlib.sha256((Path(parameters["model_dir"]) / f"shared_{target}.joblib").read_bytes()).hexdigest() == digest
                       for target, digest in manifest["model_sha256"].items())
    assert hashes_match
    for arm, count in nonoptimal_by_arm.items():
        assert count == summary["aggregate"][arm]["nonoptimal_steps"]
    result = {"passed": True, "sites": len(summary["sites"]), "audited_steps": counts,
              "audit_scope": "physical_and_accounting_consistency_not_forecast_or_contest_certification",
              "nonoptimal_steps_by_arm": nonoptimal_by_arm,
              "all_steps_optimal": not any(nonoptimal_by_arm.values()),
              "max_energy_residual_kwh": max_error, "frozen_model_hashes_unchanged": hashes_match,
              "operating_cost_better_sites": sum(s["arms"]["auxiliary"]["cost_eur"] < s["arms"]["persistence"]["cost_eur"]
                                                  for s in summary["sites"])}
    (root / "audit.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("outputs", type=Path, nargs="+")
    args = parser.parse_args()
    for directory in args.outputs:
        print(directory, json.dumps(audit(directory)))
