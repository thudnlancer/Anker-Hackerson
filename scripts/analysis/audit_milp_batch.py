"""Audit a persisted MILP batch against its source data and summary."""

from pathlib import Path
import json
import argparse
import numpy as np
import pandas as pd

from src.energy_lab.backtest import load_csv


def audit(input_dir: Path, max_sites: int) -> dict:
    trace_path = input_dir / "strategy_comparison_trace.csv"
    summary_path = input_dir / "strategy_comparison_summary.json"
    trace = pd.read_csv(trace_path, dtype={"site": str}, parse_dates=["time"])
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert summary["solver"] == "milp"
    expected = max_sites * 442 * 2
    assert trace.shape[0] == expected and trace.site.nunique() == max_sites
    assert trace.groupby(["site", "policy"]).size().eq(442).all()
    assert not trace.duplicated(["site", "time", "policy"]).any()
    for _, group in trace.groupby(["site", "policy"]):
        assert group.time.diff().dropna().eq(pd.Timedelta(hours=1)).all()
        assert abs(group.soc_before.iloc[0] - 50) < 1e-8
        assert np.allclose(group.soc_before.iloc[1:], group.soc_after.iloc[:-1], rtol=0, atol=1e-8)
    assert set(trace.policy) == {"rule", "forecast_milp_persistence"}
    opt = trace.policy.eq("forecast_milp_persistence")
    assert trace.loc[opt, "lp_status"].eq("optimal").all()
    assert trace.loc[opt, "lp_fallback"].eq(False).all()
    assert trace.soc_after.between(10 - 1e-8, 100 + 1e-8).all()
    assert trace.power_w.between(0, 5000 + 1e-8).all()
    assert np.isfinite(trace[["cost_eur", "soc_before", "soc_after", "power_w",
                              "grid_import_kwh", "grid_export_kwh"]].to_numpy()).all()
    raw = pd.concat([
        load_csv(path).assign(site=path.stem)
        for path in sorted(Path("forecast_data").rglob("*.csv"), key=lambda p: int(p.stem))
        if path.stem in set(trace.site)
    ], ignore_index=True)
    joined = trace.merge(raw[["site", "time", "pv", "load", "buy", "sell"]],
                         on=["site", "time"], how="left", validate="many_to_one")
    assert joined[["pv", "load", "buy", "sell"]].notna().all().all()
    charge = np.where(joined.flow.eq("charge"), joined.power_w / 1000, 0)
    discharge = np.where(joined.flow.eq("discharge"), joined.power_w / 1000, 0)
    battery = (joined.soc_after - joined.soc_before) * .14 - (charge * .95 - discharge)
    grid = joined.pv / 1000 + discharge + joined.grid_import_kwh - joined.load / 1000 - charge - joined.grid_export_kwh
    cost = joined.cost_eur - joined.grid_import_kwh * joined.buy + joined.grid_export_kwh * joined.sell
    assert max(float(battery.abs().max()), float(grid.abs().max()), float(cost.abs().max())) < 1e-8
    assert not (joined.grid_import_kwh.gt(1e-8) & joined.grid_export_kwh.gt(1e-8)).any()
    for item in summary["policies"]:
        group = trace[trace.policy.eq(item["policy"])]
        assert item["decision_steps"] == len(group)
        assert np.isclose(item["total_cost_eur"], group.cost_eur.sum(), rtol=0, atol=1e-8)
        settled = group.cost_eur.sum() - group.groupby("site").soc_after.last().sum() * .14 * .1
        assert np.isclose(item["total_settled_cost_eur"], settled, rtol=0, atol=1e-8)
    result = {
        "passed": True,
        "sites": max_sites,
        "rows": len(trace),
        "milp_optimal_steps": int(opt.sum()),
        "milp_fallback_steps": int(trace.loc[opt, "lp_fallback"].sum()),
        "maximum_battery_residual_kwh": float(battery.abs().max()),
        "maximum_grid_residual_kwh": float(grid.abs().max()),
        "maximum_cost_residual_eur": float(cost.abs().max()),
        "minimum_soc_percent": float(trace.soc_after.min()),
        "maximum_power_w": float(trace.power_w.max()),
        "note": "Rule lp_status/lp_fallback are null because these fields apply only to the optimizer policy.",
    }
    (input_dir / "audit_report.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=Path("outputs/grid_10site_soc10_deg002_milp_persistence"))
    parser.add_argument("--sites", type=int, default=10)
    args = parser.parse_args()
    result = audit(args.input, args.sites)
    print(json.dumps(result, indent=2))
