"""Frozen 80/20 site split plus predeclared reserve/wear sensitivity checks."""
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
import argparse
import hashlib
import json
import sys

import joblib
import pandas as pd
from threadpoolctl import threadpool_limits

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
from energy_lab.backtest import load_csv
from energy_lab.forecast import load_weather_csv, fit_shared_model_full
from energy_lab.strategy_backtest import _init_site_worker, _evaluate_site_task
from energy_lab.validation import self_use_trace, audit_trace, metrics, compare


VARIANTS = {"frozen": (10., .02), "reserve20": (20., .02), "wear005": (10., .05)}


def run(data, output, workers):
    started = perf_counter()
    if (output / "summary.json").exists() or (output / "protocol.json").exists():
        raise FileExistsError("Choose a new directory; this validation runner does not resume")
    output.mkdir(parents=True, exist_ok=True)
    files = sorted(data.glob("*.csv"), key=lambda p: int(p.stem))
    assert len(files) == 100 and workers > 0
    test = [p for p in files if int(p.stem) % 5 == 0]
    train = [p for p in files if p not in test]
    assert len(train) == 80 and len(test) == 20 and not set(train) & set(test)
    protocol = {"declared_utc": datetime.now(timezone.utc).isoformat(),
                "training_site_ids": [p.stem for p in train], "evaluation_site_ids": [p.stem for p in test],
                "training_fraction": .8, "evaluation_fraction": .2, "initial_soc": 50,
                "variants": VARIANTS, "primary_variant": "frozen", "workers": workers, "worker_threads": 1,
                "horizon_hours": 24, "step_hours": 1, "terminal_price": .1, "weather": "persistence",
                "no_selection_rule": "Report all variants; do not select a new competition configuration from these results.",
                "retrospective_limit": "All 100 sites/time periods have appeared in earlier experiments. This is a new exclusion from model fitting, NOT untouched prospective data.",
                "history_semantics": "Observed history of held-out sites is allowed as lag input, never as training labels.",
                "data_sha256": {str(p.resolve()): hashlib.sha256(p.read_bytes()).hexdigest() for p in files},
                "code_sha256": {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in
                                [Path(__file__), *Path("src/energy_lab").glob("*.py")]}}
    (output / "protocol.json").write_text(json.dumps(protocol, indent=2), encoding="utf-8")
    frames = [load_weather_csv(p) for p in train]
    frames = [f.iloc[:int(len(f)*.8)].copy() for f in frames]
    first_eval = min(load_weather_csv(p).time.iloc[int(len(load_weather_csv(p))*.8)] for p in test)
    assert max(f.time.max() for f in frames) < first_eval
    with threadpool_limits(limits=1):
        pv = fit_shared_model_full(frames, "pv")
        load = fit_shared_model_full(frames, "load")
    model_hash = joblib.hash((pv, load))
    joblib.dump((pv, load), output / "validation_models.joblib")
    (output / "model.json").write_text(json.dumps({"model_hash": model_hash,
        "last_training_time": str(max(f.time.max() for f in frames)), "first_evaluation_time": str(first_eval)}), encoding="utf-8")
    jobs = [(label, p, (str(p), .8, 50., 14., 5000., .95, wear, reserve, .1, 24, "persistence", "milp"))
            for label, (reserve, wear) in VARIANTS.items() for p in test]
    completed = [None]*len(jobs)
    checkpoint = output / "site_results"
    checkpoint.mkdir()
    # Captures actual Python exceptions/exit status; no native stderr parsing.
    with ProcessPoolExecutor(max_workers=workers, initializer=_init_site_worker, initargs=(pv, load, 1)) as pool:
        futures = {pool.submit(_evaluate_site_task, task): i for i, (_, _, task) in enumerate(jobs)}
        for future in as_completed(futures):
            i = futures[future]
            rows, forecast = future.result()
            label, path, _ = jobs[i]
            raw = load_csv(path)
            base = self_use_trace(raw, path.stem, int(len(raw)*.8), soc_min=VARIANTS[label][0])
            trace = pd.concat([pd.DataFrame(rows), base], ignore_index=True)
            audit = audit_trace(trace, raw, soc_min=VARIANTS[label][0])
            assert trace[trace.policy.str.startswith("forecast")].lp_status.eq("optimal").all()
            payload = {"variant": label, "site": path.stem, "rows": trace.to_dict("records"),
                       "forecast": forecast, "audit": audit}
            (checkpoint / f"{label}_{path.stem}.json").write_text(json.dumps(payload, default=str), encoding="utf-8")
            completed[i] = payload
            message = {"completed": sum(x is not None for x in completed), "tasks": len(jobs),
                       "variant": label, "site": path.stem, "elapsed_seconds": perf_counter()-started}
            with (output / "progress.jsonl").open("a", encoding="utf-8") as log:
                log.write(json.dumps(message)+"\n")
            print(json.dumps(message), flush=True)
    results, full = {}, []
    for label, (reserve, wear) in VARIANTS.items():
        chosen = [x for x in completed if x["variant"] == label]
        trace = pd.DataFrame([r for x in chosen for r in x["rows"]])
        pm = metrics(trace, wear=wear)
        pm.to_csv(output / f"{label}_per_site.csv")
        results[label] = {"soc_min": reserve, "wear_coefficient": wear,
                          "comparison": compare(pm, "forecast_milp_persistence"),
                          "totals": pm.groupby("policy").sum().drop(columns="final_soc").to_dict("index")}
        # Shared .02 evaluation coefficient separates action changes from changes
        # in the optimizer's wear coefficient. Both views are explicitly labeled.
        results[label]["common_wear002_comparison"] = compare(metrics(trace, wear=.02), "forecast_milp_persistence")
        trace["variant"] = label
        full.append(trace)
    pd.concat(full, ignore_index=True).to_csv(output / "trace.csv", index=False)
    report = {"passed": True, "model_hash": model_hash, "training_sites": 80, "evaluation_sites": 20,
              "variants": results, "total_seconds": perf_counter()-started,
              "limits": protocol["retrospective_limit"], "parameters_frozen_for_competition": False}
    (output / "summary.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2), flush=True)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--data", type=Path, default=Path("forecast_data"))
    p.add_argument("--output", type=Path, default=Path("outputs/holdout80_20_frozen"))
    p.add_argument("--workers", type=int, default=8)
    a = p.parse_args()
    run(a.data, a.output, a.workers)
