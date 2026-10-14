"""Plan feasibility checks against one saved decision snapshot."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import inspect

from .providers import ModelOutcome, get_provider
from .shared_provider import SharedMilpProvider


def _deadline_step(snapshot: dict, deadline: datetime, count: int) -> int:
    start = datetime.fromisoformat(snapshot["device_time"].replace("Z", "+00:00"))
    step_seconds = snapshot["step_hours"] * 3600
    offset = (deadline.astimezone(timezone.utc) - start.astimezone(timezone.utc)).total_seconds()
    raw_step = offset / step_seconds
    nearest = round(raw_step)
    if offset <= 0 or abs(raw_step - nearest) > 1e-6:
        raise ValueError("deadline must align with a future model step")
    if nearest < 1 or nearest > count:
        raise ValueError("deadline must be inside the saved forecast horizon")
    return nearest - 1


def _solve_floor(provider, snapshot, contract, step, target_kwh):
    count = len(snapshot["pv_forecast_kw"])
    floors = [None] * count
    floors[step] = target_kwh
    try:
        return provider.solve(snapshot, contract, energy_floor_kwh=floors)
    except TypeError as exc:
        if "energy_floor_kwh" in str(exc):
            raise ModelOutcome("unsupported", "当前共享优化器尚未集成截止时刻电量约束。") from exc
        raise


def _max_energy_by(snapshot, index):
    initial = snapshot["capacity_kwh"] * snapshot["soc_percent"] / 100
    maximum = snapshot["capacity_kwh"] * snapshot["soc_max_percent"] / 100
    charge = snapshot["max_charge_kw"] * snapshot["step_hours"] * (index + 1) * snapshot["charge_efficiency"]
    return min(maximum, initial + charge)


def _attempt(provider, snapshot, contract, index, target_kwh):
    try:
        return {"status": "feasible", "solution": _solve_floor(provider, snapshot, contract, index, target_kwh)}
    except ModelOutcome as exc:
        return {"status": exc.status, "message": str(exc)}


def check_feasibility(decision: dict, target_soc_percent: float, deadline: datetime) -> dict:
    snapshot = decision["snapshot"]
    provider = get_provider(decision)
    if not isinstance(provider, SharedMilpProvider):
        return {"status": "unsupported", "message": "该决策没有匹配且通过源码校验的重放模型。",
                "decision_id": decision["id"]}
    if "energy_floor_kwh" not in inspect.signature(provider.optimizer()).parameters:
        return {"status": "unsupported", "message": "当前共享优化器尚未集成截止时刻电量约束。",
                "decision_id": decision["id"]}
    count = len(snapshot["pv_forecast_kw"])
    if count == 0:
        return {"status": "unsupported", "message": "保存快照没有可重放的预测时序。",
                "decision_id": decision["id"]}
    index = _deadline_step(snapshot, deadline, count)
    contract = decision["replay_context"]["command_contract"]
    target_kwh = snapshot["capacity_kwh"] * target_soc_percent / 100
    max_reachable = _max_energy_by(snapshot, index)

    try:
        baseline = provider.solve(snapshot, contract,
                                  first_step=decision["replay_context"]["first_step"])
    except ModelOutcome as exc:
        return {"status": exc.status, "message": "保存的基准计划无法重放：" + str(exc),
                "decision_id": decision["id"]}
    saved_cost = decision.get("cost")
    if saved_cost and any(abs(baseline["cost"][key] - saved_cost[key]) > 1e-6 for key in baseline["cost"]):
        return {"status": "failed", "message": "保存快照的基准重放费用与决策证据不一致。",
                "decision_id": decision["id"]}

    requested = _attempt(provider, snapshot, contract, index, target_kwh)
    if requested["status"] == "feasible":
        solution = requested["solution"]
        completed = next((step["step"] for step in solution["plan"]
                          if step["soc_end_percent"] + 1e-7 >= target_soc_percent), None)
        return {"status": "feasible", "decision_id": decision["id"],
                "target_soc_percent": target_soc_percent, "deadline": deadline.isoformat(),
                "deadline_step": index + 1, "max_reachable_energy_kwh": max_reachable,
                "expected_completion_step": completed,
                "baseline_cost_eur": baseline["cost"]["objective_eur"],
                "planned_cost_eur": solution["cost"]["objective_eur"],
                "cost_delta_eur": solution["cost"]["objective_eur"] - baseline["cost"]["objective_eur"],
                "plan": solution["plan"], "adjustments": [],
                "attribution": "optimizer_model_estimate",
                "assumptions": ["使用导入时保存的设备时间、预测、电价、SOC 和模型参数。",
                    "费用差是加截止电量约束后的优化目标减保存快照基准重放；不是实际结算或收益。"]}
    elif requested["status"] == "unsupported":
        return {"status": "unsupported", "message": requested["message"],
                "decision_id": decision["id"], "max_reachable_energy_kwh": max_reachable}
    elif requested["status"] == "timeout":
        return {"status": "timeout", "message": "截止电量约束求解超时，不能据此判定计划不可行。",
                "decision_id": decision["id"], "max_reachable_energy_kwh": max_reachable}
    elif requested["status"] != "infeasible":
        return {"status": "failed", "message": requested.get("message", "求解失败。"),
                "decision_id": decision["id"]}

    adjustments = []
    for adjusted_target in (max(snapshot["soc_min_percent"], target_soc_percent - 5),
                            max(snapshot["soc_min_percent"], target_soc_percent - 10)):
        if adjusted_target >= target_soc_percent or any(a["target_soc_percent"] == adjusted_target for a in adjustments):
            continue
        attempt = _attempt(provider, snapshot, contract, index,
                           snapshot["capacity_kwh"] * adjusted_target / 100)
        if attempt["status"] == "feasible":
            adjustments.append({"kind": "lower_target", "target_soc_percent": adjusted_target,
                "status": "feasible", "cost_delta_eur": attempt["solution"]["cost"]["objective_eur"]
                    - baseline["cost"]["objective_eur"]})

    if index + 1 < count:
        later = _attempt(provider, snapshot, contract, index + 1, target_kwh)
        if later["status"] == "feasible":
            later_deadline = datetime.fromisoformat(snapshot["device_time"].replace("Z", "+00:00")) + timedelta(
                seconds=snapshot["step_hours"] * 3600 * (index + 2))
            adjustments.append({"kind": "extend_deadline", "deadline": later_deadline.isoformat(),
                "status": "feasible", "cost_delta_eur": later["solution"]["cost"]["objective_eur"]
                    - baseline["cost"]["objective_eur"]})

    reason = "截止约束模型判定不可行。"
    if target_kwh > max_reachable + 1e-7:
        reason = "目标电量超过截止前按最大充电功率估算的可达上界。"
    return {"status": "infeasible", "decision_id": decision["id"],
            "target_soc_percent": target_soc_percent, "deadline": deadline.isoformat(),
            "deadline_step": index + 1, "max_reachable_energy_kwh": max_reachable,
            "target_energy_kwh": target_kwh, "message": reason,
            "adjustments": adjustments, "attribution": "optimizer_model_estimate",
            "assumptions": ["可达上界按设备最大充电功率、效率和截止前剩余步数计算，未计入未来放电。",
                "不可行结论仅针对保存的预测、价格、设备边界和模型；不是对实际设备的保证。"]}
