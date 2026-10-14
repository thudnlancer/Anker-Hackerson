import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from tempfile import TemporaryDirectory
from unittest.mock import patch

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from energy_lab.contest_replay import (
    aggregate_sites, execute_command, power_inputs, select_guarded, self_use, window_metrics,
)
from energy_lab.model import Battery
from energy_lab.rolling import optimize_window
from run_contest_strategy_replay import evaluate, pv_forecast, visible_window


class ContestReplayTests(unittest.TestCase):
    def frame(self, n=220):
        return pd.DataFrame({"time": pd.date_range("2032-01-01", periods=n, freq="h"),
                             "pv": 1000., "load": 500., "shortwave_radiation_wm2": 500.,
                             "buy": .3, "sell": .1})

    def inputs(self):
        raw = self.frame()
        return raw, raw.iloc[168:192][["time", "buy", "sell", "shortwave_radiation_wm2"]]

    def test_future_target_columns_are_rejected(self):
        raw = self.frame()
        with self.assertRaisesRegex(ValueError, "future target"):
            power_inputs(raw.iloc[168], raw.iloc[:168], raw.iloc[168:192])

    def test_calibration_is_past_only_and_current_observation_wins(self):
        raw, published = self.inputs()
        current = raw.iloc[168].copy()
        current.pv, current.load = 123., 456.
        paths = power_inputs(current, raw.iloc[:168], published)
        self.assertEqual(paths["irradiance_pv"][0], 123.)
        self.assertEqual(paths["irradiance_pv"][1], 1000.)
        self.assertEqual(paths["interval_load"][0], 456.)
        self.assertEqual(paths["calibration"]["samples"], 168)
        with self.assertRaisesRegex(ValueError, "precede"):
            power_inputs(current, raw.iloc[:169], published)

    def test_no_daylight_calibration_falls_back(self):
        raw, published = self.inputs()
        raw.loc[:167, "shortwave_radiation_wm2"] = 0.
        paths = power_inputs(raw.iloc[168], raw.iloc[:168], published)
        self.assertIsNone(paths["calibration"])
        np.testing.assert_array_equal(paths["irradiance_pv"], paths["persistence_pv"])

    def test_load_scenarios_follow_device_hour_not_daily_constant(self):
        raw, published = self.inputs()
        raw.loc[:167, "load"] = raw.iloc[:168].time.dt.hour * 100.
        current = raw.iloc[168].copy()
        current.load = 10.
        paths = power_inputs(current, raw.iloc[:168], published)
        for scenario in paths["load_scenarios"]:
            np.testing.assert_allclose(scenario[:3], [10., 100., 200.])
        np.testing.assert_allclose(paths["interval_load"][:3], [10., 100., 200.])

    def test_negative_sell_price_penalizes_export_not_import(self):
        row = self.frame().iloc[0].copy()
        row.sell = -.1
        full = self_use(Battery(soc=100.), row)
        self.assertAlmostEqual(full["cost_eur"], .05)
        row.pv = 0.
        empty = self_use(Battery(soc=0.), row)
        self.assertAlmostEqual(empty["cost_eur"], .15)

    def test_solver_timeout_continues_whole_window_with_safe_fallback(self):
        raw = self.frame(840).rename(columns={"pv": "pv_power_avg_w", "load": "load_power_avg_w",
                                               "buy": "import_price_eur_kwh", "sell": "export_price_eur_kwh"})
        with TemporaryDirectory() as temp:
            directory = Path(temp)
            path = directory / "1.csv"
            raw.to_csv(path, index=False)
            with patch("run_contest_strategy_replay.optimize_window",
                       return_value=SimpleNamespace(status="limit_reached")) as mocked:
                result = evaluate(path, directory, 1, 0, .02, .02, 5., "zero_flow")
            self.assertEqual(mocked.call_count, 168)
            self.assertEqual(result["arms"]["persistence"]["fallback_steps"], 168)
            self.assertEqual(result["arms"]["persistence"]["settled_saving_eur"], 0.)
            self.assertTrue(result["physical_audit"]["passed"])
            trace = pd.read_csv(directory / "site_1_trace.csv")
            self.assertTrue(trace.command_mode.eq("self_consumption").all())
            self.assertTrue(trace.attribution.eq("non-optimizer-attributed").all())
            self.assertEqual(result["successful_plan_idle_mode"], "zero_flow")
            self.assertEqual(result["accounting_arm"], "zero_flow")

    def test_window_shrinks_and_estimate_does_not_use_unpublished_prices(self):
        raw = self.frame()
        before, estimate = visible_window(raw, 170, 168, 210)
        raw.loc[194:, "buy"] = 9999.
        after, changed = visible_window(raw, 170, 168, 210)
        pd.testing.assert_frame_equal(before, after)
        self.assertEqual(estimate, changed)
        self.assertEqual(len(visible_window(raw, 209, 168, 210)[0]), 1)

    def test_terminal_price_current_visible_mode_is_causal_and_distinct(self):
        raw = self.frame(220)
        raw.loc[168:179, "buy"] = .9
        published, current_mean = visible_window(raw, 180, 168, 210, "current_visible_mean")
        _, legacy_mean = visible_window(raw, 180, 168, 210, "a5_eval_start_mean")
        self.assertAlmostEqual(current_mean, published.buy.mean())
        self.assertNotAlmostEqual(current_mean, legacy_mean)
        _, zero = visible_window(raw, 180, 168, 210, "zero")
        self.assertEqual(zero, 0.0)

    def test_hybrid_pv_rule_uses_persistence_for_first_future_hour(self):
        paths = {"persistence_pv": np.array([111., 222., 333., 444.]),
                 "irradiance_pv": np.array([111., 999., 888., 777.]),
                 "calibration": {"samples": 3}}
        result = pv_forecast(paths, SimpleNamespace(), "hybrid")
        np.testing.assert_array_equal(result, [111., 222., 888., 777.])
        paths["calibration"]["samples"] = 2
        np.testing.assert_array_equal(pv_forecast(paths, SimpleNamespace(), "hybrid"),
                                      paths["persistence_pv"])

    def test_full_battery_self_use_does_not_consume_phantom_charge(self):
        row = self.frame().iloc[0]
        result = self_use(Battery(soc=100.), row)
        self.assertEqual(result["power_w"], 0.)
        self.assertEqual(result["soc_after"], 100.)
        self.assertAlmostEqual(result["grid_export_kwh"], .5)

    def test_empty_battery_self_use_imports_deficit(self):
        row = self.frame().iloc[0].copy()
        row.pv = 0.
        result = self_use(Battery(soc=0.), row)
        self.assertEqual(result["power_w"], 0.)
        self.assertAlmostEqual(result["grid_import_kwh"], .5)

    def test_hold_uses_self_consumption_not_zero_flow(self):
        row = self.frame().iloc[0]
        result = execute_command(Battery(), row, SimpleNamespace(flow="idle"), row.pv, row.load)
        self.assertEqual(result["command_mode"], "self_consumption")
        self.assertEqual(result["power_w"], 500.)
        self.assertFalse(result["optimizer_attributed"])
        self.assertEqual(result["attribution"], "non-optimizer-attributed")

    def test_zero_flow_is_explicit_offline_diagnostic(self):
        row = self.frame().iloc[0]
        result = execute_command(Battery(), row, SimpleNamespace(flow="idle"), row.pv, row.load,
                                 hold_mode="zero_flow")
        self.assertEqual(result["command_mode"], "zero_flow")
        self.assertEqual(result["command_flow"], "charge")
        self.assertEqual(result["power_w"], 0.)
        self.assertTrue(result["optimizer_attributed"])
        self.assertEqual(result["attribution"], "optimizer-attributed")

    def test_zero_charge_executes_optimizer_energy_grid_and_objective(self):
        # Include genuine idle, charge and discharge plans. With exact powers,
        # replay must match the optimized trajectory, not secretly self-use.
        for pv, load, buy, sell, terminal in (
                ([1000.], [0.], [.3], [.2], 0.),
                ([0.], [1000.], [.1], [0.], 1.),
                ([0., 2000., 0.], [1000., 500., 1500.], [.4, .1, .5], [.1, .05, .3], .2)):
            plan = optimize_window(pd.Series(pd.date_range("2032-01-01", periods=len(pv), freq="h")),
                                   np.array(pv), np.array(load), np.array(buy), np.array(sell),
                                   50., solver="milp", command_contract="sop", terminal_price=terminal,
                                   degradation_cost_eur_kwh=.02)
            self.assertEqual(plan.status, "optimal")
            battery, cost = Battery(), 0.
            for i, action in plan.plan.iterrows():
                row = pd.Series({"pv": pv[i], "load": load[i], "buy": buy[i], "sell": sell[i]})
                result = execute_command(battery, row, action, pv[i], load[i], hold_mode="zero_flow")
                self.assertAlmostEqual(result["soc_after"], action.soc_end)
                self.assertAlmostEqual(result["grid_import_kwh"]*1000, action.grid_import_w)
                self.assertAlmostEqual(result["grid_export_kwh"]*1000, action.grid_export_w)
                cost += result["cost_eur"] + .02*result["throughput_kwh"]
            self.assertAlmostEqual(cost - battery.soc*.14*terminal, plan.objective)

    def test_invalid_guarded_plan_does_not_block_strategy(self):
        plan = SimpleNamespace(status="optimal", plan=None)
        paths = {"load_scenarios": [None], "irradiance_pv": None,
                 "current_load": None, "interval_load": None}
        with patch("energy_lab.contest_replay.scenario_cost", side_effect=ValueError("unrepresentable")):
            accepted, margin = select_guarded(plan, plan, None, paths, 50., .3, .02, .02)
        self.assertFalse(accepted)
        self.assertIsNone(margin)

    def test_discharge_target_is_preserved_but_energy_is_clipped(self):
        row = self.frame().iloc[0].copy()
        row.pv, row.load = 0., 1000.
        action = SimpleNamespace(flow="discharge", discharge_w=2000.)
        result = execute_command(Battery(soc=1.), row, action, 0., 1000.)
        self.assertEqual(result["command_target_w"], 1000.)
        self.assertAlmostEqual(result["power_w"], 140.)
        self.assertAlmostEqual(result["grid_import_kwh"], .86)

    def test_settlement_inventory_and_wear_are_separate(self):
        trace = pd.DataFrame({"arm": ["self_consumption", "candidate"],
                              "cost_eur": [10., 8.], "soc_after": [50., 60.],
                              "throughput_kwh": [1., 2.], "fallback": [False, False],
                              "nonoptimal_solves": [0, 0]})
        result = window_metrics(trace, .3, 100., .02)
        self.assertAlmostEqual(result["candidate"]["settled_saving_eur"], 2.42)
        self.assertAlmostEqual(result["candidate"]["saving_settled_percent_estimate"], 2.42)
        self.assertAlmostEqual(result["candidate"]["settled_saving_after_internal_wear_eur"], 2.40)

    def test_percentage_is_ratio_of_sums_not_mean_of_percentages(self):
        reports = [{"k_eur": 100., "arms": {"candidate": {"settled_saving_eur": 10., "final_soc": 50.,
                    "saving_settled_percent_estimate": 10.}}},
                   {"k_eur": 900., "arms": {"candidate": {"settled_saving_eur": 0., "final_soc": 50.,
                    "saving_settled_percent_estimate": 0.}}}]
        self.assertEqual(aggregate_sites(reports)["arms"]["candidate"]["saving_settled_percent_estimate"], 1.)

    def test_attribution_counts_and_legacy_null_aggregate(self):
        trace = pd.DataFrame({"arm": ["self_consumption", "self_consumption", "candidate", "candidate"],
                              "cost_eur": [1., 1., 1., 1.], "soc_after": [50.] * 4,
                              "throughput_kwh": [0.] * 4, "fallback": [False, False, False, True],
                              "nonoptimal_solves": [0, 0, 0, 1],
                              "optimizer_attributed": [False, False, True, False]})
        metrics = window_metrics(trace, .2, 10., .02)
        self.assertEqual(metrics["candidate"]["optimizer_attributed_steps"], 1)
        self.assertEqual(metrics["candidate"]["non_optimizer_attributed_steps"], 1)
        self.assertEqual(metrics["self_consumption"]["optimizer_attributed_steps"], 0)
        legacy = trace.drop(columns="optimizer_attributed")
        old_metrics = window_metrics(legacy, .2, 10., .02)
        self.assertIsNone(old_metrics["candidate"]["optimizer_attributed_steps"])
        combined = aggregate_sites([{"k_eur": 10., "arms": old_metrics},
                                    {"k_eur": 10., "arms": metrics}])
        self.assertIsNone(combined["arms"]["candidate"]["optimizer_attributed_steps"])

    def test_gate_rejects_if_any_scenario_loses(self):
        plan = SimpleNamespace(status="optimal", plan=None)
        paths = {"load_scenarios": [None]*3, "irradiance_pv": None,
                 "current_load": None, "interval_load": None}
        with patch("energy_lab.contest_replay.scenario_cost", side_effect=[1., 0., 1., 2., 1., 0.]):
            accepted, margin = select_guarded(plan, plan, None, paths, 50., .3, .02, .02)
        self.assertFalse(accepted)
        self.assertEqual(margin, -1.)

    def test_gate_accepts_only_if_all_scenarios_exceed_threshold(self):
        plan = SimpleNamespace(status="optimal", plan=None)
        paths = {"load_scenarios": [None]*3, "irradiance_pv": None,
                 "current_load": None, "interval_load": None}
        with patch("energy_lab.contest_replay.scenario_cost", side_effect=[1., .9, 1., .9, 1., .9]):
            accepted, margin = select_guarded(plan, plan, None, paths, 50., .3, .02, .02)
        self.assertTrue(accepted)
        self.assertAlmostEqual(margin, .1)


if __name__ == "__main__":
    unittest.main()
