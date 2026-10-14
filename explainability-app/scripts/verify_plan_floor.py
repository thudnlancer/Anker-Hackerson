"""Five-case acceptance using the committed shared optimizer, without HA writes."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import inspect
import json
from pathlib import Path
from unittest.mock import patch

from energy_explain.plans import check_feasibility
from energy_explain.schemas import Snapshot
from energy_explain.shared_provider import MODEL_VERSION, SOURCE_SHA256, SharedMilpProvider
import energy_lab.rolling as rolling


def verify():
    provider = SharedMilpProvider()
    assert provider.optimizer() is rolling.optimize_window
    source = Path(inspect.getfile(rolling.optimize_window))
    assert hashlib.sha256(source.read_bytes()).hexdigest() == SOURCE_SHA256
    n = 96
    snapshot = Snapshot.model_validate({
        "device_time": "2031-01-01T00:00:00Z", "observed_at": "2026-10-14T00:00:00Z",
        "soc_percent": 20, "pv_kw": 0, "load_kw": 1,
        "capacity_kwh": 10, "max_charge_kw": 2, "max_discharge_kw": 2,
        "step_hours": 0.25, "charge_efficiency": 0.95, "discharge_efficiency": 1,
        "pv_forecast_kw": [0] * n, "load_forecast_kw": [1] * n,
        "import_eur_kwh": [0.1 + i * 0.003 for i in range(n)],
        "export_eur_kwh": [0.03] * n,
        "degradation_eur_kwh": 0.02, "terminal_eur_kwh": 0.1,
        "sources": {k: "synthetic" for k in ("pv", "load", "pv_forecast", "load_forecast")},
        "forecast_status": "ready",
    }).model_dump(mode="json")
    original = provider.solve(snapshot, "sop")
    first = {k: original["plan"][0][k] for k in
             ("charge_kw", "discharge_kw", "grid_import_kw", "grid_export_kw")}
    record = {"id": "real-floor-acceptance", "source_kind": "external",
        "model_version": MODEL_VERSION, "snapshot": snapshot, "cost": original["cost"],
        "provenance": {"input_kind": "synthetic"},
        "action": {"kind": "charge", "control_semantics": "battery_charge", "power_kw": first["charge_kw"]},
        "replay_context": {"command_contract": "sop", "upstream_source_sha256": SOURCE_SHA256,
                           "first_step": first}}
    assert provider.supports(record)
    deadline = datetime(2031, 1, 1, 2, tzinfo=timezone.utc)
    feasible = check_feasibility(record, 50, deadline)
    assert feasible["status"] == "feasible", feasible
    assert feasible["plan"][7]["soc_end_percent"] >= 50 - 1e-6
    unreachable = check_feasibility(record, 90, datetime(2031, 1, 1, 0, 15, tzinfo=timezone.utc))
    assert unreachable["status"] == "infeasible", unreachable
    assert abs(unreachable["max_reachable_energy_kwh"] - 2.475) < 1e-8

    # Invoke real SciPy/HiGHS; reduce only the constrained call's solver budget.
    # Neither optimize_window nor solver outcomes are stubbed.
    native_milp = rolling.milp
    solver_calls = []
    def limited_milp(*args, **kwargs):
        if solver_calls:
            kwargs["options"] = {**kwargs["options"], "time_limit": 1e-9}
        result = native_milp(*args, **kwargs)
        solver_calls.append({"status": int(result.status), "message": str(result.message),
                             "time_limit": kwargs["options"]["time_limit"]})
        return result
    with patch.object(rolling, "milp", side_effect=limited_milp):
        timeout = check_feasibility(record, 50, deadline)
    assert timeout["status"] == "timeout", timeout
    assert [c["status"] for c in solver_calls] == [0, 1], solver_calls

    # Capability fault injection uses the actual old optimizer, not a solver stub.
    from energy_explain.strategy_vendor.rolling import optimize_window as legacy_optimizer
    with patch("energy_explain.plans.get_provider", return_value=provider), \
         patch.object(provider, "optimizer", return_value=legacy_optimizer):
        unsupported = check_feasibility(record, 50, deadline)
    assert unsupported["status"] == "unsupported", unsupported
    try:
        check_feasibility(record, 50, datetime(2031, 1, 1, 0, 7, tzinfo=timezone.utc))
    except ValueError as exc:
        unaligned = {"status": "rejected", "message": str(exc)}
    else:
        raise AssertionError("unaligned deadline was accepted")
    return {"passed": True, "model_version": MODEL_VERSION, "source_sha256": SOURCE_SHA256,
        "optimizer_path": str(source), "input_kind": "synthetic", "writes_performed": 0,
        "scope": "real_shared_optimizer_on_artificial_inputs_not_live_device_or_official_settlement",
        "cases": {"feasible": feasible, "unreachable": unreachable, "timeout": timeout,
                  "missing_capability": unsupported, "unaligned_deadline": unaligned},
        "timeout_solver_calls": solver_calls,
        "fault_injection": {"timeout": "real HiGHS constrained solve budget 1e-9 seconds",
                            "missing_capability": "legacy optimizer signature; provider selection injected"}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = verify()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"passed": report["passed"], "cases": {
        k: v["status"] for k, v in report["cases"].items()}, "output": str(args.output)}))


if __name__ == "__main__":
    main()
