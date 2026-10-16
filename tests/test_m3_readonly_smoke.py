"""Three-site, single-tick MILP read-only smoke and failure isolation.

These are deterministic injected inputs, not an online M3 acceptance run.
"""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_run_live_chain import StubHA, StubSim, make_prices, plan, state
from scripts.online.run_live import run_once_milp


def three_site_config():
    return {"stations": {str(site): {"suffix": f"15{site:02d}",
                                      "zero_flow_verified": True}
                         for site in (1, 2, 3)},
            "stale_after_seconds": 15.0, "capacity_kwh": 14.0,
            "max_power_w": 5000.0, "soc_min": 0.0, "soc_max": 100.0,
            "solver_seconds": 2.0, "device_timezone": "UTC"}


class ThreeSiteReadonlySmokeTests(unittest.TestCase):
    def test_three_sites_single_tick_zero_writes_and_audit(self):
        ha = StubHA({f"15{site:02d}": state() for site in (1, 2, 3)})
        with tempfile.TemporaryDirectory() as temp:
            audit = Path(temp) / "audit.csv"
            with patch("energy_lab.shadow.optimize_window", return_value=plan()):
                rows = run_once_milp(three_site_config(), ha, StubSim(make_prices()),
                                     write_enabled=False, carry={}, audit_csv=audit)
            logged = pd.read_csv(audit)
        self.assertEqual([row["site"] for row in rows], ["1", "2", "3"])
        self.assertTrue(all(row["writes_performed"] == 0 for row in rows))
        self.assertTrue(all(row["readback_verdict"] == "not_sent_dry_run" for row in rows))
        self.assertEqual(set(logged.site.astype(str)), {"1", "2", "3"})
        self.assertTrue(logged.writes_performed.eq(0).all())
        self.assertEqual(ha.calls, [])

    def test_one_stale_station_does_not_block_two_healthy_sites(self):
        ha = StubHA({"1501": state(), "1502": state(stale=True), "1503": state()})
        with patch("energy_lab.shadow.optimize_window", return_value=plan()):
            rows = run_once_milp(three_site_config(), ha, StubSim(make_prices()),
                                 write_enabled=False, carry={})
        self.assertEqual([row["status"] for row in rows],
                         ["zero_flow_dispatched", "stale_or_unavailable", "zero_flow_dispatched"])
        self.assertEqual(ha.calls, [])
        self.assertTrue(all(row.get("writes_performed", 0) == 0 for row in rows))

    def test_one_station_read_failure_does_not_block_others(self):
        def failed_read():
            raise RuntimeError("injected HA read failure")
        ha = StubHA({"1501": state(), "1502": failed_read, "1503": state()})
        with patch("energy_lab.shadow.optimize_window", return_value=plan()):
            rows = run_once_milp(three_site_config(), ha, StubSim(make_prices()),
                                 write_enabled=False, carry={})
        self.assertEqual([row["status"] for row in rows],
                         ["zero_flow_dispatched", "station_error", "zero_flow_dispatched"])
        self.assertEqual(ha.calls, [])

    def test_missing_price_and_bad_device_time_skip_optimizer_without_writes(self):
        prices = make_prices()
        del prices["today"]["points"][3]
        ha = StubHA({"1501": state(), "1502": state(), "1503": state()})
        with patch("energy_lab.shadow.optimize_window") as optimize:
            rows = run_once_milp(three_site_config(), ha, StubSim(prices),
                                 write_enabled=False, carry={})
        optimize.assert_not_called()
        self.assertTrue(all(row["status"] == "fallback_self_use_mode" for row in rows))
        self.assertTrue(all(row["attribution"] == "non-optimizer-attributed" for row in rows))
        self.assertTrue(all(row["writes_performed"] == 0 for row in rows))
        self.assertEqual(ha.calls, [])

        ha = StubHA({"1501": state(),
                     "1502": state(device_time="2031-01-03T00:15:00+00:00"),
                     "1503": state()})
        with patch("energy_lab.shadow.optimize_window", return_value=plan()) as optimize:
            rows = run_once_milp(three_site_config(), ha, StubSim(make_prices()),
                                 write_enabled=False, carry={})
        self.assertEqual(rows[1]["status"], "fallback_self_use_mode")
        self.assertEqual(rows[1]["reason"], "device_api_date_hour_conflict")
        self.assertEqual(rows[1]["attribution"], "non-optimizer-attributed")
        self.assertEqual(rows[1]["writes_performed"], 0)
        self.assertEqual(optimize.call_count, 2)
        self.assertEqual(ha.calls, [])


if __name__ == "__main__":
    unittest.main()
