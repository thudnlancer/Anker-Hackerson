"""Gradient-descent battery scheduling strategy (Python port of PROJECT_Ank's
ContestGradientPlan.m, the contest variant with the soft solve-time limit and
the self-consumption deadband physics fix).

The solver optimizes battery energy increments u (kWh per step, + charge) with
Adam on a differentiable surrogate (smooth bill + degradation + soft start
gates + variation + terminal penalty), applies a sequential feasibility repair
after every step, and selects the final candidate by the exact non-smooth
cost.  It is a heuristic: the returned plan is feasible but never claimed
globally optimal (suboptimal=True, mirroring MILP time-limit incumbents).

The port is line-faithful to the MATLAB reference; keep numerical behavior
aligned when editing.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from time import monotonic

import numpy as np
import pandas as pd

from ..rolling import RollingPlan
from .base import OptimizerStrategy, StrategyRequest


@dataclass
class GradientConfig:
    dt: float = 0.25                       # step hours; solve() overrides from request
    capacity: float = 14.0
    emin: float = 1.4
    emax: float = 13.3
    initial_energy: float = 7.0
    target_energy: float = 7.0
    pmax: float = 5.0
    eta_charge: float = 0.95
    eta_discharge: float = 1.0
    grid_buy_max: float = 15.0
    grid_sell_max: float = 5.0
    degradation: float = 0.002
    start_cost: float = 0.03
    variation_cost: float = 0.005
    terminal_penalty: float = 0.10
    deadband: float = 0.05
    gate_scale: float = 0.2
    hinge_width: float = 0.1
    smooth_power: float = 0.02
    learning_rate: float = 0.08
    iterations: int = 120
    line_search_steps: int = 8
    max_solve_seconds: float = float("inf")

    def validate(self) -> None:
        if not (self.dt > 0 and 0 < self.eta_charge <= 1 and 0 < self.eta_discharge <= 1):
            raise ValueError("invalid step or efficiency")
        if not (self.learning_rate > 0 and self.smooth_power > 0
                and self.gate_scale > 0 and self.hinge_width > 0):
            raise ValueError("learning rate and smoothing scales must be positive")
        if not (0 <= self.deadband < self.pmax and self.emin < self.emax):
            raise ValueError("invalid deadband or energy bounds")
        if self.iterations <= 0 or self.line_search_steps <= 0:
            raise ValueError("iterations and line search steps must be positive")


@dataclass
class Forecast:
    pv: np.ndarray
    load: np.ndarray
    buy: np.ndarray
    sell: np.ndarray


def power(u: np.ndarray, c: GradientConfig) -> tuple[np.ndarray, np.ndarray]:
    """Battery power kW (+ charge) from energy increments, with du/dp."""
    p = np.maximum(u, 0) / (c.eta_charge * c.dt) + np.minimum(u, 0) * c.eta_discharge / c.dt
    dp = np.full_like(u, c.eta_discharge / c.dt)
    dp[u > 0] = 1 / (c.eta_charge * c.dt)
    dp[u == 0] = 0.5 * (1 / (c.eta_charge * c.dt) + c.eta_discharge / c.dt)
    return p, dp


def increment(p: np.ndarray, c: GradientConfig) -> np.ndarray:
    p = np.asarray(p, dtype=float)
    return c.dt * (c.eta_charge * np.maximum(p, 0) + np.minimum(p, 0) / c.eta_discharge)


def self_consumption(pv: float, load_kw: float, energy: float,
                     c: GradientConfig) -> tuple[float, float]:
    """Automatic self-use physics: (battery power kW, new energy kWh)."""
    pc = min(max(0.0, pv - load_kw), c.pmax,
             max(0.0, c.emax - energy) / (c.eta_charge * c.dt))
    pd_ = min(max(0.0, load_kw - pv), c.pmax,
              max(0.0, energy - c.emin) * c.eta_discharge / c.dt)
    p = pc - pd_
    return p, energy + c.eta_charge * pc * c.dt - pd_ * c.dt / c.eta_discharge


def repair(raw: np.ndarray, d: Forecast, energy: float, c: GradientConfig) -> np.ndarray:
    """Sequential feasibility repair (not a global Euclidean projection)."""
    raw = np.asarray(raw, dtype=float)
    u = np.zeros_like(raw)
    e = energy
    for t in range(raw.size):
        base = d.load[t] - d.pv[t]
        lo_p = max(-c.pmax, -c.grid_sell_max - base)
        hi_p = min(c.pmax, c.grid_buy_max - base)
        lo = max(increment(np.array(lo_p), c).item(), c.emin - e)
        hi = min(increment(np.array(hi_p), c).item(), c.emax - e)
        if lo > hi + 1e-9:
            raise ValueError("GradientInfeasible: load/pv cannot satisfy grid limits")
        value = max(lo, min(hi, raw[t]))
        p, _ = power(np.array(value), c)
        p = p.item()
        if p < 0 and base + p > 1e-9:
            # Partial discharge while still importing cannot map to a non-negative
            # sell target; snap to idle or exact cover, whichever is nearer raw.
            cover = increment(np.array(-base), c).item()
            candidates = []
            if lo <= 0 <= hi:
                candidates.append(0.0)
            if lo - 1e-9 <= cover <= hi + 1e-9:
                candidates.append(cover)
            if not candidates:
                raise ValueError("GradientMapping: no representable discharge action")
            value = min(candidates, key=lambda x: abs(x - raw[t]))
        p = power(np.array(value), c)[0].item()
        if abs(p) < c.deadband and lo <= 0 <= hi:
            # No verified hold interface: a zero action executes self-use, and
            # the prediction must account for it.
            automatic, _ = self_consumption(d.pv[t], d.load[t], e, c)
            value = increment(np.array(automatic), c).item()
            if not (lo - 1e-8 <= value <= hi + 1e-8):
                raise ValueError("GradientRepair: self-use action violates device limits")
        u[t] = value
        e += value
    return u


def hinge(x: np.ndarray, width: float) -> tuple[np.ndarray, np.ndarray]:
    h = np.zeros_like(x)
    g = np.zeros_like(x)
    middle = (x > 0) & (x < width)
    upper = x >= width
    h[middle] = x[middle] ** 2 / (2 * width)
    g[middle] = x[middle] / width
    h[upper] = x[upper] - width / 2
    g[upper] = 1.0
    return h, g


def objective(u: np.ndarray, d: Forecast, energy: float, previous_power: float,
              c: GradientConfig) -> tuple[float, np.ndarray]:
    """Differentiable surrogate objective and analytic gradient."""
    p, dp = power(u, c)
    grid = d.load - d.pv + p
    eps = c.smooth_power
    root = np.sqrt(grid ** 2 + eps ** 2)
    bill = 0.5 * (d.buy + d.sell) * grid + 0.5 * (d.buy - d.sell) * root
    throughput = np.sqrt(p ** 2 + eps ** 2)
    g_p = c.dt * (0.5 * (d.buy + d.sell) + 0.5 * (d.buy - d.sell) * grid / root
                  + c.degradation * p / throughput)
    positive = np.maximum(p, 0)
    negative = np.maximum(-p, 0)
    qc = 1 - np.exp(-((positive / c.gate_scale) ** 2))
    qd = 1 - np.exp(-((negative / c.gate_scale) ** 2))
    qc0 = 1 - np.exp(-((max(previous_power, 0.0) / c.gate_scale) ** 2))
    qd0 = 1 - np.exp(-((max(-previous_power, 0.0) / c.gate_scale) ** 2))
    dc = qc - np.concatenate([[qc0], qc[:-1]])
    dd = qd - np.concatenate([[qd0], qd[:-1]])
    hc, gc = hinge(dc, c.hinge_width)
    hd, gd = hinge(dd, c.hinge_width)
    dqc = 2 * positive / c.gate_scale ** 2 * np.exp(-((positive / c.gate_scale) ** 2))
    dqd = -2 * negative / c.gate_scale ** 2 * np.exp(-((negative / c.gate_scale) ** 2))
    gate_gradient = ((gc - np.concatenate([gc[1:], [0.0]])) * dqc
                     + (gd - np.concatenate([gd[1:], [0.0]])) * dqd)
    delta = p - np.concatenate([[previous_power], p[:-1]])
    tv_root = np.sqrt(delta ** 2 + eps ** 2)
    v = delta / tv_root
    g_p = g_p + c.start_cost * gate_gradient + c.variation_cost * (v - np.concatenate([v[1:], [0.0]]))
    end_energy = energy + float(np.sum(u))
    inventory_price = float(np.median(d.buy))
    terminal = (inventory_price * (energy - end_energy)
                + c.terminal_penalty * (end_energy - c.target_energy) ** 2)
    value = (float(np.sum(bill + c.degradation * (throughput - eps))) * c.dt
             + c.start_cost * float(np.sum(hc + hd))
             + c.variation_cost * float(np.sum(tv_root - eps)) + terminal)
    g = g_p * dp - inventory_price + 2 * c.terminal_penalty * (end_energy - c.target_energy)
    return value, g


def exact(u: np.ndarray, d: Forecast, energy: float, previous_power: float,
          c: GradientConfig) -> dict:
    """True piecewise bill and discrete start count; the selection criterion."""
    p, _ = power(u, c)
    grid = d.load - d.pv + p
    mode = np.sign(p)
    mode[np.abs(p) < c.deadband - 1e-8] = 0
    previous_mode = 0.0 if abs(previous_power) < c.deadband else float(np.sign(previous_power))
    started = (mode != 0) & (mode != np.concatenate([[previous_mode], mode[:-1]]))
    net_cost = float(np.sum(d.buy * np.maximum(grid, 0) - d.sell * np.maximum(-grid, 0)
                            + c.degradation * np.abs(p))) * c.dt
    end_energy = energy + float(np.sum(u))
    adjusted = net_cost - float(np.median(d.buy)) * (end_energy - energy)
    variation = float(np.sum(np.abs(np.diff(np.concatenate([[previous_power], p])))))
    weighted = (adjusted + c.start_cost * float(np.sum(started))
                + c.variation_cost * variation
                + c.terminal_penalty * (end_energy - c.target_energy) ** 2)
    return {"net_cost": net_cost, "adjusted_cost": adjusted, "starts": int(np.sum(started)),
            "variation": variation, "weighted_cost": weighted, "end_energy": end_energy}


def solve(d: Forecast, energy: float, previous_power: float, c: GradientConfig,
          warm_power: np.ndarray | None = None) -> dict:
    c.validate()
    d = Forecast(*[np.asarray(np.atleast_1d(a), dtype=float).ravel()
                   for a in (d.pv, d.load, d.buy, d.sell)])
    n = d.buy.size
    if n == 0 or not (d.load >= 0).all() or not (d.pv >= 0).all():
        raise ValueError("empty forecast or negative power")
    for name, a in (("pv", d.pv), ("load", d.load), ("sell", d.sell)):
        if a.size != n or not np.isfinite(a).all():
            raise ValueError(f"forecast field {name} length/finite mismatch")
    if not (np.isfinite(energy) and c.emin <= energy <= c.emax):
        raise ValueError("initial energy outside [emin, emax]")
    if not (np.isfinite(previous_power) and abs(previous_power) <= c.pmax):
        raise ValueError("invalid previous power")

    seeds = [np.zeros(n), increment(d.pv - d.load, c)]
    ordered = np.sort(d.buy)
    low = ordered[max(0, int(round(0.25 * n)) - 1)]
    high = ordered[max(0, int(round(0.75 * n)) - 1)]
    price_power = np.zeros(n)
    if high > low + 1e-9:
        price_power[d.buy <= low] = c.pmax
        price_power[d.buy >= high] = -c.pmax
    seeds.append(increment(price_power, c))
    if warm_power is not None:
        warm = np.asarray(warm_power, dtype=float).ravel()
        if warm.size != n:
            raise ValueError("warm start plan length mismatch")
        seeds.append(increment(warm, c))

    best_score = float("inf")
    best_u: np.ndarray | None = None
    best_initial = float("inf")
    accepted = 0
    best_source = ""
    timer = monotonic()
    for s, seed in enumerate(seeds):
        if best_u is not None and monotonic() - timer >= c.max_solve_seconds:
            break
        u = repair(seed, d, energy, c)
        exact_now = exact(u, d, energy, previous_power, c)
        best_initial = min(best_initial, exact_now["weighted_cost"])
        if exact_now["weighted_cost"] < best_score:
            best_score = exact_now["weighted_cost"]
            best_u = u
            best_source = f"seed{s}"
        m = np.zeros(n)
        v = np.zeros(n)
        f, g = objective(u, d, energy, previous_power, c)
        for iteration in range(1, c.iterations + 1):
            if monotonic() - timer >= c.max_solve_seconds:
                break
            m = 0.9 * m + 0.1 * g
            v = 0.999 * v + 0.001 * g ** 2
            direction = ((m / (1 - 0.9 ** iteration))
                         / (np.sqrt(v / (1 - 0.999 ** iteration)) + 1e-8))
            if float(g @ direction) <= 0:
                direction = g / max(1.0, float(np.linalg.norm(g, np.inf)))
            success = False
            candidate = u
            for line in range(c.line_search_steps):
                if monotonic() - timer >= c.max_solve_seconds:
                    break
                step = c.learning_rate * 0.5 ** line
                candidate = repair(u - step * direction, d, energy, c)
                new_f, new_g = objective(candidate, d, energy, previous_power, c)
                if new_f < f - 1e-10:
                    success = True
                    break
            if not success:
                break
            change = float(np.max(np.abs(candidate - u)))
            u, f, g = candidate, new_f, new_g
            accepted += 1
            exact_now = exact(u, d, energy, previous_power, c)
            if exact_now["weighted_cost"] < best_score:
                best_score = exact_now["weighted_cost"]
                best_u = u
                best_source = f"seed{s}/iter{iteration}"
            if change < 1e-6:
                break

    assert best_u is not None
    p, _ = power(best_u, c)
    grid = d.load - d.pv + p
    e = energy + np.cumsum(best_u)
    if not (np.all(e >= c.emin - 1e-7) and np.all(e <= c.emax + 1e-7)
            and np.all(np.abs(p) <= c.pmax + 1e-7)):
        raise ValueError("post-repair constraint check failed")
    if not (np.all(grid <= c.grid_buy_max + 1e-7) and np.all(grid >= -c.grid_sell_max - 1e-7)):
        raise ValueError("grid power bounds violated")

    actions = []
    previous_energy = energy
    for k in range(n):
        automatic, _ = self_consumption(d.pv[k], d.load[k], previous_energy, c)
        if p[k] < -1e-8 and grid[k] > 1e-7:
            if abs(p[k] - automatic) >= 1e-7:
                raise ValueError("partial discharge must execute via self-consumption mode")
            actions.append("self_consumption")
        elif p[k] > 1e-8:
            actions.append("charge")
        elif p[k] < -1e-8:
            actions.append("discharge")
        else:
            actions.append("self_consumption")
        previous_energy = e[k]

    return {"actions": actions, "power_kw": p, "increment_kwh": best_u, "energy_kwh": e,
            "grid_buy_kw": np.maximum(grid, 0), "grid_sell_kw": np.maximum(-grid, 0),
            "metrics": exact(best_u, d, energy, previous_power, c),
            "initial_best_weighted_cost": best_initial,
            "accepted_gradient_steps": accepted, "best_source": best_source,
            "improved_over_initial": best_score < best_initial - 1e-8,
            "is_global_optimal": False, "solve_seconds": monotonic() - timer}


class GradientStrategy(OptimizerStrategy):
    name = "gradient"

    def __init__(self, **overrides):
        self.overrides = overrides

    def solve(self, request: StrategyRequest) -> RollingPlan:
        request.validate()
        config_fields = dict(
            dt=request.step_hours, capacity=request.capacity_kwh,
            emin=request.soc_min / 100 * request.capacity_kwh,
            emax=request.soc_max / 100 * request.capacity_kwh,
            initial_energy=request.soc_percent / 100 * request.capacity_kwh,
            target_energy=(request.soc_min + request.soc_max) / 200 * request.capacity_kwh,
            pmax=request.max_power_w / 1000,
            max_solve_seconds=request.time_limit_seconds,
            eta_charge=request.charge_efficiency,
            # Scenario economics: the caller's wear cost is honored so strategy
            # comparisons see identical physics; pass degradation=... to the
            # constructor to pin the MATLAB-tuned value instead.
            degradation=request.degradation_cost_eur_kwh)
        c = GradientConfig(**{**config_fields, **self.overrides})
        d = Forecast(pv=request.pv_w / 1000, load=request.load_w / 1000,
                     buy=np.asarray(request.import_price, dtype=float),
                     sell=np.asarray(request.export_price, dtype=float))
        plan = solve(d, c.initial_energy, request.previous_power_kw, c)
        return RollingPlan(plan=_plan_frame(request, plan),
                           objective=plan["metrics"]["weighted_cost"],
                           status="heuristic", suboptimal=True,
                           message=(f"gradient:{plan['best_source']} "
                                    f"steps={plan['accepted_gradient_steps']} "
                                    f"t={plan['solve_seconds']:.3f}s"))


def _plan_frame(request: StrategyRequest, plan: dict) -> pd.DataFrame:
    p = plan["power_kw"]
    charge = np.maximum(p, 0) * 1000
    discharge = np.maximum(-p, 0) * 1000
    flow = np.where(np.array(plan["actions"]) == "charge", "charge",
                    np.where(np.array(plan["actions"]) == "discharge", "discharge", "idle"))
    return pd.DataFrame({
        "time": pd.to_datetime(request.times).to_numpy(), "flow": flow,
        "charge_w": charge, "discharge_w": discharge,
        "grid_import_w": plan["grid_buy_kw"] * 1000,
        "grid_export_w": plan["grid_sell_kw"] * 1000,
        "soc_end": plan["energy_kwh"] / request.capacity_kwh * 100,
        "intended_command_mode": np.where(flow == "idle", "zero_flow_command", "dispatch"),
    })
