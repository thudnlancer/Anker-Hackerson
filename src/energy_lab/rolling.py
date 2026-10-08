from __future__ import annotations

from dataclasses import dataclass
import numpy as np
import pandas as pd
from scipy.optimize import linprog


@dataclass
class RollingPlan:
    plan: pd.DataFrame
    objective: float
    status: str
    message: str


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
) -> RollingPlan:
    """Solve one rolling LP window; only the first row should be executed."""
    n = len(pv_w)
    if n == 0:
        raise ValueError("empty optimization window")
    arrays = [np.asarray(x, dtype=float) for x in (pv_w, load_w, import_price, export_price)]
    if any(len(x) != n for x in arrays):
        raise ValueError("all forecast arrays must have equal length")
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

    a_eq, b_eq = [], []
    initial_energy = capacity_kwh * soc_percent / 100
    for t in range(n):
        row = np.zeros(5 * n)
        row[t] = 1.0
        row[n + t] = -1.0
        row[2 * n + t] = -1.0
        row[3 * n + t] = 1.0
        a_eq.append(row)
        b_eq.append((pv_w[t] - load_w[t]) / 1000.0)
        soc_row = np.zeros(5 * n)
        soc_row[4 * n + t] = 1.0
        soc_row[t] = -charge_efficiency * step_hours
        soc_row[n + t] = step_hours
        if t:
            soc_row[4 * n + t - 1] = -1.0
            rhs = 0.0
        else:
            rhs = initial_energy
        a_eq.append(soc_row)
        b_eq.append(rhs)
    bounds = (
        [(0.0, max_power_w / 1000)] * n
        + [(0.0, max_power_w / 1000)] * n
        + [(0.0, None)] * n
        + [(0.0, None)] * n
        + [(capacity_kwh * soc_min / 100, capacity_kwh * soc_max / 100)] * n
    )
    result = linprog(c, A_eq=np.asarray(a_eq), b_eq=np.asarray(b_eq), bounds=bounds, method="highs")
    if not result.success:
        empty = pd.DataFrame({"time": times, "flow": ["hold"] * n, "charge_w": 0.0, "discharge_w": 0.0, "grid_import_w": 0.0, "grid_export_w": 0.0, "soc_end": soc_percent})
        return RollingPlan(empty, float("nan"), "infeasible", result.message)
    x = result.x
    charge, discharge = x[:n] * 1000, x[n:2 * n] * 1000
    grid_import, grid_export = x[2 * n:3 * n] * 1000, x[3 * n:4 * n] * 1000
    energy = x[4 * n:5 * n]
    flow = np.where(charge > 1e-3, "charge", np.where(discharge > 1e-3, "discharge", "hold"))
    plan = pd.DataFrame({
        "time": pd.to_datetime(times).to_numpy(), "flow": flow,
        "charge_w": charge, "discharge_w": discharge,
        "grid_import_w": grid_import, "grid_export_w": grid_export,
        "soc_end": energy / capacity_kwh * 100,
    })
    # The LP has positive cycling cost, so simultaneous charge/discharge is not optimal.
    return RollingPlan(plan, float(result.fun), "optimal", result.message)
