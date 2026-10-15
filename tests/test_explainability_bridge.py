"""Same-solve evidence sidecar tests; all inputs are fixtures and no network is used."""
from __future__ import annotations

from contextlib import redirect_stdout
import copy
import io
import json
from pathlib import Path
import runpy
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "src"))

from energy_lab.shadow import daily_points, evaluate_snapshot, make_candidate
from energy_lab.explainability_evidence import (EvidenceExportError,
    SOLVER_ZERO_TOLERANCE_W, build_explainability_record)
from energy_lab.interface_contract import map_battery_action
from energy_lab.rolling import optimize_window


def fixture_snapshot():
    prices = {}
    for day_index, (day, date) in enumerate((("today", "2031-01-01"), ("tomorrow", "2031-01-02"))):
        prices[day] = {"date": date, "points": [
            {"start": f"{date}T{hour:02d}:00:00Z", "import_price": 0.3 + day_index * 0.003 + hour * 0.0001,
             "export_price": 0.1, "authorization": "PRIVATE-PRICE-HEADER"}
            for hour in range(24)]}
    state = {"soc": "45", "solar_power": "400", "home_load": "1000",
             "device_time": "2031-01-01T23:15:00+00:00",
             "device_time_last_changed": "2026-10-13T00:00:04Z",
             "ha_token": "PRIVATE-STATE-TOKEN", "config": {"password": "PRIVATE-CONFIG-PASSWORD"}}
    return {"received_at": "2026-10-13T00:00:05Z", "prices": prices,
            "stations": {"1": {"state": state, "received_at": "2026-10-13T00:00:05Z",
                                 "weather": {"unavailable": True}}},
            "configuration": {"sim_base": "PRIVATE-CONNECTION-URL", "password": "PRIVATE-SNAPSHOT-PASSWORD"}}


class ExplainabilityBridgeTests(unittest.TestCase):
    def setUp(self):
        self.snapshot = fixture_snapshot()
        self.state = self.snapshot["stations"]["1"]["state"]
        self.prices = daily_points(self.snapshot["prices"])
        self.identity = {"run_id": "unit-bridge", "site_id": "1", "source_ref": "unit:same-solve",
                         "config": {"token": "PRIVATE-IDENTITY-TOKEN"}}

    def capture(self, **kwargs):
        records = []
        candidate = make_candidate(self.state, self.prices, solver_seconds=10,
            evidence_sink=records.append, evidence_identity=self.identity,
            evidence_observed_at=self.snapshot["received_at"], **kwargs)
        self.assertEqual(candidate["status"], "candidate_only", candidate)
        self.assertEqual(len(records), 1, candidate)
        return candidate, records[0]

    def test_opt_in_hook_does_not_change_original_candidate(self):
        original = make_candidate(self.state, self.prices, solver_seconds=10)
        candidate, _ = self.capture()
        self.assertEqual(candidate, original)
        self.assertNotIn("explainability_export", original)
        self.assertEqual(candidate["writes_performed"], 0)
        self.assertFalse(candidate["hardware_verified"])

    def test_same_solve_exports_units_clock_horizon_parameters_and_cost(self):
        candidate, record = self.capture()
        s = record["snapshot"]
        self.assertEqual(s["soc_percent"], 45)
        self.assertEqual(s["pv_kw"], 0.4)
        self.assertEqual(s["load_kw"], 1)
        self.assertEqual(s["capacity_kwh"], 14)
        self.assertEqual(s["max_charge_kw"], 5)
        self.assertEqual(s["max_discharge_kw"], 5)
        self.assertEqual(s["step_hours"], 0.25)
        self.assertEqual(s["charge_efficiency"], 0.95)
        self.assertEqual(s["discharge_efficiency"], 1)
        self.assertEqual(s["degradation_eur_kwh"], 0.02)
        self.assertEqual(s["soc_min_percent"], 0)
        self.assertEqual(s["soc_max_percent"], 100)
        self.assertEqual(s["observed_at"], "2026-10-13T00:00:05+00:00")
        self.assertEqual(s["device_time"], "2031-01-01T23:15:00+00:00")
        times = pd.date_range(s["device_time"], periods=96, freq="15min")
        expected_buy = [0.3 + (time.day - 1) * 0.003 + time.hour * 0.0001 for time in times]
        for key in ("pv_forecast_kw", "load_forecast_kw", "import_eur_kwh", "export_eur_kwh"):
            self.assertEqual(len(s[key]), 96)
        np.testing.assert_allclose(s["import_eur_kwh"], expected_buy, atol=1e-12)
        self.assertAlmostEqual(s["terminal_eur_kwh"], sum(expected_buy) / 96, places=12)
        self.assertNotAlmostEqual(s["terminal_eur_kwh"], expected_buy[0], places=6)
        np.testing.assert_allclose(s["pv_forecast_kw"], [0.4] * 96)
        np.testing.assert_allclose(s["load_forecast_kw"], [1] * 96)
        first = record["replay_context"]["first_step"]
        self.assertEqual(set(first), {"charge_kw", "discharge_kw", "grid_import_kw", "grid_export_kw"})
        self.assertEqual(record["replay_context"]["command_contract"], "sop")
        self.assertAlmostEqual(first["charge_kw"], 0, places=6)
        self.assertAlmostEqual(first["discharge_kw"], 0, places=6)
        self.assertAlmostEqual(first["grid_import_kw"], 0.6, places=6)
        self.assertAlmostEqual(first["grid_export_kw"], 0, places=6)
        cost = record["cost"]
        self.assertAlmostEqual(cost["purchase_eur"], sum(expected_buy) * 0.6 * 0.25, places=6)
        self.assertAlmostEqual(cost["terminal_credit_eur"], 14 * 0.45 * s["terminal_eur_kwh"], places=6)
        self.assertAlmostEqual(cost["degradation_eur"], 0, places=6)
        self.assertAlmostEqual(cost["objective_eur"], cost["purchase_eur"] - cost["export_revenue_eur"]
                               + cost["degradation_eur"] - cost["terminal_credit_eur"], places=6)
        self.assertAlmostEqual(record["action"]["power_kw"], candidate["candidate"]["target_w"] / 1000, places=6)

    def test_observations_are_distinguished_from_future_assumptions_and_secrets_excluded(self):
        _, record = self.capture()
        self.assertEqual(record["snapshot"]["sources"], {"pv": "observed", "load": "observed",
            "pv_forecast": "assumption", "load_forecast": "assumption"})
        self.assertEqual(record["provenance"]["input_kind"], "mixed")
        self.assertEqual(record["snapshot"]["forecast_status"], "fallback")
        self.assertTrue(record["snapshot"]["fallback_reason"])
        serialized = json.dumps(record, allow_nan=False)
        for marker in ("PRIVATE-PRICE-HEADER", "PRIVATE-STATE-TOKEN", "PRIVATE-CONFIG-PASSWORD",
                       "PRIVATE-IDENTITY-TOKEN", "PRIVATE-CONNECTION-URL", "PRIVATE-SNAPSHOT-PASSWORD"):
            self.assertNotIn(marker, serialized)
        self.identity["input_kind"] = "synthetic"
        _, synthetic = self.capture()
        self.assertEqual(set(synthetic["snapshot"]["sources"].values()), {"synthetic"})
        self.assertEqual(synthetic["provenance"]["input_kind"], "synthetic")

    def test_supplied_calibrated_forecast_preserves_first_observation(self):
        times = pd.date_range(self.state["device_time"], periods=96, freq="15min")
        forecast = pd.DataFrame({"time": times, "pv": [600.0] * 96, "load": [1200.0] * 96,
                                 "credential_debug": "PRIVATE-FORECAST-COLUMN"})
        _, record = self.capture(forecast=forecast,
            forecast_mode="api_weather_calibrated_pv_current_load_persistence")
        self.assertEqual(record["snapshot"]["pv_forecast_kw"][0], 0.4)
        self.assertEqual(record["snapshot"]["load_forecast_kw"][0], 1)
        self.assertEqual(record["snapshot"]["pv_forecast_kw"][1], 0.6)
        self.assertEqual(record["snapshot"]["load_forecast_kw"][1], 1.2)
        self.assertEqual(record["snapshot"]["sources"]["pv_forecast"], "predicted")
        self.assertEqual(record["snapshot"]["sources"]["load_forecast"], "assumption")
        self.assertNotIn("PRIVATE-FORECAST-COLUMN", json.dumps(record))

    def test_bad_or_stale_site_never_exports_and_other_site_is_preserved(self):
        good = self.snapshot["stations"]["1"]
        self.snapshot["stations"]["2"] = {**good, "state": {**self.state, "soc": "unavailable"}}
        self.snapshot["stations"]["3"] = {**good, "state": {**self.state,
            "device_time_last_changed": "2026-10-12T00:00:00Z"}}
        baseline = evaluate_snapshot(self.snapshot, now=self.snapshot["received_at"], solver_seconds=10)
        records = []
        actual = evaluate_snapshot(self.snapshot, now=self.snapshot["received_at"], solver_seconds=10,
            evidence_sink=records.append, evidence_identity={"run_id": "mixed-sites"})
        self.assertEqual(actual, baseline)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["site_id"], "1")
        self.assertEqual(actual["stations"]["2"]["status"], "blocked")
        self.assertEqual(actual["stations"]["3"]["status"], "blocked")

    def test_sink_failure_cannot_change_candidate_or_expose_exception_message(self):
        baseline = make_candidate(self.state, self.prices, solver_seconds=10)
        def fail(record):
            raise RuntimeError("PRIVATE-SINK-EXCEPTION-CONTENT")
        result = make_candidate(self.state, self.prices, solver_seconds=10, evidence_sink=fail,
            evidence_identity=self.identity, evidence_observed_at=self.snapshot["received_at"])
        error = result.pop("explainability_export")
        self.assertEqual(result, baseline)
        self.assertEqual(error, {"status": "failed", "error_type": "RuntimeError"})
        self.assertNotIn("PRIVATE-SINK-EXCEPTION-CONTENT", json.dumps(result | {"error": error}))

    def test_export_validation_reports_fixed_code_without_input_text(self):
        records = []
        result = make_candidate(self.state, self.prices, solver_seconds=10,
            evidence_sink=records.append,
            evidence_identity={"run_id": "PRIVATE-INVALID-IDENTIFIER/SECRET", "site_id": "1"},
            evidence_observed_at=self.snapshot["received_at"])
        self.assertEqual(result["explainability_export"], {"status": "failed",
            "error_type": "EvidenceExportError", "failure_code": "invalid_identifier"})
        self.assertEqual(records, [])
        self.assertNotIn("PRIVATE-", json.dumps(result))

    def test_sink_cannot_supply_export_failure_code_or_expose_error_text(self):
        def fail(record):
            raise EvidenceExportError("PRIVATE-SINK-EXCEPTION-CONTENT")
        result = make_candidate(self.state, self.prices, solver_seconds=10, evidence_sink=fail,
            evidence_identity=self.identity, evidence_observed_at=self.snapshot["received_at"])
        self.assertEqual(result["explainability_export"], {"status": "failed",
            "error_type": "EvidenceExportError"})
        self.assertNotIn("PRIVATE-", json.dumps(result))

    def test_solver_failure_never_exports_evidence(self):
        records = []
        with patch("energy_lab.shadow.optimize_window", return_value=SimpleNamespace(status="infeasible")):
            result = make_candidate(self.state, self.prices, evidence_sink=records.append,
                evidence_identity=self.identity, evidence_observed_at=self.snapshot["received_at"])
        self.assertEqual(result["status"], "infeasible")
        self.assertIsNone(result["candidate"])
        self.assertEqual(records, [])

    def test_offline_runner_explicit_export_flag_writes_only_opt_in_sidecar(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)
            snapshot_path = path / "snapshot.json"
            snapshot_path.write_text(json.dumps(self.snapshot), encoding="utf-8")
            def run(output, extra):
                argv = [str(PROJECT / "run_shadow_dry_run.py"), "--snapshot", str(snapshot_path),
                    "--replay-at-capture", "--config", str(path / "unused-config.json"),
                    "--model-directory", str(path / "missing-models"), "--output", str(output), *extra]
                with patch.object(sys, "argv", argv), patch("requests.Session", side_effect=AssertionError("offline runner used network")), redirect_stdout(io.StringIO()):
                    runpy.run_path(str(PROJECT / "run_shadow_dry_run.py"), run_name="__main__")
                return json.loads((output / "result.json").read_text())
            baseline = run(path / "without-hook", [])
            self.assertNotIn("explainability_export", baseline)
            self.assertFalse((path / "without-hook" / "explainability-decisions.jsonl").exists())
            exported = run(path / "with-hook", ["--explainability-run-id", "offline-unit",
                "--explainability-input-kind", "synthetic"])
            self.assertEqual(exported["stations"], baseline["stations"])
            self.assertEqual(exported["writes_performed"], 0)
            self.assertEqual(exported["explainability_export"]["records_written"], 1)
            lines = (path / "with-hook" / "explainability-decisions.jsonl").read_text().splitlines()
            self.assertEqual(len(lines), 1)
            record = json.loads(lines[0])
            self.assertEqual(record["run_id"], "offline-unit")
            self.assertEqual(record["provenance"]["input_kind"], "synthetic")
            self.assertNotIn("PRIVATE-", lines[0])


class SolverRoundoffExportTests(unittest.TestCase):
    """Artificial replay fixture; it is not the lost site-3 live snapshot."""

    @classmethod
    def setUpClass(cls):
        # A low SOC and a persistent 862 W deficit exercise a zero energy bound.
        # The final prices are deliberately extended fixture values; no live
        # identity or claim about the original failed input is embedded here.
        buy = [.29462, .23532, .19338, .14078, .13293, .13638, .17914,
               .22341, .26213, .29903, .37722, .37167, .31374, .29721,
               .28448, .27826, .29048, .28469, .28072, .2838, .28145,
               .30007, .34064, .35548]
        sell = [.17854, .11799, .07517, .02146, .01343, .01696, .06063,
                .10583, .14537, .18305, .26289, .25723, .19807, .18119,
                .16819, .16184, .17432, .16841, .16435, .1675, .1651,
                .18411, .22554, .24069]
        cls.buy = [buy[0]] + [x for x in buy[1:-1] for _ in range(4)] + [buy[-1]]
        cls.sell = [sell[0]] + [x for x in sell[1:-1] for _ in range(4)] + [sell[-1]]
        cls.buy += [buy[-3]] + [buy[-2]] * 4 + [buy[-1]]
        cls.sell += [sell[-3]] + [sell[-2]] * 4 + [sell[-1]]
        cls.times = pd.Series(pd.date_range("2031-01-01T08:45:00Z", periods=96, freq="15min"))
        cls.pv = np.full(96, 113.)
        cls.load = np.full(96, 975.)
        cls.state = {"soc": 4., "solar_power": 113., "home_load": 975.}
        cls.result = optimize_window(cls.times, cls.pv, cls.load, np.array(cls.buy),
            np.array(cls.sell), 4., capacity_kwh=14., max_power_w=5000.,
            terminal_price=None, degradation_cost_eur_kwh=.02, solver="milp",
            step_hours=.25, command_contract="sop", time_limit_seconds=10.)
        if cls.result.status != "optimal":
            raise AssertionError("artificial low-SOC fixture must solve optimally")
        first = cls.result.plan.iloc[0]
        cls.mapping = map_battery_action(str(first.flow), float(first.discharge_w), 113., 975.)

    def export(self, **overrides):
        options = {"identity": {"run_id": "unit-low-soc", "site_id": "3", "input_kind": "synthetic"},
            "observed_at": "2026-10-15T00:00:00Z", "state": self.state,
            "times": self.times, "pv_w": self.pv, "load_w": self.load,
            "import_price": self.buy, "export_price": self.sell,
            "result": self.result, "mapping": self.mapping, "forecast_mode": "artificial-persistence",
            "capacity_kwh": 14., "max_power_w": 5000., "soc_min": 0., "soc_max": 100.}
        options.update(overrides)
        return build_explainability_record(**options)

    def test_actual_optimizer_zero_bound_roundoff_exports_with_cost_reconciliation(self):
        record = self.export()
        self.assertAlmostEqual(self.result.plan.iloc[0].discharge_w, 862., places=6)
        self.assertAlmostEqual(self.result.plan.iloc[0].soc_end, 2.460714285714286, places=8)
        for value in record["replay_context"]["first_step"].values():
            self.assertGreaterEqual(value, 0.)
        cost = record["cost"]
        self.assertAlmostEqual(cost["purchase_eur"] - cost["export_revenue_eur"]
            + cost["degradation_eur"] - cost["terminal_credit_eur"],
            self.result.objective, places=6)
        self.assertEqual(record["provenance"]["input_kind"], "synthetic")

    def test_only_solver_zero_tails_are_normalized_and_original_plan_is_unchanged(self):
        result = copy.deepcopy(self.result)
        result.plan.loc[0, "grid_import_w"] = -7.456035788777626e-9
        original = result.plan.copy(deep=True)
        record = self.export(result=result)
        self.assertEqual(record["replay_context"]["first_step"]["grid_import_kw"], 0.)
        self.assertIn("0.000001 W", record["provenance"]["note"])
        pd.testing.assert_frame_equal(result.plan, original)

    def test_material_negative_solver_power_still_rejects(self):
        result = copy.deepcopy(self.result)
        result.plan.loc[5, "grid_import_w"] = -2 * SOLVER_ZERO_TOLERANCE_W
        with self.assertRaises(EvidenceExportError) as caught:
            self.export(result=result)
        self.assertEqual(caught.exception.code, "negative_solver_power")

    def test_previously_accepted_zero_tails_keep_identical_record_bytes(self):
        result = copy.deepcopy(self.result)
        columns = ["charge_w", "discharge_w", "grid_import_w", "grid_export_w"]
        result.plan[columns] = result.plan[columns].clip(lower=0.)
        baseline = self.export(result=result)
        result.plan.loc[0, "grid_import_w"] = -1e-10
        self.assertEqual(self.export(result=result), baseline)

    def test_input_observations_do_not_use_the_solver_tolerance(self):
        with self.assertRaises(EvidenceExportError) as caught:
            self.export(state={**self.state, "solar_power": -7.456035788777626e-9})
        self.assertEqual(caught.exception.code, "negative_input_value")

    def test_cost_error_is_not_hidden_by_roundoff_normalization(self):
        result = copy.deepcopy(self.result)
        result.objective += .01
        with self.assertRaises(EvidenceExportError) as caught:
            self.export(result=result)
        self.assertEqual(caught.exception.code, "solver_cost_mismatch")


if __name__ == "__main__":
    unittest.main()
