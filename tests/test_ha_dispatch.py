"""No network: exercise actual response handling and partial HA submissions."""
from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from energy_lab.ha_client import HAClient
from energy_lab.ha_dispatch import DispatchCommand, dispatch_command


WALL = datetime(2026, 10, 14, 6, 0, tzinfo=timezone.utc)
DEVICE = datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc)
SUFFIX = "anker_solix_solarbank_max_ac_unit_device"


def command(**kwargs):
    values = dict(run_id="run-test", site_id="1", decision_id="decision-test",
                  instruction_id="instruction-test", suffix=SUFFIX, issued_device_time=DEVICE,
                  flow="charge", target_w=2000, semantics="battery_charge_target")
    values.update(kwargs)
    return DispatchCommand(**values)


def response(status=200, body=None):
    fake = Mock(status_code=status)
    fake.json.return_value = [] if body is None else body
    return fake


def clock():
    ticks = iter(WALL + timedelta(milliseconds=10 * tick) for tick in range(40))
    return lambda: next(ticks)


class HADispatchTests(unittest.TestCase):
    def client(self):
        return HAClient("http://unit.test:8123", "unit-test-token", timeout=2)

    def test_no_live_permission_means_no_call_guard_or_sleep(self):
        post, guard, sleep = Mock(), Mock(), Mock()
        result = dispatch_command(self.client(), command(), post=post, before_step=guard,
                                  sleep=sleep, clock=clock())
        self.assertEqual(result.status, "not_sent")
        self.assertEqual((result.services_attempted, result.services_completed), (0, 0))
        self.assertFalse(result.uncertain_side_effects)
        post.assert_not_called()
        guard.assert_not_called()
        sleep.assert_not_called()
        with self.assertRaisesRegex(ValueError, "complete accepted"):
            result.as_submission_receipt()

    def test_all_three_actual_responses_create_associated_submission(self):
        expected_entities = (
            f"select.{SUFFIX}_operating_mode_device_runs_by_command_in_third_party_controlled",
            f"select.{SUFFIX}_grid_flow", f"number.{SUFFIX}_power_control")
        post = Mock(side_effect=[response(body=[{"entity_id": entity, "state": state}])
                                 for entity, state in zip(expected_entities,
                                                          ("third_party_control", "charge", "2000"))])
        guard, sleep = Mock(return_value=True), Mock()
        saved = command()
        result = dispatch_command(self.client(), saved, live=True, post=post,
                                  before_step=guard, sleep=sleep, clock=clock())
        self.assertEqual(result.status, "accepted")
        self.assertEqual((result.services_attempted, result.services_completed), (3, 3))
        self.assertEqual([item.http_status for item in result.steps], [200, 200, 200])
        self.assertEqual([item.target_state for item in result.steps], ["third_party_control", "charge", 2000])
        self.assertTrue(all(item.response_matches_requested_state for item in result.steps))
        self.assertEqual([call.args for call in guard.call_args_list], [(saved, 0), (saved, 1), (saved, 2)])
        sleep.assert_called_once_with(1.0)
        self.assertEqual([call.kwargs["json"] for call in post.call_args_list], [
            {"entity_id": expected_entities[0], "option": "third_party_control"},
            {"entity_id": expected_entities[1], "option": "charge"},
            {"entity_id": expected_entities[2], "value": 2000},
        ])
        self.assertTrue(all(call.kwargs["allow_redirects"] is False for call in post.call_args_list))
        self.assertTrue(all(call.kwargs["timeout"] == 2 for call in post.call_args_list))
        self.assertTrue(all(item.completed_wall_time >= item.started_wall_time >= result.issued_wall_time
                            for item in result.steps))
        exported = result.as_submission_receipt()
        self.assertEqual(tuple(exported[name] for name in ("run_id", "site_id", "decision_id", "instruction_id")),
                         ("run-test", "1", "decision-test", "instruction-test"))
        self.assertEqual(exported["target_kw"], 2)
        self.assertEqual(exported["issued_device_time"], DEVICE.isoformat())
        self.assertEqual(exported["sender_status"], "accepted")
        self.assertEqual(exported["raw_receipt"], {"http_status": 200, "services_expected": 3,
                                                 "services_completed": 3})
        # A sender-generated instruction ID is not mislabelled as an HA request ID.
        self.assertNotIn("request_id", exported["raw_receipt"])
        encoded = json.dumps(result.to_dict(), allow_nan=False)
        self.assertNotIn("unit-test-token", encoded)
        self.assertNotIn("Authorization", encoded)
        self.assertNotIn("confirmed", encoded)
        self.assertEqual(result.to_dict()["evidence_scope"], "ha_service_submission")

    def test_discharge_zero_is_valid_grid_export_target(self):
        saved = command(flow="discharge", target_w=0, semantics="grid_export_target")
        post = Mock(side_effect=[response(), response(), response()])
        result = dispatch_command(self.client(), saved, live=True, post=post, sleep=Mock(), clock=clock())
        self.assertEqual(result.as_submission_receipt()["target_kw"], 0)
        self.assertEqual(result.as_submission_receipt()["semantics"], "grid_export_target")
        self.assertEqual(post.call_args_list[1].kwargs["json"]["option"], "discharge")
        self.assertEqual(post.call_args_list[2].kwargs["json"]["value"], 0)

    def test_unchanged_entities_do_not_become_telemetry_confirmation(self):
        post = Mock(side_effect=[response(), response(), response()])
        result = dispatch_command(self.client(), command(), live=True, post=post, sleep=Mock(), clock=clock())
        self.assertEqual(result.status, "accepted")
        self.assertTrue(all(item.target_entity_returned is False for item in result.steps))
        self.assertTrue(all(item.response_matches_requested_state is None for item in result.steps))
        self.assertEqual(result.as_submission_receipt()["sender_status"], "accepted")

    def test_wrong_state_is_recorded_without_claiming_device_confirmation(self):
        post = Mock(side_effect=[response(), response(), response(body=[{
            "entity_id": f"number.{SUFFIX}_power_control", "state": "1000"}])])
        result = dispatch_command(self.client(), command(), live=True, post=post, sleep=Mock(), clock=clock())
        self.assertEqual(result.status, "accepted")  # HTTP submission only.
        self.assertFalse(result.steps[-1].response_matches_requested_state)
        self.assertEqual(result.steps[-1].target_state, 1000)

    def test_http_failure_stops_immediately_and_preserves_partial_steps(self):
        for failed_index in range(3):
            with self.subTest(failed_index=failed_index):
                failed = response(status=500)
                post = Mock(side_effect=[response() for _ in range(failed_index)] + [failed])
                result = dispatch_command(self.client(), command(), live=True, post=post, sleep=Mock(), clock=clock())
                self.assertEqual(result.status, "failed" if failed_index == 0 else "partial_failure")
                self.assertEqual((result.services_attempted, result.services_completed),
                                 (failed_index + 1, failed_index))
                self.assertEqual(result.steps[-1].outcome, "unknown")
                self.assertEqual(result.steps[-1].http_status, 500)
                self.assertTrue(result.uncertain_side_effects)
                self.assertEqual(post.call_count, failed_index + 1)
                failed.json.assert_not_called()
                with self.assertRaisesRegex(ValueError, "complete accepted"):
                    result.as_submission_receipt()

    def test_auth_failure_is_not_retried_or_authenticated(self):
        post = Mock(side_effect=[response(), response(status=401), AssertionError("should not retry")])
        result = dispatch_command(self.client(), command(), live=True, post=post, sleep=Mock(), clock=clock())
        self.assertEqual(result.status, "partial_failure")
        self.assertEqual(result.steps[-1].http_status, 401)
        self.assertEqual(post.call_count, 2)

    def test_redirect_is_not_followed_or_marked_accepted(self):
        post = Mock(return_value=response(status=302))
        result = dispatch_command(self.client(), command(), live=True, post=post, sleep=Mock(), clock=clock())
        self.assertEqual((result.status, result.services_completed), ("failed", 0))
        self.assertFalse(post.call_args.kwargs["allow_redirects"])

    def test_transport_timeout_has_unknown_side_effects_no_error_message(self):
        post = Mock(side_effect=[response(), requests.Timeout("SECRET_AUTH_HEADER")])
        result = dispatch_command(self.client(), command(), live=True, post=post, sleep=Mock(), clock=clock())
        self.assertEqual(result.status, "partial_failure")
        self.assertEqual(result.steps[-1].error_type, "Timeout")
        self.assertIsNone(result.steps[-1].http_status)
        self.assertEqual(result.steps[-1].outcome, "unknown")
        self.assertTrue(result.uncertain_side_effects)
        self.assertNotIn("SECRET_AUTH_HEADER", json.dumps(result.to_dict()))
        self.assertEqual(post.call_count, 2)

    def test_first_transport_failure_does_not_claim_no_side_effects(self):
        post = Mock(side_effect=requests.ConnectionError("unit-test-sensitive-url"))
        result = dispatch_command(self.client(), command(), live=True, post=post, sleep=Mock(), clock=clock())
        self.assertEqual(result.status, "failed")
        self.assertEqual((result.services_attempted, result.services_completed), (1, 0))
        self.assertTrue(result.uncertain_side_effects)

    def test_malformed_200_response_is_uncertain_and_stops(self):
        for body in ({"error": "sensitive-body"}, ["sensitive-body"]):
            with self.subTest(body_type=type(body).__name__):
                post = Mock(return_value=response(body=body))
                result = dispatch_command(self.client(), command(), live=True, post=post, sleep=Mock(), clock=clock())
                self.assertEqual(result.status, "failed")
                self.assertEqual(result.steps[-1].http_status, 200)
                self.assertTrue(result.uncertain_side_effects)
                self.assertEqual(post.call_count, 1)
                self.assertNotIn("sensitive-body", json.dumps(result.to_dict()))

    def test_non_json_response_preserves_http_status_without_raw_body(self):
        fake = response()
        fake.json.side_effect = ValueError("sensitive-raw-body")
        post = Mock(return_value=fake)
        result = dispatch_command(self.client(), command(), live=True, post=post, sleep=Mock(), clock=clock())
        self.assertEqual(result.steps[-1].error_type, "ValueError")
        self.assertEqual(result.steps[-1].http_status, 200)
        self.assertTrue(result.uncertain_side_effects)
        self.assertNotIn("sensitive-raw-body", json.dumps(result.to_dict()))

    def test_response_keeps_only_known_control_values_and_numeric_power(self):
        post = Mock(side_effect=[response(body=[{
            "entity_id": f"select.{SUFFIX}_operating_mode_device_runs_by_command_in_third_party_controlled",
            "state": "SECRET_TOKEN", "context": {"auth": "SECRET_TOKEN"}},
            {"entity_id": "sensor.other", "state": "SECRET_TOKEN"}]), response(),
            response(body=[{"entity_id": f"number.{SUFFIX}_power_control", "state": "nan"}])])
        result = dispatch_command(self.client(), command(), live=True, post=post, sleep=Mock(), clock=clock())
        self.assertIsNone(result.steps[0].target_state)
        self.assertFalse(result.steps[0].response_matches_requested_state)
        self.assertIsNone(result.steps[2].target_state)
        self.assertNotIn("SECRET_TOKEN", json.dumps(result.to_dict(), allow_nan=False))
        self.assertEqual(result.steps[0].changed_entities_count, 2)

    def test_guard_rejection_before_first_step_has_no_requests(self):
        post, sleep = Mock(), Mock()
        result = dispatch_command(self.client(), command(), live=True, before_step=lambda *_: False,
                                  post=post, sleep=sleep, clock=clock())
        self.assertEqual(result.status, "blocked")
        self.assertEqual(result.steps[0].error_type, "guard_rejected")
        self.assertEqual(result.services_attempted, 0)
        post.assert_not_called()
        sleep.assert_not_called()

    def test_guard_rechecks_after_mode_gap_and_blocks_stale_next_step(self):
        calls = []
        def guard(saved, index):
            calls.append(("guard", index))
            return index == 0
        def post(*args, **kwargs):
            calls.append(("post", kwargs["json"]["option"]))
            return response()
        def sleep(seconds):
            calls.append(("sleep", seconds))
        result = dispatch_command(self.client(), command(), live=True, before_step=guard,
                                  post=post, sleep=sleep, clock=clock())
        self.assertEqual(calls, [("guard", 0), ("post", "third_party_control"), ("sleep", 1.0), ("guard", 1)])
        self.assertEqual(result.status, "partial_failure")
        self.assertEqual((result.services_attempted, result.services_completed), (1, 1))
        self.assertFalse(result.steps[-1].request_sent)

    def test_guard_exception_is_blocked_without_logging_its_message(self):
        def guard(*args):
            raise RuntimeError("SECRET_GUARD_ERROR")
        post = Mock()
        result = dispatch_command(self.client(), command(), live=True, before_step=guard,
                                  post=post, sleep=Mock(), clock=clock())
        self.assertEqual(result.status, "blocked")
        self.assertEqual(result.steps[0].error_type, "RuntimeError")
        self.assertNotIn("SECRET_GUARD_ERROR", json.dumps(result.to_dict()))
        post.assert_not_called()

    def test_guard_requires_boolean_true(self):
        for value in (None, 1, "true", "false"):
            with self.subTest(value=value):
                post = Mock()
                result = dispatch_command(self.client(), command(), live=True,
                                          before_step=lambda *_: value, post=post, clock=clock())
                self.assertEqual(result.status, "blocked")
                post.assert_not_called()

    def test_invalid_command_is_rejected_before_dispatch_can_write(self):
        for values in (
            {"flow": "hold"}, {"semantics": "grid_charge"}, {"semantics": "grid_export_target"},
            {"target_w": -1}, {"target_w": 5000.001}, {"target_w": float("nan")},
            {"target_w": float("inf")}, {"target_w": True}, {"target_w": "2000"},
            {"issued_device_time": DEVICE.replace(tzinfo=None)}, {"run_id": "../run"},
            {"suffix": "unit-device/api/states"}, {"suffix": "<placeholder>"},
        ):
            with self.subTest(fields=tuple(values)):
                with self.assertRaises(ValueError):
                    command(**values)

    def test_receipt_and_command_are_frozen(self):
        saved = command()
        with self.assertRaises(FrozenInstanceError):
            saved.target_w = 3000
        result = dispatch_command(self.client(), saved, clock=clock())
        with self.assertRaises(FrozenInstanceError):
            result.status = "accepted"

    def test_accepted_export_rejects_fabricated_partial_receipt(self):
        post = Mock(side_effect=[response(), response(status=500)])
        result = dispatch_command(self.client(), command(), live=True, post=post, sleep=Mock(), clock=clock())
        fabricated = replace(result, status="accepted")
        with self.assertRaisesRegex(ValueError, "complete accepted"):
            fabricated.as_submission_receipt()

    def test_connection_fields_are_rejected_before_network(self):
        for base, token in (("file:///tmp/ha", "unit-token"),
                            ("http://user:secret@unit.test", "unit-token"),
                            ("http://unit.test?secret=token", "unit-token"),
                            ("http://unit.test#fragment", "unit-token"),
                            ("http://unit.test", "<placeholder>")):
            with self.subTest(base_scheme=base.split(":")[0]):
                post = Mock()
                with self.assertRaises(ValueError):
                    dispatch_command(HAClient(base, token), command(), live=True, post=post, clock=clock())
                post.assert_not_called()

    def test_truthy_live_value_cannot_accidentally_enable_writes(self):
        for value in (1, "true", "false", None):
            with self.subTest(value=value):
                post = Mock()
                with self.assertRaisesRegex(ValueError, "explicit boolean"):
                    dispatch_command(self.client(), command(), live=value, post=post, clock=clock())
                post.assert_not_called()

    def test_existing_ha_client_dry_run_remains_compatible(self):
        with patch("energy_lab.ha_client.requests.post") as post:
            self.assertIsNone(self.client().set_dispatch(SUFFIX, "charge", 2000, live=False))
            self.assertIsNone(self.client().set_power(SUFFIX, "charge", 2000, live=False))
            self.assertIsNone(self.client().set_self_consumption(SUFFIX, live=False))
        post.assert_not_called()


if __name__ == "__main__":
    unittest.main()
