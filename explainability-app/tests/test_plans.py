"""Plan checks must not present missing model support as a feasibility verdict."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from energy_explain.plans import check_feasibility
from energy_explain.providers import ModelOutcome
from energy_explain.shared_provider import SharedMilpProvider


def decision():
    return {"id": "saved", "model_version": "shared-test", "snapshot": {
        "device_time": "2031-01-01T00:00:00Z", "step_hours": 1,
        "pv_forecast_kw": [0, 0, 0], "soc_percent": 20,
        "soc_min_percent": 0, "soc_max_percent": 100,
        "capacity_kwh": 10, "max_charge_kw": 2, "charge_efficiency": 1,
    }, "replay_context": {"command_contract": "sop", "first_step": {
        "charge_kw": 0, "discharge_kw": 0, "grid_import_kw": 0, "grid_export_kw": 0}}}


class StubShared(SharedMilpProvider):
    def __init__(self, feasible=True):
        self.feasible = feasible
        self.floors = []

    def optimizer(self):
        def optimize_window(*args, energy_floor_kwh=None, **kwargs):
            pass
        return optimize_window

    def solve(self, snapshot, contract, *, first_step=None, energy_floor_kwh=None):
        self.floors.append(energy_floor_kwh)
        if energy_floor_kwh and not self.feasible:
            raise ModelOutcome("infeasible", "no plan")
        return {"cost": {"objective_eur": 5 if energy_floor_kwh else 3},
                "plan": [{"step": i, "soc_end_percent": 20 + i * 20} for i in range(3)]}


def test_shared_model_feasible_cost_and_deadline():
    stub = StubShared()
    with patch("energy_explain.plans.get_provider", return_value=stub):
        result = check_feasibility(decision(), 40, datetime(2031, 1, 1, 2, tzinfo=timezone.utc))
    assert result["status"] == "feasible"
    assert result["deadline_step"] == 2
    assert result["cost_delta_eur"] == 2
    assert stub.floors == [None, [None, 4, None]]


def test_infeasible_and_adjustments_are_separate():
    stub = StubShared(feasible=False)
    with patch("energy_explain.plans.get_provider", return_value=stub):
        result = check_feasibility(decision(), 90, datetime(2031, 1, 1, 1, tzinfo=timezone.utc))
    assert result["status"] == "infeasible"
    assert result["max_reachable_energy_kwh"] == 4
    assert "cost_delta_eur" not in result


def test_demo_and_missing_floor_support_are_unsupported():
    with patch("energy_explain.plans.get_provider", return_value=None):
        assert check_feasibility(decision(), 40, datetime(2031, 1, 1, 1, tzinfo=timezone.utc))["status"] == "unsupported"
    stub = StubShared()
    stub.optimizer = lambda: lambda *args, **kwargs: None
    with patch("energy_explain.plans.get_provider", return_value=stub):
        assert check_feasibility(decision(), 40, datetime(2031, 1, 1, 1, tzinfo=timezone.utc))["status"] == "unsupported"


def test_deadline_uses_device_clock_and_step_alignment():
    stub = StubShared()
    with patch("energy_explain.plans.get_provider", return_value=stub):
        with pytest.raises(ValueError):
            check_feasibility(decision(), 40, datetime(2031, 1, 1, 1, 30, tzinfo=timezone.utc))
        with pytest.raises(ValueError):
            check_feasibility(decision(), 40, datetime(2031, 1, 2, tzinfo=timezone.utc))


def test_saved_baseline_cost_mismatch_cannot_be_reported_feasible():
    record = decision()
    record["cost"] = {"objective_eur": 999}
    with patch("energy_explain.plans.get_provider", return_value=StubShared()):
        result = check_feasibility(record, 40, datetime(2031, 1, 1, 2, tzinfo=timezone.utc))
    assert result["status"] == "failed"
    assert "费用" in result["message"]
