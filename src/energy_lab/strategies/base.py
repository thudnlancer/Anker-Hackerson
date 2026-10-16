"""Pluggable optimizer strategy interface (milestone: strategy comparison).

Every strategy turns one 15-minute-step window request into a RollingPlan, so
callers (shadow.make_candidate, contest_replay) never see solver specifics.

Units follow the existing pipeline contract: power in W, energy in kWh,
prices in EUR/kWh, times tz-aware at step_hours spacing.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from ..rolling import RollingPlan


@dataclass
class StrategyRequest:
    times: pd.Series
    pv_w: np.ndarray
    load_w: np.ndarray
    import_price: np.ndarray
    export_price: np.ndarray
    soc_percent: float
    capacity_kwh: float = 14.0
    max_power_w: float = 5000.0
    soc_min: float = 0.0
    soc_max: float = 100.0
    step_hours: float = 0.25
    time_limit_seconds: float = 5.0
    previous_power_kw: float = 0.0
    charge_efficiency: float = 0.95
    # Optional terminal-value / wear knobs used by the offline replay; the live
    # shadow path keeps its frozen defaults (terminal None, wear 0.02).
    terminal_price: float | None = None
    degradation_cost_eur_kwh: float = 0.02

    def validate(self) -> None:
        n = len(self.times)
        arrays = [np.asarray(a, dtype=float)
                  for a in (self.pv_w, self.load_w, self.import_price, self.export_price)]
        if n == 0 or any(len(a) != n for a in arrays):
            raise ValueError("request arrays must match the time index length")
        if not all(np.isfinite(a).all() for a in arrays):
            raise ValueError("request arrays must be finite")
        if not 0 <= self.soc_min < self.soc_max <= 100:
            raise ValueError("require 0 <= soc_min < soc_max <= 100")
        if not self.soc_min <= self.soc_percent <= self.soc_max:
            raise ValueError("soc_percent outside [soc_min, soc_max]")
        if self.capacity_kwh <= 0 or self.max_power_w <= 0 or self.step_hours <= 0:
            raise ValueError("capacity, max power and step must be positive")


class OptimizerStrategy:
    """Strategy protocol: name + solve(request) -> RollingPlan."""

    name: str = "abstract"

    def solve(self, request: StrategyRequest) -> RollingPlan:  # pragma: no cover
        raise NotImplementedError
