"""Offline rolling decision and explicit fallback, shared by experiments."""
from dataclasses import dataclass
import numpy as np

from .model import dispatch
from .rolling import RollingPlan, optimize_window


def self_consumption(pv_w, load_w):
    """Use surplus for charging and battery for deficit; residual PV may export."""
    surplus = float(pv_w) - float(load_w)
    return ("charge", surplus) if surplus > 0 else ("discharge", -surplus) if surplus < 0 else ("hold", 0.0)


@dataclass
class Decision:
    flow: str
    power_w: float
    status: str
    reason: str
    fallback: bool
    plan: RollingPlan | None


def decide(times, pv, load, buy, sell, soc, *, observed_pv, observed_load, **settings):
    # A bad observation cannot safely drive a fallback; the caller must skip it.
    if not np.isfinite([observed_pv, observed_load, soc]).all() or min(observed_pv, observed_load) < 0 or not 0 <= soc <= 100:
        raise ValueError("invalid current observation: do not issue a command")
    try:
        plan = optimize_window(times, pv, load, buy, sell, soc, **settings)
        status, reason = plan.status, plan.message
    except ValueError as exc:
        plan, status, reason = None, "invalid_input", str(exc)
    if status == "optimal":
        first = plan.plan.iloc[0]
        flow = str(first.flow)
        power = float(first.charge_w if flow == "charge" else first.discharge_w if flow == "discharge" else 0)
        return Decision(flow, power, status, reason, False, plan)
    # Preserve the established rule fallback if prices are usable. Corrupted
    # forecasts/prices use observed self-consumption without invented prices.
    prices_valid = len(buy) and len(sell) and np.isfinite(buy).all() and np.isfinite(sell).all()
    if status != "invalid_input" and prices_valid:
        flow, power, _ = dispatch(float(buy[0]), float(sell[0]), observed_pv, observed_load,
                                  soc, list(buy[1:]), settings.get("capacity_kwh", 14),
                                  settings.get("max_power_w", 5000))
    else:
        flow, power = self_consumption(observed_pv, observed_load)
    return Decision(flow, power, status, reason, True, plan)
