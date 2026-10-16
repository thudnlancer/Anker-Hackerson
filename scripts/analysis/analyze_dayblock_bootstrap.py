"""Day-block bootstrap decision analysis for the D2 irradiance-calibration expansion.

Pools frozen-forecast score rows from per-batch analysis.json files and answers,
per forecast lead (1/2/4 device hours), whether irradiance-calibrated PV beats
current-PV persistence. Pre-registered rules:

- Primary metric: paired MAE ratio (calibrated / persistence) on daylight targets
  only; daylight target := truth_pv_w > 50 W OR predicted_pv_w > 50 W (target-hour
  irradiance is not stored in score rows, this proxy is fixed before analysis).
  All-hours metrics are reported separately and never drive the verdict.
- Confidence: device-day block bootstrap (resample source device dates with
  replacement, all sites pooled), B resamples, percentile 95% CI on the MAE ratio.
- Verdict per lead: CI upper < 1 -> "calibrated"; CI lower > 1 -> "persistence";
  otherwise -> "inconclusive_keep_persistence" (a valid decision outcome).
- Q3 shrinkage: best alpha on the full daylight sample per lead, then bootstrap CI
  of MAE(alpha)/MAE(persistence); reported with a selection-bias caveat only.
- Minimum evidence: >= min_days distinct device days and >= min_pairs daylight
  pairs per lead, else "insufficient_data".

Read-only: consumes analysis.json files, never touches live endpoints.
"""
import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

LEADS = (1, 2, 4)
ALPHAS = (0.0, 0.25, 0.5, 0.75, 1.0)
DAYLIGHT_TRUTH_W = 50.0
DAYLIGHT_PREDICTED_W = 50.0


def assign_day(frame):
    if "day" not in frame.columns:
        frame = frame.copy()
        times = pd.to_datetime(frame["source_device_time"], utc=True)
        frame["day"] = times.dt.date.astype(str)
    return frame


def load_rows(analysis_paths):
    """Load frozen-forecast rows; dedup same station/origin/lead defensively."""
    rows = []
    for path in analysis_paths:
        report = json.loads(Path(path).read_text(encoding="utf-8"))
        rows.extend(report.get("future_forecast_scores") or [])
    frame = pd.DataFrame(rows)
    if frame.empty:
        return frame
    frame = frame.drop_duplicates(["site", "source_device_time", "lead_hours"])
    return assign_day(frame)


def is_daylight(frame):
    return (frame["truth_pv_w"] > DAYLIGHT_TRUTH_W) | (frame["predicted_pv_w"] > DAYLIGHT_PREDICTED_W)


def mae_ratio(frame):
    if frame.empty or frame["persistence_error_w"].mean() == 0:
        return np.nan
    return float(frame["calibrated_error_w"].mean() / frame["persistence_error_w"].mean())


def mixed_mae(frame, alpha):
    err = (alpha * frame["predicted_pv_w"] + (1 - alpha) * frame["persistence_pv_w"] - frame["truth_pv_w"]).abs()
    return float(err.mean())


def bootstrap_ratio(frame, *, statistic, resamples, seed):
    """Resample device days with replacement; return percentile 95% CI of statistic(frame)."""
    days = frame["day"].unique()
    rng = np.random.default_rng(seed)
    stats = []
    by_day = {day: frame[frame["day"] == day] for day in days}
    for _ in range(resamples):
        picked = rng.choice(days, size=len(days), replace=True)
        sample = pd.concat([by_day[day] for day in picked])
        value = statistic(sample)
        if np.isfinite(value):
            stats.append(value)
    if len(stats) < resamples * 0.9:
        return np.nan, np.nan
    return float(np.percentile(stats, 2.5)), float(np.percentile(stats, 97.5))


def verdict(ci_lo, ci_hi):
    if not (np.isfinite(ci_lo) and np.isfinite(ci_hi)):
        return "insufficient_data"
    if ci_hi < 1.0:
        return "calibrated"
    if ci_lo > 1.0:
        return "persistence"
    return "inconclusive_keep_persistence"


def analyze_lead(frame, *, resamples, seed, min_days, min_pairs):
    frame = assign_day(frame)
    days = int(frame["day"].nunique()) if not frame.empty else 0
    if days < min_days or len(frame) < min_pairs:
        return {"verdict": "insufficient_data", "daylight_pairs": len(frame), "device_days": days}
    ratio = mae_ratio(frame)
    ci_lo, ci_hi = bootstrap_ratio(frame, statistic=mae_ratio, resamples=resamples, seed=seed)
    alpha_errors = {alpha: mixed_mae(frame, alpha) for alpha in ALPHAS}
    best_alpha = min(alpha_errors, key=alpha_errors.get)
    pers_mae = float(frame["persistence_error_w"].mean())
    alpha_stat = lambda sample: mixed_mae(sample, best_alpha) / float(sample["persistence_error_w"].mean())  # noqa: E731
    alpha_lo, alpha_hi = bootstrap_ratio(frame, statistic=alpha_stat, resamples=resamples, seed=seed + 1)
    per_site = {str(site): {"pairs": int(len(g)), "mae_ratio": mae_ratio(g),
                            "calibrated_mae_w": float(g["calibrated_error_w"].mean()),
                            "persistence_mae_w": float(g["persistence_error_w"].mean())}
                for site, g in frame.groupby("site")}
    return {"verdict": verdict(ci_lo, ci_hi), "daylight_pairs": len(frame), "device_days": days,
            "mae_ratio_calibrated_over_persistence": ratio, "ratio_ci95": [ci_lo, ci_hi],
            "calibrated_mae_w": float(frame["calibrated_error_w"].mean()),
            "persistence_mae_w": pers_mae,
            "calibrated_bias_w": float(frame["calibrated_signed_error_w"].mean()),
            "per_site": per_site,
            "shrinkage_diagnostic": {"best_alpha": best_alpha, "alpha_mae_w": alpha_errors[best_alpha],
                                     "alpha_over_persistence_ci95": [alpha_lo, alpha_hi],
                                     "caveat": "alpha selected on this sample; CI not selection-corrected"}}


def run(analysis_paths, *, resamples=10000, seed=20261013, min_days=15, min_pairs=200):
    frame = load_rows(analysis_paths)
    result = {"inputs": [str(p) for p in analysis_paths], "resamples": resamples, "seed": seed,
              "min_days": min_days, "min_pairs": min_pairs,
              "daylight_rule": f"truth_pv_w>{DAYLIGHT_TRUTH_W} or predicted_pv_w>{DAYLIGHT_PREDICTED_W}",
              "leads": {}, "all_hours_secondary": {}}
    for lead in LEADS:
        subset = frame[frame["lead_hours"] == lead]
        result["leads"][str(lead)] = analyze_lead(subset[is_daylight(subset)].copy(),
                                                  resamples=resamples, seed=seed + lead,
                                                  min_days=min_days, min_pairs=min_pairs)
        if not subset.empty:
            result["all_hours_secondary"][str(lead)] = {"pairs": len(subset), "mae_ratio": mae_ratio(subset)}
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("analysis", type=Path, nargs="+", help="per-batch analysis.json paths")
    parser.add_argument("--resamples", type=int, default=10000)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = run(args.analysis, resamples=args.resamples)
    text = json.dumps(report, indent=2)
    if args.output:
        args.output.write_text(text, encoding="utf-8")
    print(text)
