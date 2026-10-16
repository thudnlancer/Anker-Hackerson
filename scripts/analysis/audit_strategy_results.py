"""Audit the archived 100-site pair and publish per-site comparisons locally.

Run from the repository root; archived input and output paths are root-relative.
"""
from pathlib import Path
import hashlib
import json

import numpy as np
import pandas as pd

from src.energy_lab.backtest import load_csv


def audit() -> None:
    raw_parts = []
    for path in Path("forecast_data").rglob("*.csv"):
        raw_parts.append(load_csv(path).assign(site=path.stem))
    raw = pd.concat(raw_parts, ignore_index=True)
    results = {}
    rules = []
    for mode in ("persistence", "oracle"):
        source = Path("outputs") / f"grid_100site_soc10_deg002_{mode}"
        output = source.with_name(source.name + "_corrected")
        trace_path = output / "strategy_comparison_trace.csv"
        trace = pd.read_csv(trace_path, dtype={"site": str}, parse_dates=["time"])
        original = pd.read_csv(source / trace_path.name, dtype={"site": str}, parse_dates=["time"])
        summary = json.loads((output / "strategy_comparison_summary.json").read_text(encoding="utf-8"))
        assert len(trace) == 88400 and trace.site.nunique() == 100
        assert set(trace.policy) == {"rule", f"forecast_lp_{mode}"}
        assert set(trace.weather_mode) == {mode}
        assert not trace.duplicated(["site", "time", "policy"]).any()
        assert trace.groupby(["site", "policy"]).size().eq(442).all()
        assert trace.flow.isin(["charge", "discharge", "idle", "hold"]).all()
        numeric = ["power_w", "soc_before", "soc_after", "grid_import_kwh",
                   "grid_export_kwh", "cost_eur", "throughput_kwh", "negative_export_loss_eur"]
        assert np.isfinite(trace[numeric]).all().all()
        lp = trace.policy.ne("rule")
        assert trace.loc[lp, "lp_status"].eq("optimal").all()
        assert trace.loc[lp, "lp_fallback"].astype(str).str.lower().eq("false").all()
        pd.testing.assert_frame_equal(trace.loc[lp].reset_index(drop=True),
                                      original.loc[original.policy.ne("rule")].reset_index(drop=True),
                                      check_exact=False, rtol=1e-12, atol=1e-10)
        joined = trace.merge(raw[["site", "time", "pv", "load", "buy", "sell"]],
                             on=["site", "time"], how="left", validate="many_to_one")
        assert joined[["pv", "load", "buy", "sell"]].notna().all().all()
        charge = np.where(joined.flow.eq("charge"), joined.power_w / 1000, 0)
        discharge = np.where(joined.flow.eq("discharge"), joined.power_w / 1000, 0)
        residuals = pd.DataFrame({
            "battery_kwh": (joined.soc_after - joined.soc_before) * .14 - (charge * .95 - discharge),
            "grid_kwh": joined.pv / 1000 + discharge + joined.grid_import_kwh
                        - joined.load / 1000 - charge - joined.grid_export_kwh,
            "cost_eur": joined.cost_eur - (joined.grid_import_kwh * joined.buy
                                            - joined.grid_export_kwh * joined.sell),
            "throughput_kwh": joined.throughput_kwh - charge - discharge,
        })
        assert residuals.abs().max().max() < 1e-8
        assert joined.soc_after.between(10 - 1e-8, 100 + 1e-8).all()
        assert joined.power_w.between(0, 5000 + 1e-8).all()
        assert joined.grid_import_kwh.ge(-1e-8).all() and joined.grid_export_kwh.ge(-1e-8).all()
        assert not (joined.grid_import_kwh.gt(1e-8) & joined.grid_export_kwh.gt(1e-8)).any()
        assert joined.loc[joined.flow.isin(["idle", "hold"]), "power_w"].abs().le(1e-8).all()
        for (_, _), group in trace.groupby(["site", "policy"]):
            assert group.time.is_monotonic_increasing
            assert group.time.diff().dropna().eq(pd.Timedelta(hours=1)).all()
            assert abs(group.soc_before.iloc[0] - 50) < 1e-8
            assert np.allclose(group.soc_before.iloc[1:], group.soc_after.iloc[:-1], atol=1e-8)
        per_site = trace.groupby(["site", "policy"]).agg(
            operating_cost_eur=("cost_eur", "sum"), final_soc=("soc_after", "last"),
            throughput_kwh=("throughput_kwh", "sum"))
        per_site["settled_cost_eur"] = per_site.operating_cost_eur - per_site.final_soc * .14 * .1
        per_site["cost_including_wear_eur"] = per_site.settled_cost_eur + per_site.throughput_kwh * .02
        for item in summary["policies"]:
            group = per_site.xs(item["policy"], level="policy")
            for key, column in [("total_cost_eur", "operating_cost_eur"),
                                ("total_settled_cost_eur", "settled_cost_eur"),
                                ("total_battery_throughput_kwh", "throughput_kwh")]:
                assert np.isclose(item[key], group[column].sum(), rtol=1e-12, atol=1e-8)
        lp_site = per_site.xs(f"forecast_lp_{mode}", level="policy")
        rule_site = per_site.xs("rule", level="policy")
        savings = rule_site - lp_site
        distributions = {}
        for metric in ("operating_cost_eur", "settled_cost_eur", "cost_including_wear_eur"):
            values = savings[metric]
            distributions[metric] = {"total": float(values.sum()), "median": float(values.median()),
                                      "minimum": float(values.min()), "maximum": float(values.max()),
                                      "positive_sites": int(values.gt(1e-8).sum())}
        per_site.to_csv(output / "per_site_metrics.csv")
        savings.to_csv(output / "per_site_savings.csv")
        result = {"passed": True, "rows": len(trace), "lp_rows_unchanged_within_tolerance": True,
                  "trace_sha256": hashlib.sha256(trace_path.read_bytes()).hexdigest(),
                  "maximum_absolute_residual": residuals.abs().max().to_dict(),
                  "savings_vs_rule": distributions,
                  "scope": "Executed hourly trajectories only; archived LP horizons are not available.",
                  "log_note": "Original runs have no saved console log; audit uses source CSV, trace and summary."}
        (output / "audit_report.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        rules.append(trace.loc[~lp].drop(columns="weather_mode").reset_index(drop=True))
        results[mode] = result
    pd.testing.assert_frame_equal(*rules)
    print(json.dumps({"batches": results, "rule_traces_match": True,
                      "price_audit": {"minimum_buy": float(raw.buy.min()),
                                      "negative_buy_rows": int(raw.buy.lt(0).sum()),
                                      "negative_sell_rows": int(raw.sell.lt(0).sum())}}, indent=2))


if __name__ == "__main__":
    audit()
