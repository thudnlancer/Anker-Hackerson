"""Online grid-battery-load physical model and conservative calibration helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable
import math

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class PhysicalParameters:
    capacity_kwh: float = 14.0
    max_power_w: float = 5000.0
    charge_efficiency: float = 0.95
    # The SOP accounts for the approximately 5% loss on the charging side;
    # it does not apply a second discharge-side efficiency penalty.
    discharge_efficiency: float = 1.0
    standby_power_w: float = 0.0


@dataclass(frozen=True)
class BalanceResult:
    net_grid_w: float
    import_w: float
    export_w: float
    residual_w: float


def power_balance(pv_w: float, load_w: float, charge_w: float, discharge_w: float,
                  grid_import_w: float | None = None, grid_export_w: float | None = None) -> BalanceResult:
    values = (pv_w, load_w, charge_w, discharge_w)
    if not all(math.isfinite(float(value)) and float(value) >= 0 for value in values):
        raise ValueError("power values must be finite and nonnegative")
    if charge_w > 1e-6 and discharge_w > 1e-6:
        raise ValueError("charge and discharge cannot be positive simultaneously")
    net = float(load_w + charge_w - pv_w - discharge_w)
    expected_import, expected_export = max(0.0, net), max(0.0, -net)
    if grid_import_w is None and grid_export_w is None:
        return BalanceResult(net, expected_import, expected_export, 0.0)
    if grid_import_w is None or grid_export_w is None:
        raise ValueError("both grid import and export are required for residual checking")
    if not all(math.isfinite(float(value)) and float(value) >= 0 for value in (grid_import_w, grid_export_w)):
        raise ValueError("grid values must be finite and nonnegative")
    observed_net = float(grid_import_w) - float(grid_export_w)
    return BalanceResult(net, expected_import, expected_export, observed_net - net)


def next_soc_percent(soc_percent: float, charge_w: float, discharge_w: float,
                     duration_hours: float, parameters: PhysicalParameters) -> float:
    if not math.isfinite(soc_percent) or not 0 <= soc_percent <= 100:
        raise ValueError("soc must be in [0, 100]")
    if not math.isfinite(duration_hours) or duration_hours <= 0:
        raise ValueError("duration_hours must be positive")
    if charge_w < 0 or discharge_w < 0 or charge_w > parameters.max_power_w + 1e-6 or discharge_w > parameters.max_power_w + 1e-6:
        raise ValueError("battery power is outside device limits")
    if charge_w > 1e-6 and discharge_w > 1e-6:
        raise ValueError("charge and discharge cannot be positive simultaneously")
    energy_kwh = soc_percent / 100 * parameters.capacity_kwh
    energy_kwh += parameters.charge_efficiency * charge_w / 1000 * duration_hours
    energy_kwh -= discharge_w / 1000 / parameters.discharge_efficiency * duration_hours
    return float(np.clip(energy_kwh / parameters.capacity_kwh * 100, 0, 100))


def calibrate_pv_scale(irradiance_wm2: Iterable[float], pv_w: Iterable[float], *, max_power_w: float = 5000.0) -> dict:
    """Fit a robust nonnegative PV/irradiance scale; no intercept or extrapolated power."""
    irradiance = np.asarray(list(irradiance_wm2), dtype=float)
    pv = np.asarray(list(pv_w), dtype=float)
    if irradiance.size != pv.size or irradiance.size < 1:
        raise ValueError("irradiance and pv must have equal non-empty lengths")
    valid = np.isfinite(irradiance) & np.isfinite(pv) & (irradiance > 25) & (pv >= 0)
    if not valid.any():
        raise ValueError("no valid daylight calibration points")
    ratios = pv[valid] / irradiance[valid]
    scale = float(np.median(ratios))
    return {"scale_w_per_wm2": max(0.0, scale), "samples": int(valid.sum()),
            "max_power_w": float(max_power_w), "method": "median_daylight_ratio"}


def pv_from_irradiance(irradiance_wm2: float, calibration: dict, *, max_power_w: float = 5000.0) -> float:
    if not math.isfinite(float(irradiance_wm2)) or irradiance_wm2 < 0:
        raise ValueError("irradiance must be finite and nonnegative")
    scale = float(calibration.get("scale_w_per_wm2", float("nan")))
    if not math.isfinite(scale) or scale < 0:
        raise ValueError("invalid PV calibration")
    return float(np.clip(irradiance_wm2 * scale, 0, max_power_w))


def load_interval(history_w: Iterable[float], *, quantiles: tuple[float, float] = (.1, .9)) -> dict:
    values = np.asarray(list(history_w), dtype=float)
    if values.size < 1 or not np.isfinite(values).all() or (values < 0).any():
        raise ValueError("load history must be finite and nonnegative")
    low, high = quantiles
    if not 0 <= low <= high <= 1:
        raise ValueError("invalid quantiles")
    return {"p_low_w": float(np.quantile(values, low)),
            "p50_w": float(np.quantile(values, .5)),
            "p_high_w": float(np.quantile(values, high)),
            "samples": int(values.size)}


def loss_diagnostic(balance: BalanceResult, *, charge_w: float, discharge_w: float,
                    standby_power_w: float = 0.0) -> dict:
    return {"power_balance_residual_w": float(balance.residual_w),
            "absolute_power_balance_residual_w": abs(float(balance.residual_w)),
            "battery_throughput_w": float(charge_w + discharge_w),
            "standby_power_w": float(max(0.0, standby_power_w))}
