"""Independent contract fixtures; do not derive input facts from implementation."""
from __future__ import annotations

from energy_explain.schemas import DecisionIn


def decision_payload(**overrides):
    payload = {
        "run_id": "test-run", "site_id": "1", "decision_id": "same-id",
        "source_kind": "demo", "model_version": "demo-milp-v1",
        "policy_version": "test-policy-v1", "solver_status": "optimal",
        "snapshot": {
            "device_time": "2027-01-24T00:00:00Z",
            "observed_at": "2026-10-13T00:00:00Z",
            "soc_percent": 20, "pv_kw": 0, "load_kw": 1,
            "capacity_kwh": 14, "max_charge_kw": 5, "max_discharge_kw": 5,
            "step_hours": 1, "charge_efficiency": 0.95,
            "discharge_efficiency": 1,
            "pv_forecast_kw": [0, 0, 0, 0],
            "load_forecast_kw": [1, 3, 3, 1],
            "import_eur_kwh": [0.1, 0.35, 0.4, 0.15],
            "export_eur_kwh": [0.04, 0.04, 0.04, 0.04],
            "degradation_eur_kwh": 0.002, "terminal_eur_kwh": 0.1,
            "sources": {
                "pv": "synthetic", "load": "synthetic",
                "pv_forecast": "synthetic", "load_forecast": "synthetic",
            },
            "forecast_status": "ready",
        },
        "action": {"kind": "charge", "power_kw": 5,
                   "control_semantics": "battery_charge"},
    }
    payload.update(overrides)
    return payload


def make_decision(**overrides):
    return DecisionIn.model_validate(decision_payload(**overrides))


def submitted(event_id="submit-1", instruction_id="instruction-1",
              issued_at="2027-01-24T00:00:00Z"):
    return {"event_id": event_id, "instruction_id": instruction_id,
            "kind": "submitted", "issued_at": issued_at}


def confirmed(event_id="confirm-1", instruction_id="instruction-1",
              issued_at="2027-01-24T00:00:00Z",
              device_time="2027-01-24T00:15:00Z"):
    return {"event_id": event_id, "instruction_id": instruction_id,
            "kind": "confirmed", "issued_at": issued_at,
            "device_time": device_time,
            "observed_at": "2026-10-13T00:01:00Z",
            "observed_mode": "charge", "observed_power_kw": 5}
