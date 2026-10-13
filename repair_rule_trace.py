from __future__ import annotations

import argparse
import json
import hashlib
from pathlib import Path

import pandas as pd

from src.energy_lab.backtest import load_csv
from src.energy_lab.model import Battery, dispatch
from src.energy_lab.strategy_backtest import _policy_step, _summarize


def repair(input_dir: Path, output_dir: Path, data_dir: Path, soc_min: float) -> None:
    if input_dir.resolve() == output_dir.resolve():
        raise ValueError("Use a separate output directory to preserve the source batch")
    report = json.loads((input_dir / "strategy_comparison_summary.json").read_text(encoding="utf-8"))
    # This recovery is deliberately limited to the two completed 100-site runs.
    if report["sites"] != 100 or report["train_fraction"] != 0.8:
        raise ValueError("Recovery supports only the archived 100-site 80/20 batches")
    for policy in report["policies"]:
        for key, value in {"initial_soc_percent": 50.0, "capacity_kwh": 14.0,
                           "max_power_w": 5000.0, "soc_min_percent": soc_min}.items():
            if policy[key] != value:
                raise ValueError(f"Unexpected archived parameter: {key}")
    if report["terminal_price_eur_per_kwh"] != 0.1:
        raise ValueError("Unexpected terminal inventory valuation")
    trace = pd.read_csv(input_dir / "strategy_comparison_trace.csv")
    trace["time"] = pd.to_datetime(trace["time"])
    raw_by_site = {}
    for path in sorted(data_dir.rglob("*.csv")):
        raw = load_csv(path)
        raw_by_site[str(path.stem)] = raw

    for site, group in trace[trace.policy == "rule"].groupby("site", sort=False):
        raw = raw_by_site[str(site)]
        split = max(1, int(len(raw) * 0.8))
        if not group.time.reset_index(drop=True).equals(raw.time.iloc[split:].reset_index(drop=True)):
            raise ValueError(f"site {site}: source timeline mismatch")
        battery = Battery(14.0, 50.0, 5000.0, 0.95)
        repaired = []
        for index in range(split, len(raw)):
            current = raw.iloc[index]
            end = min(len(raw), index + 24)
            price_window = raw.iloc[index:end]
            future_import = price_window["buy"].to_numpy(dtype=float)[1:].tolist()
            flow, power, _ = dispatch(
                float(current.buy), float(current.sell), float(current.pv), float(current.load),
                battery.soc, future_import, 14.0, 5000.0,
            )
            repaired.append(_policy_step(
                battery, flow, power, current, float(current.buy), float(current.sell), soc_min,
            ))
        mask = (trace.site == site) & trace.policy.eq("rule")
        if len(repaired) != int(mask.sum()):
            raise ValueError(f"site {site}: repaired rows {len(repaired)} != trace rows {int(mask.sum())}")
        for column in repaired[0]:
            trace.loc[mask, column] = [row[column] for row in repaired]

    output_dir.mkdir(parents=True, exist_ok=True)
    trace.to_csv(output_dir / "strategy_comparison_trace.csv", index=False)
    report.update(_summarize(trace, 50.0, 14.0, 5000.0, soc_min, 0.1))
    report["repair_note"] = "Rule rows regenerated with battery-space clipping; LP rows copied unchanged from the validated source batch."
    report["repair_source"] = str(input_dir.resolve())
    report["source_trace_sha256"] = hashlib.sha256((input_dir / "strategy_comparison_trace.csv").read_bytes()).hexdigest()
    report["recovery_assumptions"] = {"horizon_hours": 24, "step_hours": 1.0, "charge_efficiency": 0.95}
    report["accounting_note"] = "Operating and inventory-adjusted costs exclude degradation; neither is official settlement."
    (output_dir / "strategy_comparison_summary.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--data", type=Path, default=Path("forecast_data"))
    parser.add_argument("--soc-min", type=float, default=10.0)
    args = parser.parse_args()
    repair(args.input, args.output, args.data, args.soc_min)
