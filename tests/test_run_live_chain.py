"""A4 acceptance: step-4 chain dry-run end-to-end + four safety switches (V5 row 4)."""
from datetime import datetime, timezone
from pathlib import Path
import sys
import tempfile
import time
import threading
import unittest
from unittest.mock import patch

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from energy_lab.rolling import RollingPlan
from scripts.online.run_live import run_once, run_once_milp


class StubHA:
    def __init__(self, states):
        self.states = {str(k): (list(v) if isinstance(v, list) else [v])
                       for k, v in states.items()}
        self.calls = []

    def station_state(self, suffix):
        queue = self.states[str(suffix)]
        entry = queue.pop(0) if len(queue) > 1 else queue[0]
        return entry() if callable(entry) else entry

    def set_dispatch(self, suffix, flow, watts, live=False):
        self.calls.append(("set_dispatch", suffix, flow, watts, live))

    def set_self_consumption(self, suffix, live=False):
        self.calls.append(("set_self_consumption", suffix, live))


class StubSim:
    def __init__(self, prices):
        self._prices = prices

    def prices(self):
        return self._prices

    def current_price(self):
        # Independent explicit current slot, not selected by price value.
        return {"start": "2031-01-01T00:00:00Z", "end": "2031-01-01T01:00:00Z",
                "import_price": 0.3, "export_price": 0.1}


def make_prices(import_price=0.3):
    return {day: {"date": date, "points": [
        {"start": f"{date}T{hour:02d}:00:00Z",
         "end": (pd.Timestamp(f"{date}T{hour:02d}:00:00Z") + pd.Timedelta(hours=1)).isoformat(),
         "import_price": import_price, "export_price": 0.1}
        for hour in range(24)]}
        for day, date in (("today", "2031-01-01"), ("tomorrow", "2031-01-02"))}


def state(device_time="2031-01-01T00:15:00+00:00", soc="45", *, grid_flow="charge",
          power_control="0", charge_w=0.0, discharge_w=0.0, stale=False):
    changed = "2020-01-01T00:00:00+00:00" if stale else datetime.now(timezone.utc).isoformat()
    return {"soc": soc, "solar_power": "400", "home_load": "1000",
            "device_time": device_time, "device_time_last_changed": changed,
            "last_changed": changed, "operating_mode": "third_party_control",
            "grid_flow": grid_flow, "power_control": power_control,
            "battery_charging_power_w": charge_w, "battery_discharging_power_w": discharge_w,
            "grid_export_power_w": 0.0}


def cfg(verified=None):
    station = {"suffix": "1502"}
    if verified is not None:
        station["zero_flow_verified"] = verified
    return {"stations": {"1": station}, "stale_after_seconds": 15.0, "capacity_kwh": 14.0,
            "max_power_w": 5000.0, "soc_min": 0.0, "soc_max": 100.0, "solver_seconds": 1.0,
            "device_timezone": "UTC", "round_deadline_seconds": 5.0}


def plan(flow="idle", soc=45.0, charge=0.0, status="optimal"):
    frame = pd.DataFrame({"time": pd.to_datetime(["2031-01-01T00:15:00+00:00"]),
                          "flow": [flow], "charge_w": [charge], "discharge_w": [0.0],
                          "soc_end": [soc]})
    return RollingPlan(frame, 0.0, status, "mock")


class DryRunChainTests(unittest.TestCase):
    def run_chain(self, ha, config, sim, **kwargs):
        kwargs.setdefault("write_enabled", False)
        kwargs.setdefault("carry", {})
        with patch("energy_lab.shadow.optimize_window", return_value=kwargs.pop("mock_plan", plan())):
            return run_once_milp(config, ha, sim, **kwargs), kwargs["carry"]

    def test_per_station_solver_seconds_override(self):
        config = cfg(verified=True)
        config["stations"] = {"1": {"suffix": "1502", "zero_flow_verified": True},
                              "3": {"suffix": "1503", "zero_flow_verified": True,
                                    "solver_seconds": 2.5}}
        ha = StubHA({"1502": state(), "1503": state()})
        captured = []

        def fake_optimize(*args, **kwargs):
            captured.append(kwargs["time_limit_seconds"])
            return plan()

        with patch("energy_lab.shadow.optimize_window", side_effect=fake_optimize):
            run_once_milp(config, ha, StubSim(make_prices()), write_enabled=False, carry={})
        self.assertEqual(sorted(captured), [1.0, 2.5])

    def test_ha_snapshot_401_does_not_attempt_fallback_write(self):
        ha = StubHA({"1502": state()})
        failed_batch = {"points": None, "states": {},
                        "errors": {"1": "ha_snapshot_error:HTTPError:401 Unauthorized"},
                        "times": {}, "metadata": {}, "alignment_attempts": 1}
        with patch("scripts.online.run_live.read_anchored_tick", return_value=failed_batch):
            rows = run_once_milp(cfg(verified=True), ha, StubSim(make_prices()),
                                 write_enabled=True, carry={})
        self.assertEqual(rows[0]["status"], "station_error")
        self.assertEqual(rows[0]["writes_performed"], 0)
        self.assertEqual(ha.calls, [])

    def test_unknown_delivery_quarantines_next_round_without_more_writes(self):
        ha = StubHA({"1502": state()})
        carry = {}
        def fail(*args, **kwargs):
            from energy_lab.write_transaction import ACTIVE_WRITE
            ACTIVE_WRITE.get().steps.append({"service": "mock/first", "entity_id": "mock",
                                             "status": "attempted_unknown"})
            raise TimeoutError("mock_post_timeout")
        with patch("scripts.online.run_live.make_candidate", return_value={
                "status": "candidate_only", "device_time": "2031-01-01T00:15:00+00:00",
                "battery_flow": "charge", "battery_power_w": 100,
                "candidate": {"target_w": 100}}), \
             patch.object(ha, "set_dispatch", side_effect=fail) as dispatch:
            first = run_once_milp(cfg(verified=True), ha, StubSim(make_prices()),
                                  write_enabled=True, carry=carry)
            second = run_once_milp(cfg(verified=True), ha, StubSim(make_prices()),
                                   write_enabled=True, carry=carry)
        self.assertEqual(dispatch.call_count, 1)
        self.assertEqual(first[0]["status"], "delivery_failed")
        self.assertEqual(first[0]["writes_performed"], 0)
        self.assertEqual(first[0]["write_requests_attempted"], 1)
        self.assertEqual(second[0]["status"], "reconciliation_required")
        self.assertEqual(second[0]["writes_performed"], 0)
        self.assertTrue(second[0]["read_only_state_observed"])

    def test_parallel_delivery_failure_quarantines_all_attempting_sites(self):
        ha = StubHA({"1502": state(), "1503": state()})
        carry = {}
        def fail(*args, **kwargs):
            from energy_lab.write_transaction import ACTIVE_WRITE
            ACTIVE_WRITE.get().steps.append({"service": "mock/first", "entity_id": "mock",
                                             "status": "attempted_unknown"})
            raise TimeoutError("mock_post_timeout")
        with patch("scripts.online.run_live.make_candidate", return_value={
                "status": "candidate_only", "device_time": "2031-01-01T00:15:00+00:00",
                "battery_flow": "charge", "battery_power_w": 100,
                "candidate": {"target_w": 100}}), \
             patch.object(ha, "set_dispatch", side_effect=fail):
            config = cfg(verified=True)
            config["stations"] = {"1": {"suffix": "1502", "zero_flow_verified": True},
                                   "2": {"suffix": "1503", "zero_flow_verified": True}}
            rows = run_once_milp(config, ha, StubSim(make_prices()),
                                 write_enabled=True, carry=carry)
        # Parallel delivery fires both sites concurrently before any halt is
        # observed, so a shared HA outage quarantines every site that touched
        # the degraded path (not just the first to fail).
        self.assertEqual([row["status"] for row in rows],
                         ["delivery_failed", "delivery_failed"])
        self.assertTrue(carry["global_delivery_halt"])
        self.assertEqual(set(carry["delivery_unknown"]), {"1", "2"})
        self.assertEqual(ha.calls, [])

    def test_global_delivery_halt_recovers_after_two_clean_rounds(self):
        ha = StubHA({"1502": state(), "1503": state()})
        carry = {}

        def partial_fail(suffix, flow, watts, live=False):
            if suffix == "1502":
                from energy_lab.write_transaction import ACTIVE_WRITE
                ACTIVE_WRITE.get().steps.append({"service": "mock/first", "entity_id": "mock",
                                                 "status": "attempted_unknown"})
                raise TimeoutError("mock_post_timeout")
            ha.calls.append(("set_dispatch", suffix, flow, watts, live))

        config = cfg(verified=True)
        config["stations"] = {"1": {"suffix": "1502", "zero_flow_verified": True},
                               "2": {"suffix": "1503", "zero_flow_verified": True}}
        candidate = {"status": "candidate_only", "device_time": "2031-01-01T00:15:00+00:00",
                     "battery_flow": "charge", "battery_power_w": 100,
                     "candidate": {"target_w": 100}}
        with patch("scripts.online.run_live.make_candidate", return_value=candidate), \
             patch.object(ha, "set_dispatch", side_effect=partial_fail):
            first = run_once_milp(config, ha, StubSim(make_prices()),
                                  write_enabled=True, carry=carry)
        self.assertTrue(carry["global_delivery_halt"])
        self.assertEqual([row["status"] for row in first],
                         ["delivery_failed", "dispatched"])

        # One clean round: not yet enough to lift the halt; the healthy site is
        # write-suspended while the quarantined site stays reconciliation-required.
        with patch("scripts.online.run_live.make_candidate", return_value=candidate):
            run_once_milp(config, ha, StubSim(make_prices()), write_enabled=True, carry=carry)
        self.assertTrue(carry["global_delivery_halt"])

        # Second clean round lifts the halt; the quarantined site stays
        # reconciliation-required while the healthy site resumes writing.
        with patch("scripts.online.run_live.make_candidate", return_value=candidate):
            rows = run_once_milp(config, ha, StubSim(make_prices()),
                                 write_enabled=True, carry=carry)
        self.assertFalse(carry["global_delivery_halt"])
        self.assertEqual([row["status"] for row in rows],
                         ["reconciliation_required", "dispatched"])

    def test_three_sites_dispatch_concurrently_in_one_round(self):
        ha = StubHA({"1502": state(), "1503": state(), "1504": state()})
        candidate = {"status": "candidate_only", "device_time": "2031-01-01T00:15:00+00:00",
                     "battery_flow": "charge", "battery_power_w": 100,
                     "candidate": {"target_w": 100}}
        config = cfg(verified=True)
        config["stations"] = {str(i): {"suffix": f"150{i}", "zero_flow_verified": True}
                              for i in (2, 3, 4)}
        with patch("scripts.online.run_live.make_candidate", return_value=candidate):
            rows = run_once_milp(config, ha, StubSim(make_prices()),
                                 write_enabled=True, carry={})
        # Results keep the sorted site order; all three deliveries happened.
        self.assertEqual([row["site"] for row in rows], ["2", "3", "4"])
        self.assertEqual([row["status"] for row in rows], ["dispatched"] * 3)
        self.assertEqual(sorted(c[1] for c in ha.calls), ["1502", "1503", "1504"])
        self.assertTrue(all(c[2] == "charge" and c[3] == 100.0 and c[4] is True
                            for c in ha.calls))

    def test_dry_run_verified_site_full_chain_zero_writes(self):
        with tempfile.TemporaryDirectory() as temp:
            audit = Path(temp) / "audit.csv"
            results, carry = self.run_chain(StubHA({"1502": state()}), cfg(verified=True),
                                            StubSim(make_prices()), audit_csv=audit)
            row = results[0]
            self.assertEqual(row["status"], "zero_flow_dispatched")
            self.assertEqual(row["writes_performed"], 0)
            self.assertEqual(row["readback_verdict"], "not_sent_dry_run")
            self.assertEqual((row["command_mode"], row["command_flow"], row["target_w"]),
                             ("zero_flow_command", "charge", 0.0))
            self.assertIsNone(row["guard_action"])
            self.assertIn("1", carry["previous"])
            logged = pd.read_csv(audit)
            self.assertEqual(list(logged["status"]), ["zero_flow_dispatched"])
            for field in ("planned_flow", "command_mode", "command_flow", "target_w",
                          "readback_verdict", "realized_soc"):
                self.assertIn(field, logged.columns)

    def test_dry_run_unverified_site_idle_self_use_without_writes(self):
        ha = StubHA({"1502": state()})
        results, _ = self.run_chain(ha, cfg(), StubSim(make_prices()))
        self.assertEqual(results[0]["status"], "idle_self_use_mode")
        self.assertEqual(results[0]["writes_performed"], 0)
        self.assertEqual(ha.calls, [])

    def test_dispatch_strategy_entry_is_unchanged(self):
        results = run_once(cfg(), StubHA({"1502": state()}), StubSim(make_prices()),
                           write_enabled=False)
        self.assertEqual(results[0]["site"], "1")
        self.assertIn("command_flow", results[0])

    def test_stale_state_never_enters_the_chain(self):
        results, _ = self.run_chain(StubHA({"1502": state(stale=True)}), cfg(verified=True),
                                    StubSim(make_prices()))
        self.assertEqual(results[0]["status"], "stale_or_unavailable")

    def test_late_solver_candidate_is_never_written(self):
        ha = StubHA({"1502": state()})
        with patch("scripts.online.run_live.make_candidate", side_effect=lambda *a, **k: (time.sleep(.08), {
                "status": "candidate_only", "device_time": "2031-01-01T00:15:00+00:00",
                "battery_flow": "charge", "battery_power_w": 100,
                "candidate": {"target_w": 100}})[1]):
            rows = run_once_milp({**cfg(verified=True), "round_deadline_seconds": .03},
                                 ha, StubSim(make_prices()), write_enabled=True, carry={})
        self.assertEqual(ha.calls, [])
        self.assertEqual(rows[0]["writes_performed"], 0)
        self.assertIn(rows[0]["execution_safety_rejection"],
                      ("solver_deadline_exceeded", "round_deadline_exceeded"))
        self.assertEqual(rows[0]["status"], "fallback_self_use_mode")
        time.sleep(.09)

    def test_superseded_round_cannot_write_old_candidate(self):
        ha = StubHA({"1502": state()})
        entered = threading.Event()
        release = threading.Event()
        carry = {}
        previous_rows = []
        candidate = {"status": "candidate_only", "device_time": "2031-01-01T00:15:00+00:00",
                     "battery_flow": "charge", "battery_power_w": 100,
                     "candidate": {"target_w": 100}}
        def slow(*args, **kwargs):
            entered.set()
            release.wait(1)
            return candidate
        config = {**cfg(verified=True), "round_deadline_seconds": .5}
        with patch("scripts.online.run_live.make_candidate", side_effect=slow):
            previous = threading.Thread(target=lambda: previous_rows.extend(run_once_milp(
                config, ha, StubSim(make_prices()), write_enabled=True, carry=carry)))
            previous.start()
            self.assertTrue(entered.wait(.3))
            newer = run_once_milp(config, ha, StubSim(make_prices()),
                                  write_enabled=True, carry=carry)
            release.set()
            previous.join(1)
        self.assertFalse(previous.is_alive())
        self.assertEqual(ha.calls, [])
        self.assertEqual(newer[0]["writes_performed"], 0)
        self.assertEqual(previous_rows[0]["execution_safety_rejection"], "superseded_round")

    def assert_online_price_anomaly_falls_back(self, prices, expected_reason):
        ha = StubHA({"1502": state()})
        with tempfile.TemporaryDirectory() as temp, patch("energy_lab.shadow.optimize_window") as optimize:
            audit = Path(temp) / "audit.csv"
            results = run_once_milp(cfg(verified=True), ha, StubSim(prices),
                                    write_enabled=True, carry={}, audit_csv=audit)
            logged = pd.read_csv(audit).iloc[0]
        row = results[0]
        self.assertEqual(row["status"], "fallback_self_use_mode")
        self.assertFalse(row["optimizer_attributed"])
        self.assertEqual(row["attribution"], "non-optimizer-attributed")
        self.assertEqual(row["reason"], expected_reason)
        self.assertEqual(row["writes_performed"], 1)
        self.assertEqual(logged["reason"], expected_reason)
        self.assertEqual(logged["attribution"], "non-optimizer-attributed")
        self.assertEqual(ha.calls, [("set_self_consumption", "1502", True)])
        optimize.assert_not_called()

    def test_missing_online_price_falls_back_and_is_not_optimized(self):
        prices = make_prices()
        del prices["today"]["points"][3]
        self.assert_online_price_anomaly_falls_back(
            prices, "price_anchor_rejected:missing_or_noncontinuous_price_slot")

    def test_nonfinite_online_price_falls_back_and_is_not_optimized(self):
        prices = make_prices()
        prices["today"]["points"][3]["import_price"] = float("nan")
        self.assert_online_price_anomaly_falls_back(prices, "non_finite_price:import_price")

    def test_device_time_misalignment_falls_back_and_is_not_optimized(self):
        ha = StubHA({"1502": state(device_time="2031-01-03T00:15:00+00:00")})
        with tempfile.TemporaryDirectory() as temp, patch("energy_lab.shadow.optimize_window") as optimize:
            audit = Path(temp) / "audit.csv"
            results = run_once_milp(cfg(verified=True), ha, StubSim(make_prices()),
                                    write_enabled=True, carry={}, audit_csv=audit)
            logged = pd.read_csv(audit).iloc[0]
        row = results[0]
        self.assertEqual(row["status"], "fallback_self_use_mode")
        self.assertFalse(row["optimizer_attributed"])
        self.assertEqual(row["reason"], "device_api_date_hour_conflict")
        self.assertEqual(row["writes_performed"], 1)
        self.assertEqual(logged["reason"], row["reason"])
        self.assertEqual(logged["attribution"], "non-optimizer-attributed")
        self.assertEqual(ha.calls, [("set_self_consumption", "1502", True)])
        optimize.assert_not_called()

    def test_timezone_ambiguous_online_tick_falls_back(self):
        ha = StubHA({"1502": state(device_time="2031-01-01T00:15:00")})
        config = cfg(verified=True)
        del config["device_timezone"]
        sim = StubSim(make_prices())
        sim.current_price = lambda: {}
        with patch("energy_lab.shadow.optimize_window") as optimize:
            results = run_once_milp(config, ha, sim,
                                    write_enabled=True, carry={})
        self.assertEqual(results[0]["reason"],
                         "price_anchor_rejected:current_slot_missing_or_invalid")
        self.assertEqual(ha.calls, [("set_self_consumption", "1502", True)])
        optimize.assert_not_called()

    def test_aware_device_time_without_trusted_configured_timezone_falls_back(self):
        ha = StubHA({"1502": state(device_time="2031-01-01T00:15:00+00:00")})
        config = cfg(verified=True)
        del config["device_timezone"]
        sim = StubSim(make_prices())
        sim.current_price = lambda: {}
        with patch("energy_lab.shadow.optimize_window") as optimize:
            results = run_once_milp(config, ha, sim,
                                    write_enabled=True, carry={})
        self.assertEqual(results[0]["reason"],
                         "price_anchor_rejected:current_slot_missing_or_invalid")
        self.assertEqual(ha.calls, [("set_self_consumption", "1502", True)])
        optimize.assert_not_called()

    def test_invalid_device_timezone_falls_back_without_solver(self):
        ha = StubHA({"1502": state()})
        config = {**cfg(verified=True), "device_timezone": "Not/A_Timezone"}
        with patch("energy_lab.shadow.optimize_window") as optimize:
            rows = run_once_milp(config, ha, StubSim(make_prices()),
                                 write_enabled=True, carry={})
        self.assertEqual(rows[0]["reason"], "device_time_misaligned:invalid_device_timezone")
        self.assertFalse(rows[0]["optimizer_attributed"])
        self.assertEqual(ha.calls, [("set_self_consumption", "1502", True)])
        optimize.assert_not_called()

    def test_dispatch_without_trusted_timezone_blocks_economic_action(self):
        ha = StubHA({"1502": state()})
        config = cfg()
        del config["device_timezone"]
        sim = StubSim(make_prices())
        sim.current_price = lambda: {}
        with patch("scripts.online.run_live.dispatch") as dispatch:
            rows = run_once(config, ha, sim, write_enabled=True)
        self.assertEqual(rows[0]["status"], "economic_action_blocked")
        self.assertEqual(rows[0]["writes_performed"], 0)
        self.assertEqual(ha.calls, [])
        dispatch.assert_not_called()


class FourSafetySwitchTests(unittest.TestCase):
    """Fourth switch: live charge=0 idle requires site zero_flow_verified."""

    def run_live(self, ha, config, mock_plan, carry=None):
        with patch("energy_lab.shadow.optimize_window", return_value=mock_plan):
            return run_once_milp(config, ha, StubSim(make_prices()), write_enabled=True,
                                 carry=carry if carry is not None else {})

    def test_live_idle_uses_explicit_self_use_when_site_unverified(self):
        ha = StubHA({"1502": state()})
        results = self.run_live(ha, cfg(), plan())
        self.assertEqual(results[0]["status"], "idle_self_use_mode")
        self.assertEqual(results[0]["writes_performed"], 1)
        self.assertEqual(ha.calls, [("set_self_consumption", "1502", True)])

    def test_live_idle_sends_zero_charge_when_verified(self):
        ha = StubHA({"1502": state()})
        results = self.run_live(ha, cfg(verified=True), plan())
        self.assertEqual(results[0]["status"], "zero_flow_dispatched")
        self.assertEqual(results[0]["readback_verdict"], "pending_next_tick")
        self.assertEqual(ha.calls, [("set_dispatch", "1502", "charge", 0.0, True)])

    def test_live_non_optimal_takes_explicit_self_use_fallback(self):
        ha = StubHA({"1502": state()})
        failed = RollingPlan(pd.DataFrame(), float("nan"), "limit_reached", "mock")
        results = self.run_live(ha, cfg(verified=True), failed)
        self.assertEqual(results[0]["status"], "fallback_self_use_mode")
        self.assertFalse(results[0]["optimizer_attributed"])
        self.assertEqual(ha.calls, [("set_self_consumption", "1502", True)])

    def test_verified_time_limit_incumbent_keeps_status_gap_and_audit_marker(self):
        ha = StubHA({"1502": state()})
        incumbent = plan(flow="charge", charge=2000., soc=50., status="limit_reached")
        incumbent.suboptimal = True
        incumbent.mip_gap = .125
        with tempfile.TemporaryDirectory() as temp:
            audit = Path(temp) / "audit.csv"
            with patch("energy_lab.shadow.optimize_window", return_value=incumbent):
                results = run_once_milp(cfg(verified=True), ha, StubSim(make_prices()),
                                        write_enabled=True, carry={}, audit_csv=audit)
            logged = pd.read_csv(audit).iloc[0]
        row = results[0]
        self.assertEqual(row["status"], "dispatched")
        self.assertEqual(row["solver_status"], "limit_reached")
        self.assertTrue(row["accepted_incumbent"])
        self.assertEqual(row["mip_gap"], .125)
        self.assertTrue(logged["accepted_incumbent"])
        self.assertEqual(logged["mip_gap"], .125)

    def test_live_dispatch_sends_setpoint(self):
        ha = StubHA({"1502": state()})
        results = self.run_live(ha, cfg(), plan(flow="charge", charge=2000.0, soc=50.0))
        self.assertEqual(results[0]["status"], "dispatched")
        self.assertEqual(ha.calls, [("set_dispatch", "1502", "charge", 2000.0, True)])


class NextTickReadbackAndGuardTests(unittest.TestCase):
    def test_guard_revokes_verification_and_self_uses_the_next_idle(self):
        ha = StubHA({"1502": [state(soc="45"),
                              lambda: state(device_time="2031-01-01T00:30:00+00:00", soc="50")]})
        carry: dict = {}
        with patch("energy_lab.shadow.optimize_window", return_value=plan(soc=45.0)):
            first = run_once_milp(cfg(verified=True), ha, StubSim(make_prices()),
                                  write_enabled=True, carry=carry)
            second = run_once_milp(cfg(verified=True), ha, StubSim(make_prices()),
                                   write_enabled=True, carry=carry)
        self.assertEqual(first[0]["status"], "zero_flow_dispatched")
        self.assertEqual(second[0]["previous_readback_verdict"], "verified")
        self.assertEqual(second[0]["guard_action"], "revoke_zero_flow_verified")
        # The revoked site switches to explicit self-use on the next idle,
        # never a stale-command refusal.
        self.assertEqual(second[0]["status"], "idle_self_use_mode")
        self.assertFalse(carry["verified"]["1"])
        # Two writes: the first zero-flow command, then the self-use switch.
        self.assertEqual(ha.calls, [("set_dispatch", "1502", "charge", 0.0, True),
                                    ("set_self_consumption", "1502", True)])

    def test_dispatch_divergence_is_record_only_and_never_revokes(self):
        ha = StubHA({"1502": [state(soc="45"),
                              lambda: state(device_time="2031-01-01T00:30:00+00:00", soc="53",
                                            power_control="2000", charge_w=2000.0)]})
        carry: dict = {}
        with patch("energy_lab.shadow.optimize_window",
                   side_effect=[plan(flow="charge", charge=2000.0, soc=50.0), plan(soc=53.0)]):
            first = run_once_milp(cfg(verified=True), ha, StubSim(make_prices()),
                                  write_enabled=True, carry=carry)
            second = run_once_milp(cfg(verified=True), ha, StubSim(make_prices()),
                                   write_enabled=True, carry=carry)
        self.assertEqual(first[0]["status"], "dispatched")
        self.assertEqual(second[0]["previous_readback_verdict"], "verified")
        self.assertEqual(second[0]["guard_action"], "record_only")
        self.assertTrue(carry["verified"]["1"])
        # Verification survived, so the second idle still sends charge=0.
        self.assertEqual(second[0]["status"], "zero_flow_dispatched")
        self.assertEqual(len(ha.calls), 2)


if __name__ == "__main__":
    unittest.main()
