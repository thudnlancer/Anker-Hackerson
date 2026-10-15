"""API-current anchoring acceptance; all inputs here are injected, not live M3."""
import sys
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_run_live_chain import StubHA, StubSim, cfg, make_prices, plan, state
from run_live import run_once, run_once_milp, price_window, validate_milp_price_window
from energy_lab.price_anchor import anchor_prices, anchor_device_time


def slot(start="2031-01-01T00:00:00Z", price=.3):
    return {"start": start, "end": (pd.Timestamp(start) + pd.Timedelta(hours=1)).isoformat(),
            "import_price": price, "export_price": .1}


class SequenceSim(StubSim):
    def __init__(self, currents, prices=None):
        super().__init__(make_prices() if prices is None else prices)
        self.currents = list(currents)
        self.reads = 0

    def current_price(self):
        self.reads += 1
        value = self.currents.pop(0) if len(self.currents) > 1 else self.currents[0]
        if isinstance(value, Exception):
            raise value
        return value


class PriceAnchorTests(unittest.TestCase):
    def execute(self, sim=None, *, raw="2031-01-01 00:15:00", config=None, expected=None):
        config = {**cfg(verified=True), "device_timezone": None} if config is None else config
        ha = StubHA({"1502": state(device_time=raw)})
        with tempfile.TemporaryDirectory() as temp, patch(
                "energy_lab.shadow.optimize_window", return_value=plan()) as optimizer:
            audit = Path(temp) / "audit.csv"
            rows = run_once_milp(config, ha, sim or SequenceSim([slot()]),
                                 write_enabled=False, carry={}, audit_csv=audit)
            logged = pd.read_csv(audit)
        self.assertEqual(ha.calls, [])
        self.assertEqual(rows[0]["writes_performed"], 0)
        self.assertEqual(logged.iloc[0]["ha_device_time_raw"], raw)
        if expected:
            optimizer.assert_not_called()
            self.assertIn(expected, rows[0]["reason"])
            self.assertEqual(rows[0]["attribution"], "non-optimizer-attributed")
        else:
            optimizer.assert_called_once()
            self.assertEqual(rows[0]["alignment_source"], "api_current_slot")
        return rows[0]

    def test_no_configured_timezone_can_optimize_from_unique_current(self):
        row = self.execute()
        self.assertEqual(row["resolved_device_time"], "2031-01-01T00:15:00+00:00")
        self.assertEqual(row["alignment_attempts"], 1)

    def test_same_price_every_hour_does_not_change_current_slot(self):
        row = self.execute(SequenceSim([slot("2031-01-01T08:00:00Z")]), raw="2031-01-01 08:15:00")
        self.assertEqual(pd.Timestamp(row["selected_price_slot"]).hour, 8)

    def test_race_retries_then_accepts_stable_next_hour(self):
        sim = SequenceSim([slot(), slot("2031-01-01T01:00:00Z"),
                           slot("2031-01-01T01:00:00Z"), slot("2031-01-01T01:00:00Z")])
        row = self.execute(sim, raw="2031-01-01 01:15:00")
        self.assertEqual(row["alignment_attempts"], 2)
        self.assertEqual(sim.reads, 4)

    def test_persistent_cross_slot_race_rejected_after_three_attempts(self):
        sim = SequenceSim([slot(), slot("2031-01-01T01:00:00Z")] * 3)
        self.execute(sim, expected="current_slot_changed_during_read")
        self.assertEqual(sim.reads, 6)

    def test_missing_current_rejected_even_with_explicit_timezone(self):
        self.execute(SequenceSim([{}]), config=cfg(verified=True),
                     expected="current_slot_missing_or_invalid")

    def test_current_transport_failure_safely_rejected(self):
        self.execute(SequenceSim([RuntimeError("injected_current_unavailable")]),
                     expected="injected_current_unavailable")

    def test_current_price_mismatch_is_consistency_error_not_a_locator(self):
        self.execute(SequenceSim([slot(price=.7)]), expected="current_slot_price_conflict")

    def test_device_date_hour_conflict(self):
        self.execute(raw="2031-01-01 08:15:00", expected="device_api_date_hour_conflict")

    def test_explicit_timezone_conflicts_with_current_slot(self):
        self.execute(config={**cfg(verified=True), "device_timezone": "Asia/Shanghai"},
                     expected="explicit_timezone_api_conflict")

    def test_duplicate_slot_refuses_even_if_price_identical(self):
        prices = make_prices()
        prices["today"]["points"].append(dict(prices["today"]["points"][0]))
        self.execute(SequenceSim([slot()], prices), expected="duplicate_price_slot")

    def test_missing_current_slot_refuses(self):
        prices = make_prices()
        del prices["today"]["points"][0]
        self.execute(SequenceSim([slot()], prices), expected="current_slot_not_unique")

    def test_missing_future_hour_refuses(self):
        prices = make_prices()
        del prices["tomorrow"]["points"][0]
        self.execute(SequenceSim([slot()], prices), expected="missing_or_noncontinuous_price_slot")

    def test_price_current_read_fails_mid_tick_then_recovers(self):
        sim = SequenceSim([slot(), RuntimeError("temporary"), slot(), slot()])
        row = self.execute(sim)
        self.assertEqual(row["alignment_attempts"], 2)

    def test_current_slot_duplicate_with_different_price_refuses(self):
        prices = make_prices()
        duplicate = dict(prices["today"]["points"][0])
        duplicate["import_price"] = .9
        prices["today"]["points"].append(duplicate)
        self.execute(SequenceSim([slot()], prices), expected="duplicate_price_slot")

    def test_current_slot_with_missing_price_refuses(self):
        prices = make_prices()
        del prices["today"]["points"][0]["import_price"]
        self.execute(SequenceSim([slot()], prices), expected="missing_price_data:import_price")

    def test_api_offset_is_not_an_iana_timezone(self):
        anchor = pd.Timestamp("2031-01-01T00:00:00+08:00")
        with self.assertRaisesRegex(ValueError, "explicit_timezone_api_conflict"):
            anchor_device_time("2031-01-01 00:15:00", anchor, "UTC")

    def test_partial_start_requires_25_prices_for_full_24h(self):
        prices = make_prices()
        prices.pop("tomorrow")
        self.execute(SequenceSim([slot()], prices), expected="incomplete_optimizer_horizon")

    def test_exact_hour_24_price_window_is_allowed(self):
        prices = make_prices()
        prices.pop("tomorrow")
        self.execute(SequenceSim([slot()], prices), raw="2031-01-01 00:00:00")

    def test_two_day_boundary_and_api_offset_preserved_without_iana_inference(self):
        start = pd.Timestamp("2031-01-01T23:00:00+08:00")
        points = [{**slot((start + pd.Timedelta(hours=i)).isoformat()),
                   "time": (start + pd.Timedelta(hours=i)).isoformat()} for i in range(25)]
        anchor, _ = anchor_prices(points, points[0], points[0])
        tick = anchor_device_time("2031-01-01 23:45:00", anchor)
        self.assertEqual(tick.isoformat(), "2031-01-01T23:45:00+08:00")
        self.assertIsNone(validate_milp_price_window(points, tick))
        with self.assertRaisesRegex(ValueError, "date_hour_conflict"):
            anchor_device_time("2031-01-02 00:00:00", anchor)

    def test_one_of_three_stations_conflicts_others_can_optimize(self):
        config = {**cfg(verified=True), "device_timezone": None,
                  "stations": {str(i): {"suffix": f"150{i}", "zero_flow_verified": True}
                               for i in (1, 2, 3)}}
        ha = StubHA({"1501": state(), "1502": state(device_time="2031-01-01 01:15:00"),
                     "1503": state()})
        with patch("energy_lab.shadow.optimize_window", return_value=plan()) as optimizer:
            rows = run_once_milp(config, ha, SequenceSim([slot()]), write_enabled=False, carry={})
        self.assertEqual(optimizer.call_count, 2)
        self.assertEqual(rows[1]["reason"], "device_api_date_hour_conflict")
        self.assertTrue(all(r["writes_performed"] == 0 for r in rows))
        self.assertEqual(ha.calls, [])

    def test_dispatch_uses_same_current_gate_without_timezone(self):
        config = {**cfg(), "device_timezone": None}
        ha = StubHA({"1502": state(device_time="2031-01-01 00:15:00")})
        rows = run_once(config, ha, SequenceSim([slot()]), write_enabled=False)
        self.assertIn("command_flow", rows[0])
        self.assertEqual(rows[0]["alignment_source"], "api_current_slot")
        self.assertEqual(ha.calls, [])
