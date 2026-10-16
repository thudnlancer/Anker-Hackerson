from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from energy_lab.zero_flow import (apply_zero_flow_conclusion, build_zero_flow_command,
                                  confirm_restored, evidence_hash, judge_zero_flow_state,
                                  plan_restore)


def zero_flow_state(**overrides):
    state = dict(operating_mode="third_party_control", grid_flow="charge", power_control=0,
                 solar_power=1000, home_load=3000,
                 battery_charging_power_w=0, battery_discharging_power_w=0,
                 grid_import_power_w=2000, grid_export_power_w=0, device_time="2031-01-01 00:15:00")
    state.update(overrides)
    return state


class JudgeTests(unittest.TestCase):
    def test_verified_import_case(self):
        judgement = judge_zero_flow_state(zero_flow_state())
        self.assertTrue(judgement.verified, judgement.reason)
        self.assertEqual(judgement.reason, "verified")
        self.assertTrue(all(item["passed"] for item in judgement.checks.values()))

    def test_verified_export_case(self):
        state = zero_flow_state(solar_power=4000, home_load=1500,
                                grid_import_power_w=0, grid_export_power_w=2500)
        self.assertTrue(judge_zero_flow_state(state).verified)

    def test_tolerance_boundary_passes(self):
        state = zero_flow_state(battery_charging_power_w=100, grid_import_power_w=2100)
        self.assertTrue(judge_zero_flow_state(state).verified)

    def test_reject_battery_discharge_means_device_still_self_consuming(self):
        # Self-use signature: battery covers 800 W of load, so grid import drops
        # below the passive-settlement expectation. Both checks must fail.
        state = zero_flow_state(battery_discharging_power_w=800, grid_import_power_w=1200)
        judgement = judge_zero_flow_state(state)
        self.assertFalse(judgement.verified)
        self.assertIn("battery_discharge_within_tolerance", judgement.reason)
        self.assertFalse(judgement.checks["grid_import_passive_settlement"]["passed"])

    def test_reject_battery_charge_means_pv_surplus_still_charging(self):
        # Self-use signature: surplus PV charges the battery instead of exporting.
        state = zero_flow_state(solar_power=4000, home_load=1500,
                                battery_charging_power_w=2500, grid_export_power_w=0)
        judgement = judge_zero_flow_state(state)
        self.assertFalse(judgement.verified)
        self.assertIn("battery_charge_within_tolerance", judgement.reason)
        self.assertFalse(judgement.checks["grid_export_passive_settlement"]["passed"])

    def test_reject_non_passive_grid_import(self):
        state = zero_flow_state(grid_import_power_w=1500)
        judgement = judge_zero_flow_state(state)
        self.assertFalse(judgement.verified)
        self.assertIn("grid_import_passive_settlement", judgement.reason)

    def test_reject_unexpected_grid_export(self):
        state = zero_flow_state(grid_export_power_w=500)
        judgement = judge_zero_flow_state(state)
        self.assertFalse(judgement.verified)
        self.assertIn("grid_export_passive_settlement", judgement.reason)

    def test_reject_wrong_mode_echo_even_with_zero_powers(self):
        state = zero_flow_state(operating_mode="self_consumption")
        judgement = judge_zero_flow_state(state)
        self.assertFalse(judgement.verified)
        self.assertIn("setpoint_echo", judgement.reason)

    def test_reject_unavailable_and_nan_values(self):
        for bad in ("unavailable", "unknown", None, float("nan")):
            judgement = judge_zero_flow_state(zero_flow_state(battery_charging_power_w=bad))
            self.assertFalse(judgement.verified, bad)
            self.assertIsNone(judgement.checks["battery_charge_within_tolerance"]["observed"])
        judgement = judge_zero_flow_state(zero_flow_state(solar_power="unavailable"))
        self.assertFalse(judgement.verified)
        self.assertIn("grid_import_passive_settlement", judgement.reason)

    def test_missing_keys_do_not_raise(self):
        judgement = judge_zero_flow_state({})
        self.assertFalse(judgement.verified)

    def test_invalid_inputs_raise(self):
        with self.assertRaises(TypeError):
            judge_zero_flow_state("not a dict")
        with self.assertRaises(ValueError):
            judge_zero_flow_state(zero_flow_state(), battery_tolerance_w=-1)
        with self.assertRaises(ValueError):
            judge_zero_flow_state(zero_flow_state(), grid_tolerance_w=float("nan"))


class RestoreTests(unittest.TestCase):
    def test_self_consumption_baseline(self):
        plan = plan_restore({"operating_mode": "self_consumption"})
        self.assertTrue(plan.restorable)
        self.assertEqual(plan.operating_mode, "self_consumption")
        self.assertTrue(confirm_restored({"operating_mode": "self_consumption"}, plan))
        self.assertFalse(confirm_restored({"operating_mode": "third_party_control"}, plan))

    def test_third_party_baseline_restores_flow_and_power(self):
        baseline = {"operating_mode": "third_party_control", "grid_flow": "discharge", "power_control": "250"}
        plan = plan_restore(baseline)
        self.assertTrue(plan.restorable)
        self.assertEqual(plan.grid_flow, "discharge")
        self.assertEqual(plan.power_w, 250.0)
        self.assertTrue(confirm_restored(dict(baseline), plan))
        self.assertFalse(confirm_restored({**baseline, "grid_flow": "charge"}, plan))
        self.assertFalse(confirm_restored({**baseline, "power_control": 0}, plan))

    def test_unreadable_or_unknown_baseline_not_restorable(self):
        self.assertFalse(plan_restore({"operating_mode": "third_party_control",
                                       "grid_flow": "charge", "power_control": "unavailable"}).restorable)
        self.assertFalse(plan_restore({"operating_mode": "third_party_control",
                                       "grid_flow": "hold", "power_control": 0}).restorable)
        plan = plan_restore({"operating_mode": "manual"})
        self.assertFalse(plan.restorable)
        self.assertIn("manual", plan.reason)
        self.assertFalse(confirm_restored({"operating_mode": "manual"}, plan))
        with self.assertRaises(TypeError):
            plan_restore(None)


class CommandAndEvidenceTests(unittest.TestCase):
    def test_command_is_unverified_charge_zero(self):
        command = build_zero_flow_command()
        self.assertEqual((command.operating_mode, command.grid_flow, command.power_w),
                         ("third_party_control", "charge", 0.0))
        self.assertFalse(command.hardware_verified)

    def test_evidence_hash_is_order_independent(self):
        self.assertEqual(evidence_hash({"a": 1, "b": [2, 3]}), evidence_hash({"b": [2, 3], "a": 1}))
        self.assertNotEqual(evidence_hash({"a": 1}), evidence_hash({"a": 2}))


class ConfigConclusionTests(unittest.TestCase):
    def test_environment_machine_layout(self):
        config = {"environment_machine": {"stations": {"1": {"suffix": "1502"}, "2": {"suffix": "1503"}}}}
        updated = apply_zero_flow_conclusion(config, "1", True)
        self.assertTrue(updated["environment_machine"]["stations"]["1"]["zero_flow_verified"])
        self.assertNotIn("zero_flow_verified", updated["environment_machine"]["stations"]["2"])
        self.assertNotIn("zero_flow_verified", config["environment_machine"]["stations"]["1"])

    def test_top_level_layout_and_string_entry(self):
        updated = apply_zero_flow_conclusion({"stations": {"3": "1504"}}, "3", False)
        self.assertEqual(updated["stations"]["3"], {"suffix": "1504", "zero_flow_verified": False})

    def test_unknown_site_raises(self):
        with self.assertRaises(KeyError):
            apply_zero_flow_conclusion({"stations": {"1": {}}}, "9", True)
        with self.assertRaises(KeyError):
            apply_zero_flow_conclusion({}, "1", True)


if __name__ == "__main__":
    unittest.main()
