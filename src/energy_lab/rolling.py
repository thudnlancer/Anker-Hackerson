from __future__ import annotations

from dataclasses import dataclass
import numpy as np
import pandas as pd
from scipy.optimize import linprog, milp, Bounds, LinearConstraint
from functools import lru_cache
from numbers import Real


@lru_cache(maxsize=16)
def _cached_equalities(n: int, charge_efficiency: float, step_hours: float) -> np.ndarray:
    """Cache the horizon-dependent LP equality matrix across rolling calls."""
    a_eq = np.zeros((2 * n, 5 * n))
    for t in range(n):
        row = a_eq[2 * t]
        row[t] = 1.0
        row[n + t] = -1.0
        row[2 * n + t] = -1.0
        row[3 * n + t] = 1.0
        soc_row = a_eq[2 * t + 1]
        soc_row[4 * n + t] = 1.0
        soc_row[t] = -charge_efficiency * step_hours
        soc_row[n + t] = step_hours
        if t:
            soc_row[4 * n + t - 1] = -1.0
    return a_eq


@dataclass
class RollingPlan:
    plan: pd.DataFrame
    objective: float
    status: str
    message: str


def _validated_first_step(first_step, first_charge_kw, n):
    """Translate explicit nonnegative kW interventions to original first-step indexes."""
    if first_step is not None and first_charge_kw is not None:
        raise ValueError("first_step and first_charge_kw are mutually exclusive")
    indexes = {"charge_kw": 0, "discharge_kw": n,
               "grid_import_kw": 2*n, "grid_export_kw": 3*n}
    if first_step is not None:
        if not isinstance(first_step, dict):
            raise ValueError("first_step must be a dictionary of kW values")
        if any(key not in indexes for key in first_step):
            raise ValueError("first_step contains unknown power keys")
        values = first_step
    elif first_charge_kw is not None:
        values = {"charge_kw": first_charge_kw}
    else:
        values = {}
    fixed = {}
    for key, value in values.items():
        if isinstance(value, bool) or not isinstance(value, Real) or not np.isfinite(value) or value < 0:
            raise ValueError("first-step powers must be finite nonnegative numbers in kW")
        fixed[indexes[key]] = float(value)
    return fixed


def _intervention_infeasible(times, n, soc_percent):
    """Use the original failure shape; never relax an original variable bound."""
    empty = pd.DataFrame({"time": times, "flow": ["idle"] * n, "charge_w": 0.0,
                          "discharge_w": 0.0, "grid_import_w": 0.0,
                          "grid_export_w": 0.0, "soc_end": soc_percent,
                          "intended_command_mode": ["zero_flow_command"] * n})
    return RollingPlan(empty, float("nan"), "infeasible",
                       "first-step intervention exceeds an original variable bound")


def optimize_window(
    times: pd.Series,
    pv_w: np.ndarray,
    load_w: np.ndarray,
    import_price: np.ndarray,
    export_price: np.ndarray,
    soc_percent: float,
    capacity_kwh: float = 14.0,
    max_power_w: float = 5000.0,
    charge_efficiency: float = 0.95,
    terminal_price: float | None = None,
    degradation_cost_eur_kwh: float = 0.002,
    soc_min: float = 0.0,
    soc_max: float = 100.0,
    step_hours: float = 1.0,
    solver: str = "lp",
    time_limit_seconds: float = 5.0,
    command_contract: str = "battery",
    first_step: dict[str, float] | None = None,
    first_charge_kw: float | None = None,
) -> RollingPlan:
    """Solve a rolling LP or explicit mutually exclusive MILP window."""
    n = len(pv_w)
    if command_contract not in ("battery", "sop") or (command_contract == "sop" and solver != "milp"):
        raise ValueError("SOP command constraints require MILP")
    if n == 0:
        raise ValueError("empty optimization window")
    fixed_first_step = _validated_first_step(first_step, first_charge_kw, n)
    arrays = [np.asarray(x, dtype=float) for x in (pv_w, load_w, import_price, export_price)]
    if any(x.ndim != 1 or len(x) != n or not np.isfinite(x).all() for x in arrays):
        raise ValueError("all forecast arrays must have equal length")
    pv_w, load_w, import_price, export_price = arrays
    if len(times) != n or solver not in ("lp", "milp"):
        raise ValueError("invalid timestamps or solver")
    parameters = [capacity_kwh, max_power_w, charge_efficiency, soc_min, soc_max,
                  step_hours, degradation_cost_eur_kwh, time_limit_seconds]
    if not np.isfinite(parameters).all() or (terminal_price is not None and not np.isfinite(terminal_price)):
        raise ValueError("parameters must be finite")
    if not (capacity_kwh > 0 and max_power_w > 0 and 0 < charge_efficiency <= 1
            and 0 <= soc_min <= soc_max <= 100 and step_hours > 0
            and degradation_cost_eur_kwh >= 0 and time_limit_seconds > 0):
        raise ValueError("invalid device bounds or solver settings")
    if (pv_w < 0).any() or (load_w < 0).any():
        raise ValueError("PV/load must be nonnegative")
    if not 0 <= soc_percent <= 100:
        raise ValueError("soc_percent must be between 0 and 100")
    # Variables per step: charge AC kW, discharge AC kW, import kW, export kW, end energy kWh.
    c = np.zeros(5 * n)
    for t in range(n):
        c[t] = degradation_cost_eur_kwh * step_hours
        c[n + t] = degradation_cost_eur_kwh * step_hours
        c[2 * n + t] = import_price[t] * step_hours
        c[3 * n + t] = -export_price[t] * step_hours
        if t == n - 1:
            c[4 * n + t] = -(terminal_price if terminal_price is not None else float(np.mean(import_price)))

    b_eq = np.zeros(2 * n)
    initial_energy = capacity_kwh * soc_percent / 100
    for t in range(n):
        b_eq[2 * t] = (pv_w[t] - load_w[t]) / 1000.0
        b_eq[2 * t + 1] = initial_energy if t == 0 else 0.0
    bounds = (
        [(0.0, max_power_w / 1000)] * n
        + [(0.0, max_power_w / 1000)] * n
        + [(0.0, None)] * n
        + [(0.0, None)] * n
        + [(capacity_kwh * soc_min / 100, capacity_kwh * soc_max / 100)] * n
    )
    equalities = _cached_equalities(n, float(charge_efficiency), float(step_hours))
    if solver == "lp":
        fixed_bounds = list(bounds)
        for index, value in fixed_first_step.items():
            original_lower, original_upper = fixed_bounds[index]
            if value < original_lower or (original_upper is not None and value > original_upper):
                return _intervention_infeasible(times, n, soc_percent)
            fixed_bounds[index] = (value, value)
        result = linprog(c, A_eq=equalities, b_eq=b_eq, bounds=fixed_bounds, method="highs")
    else:
        # Two binary vectors select charging vs discharging and import vs export.
        # Grid big-M values follow from fixed net demand and battery power limits,
        # rather than an arbitrary grid rating. No curtailment variable is modeled.
        pmax = max_power_w / 1000
        net_load = (load_w - pv_w) / 1000
        import_max = np.maximum(0, net_load + pmax)
        export_max = np.maximum(0, -net_load + pmax)
        lower = np.zeros(7 * n)
        upper = np.concatenate([np.full(2*n, pmax), import_max, export_max,
                                np.full(n, capacity_kwh * soc_max / 100), np.ones(2*n)])
        lower[4*n:5*n] = capacity_kwh * soc_min / 100
        for index, value in fixed_first_step.items():
            if value < lower[index] or value > upper[index]:
                return _intervention_infeasible(times, n, soc_percent)
            lower[index] = upper[index] = value
        inequality_rows = (6 if command_contract == "sop" else 4)*n
        a = np.zeros((2*n + inequality_rows, 7*n))
        a[:2*n, :5*n] = equalities
        lo = np.concatenate([b_eq, np.full(inequality_rows, -np.inf)])
        hi = np.concatenate([b_eq, np.zeros(inequality_rows)])
        for t in range(n):
            j = 2*n + 4*t
            a[j, t], a[j, 5*n+t] = 1, -pmax
            a[j+1, n+t], a[j+1, 5*n+t], hi[j+1] = 1, pmax, pmax
            a[j+2, 2*n+t], a[j+2, 6*n+t] = 1, -import_max[t]
            a[j+3, 3*n+t], a[j+3, 6*n+t], hi[j+3] = 1, export_max[t], export_max[t]
            if command_contract == "sop":
                # z_charge=0 is discharge: grid_import=0 and export<=Pmax.
                # z_charge=1 is charge/idle: keep the original grid bounds.
                # This excludes saturation-dependent discharge and self-use.
                k = 6*n + 2*t
                a[k, 2*n+t], a[k, 5*n+t] = 1, -import_max[t]
                a[k+1, 3*n+t], a[k+1, 5*n+t] = 1, pmax-export_max[t]
                hi[k+1] = pmax
        result = milp(np.concatenate([c, np.zeros(2*n)]),
                      integrality=np.concatenate([np.zeros(5*n), np.ones(2*n)]),
                      bounds=Bounds(lower, upper), constraints=LinearConstraint(a, lo, hi),
                      options={"time_limit": time_limit_seconds, "mip_rel_gap": 1e-9})
    if not result.success:
        empty = pd.DataFrame({"time": times, "flow": ["idle"] * n, "charge_w": 0.0, "discharge_w": 0.0,
                              "grid_import_w": 0.0, "grid_export_w": 0.0, "soc_end": soc_percent,
                              "intended_command_mode": ["zero_flow_command"] * n})
        status = {1: "limit_reached", 2: "infeasible", 3: "unbounded"}.get(result.status, "solver_error")
        return RollingPlan(empty, float("nan"), status, result.message)
    x = result.x
    charge, discharge = x[:n] * 1000, x[n:2 * n] * 1000
    grid_import, grid_export = x[2 * n:3 * n] * 1000, x[3 * n:4 * n] * 1000
    energy = x[4 * n:5 * n]
    flow = np.where(charge > 1e-3, "charge", np.where(discharge > 1e-3, "discharge", "idle"))
    plan = pd.DataFrame({
        "time": pd.to_datetime(times).to_numpy(), "flow": flow,
        "charge_w": charge, "discharge_w": discharge,
        "grid_import_w": grid_import, "grid_export_w": grid_export,
        "soc_end": energy / capacity_kwh * 100,
        # Plan-level intent only; execution still requires site verification (idle)
        # or dispatch readback (charge/discharge). Never implies a mode switch.
        "intended_command_mode": np.where(flow == "idle", "zero_flow_command", "dispatch"),
    })
    # LP can cycle under sufficiently negative export prices; only MILP enforces exclusivity.
    return RollingPlan(plan, float(result.fun), "optimal", result.message)
