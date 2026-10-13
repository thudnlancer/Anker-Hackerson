"""Independent final audit of frozen holdout protocol and complete artifacts."""
from pathlib import Path
import hashlib
import json
import sys

import joblib
import numpy as np
import pandas as pd
from threadpoolctl import threadpool_limits

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
from energy_lab.backtest import load_csv
from energy_lab.validation import audit_trace, metrics, compare
from energy_lab.forecast import load_weather_csv, fit_shared_model_full
from energy_lab.strategy_backtest import _weather_horizon


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def main():
    folder = Path("outputs/holdout80_20_frozen")
    protocol, report = read(folder / "protocol.json"), read(folder / "summary.json")
    train, test = set(protocol["training_site_ids"]), set(protocol["evaluation_site_ids"])
    assert len(train) == 80 and len(test) == 20 and not train & test
    for key in ("data_sha256", "code_sha256"):
        for name, digest in protocol[key].items():
            assert hashlib.sha256(Path(name).read_bytes()).hexdigest() == digest, name
    model_info = read(folder / "model.json")
    loaded_models = joblib.load(folder / "validation_models.joblib")
    assert isinstance(loaded_models, tuple) and len(loaded_models) == 2
    assert {model.target for model in loaded_models} == {"pv", "load"}
    # joblib object hashes are recorded for the in-process model; serialization
    # can normalize estimator internals, so the saved artifact is checked by
    # structure and the manifest hash is checked against the run report.
    assert report["model_hash"] == model_info["model_hash"]
    training_frames = [load_weather_csv(Path("forecast_data") / f"{site}.csv")
                       for site in protocol["training_site_ids"]]
    training_frames = [f.iloc[:int(len(f)*.8)].copy() for f in training_frames]
    with threadpool_limits(limits=1):
        refitted = tuple(fit_shared_model_full(training_frames, target) for target in ("pv", "load"))
        assert joblib.hash(refitted) == report["model_hash"]
        for site in protocol["evaluation_site_ids"]:
            frame = load_weather_csv(Path("forecast_data") / f"{site}.csv")
            index = int(len(frame)*.8)
            history = frame.iloc[index-167:index+1]
            future = _weather_horizon(frame, index, 24, "persistence")
            for refit, saved in zip(refitted, loaded_models):
                np.testing.assert_array_equal(refit.predict(history, future).prediction,
                                              saved.predict(history, future).prediction)
    assert pd.Timestamp(model_info["last_training_time"]) < pd.Timestamp(model_info["first_evaluation_time"])
    exit_status = read(folder / "process_status.jsonl")
    assert exit_status["returncode"] == 0
    trace = pd.read_csv(folder / "trace.csv", dtype={"site": str}, parse_dates=["time"], float_precision="round_trip")
    assert len(trace) == 20*3*3*442
    assert not trace.duplicated(["variant", "site", "policy", "time"]).any()
    assert trace.groupby(["variant", "site", "policy"]).size().eq(442).all()
    assert set(trace.site) == test and set(trace.variant) == set(protocol["variants"])
    optimized = trace[trace.policy.str.startswith("forecast")]
    assert len(optimized) == 26520 and optimized.lp_status.eq("optimal").all()
    assert not optimized.lp_fallback.eq(True).any()
    checkpoints = [read(p) for p in sorted((folder / "site_results").glob("*.json"))]
    assert len(checkpoints) == 60
    predictions = {}
    for checkpoint in checkpoints:
        key = checkpoint["site"]
        if key in predictions:
            assert predictions[key] == checkpoint["forecast"]
        predictions[key] = checkpoint["forecast"]
    maximum_residual = 0.
    for label, (reserve, wear) in protocol["variants"].items():
        selected = trace[trace.variant.eq(label)].copy()
        for site, group in selected.groupby("site"):
            result = audit_trace(group, load_csv(Path("forecast_data") / f"{site}.csv"), soc_min=reserve)
            maximum_residual = max(maximum_residual, result["max_energy_residual_kwh"], result["max_grid_residual_kwh"])
        pm = metrics(selected, wear=wear)
        expected = compare(pm, "forecast_milp_persistence")
        for key, value in expected.items():
            actual = report["variants"][label]["comparison"][key]
            if isinstance(value, (float, int)):
                assert np.isclose(value, actual, atol=1e-8, rtol=0), (label, key)
            else:
                assert value == actual
    result = {"passed": True, "rows": len(trace), "optimization_decisions": len(optimized),
              "training_evaluation_sites_disjoint": True, "chronology_verified": True,
              "model_data_code_hashes_verified": True, "forecast_metrics_identical_across_variants": True,
              "model_refit_hash_matches": True, "saved_model_predictions_match_refit_first_horizon_20_sites": True,
              "saved_model_file_sha256": hashlib.sha256((folder / "validation_models.joblib").read_bytes()).hexdigest(),
              "fallback_steps": 0, "maximum_energy_residual_kwh": maximum_residual,
              "trace_sha256": hashlib.sha256((folder / "trace.csv").read_bytes()).hexdigest(),
              "limits": "Retrospective site exclusion, not a prospective blind test. Internal simulation, not executable HA results."}
    (folder / "audit_report.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
