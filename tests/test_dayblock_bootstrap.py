import json

import numpy as np
import pandas as pd
import pytest

from analyze_dayblock_bootstrap import (ALPHAS, analyze_lead, assign_day, bootstrap_ratio,
                                        is_daylight, load_rows, mae_ratio, mixed_mae, run, verdict)


def make_row(site, source, lead, pred, pers, truth):
    return {"site": site, "source_device_time": source, "target_device_time": source,
            "lead_hours": lead, "predicted_pv_w": pred, "persistence_pv_w": pers,
            "truth_pv_w": truth, "calibrated_error_w": abs(pred - truth),
            "calibrated_signed_error_w": pred - truth, "persistence_error_w": abs(pers - truth)}


def make_analysis(path, rows):
    path.write_text(json.dumps({"future_forecast_scores": rows}), encoding="utf-8")


def test_load_rows_dedups_same_origin(tmp_path):
    rows = [make_row("1", "2028-03-08T09:00:00+00:00", 1, 100., 90., 95.)] * 3
    a = tmp_path / "a.json"; b = tmp_path / "b.json"
    make_analysis(a, rows[:2]); make_analysis(b, rows[2:])
    frame = load_rows([a, b])
    assert len(frame) == 1
    assert frame["day"].iloc[0] == "2028-03-08"


def test_daylight_filter_requires_energy():
    frame = pd.DataFrame([make_row("1", "2028-03-08T00:00:00+00:00", 1, 0., 0., 0.),
                          make_row("1", "2028-03-08T01:00:00+00:00", 1, 0., 0., 80.),
                          make_row("1", "2028-03-08T02:00:00+00:00", 1, 120., 100., 60.)])
    mask = is_daylight(frame)
    assert mask.tolist() == [False, True, True]


def test_mae_ratio_and_mixed():
    frame = pd.DataFrame([make_row("1", "2028-03-08T09:00:00+00:00", 1, 110., 150., 100.),
                          make_row("1", "2028-03-08T10:00:00+00:00", 4, 90., 50., 100.)])
    assert mae_ratio(frame) == pytest.approx((10. + 10.) / (50. + 50.))
    assert mixed_mae(frame[frame.lead_hours == 1], 0.0) == pytest.approx(50.)
    assert mixed_mae(frame[frame.lead_hours == 1], 1.0) == pytest.approx(10.)


def test_bootstrap_ratio_reproducible_and_ordered():
    rng = np.random.default_rng(0)
    rows = []
    for day in range(20):
        for hour in (9, 10, 11, 12):
            truth = 500.
            rows.append(make_row("1", f"2028-03-{day+1:02d}T{hour}:00:00+00:00", 1,
                                 truth + rng.normal(0, 20), truth + rng.normal(0, 100), truth))
    frame = assign_day(pd.DataFrame(rows))
    lo1, hi1 = bootstrap_ratio(frame, statistic=mae_ratio, resamples=500, seed=7)
    lo2, hi2 = bootstrap_ratio(frame, statistic=mae_ratio, resamples=500, seed=7)
    assert (lo1, hi1) == (lo2, hi2)
    assert lo1 < mae_ratio(frame) < hi1
    assert hi1 < 1.0  # calibrated clearly better in this synthetic set


def test_verdict_boundaries():
    assert verdict(0.5, 0.9) == "calibrated"
    assert verdict(1.1, 1.6) == "persistence"
    assert verdict(0.8, 1.2) == "inconclusive_keep_persistence"
    assert verdict(np.nan, np.nan) == "insufficient_data"


def test_analyze_lead_marks_insufficient_evidence():
    frame = pd.DataFrame([make_row("1", f"2028-03-08T{h:02d}:00:00+00:00", 1, 100., 200., 150.)
                          for h in range(5)])
    report = analyze_lead(frame, resamples=100, seed=1, min_days=15, min_pairs=200)
    assert report["verdict"] == "insufficient_data"
    assert report["device_days"] == 1


def test_run_end_to_end_picks_persistence_when_calibrated_worse(tmp_path):
    rows = []
    for day in range(16):
        for site in ("1", "2", "3"):
            for hour in (8, 9, 10, 11, 12, 13, 14, 15, 16):
                truth = 400.
                rows.append(make_row(site, f"2028-03-{day+1:02d}T{hour:02d}:00:00+00:00", 1,
                                     truth + 120., truth + 20., truth))
    a = tmp_path / "a.json"; make_analysis(a, rows)
    report = run([a], resamples=500, min_days=15, min_pairs=200)
    lead1 = report["leads"]["1"]
    assert lead1["verdict"] == "persistence"
    assert lead1["mae_ratio_calibrated_over_persistence"] == pytest.approx(6.0)
    assert lead1["device_days"] == 16
    assert report["leads"]["2"]["verdict"] == "insufficient_data"
    assert report["all_hours_secondary"]["1"]["pairs"] == len(rows)
    assert lead1["shrinkage_diagnostic"]["best_alpha"] in ALPHAS
