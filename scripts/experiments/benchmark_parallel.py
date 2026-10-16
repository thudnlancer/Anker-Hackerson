"""Fixed-model, full-horizon process scaling benchmark. No remote access."""
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from time import perf_counter
import hashlib
import json
import sys
import argparse

import joblib
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from energy_lab.forecast import load_weather_csv, fit_shared_model_full
from energy_lab.strategy_backtest import _init_site_worker, _evaluate_site_task


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sites", type=int, default=4)
    parser.add_argument("--workers", default="1,2,4")
    parser.add_argument("--output", type=Path, default=Path("outputs/parallel_benchmark_fixed_model"))
    args = parser.parse_args()
    output = args.output
    output.mkdir(parents=True, exist_ok=True)
    paths = sorted(Path("forecast_data").rglob("*.csv"), key=lambda p: int(p.stem))
    training = []
    for path in paths:
        frame = load_weather_csv(path)
        training.append(frame.iloc[:int(len(frame)*.8)].reset_index(drop=True))
    start = perf_counter()
    models = (fit_shared_model_full(training, "pv"), fit_shared_model_full(training, "load"))
    model_hash = joblib.hash(models)
    report = {"model_hash": model_hash, "training_sites": len(paths), "evaluated_sites": args.sites,
              "training_seconds": perf_counter()-start, "runs": [],
              "note": "Same fitted models, full 442-hour holdout, 24h horizon; timing includes process startup, excludes shared training."}
    tasks = [(str(p), .8, 50., 14., 5000., .95, .02, 10., .1, 24, "persistence", "milp") for p in paths[:args.sites]]
    baseline = None
    for workers in map(int, args.workers.split(",")):
        start = perf_counter()
        if workers == 1:
            _init_site_worker(*models, 1)
            results = [_evaluate_site_task(t) for t in tasks]
        else:
            with ProcessPoolExecutor(max_workers=workers, initializer=_init_site_worker, initargs=(*models, 1)) as pool:
                results = list(pool.map(_evaluate_site_task, tasks))
        elapsed = perf_counter()-start
        trace = pd.DataFrame([row for rows, _ in results for row in rows])
        path = output / f"workers_{workers}_trace.csv"
        trace.to_csv(path, index=False)
        metrics = [r[1] for r in results]
        if baseline is None:
            baseline = (trace, metrics)
            # Independent check against the completed all-site-trained default-thread run.
            old = pd.read_csv("outputs/milp_parity_serial2/strategy_comparison_trace.csv", dtype={"site": str}, parse_dates=["time"], float_precision="round_trip")
            pd.testing.assert_frame_equal(trace[trace.site.isin(["1", "2"])].reset_index(drop=True), old,
                                          check_exact=True)
        else:
            pd.testing.assert_frame_equal(trace, baseline[0], check_exact=True)
            assert metrics == baseline[1]
        assert trace.loc[trace.policy.ne("rule"), "lp_status"].eq("optimal").all()
        run = {"workers": workers, "worker_threads": 1, "seconds": elapsed,
               "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "exact_parity": True,
               "fallback_steps": int(trace.lp_fallback.eq(True).sum())}
        report["runs"].append(run)
        (output / "summary.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        with (output / "progress.jsonl").open("a", encoding="utf-8") as log:
            log.write(json.dumps(run) + "\n")
        print(json.dumps(run), flush=True)


if __name__ == "__main__":
    main()
