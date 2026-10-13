from __future__ import annotations

import unittest

from pydantic import ValidationError

from energy_explain.schemas import Cost, DecisionIn, ExecutionEvent, SettlementIn
from tests.helpers import confirmed, decision_payload


class ContractTests(unittest.TestCase):
    def test_device_and_wall_clocks_are_distinct(self):
        # Simulation device time is in 2027; wall-clock receipt is in 2026.
        event = ExecutionEvent.model_validate(confirmed())
        self.assertLess(event.observed_at, event.issued_at)
        self.assertGreater(event.device_time, event.issued_at)

    def test_timezone_required_and_normalized(self):
        payload = decision_payload()
        payload["snapshot"]["device_time"] = "2027-01-24T08:00:00+08:00"
        parsed = DecisionIn.model_validate(payload)
        self.assertEqual(parsed.snapshot.device_time.isoformat(), "2027-01-24T00:00:00+00:00")
        payload["snapshot"]["device_time"] = "2027-01-24T00:00:00"
        with self.assertRaises(ValidationError):
            DecisionIn.model_validate(payload)

    def test_readback_older_than_instruction_rejected(self):
        with self.assertRaises(ValidationError):
            ExecutionEvent.model_validate(confirmed(device_time="2027-01-23T23:45:00Z"))

    def test_confirmation_requires_observation(self):
        for missing in ("observed_at", "device_time", "observed_mode", "observed_power_kw"):
            with self.subTest(missing=missing):
                payload = confirmed()
                payload.pop(missing)
                with self.assertRaises(ValidationError):
                    ExecutionEvent.model_validate(payload)

    def test_bad_units_bounds_horizon_and_nan_rejected(self):
        mutations = (
            lambda p: p["snapshot"].update(units={"power": "W"}),
            lambda p: p["snapshot"].update(soc_percent=101),
            lambda p: p["snapshot"].update(soc_min_percent=30),
            lambda p: p["snapshot"].update(pv_forecast_kw=[0, 0]),
            lambda p: p["snapshot"].update(load_kw=float("nan")),
            lambda p: p["snapshot"].update(capacity_kwh=0),
            lambda p: p["snapshot"].update(cache_tail="2027-01-24T01:00:00Z"),
        )
        for mutate in mutations:
            payload = decision_payload()
            mutate(payload)
            with self.subTest(payload=payload), self.assertRaises(ValidationError):
                DecisionIn.model_validate(payload)

    def test_fallback_requires_explanation_and_sources(self):
        payload = decision_payload()
        payload["snapshot"]["forecast_status"] = "fallback"
        with self.assertRaises(ValidationError):
            DecisionIn.model_validate(payload)
        payload["snapshot"]["fallback_reason"] = "cache_tail_stale_by_35.00h"
        self.assertEqual(DecisionIn.model_validate(payload).snapshot.forecast_status, "fallback")
        payload["snapshot"]["sources"].pop("load_forecast")
        with self.assertRaises(ValidationError):
            DecisionIn.model_validate(payload)

    def test_cost_components_must_reconcile(self):
        good = dict(purchase_eur=0.6, export_revenue_eur=0,
                    degradation_eur=0.024, terminal_credit_eur=0.055,
                    objective_eur=0.569)
        self.assertAlmostEqual(Cost.model_validate(good).objective_eur, 0.569)
        good["objective_eur"] = 0.568
        with self.assertRaises(ValidationError):
            Cost.model_validate(good)

    def test_zero_and_negative_tariffs_are_valid_evidence(self):
        payload = decision_payload(source_kind="external")
        payload["snapshot"]["import_eur_kwh"] = [-0.1, 0, 0.4, 0.15]
        self.assertEqual(DecisionIn.model_validate(payload).snapshot.import_eur_kwh[0], -0.1)
        Cost.model_validate(dict(purchase_eur=-0.5, export_revenue_eur=0,
                                 degradation_eur=0.02, terminal_credit_eur=0,
                                 objective_eur=-0.48))

    def test_pending_settlement_cannot_report_achieved_saving(self):
        payload = dict(event_id="settlement-1", status="pending", source_kind="official",
                       captured_at="2026-10-13T00:00:00Z",
                       window_start="2027-01-24T00:00:00Z",
                       window_end="2027-01-31T00:00:00Z")
        SettlementIn.model_validate(payload)
        payload["saving_settled_eur"] = 3
        with self.assertRaises(ValidationError):
            SettlementIn.model_validate(payload)


if __name__ == "__main__":
    unittest.main()
