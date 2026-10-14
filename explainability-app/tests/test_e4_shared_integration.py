"""E4 deadline checks against the actual shared MILP and exported evidence."""
from datetime import datetime, timedelta

from energy_explain.plans import check_feasibility
from energy_lab.shadow import evaluate_snapshot
from tests.test_shared_provider import snapshot_fixture


def test_real_shared_solver_enforces_deadline_floor():
    snapshot = snapshot_fixture()
    records = []
    result = evaluate_snapshot(
        snapshot, now=snapshot["received_at"], solver_seconds=10,
        evidence_sink=records.append,
        evidence_identity={"run_id": "e4-real-integration", "input_kind": "synthetic"},
    )
    assert result["stations"]["1"]["status"] == "candidate_only"
    assert len(records) == 1
    decision = {**records[0], "id": "e4-real-integration"}
    state = decision["snapshot"]
    start = datetime.fromisoformat(state["device_time"].replace("Z", "+00:00"))
    deadline = start + timedelta(hours=state["step_hours"] * 4)

    feasible = check_feasibility(decision, state["soc_percent"] + 1, deadline)
    assert feasible["status"] == "feasible"
    assert feasible["plan"][3]["soc_end_percent"] >= state["soc_percent"] + 1 - 1e-6
    assert feasible["deadline_step"] == 4

    unreachable = check_feasibility(decision, 100, start + timedelta(hours=state["step_hours"]))
    assert unreachable["status"] == "infeasible"
    assert unreachable["max_reachable_energy_kwh"] < state["capacity_kwh"]
