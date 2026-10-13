"""V1 step-2 execute_candidate decision table + V4 frozen-snapshot end-to-end."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from energy_lab.execute import AUDIT_FIELDS, execute_candidate
from energy_lab.rolling import RollingPlan
from energy_lab.shadow import daily_points, make_candidate


class StubHA:
    def __init__(self):
        self.calls = []

    def set_dispatch(self, suffix, flow, watts, live=False):
        self.calls.append(("set_dispatch", suffix, flow, watts, live))

    def set_self_consumption(self, suffix, live=False):
        self.calls.append(("set_self_consumption", suffix, live))


def dispatch_candidate(flow="charge", power=2000.):
    return {"status": "candidate_only",
            "candidate": {"representable": True, "flow": flow, "target_w": power,
                          "reason": "battery_charge_setpoint"},
            "battery_flow": flow, "battery_power_w": power,
            "device_time": "2031-01-01T00:15:00+00:00",
            "planned_soc_after_15min": 50.0, "solver_status": "optimal",
            "writes_performed": 0}


def idle_candidate(mapping_flow="charge", mapping_target=0.):
    return {"status": "candidate_only",
            "candidate": {"representable": True, "flow": mapping_flow, "target_w": mapping_target,
                          "reason": "idle_zero_flow_command_unverified"},
            "battery_flow": "idle", "battery_power_w": 0.0,
            "device_time": "2031-01-01T00:15:00+00:00",
            "planned_soc_after_15min": 45.0, "solver_status": "optimal",
            "writes_performed": 0}


def fallback_candidate(status="limit_reached"):
    return {"status": status, "candidate": None, "solver_status": status,
            "fallback": "no_new_command_requires_supervised_policy",
            "device_time": "2031-01-01T00:15:00+00:00", "writes_performed": 0}


def readback_state(flow="charge", target=2000., soc=50., mode="third_party_control"):
    return {"last_changed": "2026-10-13T00:00:20Z", "device_time": "2031-01-01T00:30:00+00:00",
            "operating_mode": mode, "grid_flow": flow, "power_control": target,
            "grid_export_power_w": target if flow == "discharge" else 0.0,
            "battery_charging_power_w": target if flow == "charge" else 0.0,
            "battery_discharging_power_w": 0.0, "soc": str(soc)}


SENT = dict(issued_at="2026-10-13T00:00:05Z", now="2026-10-13T00:00:21Z")


class DispatchBranchTests(unittest.TestCase):
    def test_charge_dispatch_sends_command_and_verifies_readback(self):
        ha = StubHA()
        record = execute_candidate(dispatch_candidate(), suffix="1502", ha=ha, live=True,
                                   readback_state=readback_state(), **SENT)
        self.assertEqual(ha.calls, [("set_dispatch", "1502", "charge", 2000., True)])
        self.assertEqual(record["status"], "dispatched")
        self.assertEqual(record["command_mode"], "dispatch")
        self.assertEqual(record["command_flow"], "charge")
        self.assertEqual(record["target_w"], 2000.)
        self.assertEqual(record["planned_flow"], "charge")
        self.assertEqual(record["readback_verdict"], "verified")
        self.assertEqual(record["realized_soc"], 50.)
        self.assertTrue(record["optimizer_attributed"])
        self.assertEqual(record["writes_performed"], 1)

    def test_discharge_dispatch_uses_grid_export_target(self):
        ha = StubHA()
        record = execute_candidate(dispatch_candidate("discharge", 3000.), suffix="1502", ha=ha,
                                   live=True, readback_state=readback_state("discharge", 3000.),
                                   **SENT)
        self.assertEqual(ha.calls, [("set_dispatch", "1502", "discharge", 3000., True)])
        self.assertEqual(record["readback_verdict"], "verified")

    def test_audit_record_has_exact_fields(self):
        record = execute_candidate(dispatch_candidate(), suffix="1502", ha=StubHA(), live=True,
                                   readback_state=readback_state(), **SENT)
        self.assertEqual(set(record), set(AUDIT_FIELDS))
        self.assertEqual(record["site"], "1502")


class IdleBranchTests(unittest.TestCase):
    def test_verified_site_sends_zero_charge_command(self):
        for mapping_flow, mapping_target in (("charge", 0.), ("self_consumption", 0.)):
            ha = StubHA()
            record = execute_candidate(idle_candidate(mapping_flow, mapping_target),
                                       suffix="1502", ha=ha, zero_flow_verified=True, live=True,
                                       readback_state=readback_state("charge", 0., soc=45.),
                                       **SENT)
            # The decision table is authoritative: a verified idle is executed
            # as charge=0 even if the candidate carried another idle mapping.
            self.assertEqual(ha.calls, [("set_dispatch", "1502", "charge", 0., True)])
            self.assertEqual(record["status"], "zero_flow_dispatched")
            self.assertEqual(record["command_mode"], "zero_flow_command")
            self.assertEqual(record["readback_verdict"], "verified")
            self.assertTrue(record["optimizer_attributed"])

    def test_unverified_site_refuses_without_silent_downgrade(self):
        ha = StubHA()
        record = execute_candidate(idle_candidate(), suffix="1502", ha=ha,
                                   zero_flow_verified=False, live=True,
                                   readback_state=readback_state("charge", 0.), **SENT)
        self.assertEqual(ha.calls, [])
        self.assertEqual(record["status"], "idle_unverified_noop")
        self.assertEqual(record["writes_performed"], 0)
        self.assertIsNone(record["command_flow"])
        self.assertIsNone(record["command_mode"])
        self.assertEqual(record["readback_verdict"], "not_applicable_no_command")
        self.assertFalse(record["optimizer_attributed"])

    def test_unverified_refusal_is_the_default(self):
        record = execute_candidate(idle_candidate(), suffix="1502", ha=StubHA(), live=True)
        self.assertEqual(record["status"], "idle_unverified_noop")


class FallbackBranchTests(unittest.TestCase):
    def test_fallback_sends_explicit_self_use_and_is_not_optimizer_attributed(self):
        ha = StubHA()
        record = execute_candidate(fallback_candidate(), suffix="1502", ha=ha, live=True,
                                   readback_state=readback_state(mode="self_consumption"), **SENT)
        self.assertEqual(ha.calls, [("set_self_consumption", "1502", True)])
        self.assertEqual(record["status"], "fallback_self_use_mode")
        self.assertEqual(record["command_mode"], "self_use_mode")
        self.assertEqual(record["command_flow"], "self_consumption")
        self.assertEqual(record["readback_verdict"], "mode_echo_verified")
        self.assertFalse(record["optimizer_attributed"])
        self.assertEqual(record["solver_status"], "limit_reached")
        self.assertEqual(record["reason"], "no_new_command_requires_supervised_policy")

    def test_blocked_snapshot_also_takes_the_explicit_fallback(self):
        ha = StubHA()
        record = execute_candidate({"status": "blocked", "reason": "stale_snapshot"},
                                   suffix="1502", ha=ha, live=True)
        self.assertEqual(ha.calls, [("set_self_consumption", "1502", True)])
        self.assertEqual(record["status"], "fallback_self_use_mode")
        self.assertEqual(record["reason"], "stale_snapshot")

    def test_fallback_mode_echo_mismatch_is_recorded(self):
        record = execute_candidate(fallback_candidate(), suffix="1502", ha=StubHA(), live=True,
                                   readback_state=readback_state(mode="third_party_control"),
                                   **SENT)
        self.assertEqual(record["readback_verdict"], "mode_echo_mismatch")


class DryRunAndReadbackHonestyTests(unittest.TestCase):
    def test_dry_run_writes_nothing_and_marks_unsent(self):
        ha = StubHA()
        for candidate in (dispatch_candidate(), idle_candidate(), fallback_candidate()):
            record = execute_candidate(candidate, suffix="1502", ha=ha, zero_flow_verified=True,
                                       live=False, readback_state=readback_state(), **SENT)
            self.assertEqual(record["writes_performed"], 0)
            self.assertEqual(record["readback_verdict"], "not_sent_dry_run")
        self.assertEqual(ha.calls, [])

    def test_live_without_readback_is_pending_not_verified(self):
        record = execute_candidate(dispatch_candidate(), suffix="1502", ha=StubHA(), live=True,
                                   readback_state=None)
        self.assertEqual(record["readback_verdict"], "pending_next_tick")

    def test_readback_failures_are_recorded_not_faked(self):
        mismatch = readback_state(flow="discharge")
        record = execute_candidate(dispatch_candidate(), suffix="1502", ha=StubHA(), live=True,
                                   readback_state=mismatch, **SENT)
        self.assertEqual(record["readback_verdict"], "command_state_mismatch")
        stale = dict(readback_state(), last_changed="2026-10-13T00:00:00Z")
        record = execute_candidate(dispatch_candidate(), suffix="1502", ha=StubHA(), live=True,
                                   readback_state=stale, **SENT)
        self.assertEqual(record["readback_verdict"], "stale_readback")

    def test_unknown_planned_flow_raises_instead_of_implicit_handling(self):
        candidate = dispatch_candidate()
        candidate["battery_flow"] = "hold"
        with self.assertRaisesRegex(ValueError, "unknown planned flow"):
            execute_candidate(candidate, suffix="1502", ha=StubHA(), live=True)

    def test_live_requires_a_client(self):
        with self.assertRaisesRegex(ValueError, "HA client"):
            execute_candidate(dispatch_candidate(), suffix="1502", live=True)


class FrozenSnapshotEndToEndTests(unittest.TestCase):
    """V4: make_candidate -> execute_candidate -> simulated readback chain."""

    def snapshot(self):
        prices = {day: {"date": date, "points": [
            {"start": f"{date}T{hour:02d}:00:00Z", "import_price": 0.3, "export_price": 0.1}
            for hour in range(24)]}
            for day, date in (("today", "2031-01-01"), ("tomorrow", "2031-01-02"))}
        state = {"soc": "45", "solar_power": "400", "home_load": "1000",
                 "device_time": "2031-01-01T00:15:00+00:00",
                 "device_time_last_changed": "2026-10-13T00:00:04Z"}
        return state, daily_points(prices)

    def test_idle_chain_live_and_dry_run(self):
        state, points = self.snapshot()
        plan = RollingPlan(pd.DataFrame({"flow": ["idle"], "charge_w": [0.], "discharge_w": [0.],
                                         "soc_end": [45.]}), 0., "optimal", "frozen")
        with patch("energy_lab.shadow.optimize_window", return_value=plan):
            candidate = make_candidate(state, points, idle_candidate="zero_charge_unverified")
        self.assertEqual(candidate["writes_performed"], 0)

        ha = StubHA()
        record = execute_candidate(candidate, suffix="1502", ha=ha, zero_flow_verified=True,
                                   live=True,
                                   readback_state=readback_state("charge", 0., soc=45.), **SENT)
        self.assertEqual(record["status"], "zero_flow_dispatched")
        self.assertEqual(record["readback_verdict"], "verified")

        ha = StubHA()
        record = execute_candidate(candidate, suffix="1502", ha=ha, zero_flow_verified=True,
                                   live=False)
        self.assertEqual(ha.calls, [])
        self.assertEqual(record["writes_performed"], 0)
        self.assertEqual(record["readback_verdict"], "not_sent_dry_run")


if __name__ == "__main__":
    unittest.main()
