"""Causal offline contest-window diagnostics; no network or device writes."""
from __future__ import annotations

import numpy as np
import pandas as pd

from .interface_contract import map_battery_action
from .model import Battery
from .strategy_backtest import _policy_step
from .system_model import calibrate_pv_scale, pv_from_irradiance


def self_use(battery, row):
    net = float(row.pv - row.load)
    flow = "charge" if net > 0 else "discharge" if net < 0 else "idle"
    return _policy_step(battery, flow, abs(net), row, row.buy, row.sell, 0.)


def execute_command(battery, row, action, planned_pv, planned_load, *, hold_mode="self_consumption"):
    """Map planned battery power to SOP command, then apply actual energy limits.

    Planned idle steps carry an explicit execution mode (self_consumption or the
    candidate zero-flow command). This is not an assertion that zero-charge and
    self-use are interchangeable.
    """
    if hold_mode not in ("self_consumption", "zero_flow"):
        raise ValueError("hold_mode must be self_consumption or zero_flow")
    flow = str(action.flow)
    power = float(action.charge_w if flow == "charge" else
                  action.discharge_w if flow == "discharge" else 0.)
    command_flow = flow
    # Legacy "hold" plan labels are still accepted on read (R6); the explicit
    # idle_mode decides which execution semantics an idle step gets.
    mapping = map_battery_action(flow, power, float(planned_pv), float(planned_load),
                                 idle_mode=hold_mode if flow in ("idle", "hold") else None)
    if not mapping.representable:
        raise ValueError("unrepresentable command: " + mapping.reason)
    if flow in ("hold", "idle"):
        if hold_mode == "self_consumption":
            result = self_use(battery, row)
            mode = "self_consumption"
            command_flow = "self_consumption"
        else:
            # Offline representation of a candidate zero-flow command. The
            # actual HA response must be verified before this is used live.
            result = _policy_step(battery, "idle", 0., row, row.buy, row.sell, 0.)
            mode = "zero_flow"
            command_flow = "charge"
        target = 0.
    else:
        requested = (max(0., mapping.target_w - (float(row.pv) - float(row.load)))
                     if flow == "discharge" else power)
        result = _policy_step(battery, flow, requested, row, row.buy, row.sell, 0.)
        mode, target = flow, float(mapping.target_w)
    attributed = mode != "self_consumption"
    return {**result, "command_mode": mode, "command_flow": command_flow, "command_target_w": target,
            "planned_flow": flow, "planned_power_w": power,
            # A5 attribution convention, identical to the online execute layer:
            # an explicit self-use step is device-driven and never optimizer-
            # attributed (baseline arm and failure fallback); dispatch and
            # zero-flow commands execute the optimizer's plan.
            "optimizer_attributed": attributed,
            "attribution": "optimizer-attributed" if attributed else "non-optimizer-attributed"}


def power_inputs(current, history, published):
    """Future labels are not accepted. Shortwave is an explicitly offline proxy.

    The caller passes history strictly before now, plus only published-like
    times/prices/weather (never future PV/load). First-step HA powers override.
    """
    if len(history) == 0 or not (history.time < current.time).all():
        raise ValueError("calibration history must precede the decision")
    if {"pv", "load"} & set(published.columns):
        raise ValueError("future target columns are forbidden")
    if published.time.iloc[0] != current.time:
        raise ValueError("published horizon must start at current device time")
    n = len(published)
    pv = np.full(n, float(current.pv))
    calibration = None
    try:
        calibration = calibrate_pv_scale(history.shortwave_radiation_wm2, history.pv)
        pv = np.array([pv_from_irradiance(v, calibration)
                       for v in published.shortwave_radiation_wm2])
    except ValueError:
        pass  # missing daylight history disables PV calibration, not strategy
    pv[0] = float(current.pv)
    # Match historical load by device hour.  This is still an interval
    # heuristic, but avoids the unrealistic assumption that the whole day has
    # one constant load level.  With sparse history, fall back to all history.
    historical_hours = pd.to_datetime(history.time).dt.hour.to_numpy()
    future_hours = pd.to_datetime(published.time).dt.hour.to_numpy()
    profiles = []
    for quantile in (.1, .5, .9):
        profile = []
        for hour in future_hours:
            samples = history.load.to_numpy(dtype=float)[historical_hours == hour]
            if len(samples) == 0:
                samples = history.load.to_numpy(dtype=float)
            profile.append(float(np.quantile(samples, quantile)))
        profiles.append(np.asarray(profile))
    scenarios = profiles
    p75_profile = []
    for hour in future_hours:
        samples = history.load.to_numpy(dtype=float)[historical_hours == hour]
        if len(samples) == 0:
            samples = history.load.to_numpy(dtype=float)
        p75_profile.append(float(np.quantile(samples, .75)))
    interval = np.maximum(float(current.load), np.asarray(p75_profile))
    for scenario in scenarios:
        scenario[0] = float(current.load)
    interval[0] = float(current.load)
    return {"persistence_pv": np.full(n, float(current.pv)), "irradiance_pv": pv,
            "current_load": np.full(n, float(current.load)), "interval_load": interval,
            "load_scenarios": scenarios, "calibration": calibration}


def scenario_cost(plan, published, planned_pv, planned_load, scenario_load,
                  initial_soc, terminal_price, wear, hold_mode="self_consumption"):
    """Open-loop stress evaluation of a fixed command plan, NOT robust MPC.

    Every alternative sees identical forecast PV and historical load scenarios;
    physical clipping and self-use hold are simulated. No realized future is used.
    """
    battery = Battery(soc=initial_soc)
    total = 0.
    for i, action in enumerate(plan.itertuples(index=False)):
        row = pd.Series({"pv": planned_pv[i], "load": scenario_load[i],
                         "buy": published.buy.iloc[i], "sell": published.sell.iloc[i]})
        step = execute_command(battery, row, action, planned_pv[i], planned_load[i], hold_mode=hold_mode)
        total += step["cost_eur"] + wear * step["throughput_kwh"]
    return total - battery.soc / 100 * battery.capacity_kwh * terminal_price


def select_guarded(point_plan, interval_plan, published, paths, initial_soc,
                   terminal_price, wear, threshold, hold_mode="self_consumption"):
    if not np.isfinite(threshold) or threshold < 0:
        raise ValueError("nonnegative finite gate threshold required")
    if point_plan.status != "optimal" or interval_plan.status != "optimal":
        return False, None
    gains = []
    try:
        for loads in paths["load_scenarios"]:
            point = scenario_cost(point_plan.plan, published, paths["irradiance_pv"],
                                  paths["current_load"], loads, initial_soc, terminal_price, wear, hold_mode)
            candidate = scenario_cost(interval_plan.plan, published, paths["irradiance_pv"],
                                      paths["interval_load"], loads, initial_soc, terminal_price, wear, hold_mode)
            gains.append(point - candidate)
    except ValueError:
        # A plan that cannot be represented at one scenario's grid point is
        # not a guarded candidate; it must never abort the whole replay.
        return False, None
    margin = float(min(gains))
    return margin > threshold, margin


def window_metrics(trace, settlement_price, k_eur, wear):
    if not np.isfinite([settlement_price, k_eur, wear]).all() or k_eur <= 0 or wear < 0:
        raise ValueError("invalid settlement/K/wear")
    arms = {}
    for name, part in trace.groupby("arm"):
        cash = float(part.cost_eur.sum())
        inventory = float(part.soc_after.iloc[-1]) * .14 * settlement_price
        throughput = float(part.throughput_kwh.sum())
        arms[name] = {"operating_cost_eur": cash, "inventory_value_eur": inventory,
                      "settled_cost_eur": cash - inventory,
                      "throughput_kwh": throughput, "internal_wear_eur": throughput * wear,
                      "final_soc": float(part.soc_after.iloc[-1]),
                      "fallback_steps": int(part.fallback.sum()),
                      "nonoptimal_solves": int(part.nonoptimal_solves.sum()),
                      # A5: present in new traces; legacy traces keep a null so
                      # the audit can tell "not validated" from "zero" (R6).
                      "optimizer_attributed_steps": (int(part.optimizer_attributed.sum())
                                                     if "optimizer_attributed" in part else None),
                      "non_optimizer_attributed_steps": (int((~part.optimizer_attributed.astype(bool)).sum())
                                                         if "optimizer_attributed" in part else None)}
    base = arms["self_consumption"]
    for item in arms.values():
        item["operating_saving_eur"] = base["operating_cost_eur"] - item["operating_cost_eur"]
        item["settled_saving_eur"] = base["settled_cost_eur"] - item["settled_cost_eur"]
        item["saving_settled_percent_estimate"] = item["settled_saving_eur"] / k_eur * 100
        item["settled_saving_after_internal_wear_eur"] = (
            item["settled_saving_eur"] - item["internal_wear_eur"] + base["internal_wear_eur"])
    return arms


def aggregate_sites(reports):
    k_total = sum(r["k_eur"] for r in reports)
    totals = {}
    for arm in reports[0]["arms"]:
        entries = [r["arms"][arm] for r in reports]
        totals[arm] = {key: (None if any(e[key] is None for e in entries)
                             else sum(e[key] for e in entries)) for key in entries[0]
                       if key not in ("final_soc", "saving_settled_percent_estimate")}
        totals[arm]["saving_settled_percent_estimate"] = totals[arm]["settled_saving_eur"] / k_total * 100
        totals[arm]["positive_sites"] = sum(e["settled_saving_eur"] > 1e-8 for e in entries)
    return {"k_eur": k_total, "arms": totals}
