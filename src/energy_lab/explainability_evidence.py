"""Pure business-record export from the inputs of one successful shadow solve.

No device client, HTTP request, credentials, or Pydantic dependency is involved.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import math
from pathlib import Path
import re


MODEL_VERSION = "shared-milp-603de45c-cf1"
# Pinned sha256 of energy_lab/rolling.py, including the optional E4 energy floor.
UPSTREAM_SOURCE_SHA256 = "603de45ca089b13665abd91fe2f58aa12e576909dfcba2ffb481cff2aedd13a4"
REPOSITORY_URL = "https://github.com/thudnlancer/Anker-Hackerson"
_IDENTIFIER = re.compile(r"^[A-Za-z0-9_.:-]{1,120}$")
_POWER_COLUMNS = {"charge_kw": "charge_w", "discharge_kw": "discharge_w",
                  "grid_import_kw": "grid_import_w", "grid_export_kw": "grid_export_w"}


def _number(value, name):
    value = float(value)
    if not math.isfinite(value):
        raise ValueError(name + " must be finite")
    return 0.0 if value == 0 else value


def _nonnegative(value, name):
    value = _number(value, name)
    # Solver roundoff at a zero bound is not a physical negative power.
    if -1e-9 <= value < 0:
        return 0.0
    if value < 0:
        raise ValueError(name + " must be nonnegative")
    return value


def _aware_iso(value, name):
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(name + " requires an explicit timezone")
    return value.astimezone(timezone.utc).isoformat()


def _identifier(value, name):
    value = str(value)
    if not _IDENTIFIER.fullmatch(value):
        raise ValueError("invalid " + name)
    return value


def build_explainability_record(*, identity, observed_at, state, times, pv_w, load_w,
                               import_price, export_price, result, mapping, forecast_mode,
                               capacity_kwh, max_power_w, soc_min, soc_max, input_kind="mixed"):
    """Return a JSON-compatible DecisionIn record using only this solve's inputs.

    ``identity`` supplies run_id/site_id and optionally decision_id/input_kind/source_ref.
    The caller must declare synthetic replay inputs explicitly. A real shadow
    snapshot combines current observations with assumed future power, hence mixed.
    """
    if result.status != "optimal" or not mapping.representable:
        raise ValueError("only successful representable candidates can export evidence")
    source = Path(__file__).with_name("rolling.py")
    if hashlib.sha256(source.read_bytes()).hexdigest() != UPSTREAM_SOURCE_SHA256:
        raise ValueError("shared optimizer source hash differs from the export contract")
    input_kind = identity.get("input_kind", input_kind)
    if input_kind not in {"mixed", "synthetic"}:
        raise ValueError("shadow input_kind must be mixed or synthetic")
    run_id = _identifier(identity["run_id"], "run_id")
    site_id = _identifier(identity["site_id"], "site_id")
    if len(times) != 96:
        raise ValueError("shadow export requires the actual complete 96-step horizon")
    stamps = [datetime.fromisoformat(_aware_iso(value, "model time")) for value in times]
    if any(later - earlier != timedelta(minutes=15) for earlier, later in zip(stamps, stamps[1:])):
        raise ValueError("model timestamps must match the saved 15-minute step")
    first_time = times.iloc[0] if hasattr(times, "iloc") else times[0]
    device_time = _aware_iso(first_time, "device_time")
    observed_at = _aware_iso(observed_at, "observed_at")
    decision_id = identity.get("decision_id")
    if decision_id is None:
        digest = hashlib.sha256((run_id + "\0" + site_id + "\0" + device_time).encode()).hexdigest()
        decision_id = "tick-" + digest[:24]
    decision_id = _identifier(decision_id, "decision_id")
    pv = [_nonnegative(x, "pv_w") / 1000 for x in pv_w]
    load = [_nonnegative(x, "load_w") / 1000 for x in load_w]
    buy = [_number(x, "import_price") for x in import_price]
    sell = [_number(x, "export_price") for x in export_price]
    if any(len(values) != 96 for values in (pv, load, buy, sell)) or len(times) != 96:
        raise ValueError("shadow export requires the actual complete 96-step horizon")
    actual_pv = _nonnegative(state["solar_power"], "solar_power") / 1000
    actual_load = _nonnegative(state["home_load"], "home_load") / 1000
    if abs(actual_pv - pv[0]) > 1e-6 or abs(actual_load - load[0]) > 1e-6:
        raise ValueError("first model power differs from the current observation")
    cap = _number(capacity_kwh, "capacity_kwh")
    pmax = _number(max_power_w, "max_power_w") / 1000
    soc = _number(state["soc"], "soc")
    lower, upper = _number(soc_min, "soc_min"), _number(soc_max, "soc_max")
    if cap <= 0 or pmax <= 0 or not 0 <= lower <= soc <= upper <= 100:
        raise ValueError("invalid saved battery parameters")
    frame = result.plan
    if len(frame) != 96:
        raise ValueError("successful plan must cover the saved horizon")
    if [_aware_iso(value, "plan time") for value in frame["time"]] != [_aware_iso(value, "model time") for value in times]:
        raise ValueError("plan timestamps differ from the actual model inputs")
    flows = {key: [_nonnegative(x, column) / 1000 for x in frame[column]]
             for key, column in _POWER_COLUMNS.items()}
    first_step = {key: values[0] for key, values in flows.items()}
    # These are the effective settings passed by shadow.make_candidate, including
    # the rolling optimizer's resolved terminal_price=None default.
    step_hours, charge_efficiency, discharge_efficiency = .25, .95, 1.0
    wear_rate = .02
    terminal_price = sum(buy) / len(buy)
    purchase = sum(power * price * step_hours for power, price in zip(flows["grid_import_kw"], buy))
    revenue = sum(power * price * step_hours for power, price in zip(flows["grid_export_kw"], sell))
    degradation = sum(a + b for a, b in zip(flows["charge_kw"], flows["discharge_kw"])) * wear_rate * step_hours
    terminal = _number(frame["soc_end"].iloc[-1], "soc_end") / 100 * cap * terminal_price
    objective = _number(result.objective, "objective")
    if abs(purchase - revenue + degradation - terminal - objective) > 1e-6:
        raise ValueError("exported cost components do not reconcile with the solver")
    semantic = {"charge": "battery_charge", "discharge": "grid_export_target",
                "self_consumption": "self_consumption"}.get(mapping.flow)
    if semantic is None:
        raise ValueError("unsupported command mapping")
    # The ingestion contract keeps kind in {charge, discharge, hold}: an
    # explicit self_consumption mode switch is exported as kind="hold".
    kind = "hold" if mapping.flow == "self_consumption" else mapping.flow
    action = {"kind": kind, "power_kw": _nonnegative(mapping.target_w, "target_w") / 1000,
              "control_semantics": semantic}
    synthetic = input_kind == "synthetic"
    forecast_mode = str(forecast_mode or "unspecified")
    future_pv_source = "predicted" if "calibrated_pv" in forecast_mode and "uncalibrated" not in forecast_mode else "assumption"
    sources = {"pv": "observed", "load": "observed", "pv_forecast": future_pv_source,
               "load_forecast": "assumption"}
    if synthetic:
        sources = dict.fromkeys(sources, "synthetic")
    forecast_status = "fallback" if future_pv_source == "assumption" else "ready"
    fallback_reason = ("未来光伏和负荷使用当前观测的持久假设；预测模式：" + str(forecast_mode)) if forecast_status == "fallback" else None
    source_ref = str(identity.get("source_ref", "src/energy_lab/rolling.py@sha256:" + UPSTREAM_SOURCE_SHA256))
    note = ("人工输入用于协作策略接入验证。" if synthetic else "当前功率来自观测，未来功率保留显式预测或持久假设。")
    note += "同一次 shadow MILP 求解的完整输入与候选；仅候选，未下发 HA、未确认回读、无官方结算。"
    return {
        "schema_version": 1, "run_id": run_id, "site_id": site_id, "decision_id": decision_id,
        "source_kind": "external", "model_version": MODEL_VERSION,
        "policy_version": "shadow-api-first-sop-evidence-v1", "solver_status": result.status,
        "snapshot": {"device_time": device_time, "observed_at": observed_at,
            "soc_percent": soc, "pv_kw": actual_pv, "load_kw": actual_load,
            "capacity_kwh": cap, "max_charge_kw": pmax, "max_discharge_kw": pmax,
            "step_hours": step_hours, "soc_min_percent": lower, "soc_max_percent": upper,
            "charge_efficiency": charge_efficiency, "discharge_efficiency": discharge_efficiency,
            "pv_forecast_kw": pv, "load_forecast_kw": load,
            "import_eur_kwh": buy, "export_eur_kwh": sell,
            "terminal_eur_kwh": terminal_price, "degradation_eur_kwh": wear_rate,
            "sources": sources, "history_mode": "none", "cache_tail": None,
            "forecast_status": forecast_status, "fallback_reason": fallback_reason,
            "units": {"power": "kW", "energy": "kWh", "price": "EUR/kWh", "cost": "EUR"}},
        "action": action,
        "cost": {"purchase_eur": purchase, "export_revenue_eur": revenue,
                 "degradation_eur": degradation, "terminal_credit_eur": terminal,
                 "objective_eur": objective},
        "replay_context": {"upstream_source_sha256": UPSTREAM_SOURCE_SHA256,
                           "command_contract": "sop", "first_step": first_step},
        "provenance": {"origin": "external_adapter", "input_kind": input_kind,
                       "references": [REPOSITORY_URL, source_ref, "sha256:" + UPSTREAM_SOURCE_SHA256],
                       "note": note},
    }
