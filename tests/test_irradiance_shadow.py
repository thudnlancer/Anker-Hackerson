import sys
import unittest
import json
from tempfile import TemporaryDirectory
from pathlib import Path
from unittest.mock import patch

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from run_irradiance_shadow import causal_calibration, evaluate_pair_snapshot
from analyze_irradiance_shadow import analyze
from energy_lab.shadow import make_candidate
from energy_lab.rolling import RollingPlan


class IrradianceShadowTests(unittest.TestCase):
    def pairs(self):
        return [{"site": "1", "device_time": f"2032-01-01T{h:02d}:00:00Z",
                 "received_at": "2026-10-13T00:00:00Z", "irradiance_wm2": 500., "pv_w": 1000.}
                for h in range(8, 11)]

    def test_calibration_rejects_future_and_wrong_site(self):
        tick = pd.Timestamp("2032-01-01T11:00:00Z")
        pairs = self.pairs()
        pairs.extend([{**pairs[0], "device_time": tick.isoformat(), "pv_w": 5000.},
                      {**pairs[0], "site": "2", "pv_w": 4000.},
                      {**pairs[0], "received_at": "2026-10-13T01:00:00Z", "pv_w": 3000.}])
        fitted = causal_calibration(pairs, "1", tick, "2026-10-13T00:01:00Z")
        self.assertEqual(fitted["scale_w_per_wm2"], 2.)
        self.assertEqual(fitted["device_hours"], 3)

    def test_repeated_ticks_do_not_satisfy_daylight_hour_threshold(self):
        self.assertIsNone(causal_calibration([self.pairs()[0]]*100, "1",
                          pd.Timestamp("2032-01-01T11:00:00Z"), "2026-10-13T00:01:00Z"))

    def snapshot(self):
        dates = pd.date_range("2032-01-01", periods=48, freq="h", tz="UTC")
        points = [{"timestamp": t.isoformat(), "temperature_c": 20., "cloud_cover_percent": 0.,
                   "solar_irradiance_wm2": 500., "condition": "clear"} for t in dates]
        prices = [{"start": t.isoformat(), "import_price": .3, "export_price": .1} for t in dates]
        received = "2026-10-13T00:01:00Z"
        return {"received_at": received, "prices": {"today": {"points": prices[:24]}, "tomorrow": {"points": prices[24:]}},
                "stations": {"1": {"received_at": received,
                "state": {"soc": 50., "solar_power": 1200., "home_load": 800.,
                          "device_time": "2032-01-01T11:00:00Z", "device_time_last_changed": received},
                "weather": {"today": {"points": points[:24]}, "tomorrow": {"points": points[24:]}}}}}

    def test_missing_history_still_produces_candidate_and_pair(self):
        snapshot = self.snapshot()
        with patch("run_irradiance_shadow.make_candidate", return_value={"status": "candidate_only"}) as mocked:
            results, additions = evaluate_pair_snapshot(snapshot, [], now=snapshot["received_at"])
        self.assertEqual(results["1"]["calibration_status"], "insufficient_pairs_persistence")
        self.assertEqual(len(additions), 1)
        self.assertEqual(mocked.call_args.kwargs["idle_candidate"], "zero_charge_unverified")

    def test_nowcast_scored_before_current_observation_override(self):
        snapshot = self.snapshot()
        with patch("run_irradiance_shadow.make_candidate", return_value={"status": "candidate_only"}):
            results, _ = evaluate_pair_snapshot(snapshot, self.pairs(), now=snapshot["received_at"])
        self.assertEqual(results["1"]["calibrated_nowcast_w"], 1000.)
        self.assertEqual(results["1"]["concurrent_pair_abs_error_w"], 200.)

    def test_solver_budget_is_explicit_without_changing_idle_semantics(self):
        snapshot = self.snapshot()
        with patch("run_irradiance_shadow.make_candidate", return_value={"status": "candidate_only"}) as mocked:
            evaluate_pair_snapshot(snapshot, self.pairs(), now=snapshot["received_at"], solver_seconds=.2)
        self.assertEqual(mocked.call_args.kwargs["solver_seconds"], .2)
        self.assertEqual(mocked.call_args.kwargs["idle_candidate"], "zero_charge_unverified")
        for invalid in (0., -1., float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                evaluate_pair_snapshot(snapshot, [], now=snapshot["received_at"], solver_seconds=invalid)

    def test_stale_snapshot_never_calibrated(self):
        snapshot = self.snapshot()
        results, additions = evaluate_pair_snapshot(snapshot, [], now="2026-10-13T01:01:00Z")
        self.assertEqual(results["1"]["status"], "blocked")
        self.assertEqual(additions, [])

    def test_shadow_idle_mapping_is_explicit_unverified_charge_zero(self):
        snapshot = self.snapshot()
        state = snapshot["stations"]["1"]["state"]
        from energy_lab.shadow import daily_points
        plan = RollingPlan(pd.DataFrame({"flow": ["idle"], "charge_w": [0.], "discharge_w": [0.],
                                         "soc_end": [50.]}), 0., "optimal", "mock")
        with patch("energy_lab.shadow.optimize_window", return_value=plan):
            result = make_candidate(state, daily_points(snapshot["prices"]), idle_candidate="zero_charge_unverified")
        self.assertEqual(result["candidate"]["flow"], "charge")
        self.assertEqual(result["candidate"]["target_w"], 0.)
        self.assertFalse(result["hardware_verified"])
        self.assertEqual(result["writes_performed"], 0)

    def test_future_scoring_freezes_earlier_weather_and_scale(self):
        snapshot = self.snapshot()
        with patch("run_irradiance_shadow.make_candidate", return_value={"status": "candidate_only"}):
            results, current = evaluate_pair_snapshot(snapshot, self.pairs(), now=snapshot["received_at"])
        future = {**current[0], "device_time": "2032-01-01T12:00:00Z",
                  "received_at": "2026-10-13T00:02:00Z", "pv_w": 1200.}
        report = {"pairs": self.pairs()+current+[future],
                  "cycles": [{"iteration": 1, "stations": results}]}
        with TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "snapshot_001.json").write_text(json.dumps(snapshot), encoding="utf-8")
            (root / "result.json").write_text(json.dumps(report), encoding="utf-8")
            checked = analyze(root)
        score = checked["sites"]["1"]["future_metrics"]["1"]
        self.assertEqual(score["count"], 1)
        self.assertEqual(score["calibrated_mae_w"], 200.)
        self.assertEqual(score["persistence_mae_w"], 0.)
        self.assertTrue(checked["causal_calibration_audit_passed"])
        self.assertEqual(checked["shrinkage_metrics"]["1"]["0.0"]["mae_w"], 0.)

    def test_future_evaluation_rejects_tampered_calibration(self):
        snapshot = self.snapshot()
        with patch("run_irradiance_shadow.make_candidate", return_value={"status": "candidate_only"}):
            results, current = evaluate_pair_snapshot(snapshot, self.pairs(), now=snapshot["received_at"])
        results["1"]["calibration"]["scale_w_per_wm2"] = 99.
        report = {"pairs": self.pairs()+current, "cycles": [{"iteration": 1, "stations": results}]}
        with TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "snapshot_001.json").write_text(json.dumps(snapshot), encoding="utf-8")
            (root / "result.json").write_text(json.dumps(report), encoding="utf-8")
            with self.assertRaises(AssertionError):
                analyze(root)


if __name__ == "__main__":
    unittest.main()
