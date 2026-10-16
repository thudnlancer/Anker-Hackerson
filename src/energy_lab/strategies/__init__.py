"""Optimizer strategy registry (milestone: strategy comparison).

    from energy_lab.strategies import get_strategy
    plan = get_strategy("gradient").solve(request)

Unknown names raise KeyError listing the registered strategies.
"""
from __future__ import annotations

from .base import OptimizerStrategy, StrategyRequest
from .gradient import GradientStrategy
from .milp import MilpStrategy

STRATEGIES: dict[str, type[OptimizerStrategy]] = {
    MilpStrategy.name: MilpStrategy,
    GradientStrategy.name: GradientStrategy,
}

__all__ = ["GradientStrategy", "MilpStrategy", "OptimizerStrategy", "STRATEGIES",
           "StrategyRequest", "get_strategy"]


def get_strategy(name: str, **overrides) -> OptimizerStrategy:
    try:
        cls = STRATEGIES[name]
    except KeyError:
        raise KeyError(f"unknown strategy {name!r}; registered: {sorted(STRATEGIES)}") from None
    return cls(**overrides)
