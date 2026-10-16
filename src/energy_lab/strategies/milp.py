"""MILP strategy: adapter over rolling.optimize_window (the incumbent solver).

Preserves the exact parameters the live pipeline used before the strategy
refactor (SOP command contract, degradation 0.02 EUR/kWh, no terminal price).
"""
from __future__ import annotations

from ..rolling import RollingPlan, optimize_window
from .base import OptimizerStrategy, StrategyRequest


class MilpStrategy(OptimizerStrategy):
    name = "milp"

    def solve(self, request: StrategyRequest) -> RollingPlan:
        request.validate()
        return optimize_window(
            request.times, request.pv_w, request.load_w,
            request.import_price, request.export_price,
            request.soc_percent,
            capacity_kwh=request.capacity_kwh,
            max_power_w=request.max_power_w,
            charge_efficiency=request.charge_efficiency,
            soc_min=request.soc_min, soc_max=request.soc_max,
            terminal_price=request.terminal_price,
            degradation_cost_eur_kwh=request.degradation_cost_eur_kwh,
            solver="milp", step_hours=request.step_hours,
            command_contract="sop",
            time_limit_seconds=request.time_limit_seconds)
