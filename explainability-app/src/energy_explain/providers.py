"""Pluggable replay providers. The default synthetic model never accepts external decisions."""
from __future__ import annotations

import importlib
import os
import time
from typing import Protocol

import numpy as np
from scipy.optimize import Bounds, LinearConstraint, milp

from .schemas import Cost, Snapshot


class ModelOutcome(Exception):
    def __init__(self, status: str, message: str):
        super().__init__(message)
        self.status = status


class ComparisonProvider(Protocol):
    def supports(self, decision: dict) -> bool: ...
    def compare(self, decision: dict, first_charge_kw: float) -> dict: ...


class DemoMilpProvider:
    version = "demo-milp-v1"

    def supports(self, decision: dict) -> bool:
        s, a = decision["snapshot"], decision["action"]
        arrays = [s[k] for k in ("pv_forecast_kw", "load_forecast_kw", "import_eur_kwh", "export_eur_kwh")]
        return (decision["source_kind"] == "demo" and decision["model_version"] == self.version
                and a["kind"] == "charge" and a["control_semantics"] == "battery_charge"
                and all(len(x) == 4 for x in arrays) and all(v == 0 for v in arrays[0]))

    def solve(self, snapshot: dict, first_charge_kw: float) -> dict:
        s = Snapshot.model_validate(snapshot).model_dump(mode="json")
        n, dt, cap = 4, s["step_hours"], s["capacity_kwh"]
        pc, pd = s["max_charge_kw"], s["max_discharge_kw"]
        if not np.isfinite(first_charge_kw) or first_charge_kw < 0:
            raise ValueError("first charging power must be finite and nonnegative")
        if first_charge_kw > pc:
            raise ModelOutcome("infeasible", "首步充电功率超过保存的设备功率上限。")
        arrays = [np.array(s[k], float) for k in ("pv_forecast_kw", "load_forecast_kw", "import_eur_kwh", "export_eur_kwh")]
        if any(len(x) != n for x in arrays) or np.any(arrays[0] != 0):
            raise ModelOutcome("unsupported", "演示模型仅支持四步零光伏算例。")
        pv, load, buy, sell = arrays
        ec, ed, wear = s["charge_efficiency"], s["discharge_efficiency"], s["degradation_eur_kwh"]
        initial = cap*s["soc_percent"]/100
        e_min, e_max = cap*s["soc_min_percent"]/100, cap*s["soc_max_percent"]/100
        imax, omax = load + pc, pv + pd
        c = np.zeros(7*n)
        c[:2*n] = wear*dt
        c[2*n:3*n], c[3*n:4*n] = buy*dt, -sell*dt
        c[5*n-1] = -s["terminal_eur_kwh"]
        lb = np.concatenate([np.zeros(4*n), np.full(n,e_min), np.zeros(2*n)])
        ub = np.concatenate([np.full(n,pc),np.full(n,pd),imax,omax,np.full(n,e_max),np.ones(2*n)])
        lb[0] = ub[0] = first_charge_kw
        integrality = np.zeros(7*n)
        integrality[5*n:] = 1
        rows, lows, highs = [], [], []
        def row(terms, lo, hi):
            r = np.zeros(7*n)
            for i,v in terms.items(): r[i] = v
            rows.append(r); lows.append(lo); highs.append(hi)
        for t in range(n):
            row({t:1,n+t:-1,2*n+t:-1,3*n+t:1},pv[t]-load[t],pv[t]-load[t])
            terms = {4*n+t:1,t:-ec*dt,n+t:dt/ed}
            if t: terms[4*n+t-1] = -1
            rhs = initial if t == 0 else 0
            row(terms,rhs,rhs)
            row({t:1,5*n+t:-pc},-np.inf,0)
            row({n+t:1,5*n+t:pd},-np.inf,pd)
            row({2*n+t:1,6*n+t:-imax[t]},-np.inf,0)
            row({3*n+t:1,6*n+t:omax[t]},-np.inf,omax[t])
        started = time.perf_counter()
        result = milp(c,integrality=integrality,bounds=Bounds(lb,ub),
            constraints=LinearConstraint(np.array(rows),lows,highs),options={"mip_rel_gap":1e-9,"time_limit":10})
        ms = (time.perf_counter()-started)*1000
        if result.status != 0 or not result.success:
            status = {1:"timeout",2:"infeasible"}.get(result.status,"failed")
            raise ModelOutcome(status, str(result.message))
        charge, discharge, gi, go, energy = [result.x[i*n:(i+1)*n] for i in range(5)]
        tol = 1e-6
        # Independent checks of physics and cost before exposing solver output.
        checks = [abs(charge[0]-first_charge_kw)<=tol,
            np.max(abs(pv+discharge+gi-load-charge-go))<=tol,
            np.max(abs(energy-np.r_[initial,energy[:-1]]-ec*charge*dt+discharge*dt/ed))<=tol,
            min(energy)>=e_min-tol,max(energy)<=e_max+tol,
            min(charge)>=-tol,min(discharge)>=-tol,max(charge)<=pc+tol,max(discharge)<=pd+tol,
            min(gi)>=-tol,min(go)>=-tol,np.max(np.minimum(charge,discharge))<=tol,
            np.max(np.minimum(gi,go))<=tol]
        if not all(checks): raise ModelOutcome("failed", "模型输出未通过独立守恒和边界校验。")
        purchase, revenue = float(gi@buy*dt), float(go@sell*dt)
        degradation = float(np.sum(charge+discharge)*wear*dt)
        terminal = float(energy[-1]*s["terminal_eur_kwh"])
        objective = purchase-revenue+degradation-terminal
        if abs(objective-result.fun)>tol: raise ModelOutcome("failed", "模型成本对账失败。")
        cost = Cost(purchase_eur=purchase,export_revenue_eur=revenue,degradation_eur=degradation,
                    terminal_credit_eur=terminal,objective_eur=objective)
        return {"status":"optimal","solve_ms":round(ms,3),"first_charge_kw":first_charge_kw,
                "cost":cost.model_dump(),"plan":[{"step":t,"charge_kw":float(charge[t]),
                "discharge_kw":float(discharge[t]),"grid_import_kw":float(gi[t]),"grid_export_kw":float(go[t]),
                "soc_end_percent":float(energy[t]/cap*100)} for t in range(n)]}

    def compare(self, decision: dict, first_charge_kw: float) -> dict:
        if not self.supports(decision): raise ModelOutcome("unsupported", "此决策不能使用人工演示模型。")
        try:
            baseline = self.solve(decision["snapshot"],decision["action"]["power_kw"])
        except ModelOutcome as exc:
            raise ModelOutcome("timeout" if exc.status == "timeout" else "failed", f"保存的候选动作无法在其演示模型中重放：{exc}") from exc
        if decision["cost"] and any(abs(baseline["cost"][k]-decision["cost"][k])>1e-6 for k in baseline["cost"]):
            raise ModelOutcome("failed", "基准重放成本与保存的证据不一致。")
        alternative = self.solve(decision["snapshot"],first_charge_kw)
        return {"baseline":baseline,"alternative":alternative,
                "delta_eur":alternative["cost"]["objective_eur"]-baseline["cost"]["objective_eur"],
                "assumptions":["四个小时的人工零光伏场景，单位 EUR。", "仅固定首步电池充电功率，后续步骤重新优化。",
                    "两侧使用同一输入、SOC 边界、效率、功率上限及终端电量估值。",
                    "差额为替代方案减基准方案的模型成本；负值表示替代方案更便宜。",
                    "结果不代表 HA 控制、连续策略收益或官方结算。"]}


_providers: dict[str, ComparisonProvider] = {DemoMilpProvider.version: DemoMilpProvider()}
_loaded_module: str | None = None


def register_provider(model_version: str, provider: ComparisonProvider):
    if model_version in _providers: raise ValueError("provider version already registered")
    _providers[model_version] = provider


def get_provider(decision: dict) -> ComparisonProvider | None:
    global _loaded_module
    module = os.environ.get("ENERGY_EXPLAIN_PROVIDER_MODULE")
    if module and module != _loaded_module:
        importlib.import_module(module).register(register_provider)
        _loaded_module = module
    from .recovered_provider import MODEL_VERSION, RecoveredMilpProvider
    if decision["model_version"] == MODEL_VERSION and MODEL_VERSION not in _providers:
        register_provider(MODEL_VERSION, RecoveredMilpProvider())
    from .shared_provider import MODEL_VERSION as SHARED_VERSION, SharedMilpProvider
    if decision["model_version"] == SHARED_VERSION and SHARED_VERSION not in _providers:
        register_provider(SHARED_VERSION, SharedMilpProvider())
    provider = _providers.get(decision["model_version"])
    return provider if provider and provider.supports(decision) else None


def run_comparison(decision: dict, first_charge_kw: float) -> dict:
    provider = get_provider(decision)
    if not provider: raise ModelOutcome("unsupported", "该决策未注册匹配的重放模型，无法计算反事实。")
    return provider.compare(decision, first_charge_kw)
