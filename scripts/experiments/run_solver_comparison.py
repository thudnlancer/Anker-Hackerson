"""Paired solver diagnostics using realized PV/load, not forecast performance."""
import json
from pathlib import Path
from time import perf_counter
import hashlib

import numpy as np
import pandas as pd

from src.energy_lab.backtest import load_csv
from src.energy_lab.rolling import optimize_window


def main():
    output = Path("outputs/solver_comparison_100site")
    output.mkdir(parents=True, exist_ok=True)
    rows = []
    paths = sorted(Path("forecast_data").rglob("*.csv"), key=lambda p: int(p.stem))
    with (output / "run.log").open("w", encoding="utf-8") as log:
        for path in paths:
            data = load_csv(path)
            split = int(len(data) * .8)
            cases = [("holdout_start", split, 10), ("holdout_end", len(data)-24, 50),
                     ("minimum_export_price", min(int(data.sell.idxmin()), len(data)-24), 100)]
            for label, index, soc in cases:
                w = data.iloc[index:index+24]
                record = {"site": path.stem, "case": label, "start": str(w.time.iloc[0]), "initial_soc": soc}
                plans = {}
                for solver in ("lp", "milp"):
                    started = perf_counter()
                    result = optimize_window(w.time, w.pv.to_numpy(), w.load.to_numpy(),
                                             w.buy.to_numpy(), w.sell.to_numpy(), soc,
                                             soc_min=10, degradation_cost_eur_kwh=.02,
                                             terminal_price=.1, solver=solver)
                    record[f"{solver}_seconds"] = perf_counter() - started
                    record[f"{solver}_status"] = result.status
                    assert result.status == "optimal", (path, label, solver, result.message)
                    p = result.plan
                    energy = p.soc_end.to_numpy() * .14
                    before = np.r_[soc*.14, energy[:-1]]
                    residual = energy - before - (p.charge_w*.95-p.discharge_w)/1000
                    grid_residual = (w.pv.to_numpy()+p.discharge_w+p.grid_import_w
                                     -w.load.to_numpy()-p.charge_w-p.grid_export_w)/1000
                    assert np.max(np.abs(residual)) < 1e-7 and np.max(np.abs(grid_residual)) < 1e-7
                    assert p.soc_end.between(10-1e-7, 100+1e-7).all()
                    assert p[["charge_w", "discharge_w"]].ge(-1e-7).all().all()
                    assert p[["charge_w", "discharge_w"]].le(5000+1e-7).all().all()
                    both = (p.charge_w.gt(1e-3) & p.discharge_w.gt(1e-3)).any()
                    both_grid = (p.grid_import_w.gt(1e-3) & p.grid_export_w.gt(1e-3)).any()
                    record[f"{solver}_battery_overlap"] = bool(both)
                    record[f"{solver}_grid_overlap"] = bool(both_grid)
                    if solver == "milp":
                        assert not both and not both_grid
                    record[f"{solver}_objective"] = result.objective
                    plans[solver] = p
                record["objective_gap_eur"] = record["milp_objective"] - record["lp_objective"]
                assert record["objective_gap_eur"] >= -1e-7
                record["first_action_gap_w"] = float(abs(
                    (plans["lp"].charge_w-plans["lp"].discharge_w).iloc[0]
                    -(plans["milp"].charge_w-plans["milp"].discharge_w).iloc[0]))
                rows.append(record)
            log.write(f"completed site {path.stem}\n")
            log.flush()
        trace = pd.DataFrame(rows)
        trace.to_csv(output / "window_comparison.csv", index=False)
        report = {"sites": len(paths), "windows": len(rows), "horizon": 24,
                  "scope": "Realized PV/load windows; solver mechanics only, not online forecast performance. Includes training-period minimum-export windows.",
                  "config": {"soc_min": 10, "soc_max": 100, "capacity_kwh": 14,
                             "max_power_w": 5000, "charge_efficiency": .95, "step_hours": 1,
                             "degradation_cost": .02, "terminal_price": .1, "milp_time_limit": 5},
                  "all_constraints_passed": True,
                  "maximum_objective_gap_eur": float(trace.objective_gap_eur.abs().max()),
                  "first_action_different_windows": int(trace.first_action_gap_w.gt(1e-3).sum()),
                  "milp_seconds_median": float(trace.milp_seconds.median()),
                  "milp_seconds_p95": float(trace.milp_seconds.quantile(.95)),
                  "milp_seconds_max": float(trace.milp_seconds.max()),
                  "lp_overlap_windows": int((trace.lp_battery_overlap | trace.lp_grid_overlap).sum()),
                  "source_hashes": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}}
        (output / "summary.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        log.write("completed all windows; validation passed\n")
    print(json.dumps({k:v for k,v in report.items() if k != "source_hashes"}, indent=2))


if __name__ == "__main__":
    main()
