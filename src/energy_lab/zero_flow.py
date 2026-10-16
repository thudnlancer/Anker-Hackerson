"""B1 zero-flow hardware verification: pure judgement and restore planning.

No I/O in this module. The runner (run_zero_flow_verification.py) performs the
reads and, only when explicitly authorized (--mode live plus all three safety
flags), exactly one supervised write sequence. Criteria follow
HOLD_SEMANTICS_DESIGN §4 step 1:

- battery charging and discharging power both <= tolerance (default 100 W);
- grid flow matches passive settlement: import ~ max(0, load - pv),
  export ~ max(0, pv - load);
- the setpoint echo (operating mode / grid flow / power) confirms the command
  actually took effect, so an idle self-consumption device cannot false-pass.

`charge=0 W` stays an unverified candidate until this judgement passes on real
hardware readback; a failed site is recorded in config, not only in logs.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math


ZERO_FLOW_BATTERY_TOLERANCE_W = 100.0
ZERO_FLOW_GRID_TOLERANCE_W = 100.0
SETPOINT_EPS_W = 1e-3

THIRD_PARTY_MODE = "third_party_control"
SELF_CONSUMPTION_MODE = "self_consumption"


def _finite(value: object) -> float:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return float("nan")
    return number if math.isfinite(number) else float("nan")


@dataclass(frozen=True)
class ZeroFlowCommand:
    """The single supervised write this verification is allowed to issue."""
    operating_mode: str = THIRD_PARTY_MODE
    grid_flow: str = "charge"
    power_w: float = 0.0
    purpose: str = "zero_flow_hardware_verification"
    # HOLD_SEMANTICS_DESIGN §3: charge=0 W is a candidate until readback proves it.
    hardware_verified: bool = False


def build_zero_flow_command() -> ZeroFlowCommand:
    return ZeroFlowCommand()


@dataclass
class ZeroFlowJudgement:
    verified: bool
    reason: str
    checks: dict[str, dict]


def judge_zero_flow_state(state: dict, *, battery_tolerance_w: float = ZERO_FLOW_BATTERY_TOLERANCE_W,
                          grid_tolerance_w: float = ZERO_FLOW_GRID_TOLERANCE_W) -> ZeroFlowJudgement:
    """Judge one post-command readback against the step-1 acceptance criteria."""
    if not isinstance(state, dict):
        raise TypeError("state must be a mapping")
    tolerances = (float(battery_tolerance_w), float(grid_tolerance_w))
    if any(not math.isfinite(t) or t < 0 for t in tolerances):
        raise ValueError("tolerances must be non-negative finite numbers")

    checks: dict[str, dict] = {}

    power = _finite(state.get("power_control"))
    setpoint_ok = (state.get("operating_mode") == THIRD_PARTY_MODE
                   and state.get("grid_flow") == "charge"
                   and math.isfinite(power) and abs(power) <= SETPOINT_EPS_W)
    checks["setpoint_echo"] = {
        "passed": bool(setpoint_ok),
        "observed": {"operating_mode": state.get("operating_mode"),
                     "grid_flow": state.get("grid_flow"),
                     "power_control": state.get("power_control")},
        "expected": {"operating_mode": THIRD_PARTY_MODE, "grid_flow": "charge", "power_control": 0.0},
    }

    pv = _finite(state.get("solar_power"))
    load = _finite(state.get("home_load"))
    pv_load_ok = math.isfinite(pv) and math.isfinite(load) and pv >= 0 and load >= 0
    expected_import = max(0.0, load - pv) if pv_load_ok else float("nan")
    expected_export = max(0.0, pv - load) if pv_load_ok else float("nan")

    def battery_check(name: str, key: str) -> None:
        observed = _finite(state.get(key))
        ok = math.isfinite(observed) and 0.0 <= observed <= tolerances[0]
        checks[name] = {"passed": bool(ok),
                        "observed": observed if math.isfinite(observed) else None,
                        "expected": f"0..{tolerances[0]} W",
                        "tolerance_w": tolerances[0]}

    battery_check("battery_charge_within_tolerance", "battery_charging_power_w")
    battery_check("battery_discharge_within_tolerance", "battery_discharging_power_w")

    def grid_check(name: str, key: str, expected: float) -> None:
        observed = _finite(state.get(key))
        ok = (math.isfinite(observed) and math.isfinite(expected)
              and observed >= 0.0 and abs(observed - expected) <= tolerances[1])
        checks[name] = {"passed": bool(ok),
                        "observed": observed if math.isfinite(observed) else None,
                        "expected": expected if math.isfinite(expected) else None,
                        "tolerance_w": tolerances[1]}

    grid_check("grid_import_passive_settlement", "grid_import_power_w", expected_import)
    grid_check("grid_export_passive_settlement", "grid_export_power_w", expected_export)

    failed = [name for name, item in checks.items() if not item["passed"]]
    return ZeroFlowJudgement(verified=not failed,
                             reason="verified" if not failed else "failed:" + ",".join(failed),
                             checks=checks)


@dataclass
class RestorePlan:
    """How to put the device back to exactly the baseline mode. Never invented."""
    restorable: bool
    operating_mode: str | None
    grid_flow: str | None = None
    power_w: float | None = None
    reason: str = ""


def plan_restore(baseline_state: dict) -> RestorePlan:
    if not isinstance(baseline_state, dict):
        raise TypeError("baseline state must be a mapping")
    mode = baseline_state.get("operating_mode")
    if mode == SELF_CONSUMPTION_MODE:
        return RestorePlan(True, SELF_CONSUMPTION_MODE, reason="restore_baseline_self_consumption")
    if mode == THIRD_PARTY_MODE:
        flow = baseline_state.get("grid_flow")
        power = _finite(baseline_state.get("power_control"))
        if flow not in ("charge", "discharge") or not math.isfinite(power) or power < 0:
            return RestorePlan(False, None, reason="baseline_third_party_setpoint_unreadable")
        return RestorePlan(True, THIRD_PARTY_MODE, grid_flow=str(flow), power_w=power,
                           reason="restore_baseline_third_party_setpoint")
    return RestorePlan(False, None, reason=f"unknown_baseline_mode:{mode!r}")


def confirm_restored(state: dict, plan: RestorePlan, *, power_eps_w: float = SETPOINT_EPS_W) -> bool:
    if not isinstance(state, dict) or not plan.restorable:
        return False
    if state.get("operating_mode") != plan.operating_mode:
        return False
    if plan.operating_mode == THIRD_PARTY_MODE:
        if state.get("grid_flow") != plan.grid_flow:
            return False
        power = _finite(state.get("power_control"))
        if not math.isfinite(power) or abs(power - float(plan.power_w)) > power_eps_w:
            return False
    return True


def evidence_hash(payload: object) -> str:
    canonical = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _stations_mapping(config: dict) -> dict:
    env = config.get("environment_machine")
    if isinstance(env, dict) and isinstance(env.get("stations"), dict):
        return env["stations"]
    if isinstance(config.get("stations"), dict):
        return config["stations"]
    raise KeyError("no stations mapping in configuration")


def apply_zero_flow_conclusion(config: dict, site: str, verified: bool) -> dict:
    """Return a deep copy with stations.{site}.zero_flow_verified recorded.

    Per HOLD_SEMANTICS_DESIGN §4 step 1, both true and false are valid
    conclusions and must land in configuration, not only in logs.
    """
    updated = json.loads(json.dumps(config))
    stations = _stations_mapping(updated)
    key = str(site)
    if key not in stations:
        raise KeyError(f"unknown site {site!r}")
    entry = stations[key]
    if isinstance(entry, str):
        entry = {"suffix": entry}
        stations[key] = entry
    if not isinstance(entry, dict):
        raise TypeError(f"station entry for site {site!r} is not writable")
    entry["zero_flow_verified"] = bool(verified)
    return updated
