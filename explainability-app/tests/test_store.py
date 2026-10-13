from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from energy_explain.schemas import ExecutionEvent, SettlementIn
from energy_explain.store import Conflict, NotFound, Store
from tests.helpers import confirmed, make_decision, submitted


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "test.sqlite3"
        self.store = Store(self.path)
        self.decision = make_decision()
        self.id = self.store.add_decision(self.decision)["id"]

    def tearDown(self):
        self.temp.cleanup()

    def test_immutable_ingest_is_idempotent_and_conflicts_rejected(self):
        same = self.store.add_decision(self.decision)
        self.assertEqual(same["id"], self.id)
        self.assertFalse(same["created"])
        altered = make_decision(policy_version="changed-version")
        with self.assertRaises(Conflict):
            self.store.add_decision(altered)
        self.assertEqual(self.store.get_decision(self.id)["policy_version"], "test-policy-v1")

    def test_identity_is_scoped_to_run_and_site(self):
        site2 = self.store.add_decision(make_decision(site_id="2"))["id"]
        run2 = self.store.add_decision(make_decision(run_id="other-run"))["id"]
        self.assertEqual(len({self.id, site2, run2}), 3)
        self.assertEqual(self.store.get_decision(site2)["site_id"], "2")
        self.assertEqual(self.store.get_decision(run2)["run_id"], "other-run")
        self.assertEqual(len(self.store.stations("test-run")["stations"]), 2)
        self.assertEqual(len(self.store.stations("other-run")["stations"]), 1)

    def test_returned_data_cannot_mutate_saved_snapshot(self):
        record = self.store.get_decision(self.id)
        saved_hash = record["snapshot_hash"]
        record["snapshot"]["soc_percent"] = 99
        restored = self.store.get_decision(self.id)
        self.assertEqual(restored["snapshot"]["soc_percent"], 20)
        self.assertEqual(restored["snapshot_hash"], saved_hash)
        # A new Store represents process restart; the same evidence survives.
        self.assertEqual(Store(self.path).get_decision(self.id)["snapshot_hash"], saved_hash)

    def test_explanations_are_versioned_idempotent_append_records(self):
        first = self.store.explanation(self.id)
        second = self.store.explanation(self.id)
        self.assertEqual(first["explanation_id"], second["explanation_id"])
        self.assertEqual(first["generated_at"], second["generated_at"])
        self.assertEqual(first["template_version"], "facts-template-v2")
        self.store.add_execution(self.id, ExecutionEvent.model_validate(submitted()))
        newer = self.store.explanation(self.id)
        self.assertNotEqual(first["explanation_id"], newer["explanation_id"])
        self.assertEqual(Store(self.path).explanation(self.id)["explanation_id"], newer["explanation_id"])
        with self.store.connect() as db:
            saved = db.execute("SELECT COUNT(*) FROM explanations").fetchone()[0]
        self.assertEqual(saved, 2)

    def test_matching_submission_can_be_confirmed_with_accelerated_clock(self):
        self.store.add_execution(self.id, ExecutionEvent.model_validate(submitted()))
        self.assertEqual(self.store.execution_status(self.id), "submitted")
        event = ExecutionEvent.model_validate(confirmed())
        self.store.add_execution(self.id, event)
        self.assertEqual(self.store.execution_status(self.id), "confirmed")
        self.assertFalse(self.store.add_execution(self.id, event)["created"])

    def test_orphan_and_changed_submission_time_cannot_confirm(self):
        with self.assertRaises(ValueError):
            self.store.add_execution(self.id, ExecutionEvent.model_validate(confirmed()))
        self.store.add_execution(self.id, ExecutionEvent.model_validate(submitted()))
        mismatch = confirmed(issued_at="2027-01-24T00:01:00Z")
        with self.assertRaises(ValueError):
            self.store.add_execution(self.id, ExecutionEvent.model_validate(mismatch))
        self.assertEqual(self.store.execution_status(self.id), "submitted")

    def test_delayed_old_confirmation_does_not_confirm_new_submission(self):
        self.store.add_execution(self.id, ExecutionEvent.model_validate(submitted()))
        newer = submitted("submit-2", "instruction-2", "2027-01-24T00:15:00Z")
        self.store.add_execution(self.id, ExecutionEvent.model_validate(newer))
        self.store.add_execution(self.id, ExecutionEvent.model_validate(confirmed()))
        self.assertEqual(self.store.execution_status(self.id), "submitted")
        self.store.add_execution(self.id, ExecutionEvent.model_validate(confirmed(
            "confirm-2", "instruction-2", "2027-01-24T00:15:00Z", "2027-01-24T00:30:00Z")))
        self.assertEqual(self.store.execution_status(self.id), "confirmed")

    def test_subsecond_newer_instruction_is_current(self):
        self.store.add_execution(self.id, ExecutionEvent.model_validate(submitted()))
        self.store.add_execution(self.id, ExecutionEvent.model_validate(confirmed()))
        self.store.add_execution(self.id, ExecutionEvent.model_validate(submitted(
            "submit-microsecond", "instruction-microsecond", "2027-01-24T00:00:00.000001Z")))
        self.assertEqual(self.store.execution_status(self.id), "submitted")

    def test_subsecond_newer_decision_is_current_station_record(self):
        newer = make_decision(decision_id="newer-id")
        newer.snapshot.device_time = newer.snapshot.device_time.replace(microsecond=1)
        newer_id = self.store.add_decision(newer)["id"]
        station = self.store.stations("test-run")["stations"][0]
        self.assertEqual(station["id"], newer_id)

    def test_changed_execution_event_conflicts_and_isolation_holds(self):
        event = ExecutionEvent.model_validate(submitted())
        self.store.add_execution(self.id, event)
        changed = ExecutionEvent.model_validate(submitted(issued_at="2027-01-24T00:01:00Z"))
        with self.assertRaises(Conflict):
            self.store.add_execution(self.id, changed)
        other = self.store.add_decision(make_decision(site_id="2"))["id"]
        self.assertTrue(self.store.add_execution(other, event)["created"])
        with self.assertRaises(ValueError):
            self.store.add_execution(other, ExecutionEvent.model_validate(confirmed(
                instruction_id="not-submitted-here")))

    def test_failure_matches_its_instruction(self):
        self.store.add_execution(self.id, ExecutionEvent.model_validate(submitted()))
        failure = dict(event_id="fail-1", instruction_id="instruction-1", kind="failed",
                       issued_at="2027-01-24T00:00:00Z", note="readback mismatch")
        self.store.add_execution(self.id, ExecutionEvent.model_validate(failure))
        self.assertEqual(self.store.execution_status(self.id), "failed")

    def test_settlement_is_appended_idempotently_and_scoped_to_run(self):
        self.store.add_decision(make_decision(run_id="external-run", source_kind="external"))
        payload = dict(event_id="settlement-1", status="complete", source_kind="official",
                       captured_at="2026-10-13T00:00:00Z",
                       window_start="2027-01-24T00:00:00Z",
                       window_end="2027-01-31T00:00:00Z", saving_settled_eur=-5.0)
        value = SettlementIn.model_validate(payload)
        first = self.store.add_settlement("external-run", value)
        self.assertTrue(first["created"])
        self.assertFalse(self.store.add_settlement("external-run", value)["created"])
        with self.assertRaises(Conflict):
            self.store.add_settlement("external-run", SettlementIn.model_validate(
                dict(payload, saving_settled_eur=5.0)))
        self.assertIsNone(self.store.get_settlement("test-run"))
        with self.assertRaises(NotFound):
            self.store.get_settlement("unknown-run")
        self.assertEqual(self.store.get_settlement("external-run")["saving_settled_eur"], -5)
        with self.assertRaises(ValueError):
            self.store.add_settlement("test-run", value)
        with self.assertRaises(ValueError):
            self.store.add_settlement("external-run", SettlementIn.model_validate(
                dict(payload, source_kind="demo", event_id="fake-demo-settlement")))

    def test_demo_and_external_provenance_cannot_mix_within_run(self):
        with self.assertRaises(Conflict):
            self.store.add_decision(make_decision(site_id="2", source_kind="external"))

    def test_delayed_older_settlement_does_not_regress_state(self):
        self.store.add_decision(make_decision(run_id="external-run", source_kind="external"))
        common = dict(source_kind="official", window_start="2027-01-24T00:00:00Z",
                      window_end="2027-01-31T00:00:00Z")
        newer = SettlementIn.model_validate(dict(common, event_id="done", status="complete",
            captured_at="2026-10-13T01:00:00Z", saving_settled_eur=4.2))
        older = SettlementIn.model_validate(dict(common, event_id="pending", status="pending",
            captured_at="2026-10-13T00:00:00Z"))
        self.store.add_settlement("external-run", newer)
        self.store.add_settlement("external-run", older)
        self.assertEqual(self.store.get_settlement("external-run")["event_id"], "done")

    def test_subsecond_newer_settlement_is_current(self):
        self.store.add_decision(make_decision(run_id="external-run", source_kind="external"))
        common = dict(source_kind="official", status="complete",
                      window_start="2027-01-24T00:00:00Z",
                      window_end="2027-01-31T00:00:00Z")
        newer = SettlementIn.model_validate(dict(common, event_id="newer",
            captured_at="2026-10-13T00:00:00.000001Z", saving_settled_eur=4.2))
        older = SettlementIn.model_validate(dict(common, event_id="older",
            captured_at="2026-10-13T00:00:00Z", saving_settled_eur=3))
        self.store.add_settlement("external-run", newer)
        self.store.add_settlement("external-run", older)
        self.assertEqual(self.store.get_settlement("external-run")["event_id"], "newer")


if __name__ == "__main__":
    unittest.main()
