"""HA POST fault injection; requests.post is always mocked."""
from pathlib import Path
import sys
import time
import unittest
from unittest.mock import patch

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from energy_lab.execute import execute_candidate
from energy_lab.ha_client import HAClient

# ``exercise`` mocks ``energy_lab.write_transaction.time.sleep``, which is the
# same ``time`` module object imported here. Save the real sleep up front so a
# fault can still consume real wall-clock budget (a slow POST) while the SOP
# mode-gap sleep stays mocked.
_REAL_SLEEP = time.sleep


def candidate():
    return {"status": "candidate_only", "battery_flow": "charge",
            "battery_power_w": 100, "candidate": {"target_w": 100},
            "device_time": "2031-01-01T00:15:00+00:00", "solver_status": "optimal"}


class Response:
    def __init__(self, error=None):
        self.error = error

    def raise_for_status(self):
        if self.error:
            raise self.error

    def json(self):
        return []


class WriteFaultTests(unittest.TestCase):
    def setUp(self):
        self.ha = HAClient("http://mock.invalid", "fake-token")

    def exercise(self, effects, *, seconds=3, current=None):
        with patch.object(self.ha.session, "post", side_effect=effects) as post, \
             patch("energy_lab.write_transaction.time.sleep"):
            row = execute_candidate(candidate(), suffix="1501", ha=self.ha, live=True,
                                    deadline=time.monotonic() + seconds, current=current)
        self.assertEqual(post.call_count, len(row["delivery_steps"]))
        return row, post

    def test_first_post_timeout_is_unknown_and_never_retried(self):
        row, post = self.exercise([requests.Timeout("uncertain")])
        self.assertEqual(post.call_count, 1)
        self.assertEqual(row["status"], "delivery_failed")
        self.assertEqual(row["writes_performed"], 0)
        self.assertEqual(row["write_requests_attempted"], 1)
        self.assertEqual(row["write_requests_acknowledged"], 0)
        self.assertEqual(row["delivery_steps"][0]["status"], "attempted_unknown")
        self.assertTrue(row["requires_reconciliation"])
        self.assertFalse(row["optimizer_attributed"])

    def test_first_post_5xx_is_not_retried(self):
        response = Response(requests.HTTPError("500"))
        response.status_code = 500
        row, post = self.exercise([response])
        self.assertEqual(post.call_count, 1)
        self.assertTrue(row["requires_reconciliation"])
        self.assertEqual(row["delivery_steps"][0]["status"], "http_rejected_not_device_verified")
        self.assertEqual(row["delivery_steps"][0]["http_status"], 500)

    def test_second_and_third_post_failures_preserve_prefix(self):
        for failure_index in (1, 2):
            effects = [Response()] * failure_index + [requests.Timeout("late")]
            row, post = self.exercise(effects)
            self.assertEqual(post.call_count, failure_index + 1)
            self.assertEqual(row["write_requests_acknowledged"], failure_index)
            self.assertEqual(row["write_requests_attempted"], failure_index + 1)
            self.assertTrue(row["requires_reconciliation"])
            self.assertEqual(row["delivery_steps"][-1]["status"], "attempted_unknown")

    def test_deadline_and_superseded_generation_prevent_next_post(self):
        for current, seconds in ((lambda: False, 3), (None, -0.001)):
            row, post = self.exercise([], current=current, seconds=seconds)
            self.assertEqual(post.call_count, 0)
            self.assertEqual(row["write_requests_attempted"], 0)
            self.assertFalse(row["requires_reconciliation"])

    def test_gap_budget_stops_after_mode_ack(self):
        # Reserve passes (>=2s), but a slow mode ack leaves under 1s for the
        # SOP gap, so the grid_flow POST is never attempted.
        def slow_first(*args, **kwargs):
            _REAL_SLEEP(1.2)
            return Response()
        row, post = self.exercise(slow_first, seconds=2.1)
        self.assertEqual(post.call_count, 1)
        self.assertEqual(row["write_requests_acknowledged"], 1)
        self.assertTrue(row["requires_reconciliation"])

    def test_slow_first_response_expires_round_without_second_post(self):
        def slow(*args, **kwargs):
            _REAL_SLEEP(2.2)
            return Response()
        row, post = self.exercise(slow, seconds=2.1)
        self.assertEqual(post.call_count, 1)
        self.assertEqual(row["write_requests_acknowledged"], 1)
        self.assertTrue(row["requires_reconciliation"])
        self.assertEqual(row["status"], "delivery_failed")


if __name__ == "__main__":
    unittest.main()
