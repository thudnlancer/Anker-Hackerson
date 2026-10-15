"""Opening alignment recovery is narrow, explicit, atomic and auditable."""
from copy import deepcopy
from pathlib import Path
import json
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from energy_lab.explainability_stream import ClockRegression, StreamCheckpoint
from test_explainability_stream import config, record


RUN = "unit-opening"
WINDOW_START = "2026-10-10T00:00:00Z"
WINDOW_END = "2026-10-18T00:00:00Z"
OLD = {"1": "2026-10-21T23:15:00Z", "2": "2026-10-21T23:15:00Z",
       "3": "2026-10-10T08:30:00Z"}
CURRENT = {site: "2026-10-10T08:45:00Z" for site in ("1", "2", "3")}


class OpeningAlignmentRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "checkpoint.sqlite"
        self.cp = StreamCheckpoint(self.path)
        self.cp.bind(RUN, config()["stations"])
        self.cp.observe(RUN, OLD)
        key, _ = self.cp.stage(record(run=RUN, site="3", tick=OLD["3"]))
        self.cp.delivered(key)
        self.finish("partial_collection", "IncompleteCollection")
        with self.assertRaises(ClockRegression):
            self.cp.observe(RUN, CURRENT)
        self.finish("clock_regression", "ClockRegression")

    def tearDown(self):
        self.temp.cleanup()

    def finish(self, reason, error):
        self.cp.finish(RUN, dict(last_success_at=None,
            last_error_at="2026-10-14T17:21:11Z", last_error=error, reason=reason))

    def recover(self, **overrides):
        values = dict(window_start=WINDOW_START, window_end=WINDOW_END,
                      current_ticks=CURRENT, source_ref="outputs/unit/opening-proof.json")
        values.update(overrides)
        return self.cp.recover_opening_alignment(RUN, **values)

    def rows(self, table):
        with self.cp._connect() as db:
            return [dict(row) for row in db.execute("SELECT * FROM " + table + " ORDER BY run_id")]

    def test_only_unstaged_outside_clocks_are_deleted_and_all_evidence_is_preserved(self):
        records, cycles, errors = self.rows("stream_records"), self.rows("stream_cycles"), self.cp.state(RUN)
        with patch("requests.sessions.Session.request", side_effect=AssertionError("recovery used HTTP")):
            audit = self.recover()
        self.assertEqual(audit["removed_sites"], ["1", "2"])
        self.assertEqual(set(audit["old_ticks"]), {"1", "2", "3"})
        self.assertEqual(set(audit["current_ticks"]), {"1", "2", "3"})
        self.assertEqual(audit["new_ticks"], {"3": "2026-10-10T08:30:00+00:00"})
        self.assertEqual(self.rows("stream_clocks"), [dict(run_id=RUN, site_id="3", tick=OLD["3"])])
        self.assertEqual(self.rows("stream_records"), records)
        self.assertEqual(self.rows("stream_cycles"), cycles)
        expected = deepcopy(errors); expected["blocked_reason"] = None
        self.assertEqual(self.cp.state(RUN), expected)
        stored = self.rows("stream_recoveries")
        self.assertEqual(len(stored), 1)
        self.assertEqual(json.loads(stored[0]["payload"]), audit)
        # The normal observe operation, rather than recovery, saves new clocks.
        self.cp.observe(RUN, CURRENT)
        self.assertEqual({row["site_id"]:row["tick"] for row in self.rows("stream_clocks")}, CURRENT)

    def test_recovery_is_one_time_even_if_a_later_regression_occurs(self):
        self.recover()
        with self.cp._connect() as db:
            db.execute("UPDATE stream_runs SET blocked_reason='clock_regression' WHERE run_id=?", (RUN,))
        with self.assertRaisesRegex(ValueError, "already used"):
            self.recover()
        self.assertEqual(len(self.rows("stream_recoveries")), 1)

    def test_saved_bad_site_evidence_pending_or_delivered_cannot_be_cleared(self):
        for delivered in (False, True):
            with self.subTest(delivered=delivered):
                key, _ = self.cp.stage(record(run=RUN, site="1", tick=OLD["1"]))
                if delivered:
                    self.cp.delivered(key)
                before = {table:self.rows(table) for table in ("stream_runs", "stream_clocks", "stream_records", "stream_cycles")}
                with self.assertRaisesRegex(ValueError, "any saved evidence"):
                    self.recover()
                self.assertEqual({table:self.rows(table) for table in before}, before)
                self.assertEqual(self.rows("stream_recoveries"), [])

    def test_current_tick_cannot_rewind_a_valid_saved_site(self):
        current = dict(CURRENT, **{"3": "2026-10-10T08:15:00Z"})
        with self.assertRaisesRegex(ValueError, "rewind an in-window clock"):
            self.recover(current_ticks=current)
        self.assertEqual(self.cp.state(RUN)["blocked_reason"], "clock_regression")
        self.assertEqual(self.rows("stream_recoveries"), [])

    def test_midrun_regression_after_cycle_two_stays_blocked(self):
        self.finish("clock_regression", "ClockRegression")
        with self.assertRaisesRegex(ValueError, "within two cycles"):
            self.recover()
        self.assertEqual(self.cp.state(RUN)["cycle"], 3)
        self.assertEqual(self.rows("stream_recoveries"), [])

    def test_other_block_reason_or_unblocked_run_cannot_recover(self):
        for reason in (None, "authentication_failed"):
            with self.subTest(reason=reason), self.cp._connect() as db:
                db.execute("UPDATE stream_runs SET blocked_reason=? WHERE run_id=?", (reason, RUN))
            with self.assertRaisesRegex(ValueError, "opening clock regression"):
                self.recover()
        self.assertEqual(self.rows("stream_recoveries"), [])

    def test_all_inwindow_regression_is_not_an_opening_alignment_error(self):
        with self.cp._connect() as db:
            db.execute("UPDATE stream_clocks SET tick='2026-10-10T08:30:00Z' WHERE run_id=?", (RUN,))
        with self.assertRaisesRegex(ValueError, "no out-of-window"):
            self.recover()

    def test_current_ticks_must_be_exact_saved_sites_and_all_inside_window(self):
        cases = [dict(CURRENT, **{"1": WINDOW_END}), dict(CURRENT, **{"2": "2026-10-09T23:45:00Z"}),
                 {"1": CURRENT["1"], "2": CURRENT["2"]},
                 {"1": CURRENT["1"], "2": CURRENT["2"], "4": CURRENT["3"]}]
        before = self.rows("stream_clocks")
        for ticks in cases:
            with self.subTest(ticks=ticks), self.assertRaises(ValueError):
                self.recover(current_ticks=ticks)
        self.assertEqual(self.rows("stream_clocks"), before)
        self.assertEqual(self.rows("stream_recoveries"), [])

    def test_window_and_current_timestamps_require_explicit_timezone(self):
        for changes in (dict(window_start="2026-10-10"),
                        dict(window_end=WINDOW_START),
                        dict(current_ticks=dict(CURRENT, **{"2":"2026-10-10T08:45:00"})),
                        dict(source_ref=""), dict(source_ref="bad\nreference")):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.recover(**changes)
        self.assertEqual(self.rows("stream_recoveries"), [])

    def test_another_run_with_same_site_evidence_is_untouched(self):
        self.cp.bind("other-run", config()["stations"])
        self.cp.observe("other-run", OLD)
        self.cp.stage(record(run="other-run", site="1", tick=OLD["1"]))
        other_records = [row for row in self.rows("stream_records") if row["run_id"] == "other-run"]
        other_clocks = [row for row in self.rows("stream_clocks") if row["run_id"] == "other-run"]
        self.recover()
        self.assertEqual([row for row in self.rows("stream_records") if row["run_id"] == "other-run"], other_records)
        self.assertEqual([row for row in self.rows("stream_clocks") if row["run_id"] == "other-run"], other_clocks)


if __name__ == "__main__":
    unittest.main()
