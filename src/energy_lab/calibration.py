"""B3 HA physical calibration: pure per-sample checks and aggregation.

No I/O here. The runner (audit_physical_calibration.py) samples HA read-only
and feeds each station snapshot through these checks. Scope per
PROJECT_STATUS_AND_WORKSTREAMS line B3:

- PV sensor rating: pv must be finite and >= 0, and <= the configured rating
  when one is supplied (no nameplate is invented; observed max is reported);
- load sign/direction: home load must be finite and >= 0;
- battery power entity three-way consistency: battery charge/discharge are
  mutually exclusive, grid import/export are mutually exclusive, and the node
  balance pv + import + discharge == load + export + charge holds within a
  sampling tolerance.

A sample whose six power fields are not all readable is counted as unusable,
never silently passed.
"""
from __future__ import annotations

import math


POWER_FIELDS = ("solar_power", "home_load",
                "battery_charging_power_w", "battery_discharging_power_w",
                "grid_import_power_w", "grid_export_power_w")

DEFAULT_MUTEX_TOLERANCE_W = 100.0
DEFAULT_BALANCE_TOLERANCE_W = 250.0


def _finite(value: object) -> float:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return float("nan")
    return number if math.isfinite(number) else float("nan")


def check_sample(state: dict, *, pv_rating_w: float | None = None,
                 mutex_tolerance_w: float = DEFAULT_MUTEX_TOLERANCE_W,
                 balance_tolerance_w: float = DEFAULT_BALANCE_TOLERANCE_W) -> dict:
    """Check one sampled station state. Pure function; no freshness or I/O."""
    if not isinstance(state, dict):
        raise TypeError("state must be a mapping")
    tolerances = {"mutex": float(mutex_tolerance_w), "balance": float(balance_tolerance_w)}
    if any(not math.isfinite(t) or t < 0 for t in tolerances.values()):
        raise ValueError("tolerances must be non-negative finite numbers")
    if pv_rating_w is not None and (not math.isfinite(float(pv_rating_w)) or float(pv_rating_w) <= 0):
        raise ValueError("pv_rating_w must be a positive finite number when supplied")

    values = {field: _finite(state.get(field)) for field in POWER_FIELDS}
    observed = {field: (values[field] if math.isfinite(values[field]) else state.get(field))
                for field in POWER_FIELDS}
    if not all(math.isfinite(values[field]) for field in POWER_FIELDS):
        missing = [field for field in POWER_FIELDS if not math.isfinite(values[field])]
        return {"usable": False, "reason": "unreadable_power_fields:" + ",".join(missing),
                "observed": observed, "checks": {}, "residual_w": None}

    pv, load = values["solar_power"], values["home_load"]
    charge, discharge = values["battery_charging_power_w"], values["battery_discharging_power_w"]
    grid_in, grid_out = values["grid_import_power_w"], values["grid_export_power_w"]

    checks: dict[str, dict] = {}
    rating_ok = pv >= 0.0 and (pv_rating_w is None or pv <= float(pv_rating_w))
    checks["pv_sensor_rating"] = {
        "passed": bool(rating_ok), "observed": pv,
        "expected": f"0..{float(pv_rating_w)} W" if pv_rating_w is not None else ">= 0 W (no rating configured)"}
    checks["load_sign_direction"] = {"passed": bool(load >= 0.0), "observed": load, "expected": ">= 0 W"}
    battery_mutex = min(charge, discharge) <= tolerances["mutex"] and min(charge, discharge) >= 0.0
    checks["battery_mutual_exclusion"] = {
        "passed": bool(battery_mutex), "observed": {"charge": charge, "discharge": discharge},
        "expected": f"min(charge, discharge) <= {tolerances['mutex']} W"}
    grid_mutex = min(grid_in, grid_out) <= tolerances["mutex"] and min(grid_in, grid_out) >= 0.0
    checks["grid_mutual_exclusion"] = {
        "passed": bool(grid_mutex), "observed": {"import": grid_in, "export": grid_out},
        "expected": f"min(import, export) <= {tolerances['mutex']} W"}
    residual = (pv + grid_in + discharge) - (load + grid_out + charge)
    checks["power_balance"] = {
        "passed": bool(abs(residual) <= tolerances["balance"]),
        "observed": residual, "expected": f"|residual| <= {tolerances['balance']} W",
        "equation": "pv + grid_import + battery_discharge - load - grid_export - battery_charge"}

    failed = [name for name, item in checks.items() if not item["passed"]]
    return {"usable": True,
            "reason": "checked" if not failed else "failed:" + ",".join(failed),
            "observed": observed, "checks": checks, "residual_w": residual}


def summarize_site(results: list[dict]) -> dict:
    """Aggregate one site's per-sample check dicts into a calibration verdict."""
    total = len(results)
    unusable = [r for r in results if not r.get("usable")]
    usable = [r for r in results if r.get("usable")]
    failures: dict[str, int] = {}
    residuals: list[float] = []
    pv_max: float | None = None
    for result in usable:
        for name, item in result.get("checks", {}).items():
            if not item["passed"]:
                failures[name] = failures.get(name, 0) + 1
        if result.get("residual_w") is not None:
            residuals.append(abs(float(result["residual_w"])))
        pv_value = result.get("observed", {}).get("solar_power")
        if isinstance(pv_value, (int, float)) and math.isfinite(float(pv_value)):
            pv_max = float(pv_value) if pv_max is None else max(pv_max, float(pv_value))
    residuals.sort()
    p95 = residuals[min(len(residuals) - 1, int(math.ceil(0.95 * len(residuals))) - 1)] if residuals else None
    calibrated = total > 0 and not unusable and not failures
    return {"samples": total, "usable": len(usable), "unusable": len(unusable),
            "failures_by_check": failures,
            "pv_max_observed_w": pv_max,
            "balance_residual_abs_w": {"max": residuals[-1] if residuals else None,
                                       "p95": p95,
                                       "mean": (sum(residuals) / len(residuals)) if residuals else None},
            "unusable_reasons": sorted({r.get("reason", "unknown") for r in unusable}),
            "calibrated": bool(calibrated),
            "verdict": ("calibrated" if calibrated else
                        "insufficient_samples" if total == 0 else
                        "unusable_samples" if unusable and not failures else "violations_detected")}


def summarize(results_by_site: dict[str, list[dict]]) -> dict:
    sites = {str(site): summarize_site(results) for site, results in sorted(results_by_site.items())}
    return {"sites": sites,
            "all_calibrated": bool(sites) and all(item["calibrated"] for item in sites.values())}
