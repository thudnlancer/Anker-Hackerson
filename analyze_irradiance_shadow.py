"""Score frozen earlier API snapshots against later HA pairs, without refitting."""
import argparse
from pathlib import Path
import json
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
from energy_lab.online_contract import normalize_weather_payload


def metrics(rows):
    if not rows:
        return {"count": 0}
    frame = pd.DataFrame(rows)
    return {"count": len(frame), "calibrated_mae_w": float(frame.calibrated_error_w.mean()),
            "persistence_mae_w": float(frame.persistence_error_w.mean()),
            "calibrated_bias_w": float(frame.calibrated_signed_error_w.mean())}


def analyze(root):
    report = json.loads((root / "result.json").read_text(encoding="utf-8"))
    pairs = report["pairs"]
    verified = 0
    rows = []
    status_counts = {}
    concurrent = {}
    for cycle in report["cycles"]:
        path = root / f"snapshot_{cycle['iteration']:03d}.json"
        if not path.exists():
            continue
        snapshot = json.loads(path.read_text(encoding="utf-8"))
        for site, result in cycle.get("stations", {}).items():
            key = result["status"]
            status_counts[key] = status_counts.get(key, 0)+1
            calibration = result.get("calibration")
            if not calibration:
                continue
            tick = pd.Timestamp(result["device_time"])
            received = pd.Timestamp(snapshot["stations"][site]["received_at"])
            # Recompute the past-only coefficient independently from saved pairs.
            past = [p for p in pairs if p["site"] == site and
                    tick-pd.Timedelta(hours=168) <= pd.Timestamp(p["device_time"]) < tick
                    and pd.Timestamp(p["received_at"]) < received]
            table = pd.DataFrame(past)
            table["hour"] = pd.to_datetime(table.device_time, utc=True).dt.floor("h")
            grouped = table.groupby("hour")[["irradiance_wm2", "pv_w"]].median()
            daylight = grouped[grouped.irradiance_wm2 > 25]
            expected = float(np.median(daylight.pv_w/daylight.irradiance_wm2))
            assert len(daylight) >= 3
            assert abs(expected-calibration["scale_w_per_wm2"]) < 1e-8
            assert calibration["device_hours"] == len(daylight)
            verified += 1
            concurrent.setdefault(site, []).append(result["concurrent_pair_abs_error_w"])
            weather = normalize_weather_payload(snapshot["stations"][site]["weather"]).set_index("time")
            for lead in (1, 2, 4):
                future_tick = tick + pd.Timedelta(hours=lead)
                matches = [p for p in pairs if p["site"] == site
                           and pd.Timestamp(p["device_time"]) == future_tick
                           and pd.Timestamp(p["received_at"]) > received]
                if not matches or future_tick.floor("h") not in weather.index:
                    continue
                observed = min(matches, key=lambda p: pd.Timestamp(p["received_at"]))
                irradiance = float(weather.loc[future_tick.floor("h"), "solar_irradiance_wm2"])
                predicted = float(np.clip(expected*irradiance, 0., 5000.))
                truth = float(observed["pv_w"])
                rows.append({"site": site, "source_device_time": tick.isoformat(),
                             "target_device_time": future_tick.isoformat(), "lead_hours": lead,
                             "predicted_pv_w": predicted, "persistence_pv_w": float(result["observed_pv_w"]),
                             "truth_pv_w": truth,
                             "calibrated_error_w": abs(predicted-truth),
                             "calibrated_signed_error_w": predicted-truth,
                             "persistence_error_w": abs(float(result["observed_pv_w"])-truth)})
    # Repeated polls in the same tick must not count as independent forecasts.
    if rows:
        rows = pd.DataFrame(rows).drop_duplicates(["site", "source_device_time", "lead_hours"]).to_dict("records")
    per_site = {site: {"concurrent_pair_mae_w": float(np.mean(errors)), "concurrent_count": len(errors),
                      "future_metrics": {str(lead): metrics([r for r in rows if r["site"] == site and r["lead_hours"] == lead])
                                         for lead in (1, 2, 4)}} for site, errors in concurrent.items()}
    shrinkage = {}
    for lead in (1, 2, 4):
        subset = [r for r in rows if r["lead_hours"] == lead]
        shrinkage[str(lead)] = {str(alpha): {
            "count": len(subset),
            "mae_w": float(np.mean([abs((alpha*r["predicted_pv_w"] + (1-alpha)*r["persistence_pv_w"])-r["truth_pv_w"])
                                     for r in subset])) if subset else None,
            "better_than_persistence": sum(abs((alpha*r["predicted_pv_w"] + (1-alpha)*r["persistence_pv_w"])-r["truth_pv_w"])
                                            < abs(r["persistence_pv_w"]-r["truth_pv_w"]) for r in subset) if subset else 0,
        } for alpha in (0., .25, .5, .75, 1.)}
    analysis = {"causal_calibration_audit_passed": True, "verified_calibrations": verified,
                "status_counts": status_counts, "cycles": len(report["cycles"]), "total_pairs_including_seed": len(pairs),
                "sites": per_site, "future_forecast_scores": rows, "shrinkage_metrics": shrinkage,
                "writes_performed": 0, "production_approved": False,
                "limits": ["Very small single-day correlated sample; no significance or cross-day generalization claim.",
                           "Future scores freeze the earlier received API snapshot and past-only calibration.",
                           "Exact device timestamps required; no interpolation or shifting mismatched station clocks.",
                           "Concurrent errors are not future errors; reported separately.",
                           "No hardware response verification and no closed-loop strategy revenue measurement."]}
    (root / "analysis.json").write_text(json.dumps(analysis, indent=2), encoding="utf-8")
    return {k: v for k, v in analysis.items() if k != "future_forecast_scores"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    print(json.dumps(analyze(parser.parse_args().output), indent=2))
