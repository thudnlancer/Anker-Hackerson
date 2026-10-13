"""Adapter for the actual historical rolling MILP, recovered from supplied log evidence."""
from __future__ import annotations

import hashlib
from pathlib import Path
import time

import numpy as np

from .providers import ModelOutcome
from .schemas import Cost, Snapshot

SOURCE_SHA256 = "4197a84d871f635236b320f63e378bae6914e4fa9728d048a5ba08f8af9ba2d1"
ADAPTED_SHA256 = "6e2b1ca5e372f9d6a70bc5322ac3f699b417e4ab336df2a25c8a3add84b62d11"
MODEL_VERSION = "historical-milp-4197a84d-cf1"
TOL = 1e-6


def verify_source():
    vendor = Path(__file__).parent / "strategy_vendor"
    for name,expected in (("rolling_original.py",SOURCE_SHA256),("rolling.py",ADAPTED_SHA256)):
        if hashlib.sha256((vendor/name).read_bytes()).hexdigest() != expected:
            raise ModelOutcome("failed", "恢复源码或首步适配源码的哈希与锁定版本不一致。")


class RecoveredMilpProvider:
    model_version = MODEL_VERSION
    source_sha256 = SOURCE_SHA256
    source_description = "使用用户日志中恢复的真实 rolling.py MILP 方程及约束，源码哈希已锁定。"
    deployment_note = "本次反事实不下发 HA、不验证回读或生成官方结算；实际执行与收益依据独立记录，历史源码版本未核对当前 Windows/AWS 部署。"

    def verify_source(self):
        verify_source()

    def optimizer(self):
        from .strategy_vendor.rolling import optimize_window
        return optimize_window

    def supports(self, decision: dict) -> bool:
        s, r = decision["snapshot"], decision.get("replay_context")
        arrays = [s[k] for k in ("pv_forecast_kw","load_forecast_kw","import_eur_kwh","export_eur_kwh")]
        if not (decision["source_kind"] == "external" and decision["model_version"] == self.model_version
                and r and r["upstream_source_sha256"] == self.source_sha256 and decision.get("cost") is not None
                and 0 < len(arrays[0]) <= 96 and all(len(x) == len(arrays[0]) for x in arrays)
                and s["max_charge_kw"] > 0 and abs(s["max_charge_kw"]-s["max_discharge_kw"])<1e-9
                and s["discharge_efficiency"] == 1):
            return False
        if (decision.get("provenance") or {}).get("input_kind") == "incomplete": return False
        a, first = decision["action"], r["first_step"]
        if a["control_semantics"] in {"grid_charge","grid_export_target"} and r["command_contract"] != "sop":
            return False
        semantic_key = {"battery_charge":"charge_kw","grid_charge":"grid_import_kw","grid_export_target":"grid_export_kw"}.get(a["control_semantics"])
        if semantic_key:
            return abs(a["power_kw"]-first[semantic_key])<=TOL
        if a["kind"] == "hold" and a["control_semantics"] == "self_consumption":
            return a["power_kw"] == 0 and first["charge_kw"] <= TOL
        if a["control_semantics"] == "external" and r["command_contract"] == "battery":
            key = "charge_kw" if a["kind"] == "charge" else "discharge_kw" if a["kind"] == "discharge" else None
            return key is not None and abs(a["power_kw"]-first[key])<=TOL
        return False

    def solve(self, snapshot: dict, command_contract: str, *, first_step=None, first_charge_kw=None) -> dict:
        self.verify_source()
        optimize_window = self.optimizer()
        import pandas as pd
        s = Snapshot.model_validate(snapshot).model_dump(mode="json")
        if s["discharge_efficiency"] != 1 or abs(s["max_charge_kw"]-s["max_discharge_kw"])>=1e-9:
            raise ModelOutcome("unsupported", "此历史模型要求放电效率为 1 且充放电功率上限相同。")
        n, dt, cap = len(s["pv_forecast_kw"]), s["step_hours"], s["capacity_kwh"]
        if n == 0 or any(len(s[k]) != n for k in ("load_forecast_kw","import_eur_kwh","export_eur_kwh")):
            raise ModelOutcome("unsupported", "缺少完整且等长的模型输入序列。")
        times = pd.Series(pd.date_range(s["device_time"],periods=n,freq=pd.Timedelta(hours=dt)))
        started = time.perf_counter()
        result = optimize_window(times,np.array(s["pv_forecast_kw"])*1000,np.array(s["load_forecast_kw"])*1000,
            np.array(s["import_eur_kwh"]),np.array(s["export_eur_kwh"]),s["soc_percent"],
            capacity_kwh=cap,max_power_w=s["max_charge_kw"]*1000,charge_efficiency=s["charge_efficiency"],
            terminal_price=s["terminal_eur_kwh"],degradation_cost_eur_kwh=s["degradation_eur_kwh"],
            soc_min=s["soc_min_percent"],soc_max=s["soc_max_percent"],step_hours=dt,solver="milp",
            time_limit_seconds=10,command_contract=command_contract,first_step=first_step,first_charge_kw=first_charge_kw)
        ms = (time.perf_counter()-started)*1000
        if result.status != "optimal":
            status = {"limit_reached":"timeout","infeasible":"infeasible"}.get(result.status,"failed")
            raise ModelOutcome(status,result.message)
        frame = result.plan
        charge, discharge, gi, go = [frame[k].to_numpy(dtype=float)/1000 for k in ("charge_w","discharge_w","grid_import_w","grid_export_w")]
        energy = frame["soc_end"].to_numpy(dtype=float)*cap/100
        all_values = np.r_[charge,discharge,gi,go,energy,result.objective]
        if not np.isfinite(all_values).all(): raise ModelOutcome("failed", "历史模型返回非有限的成功结果。")
        pv, load = np.array(s["pv_forecast_kw"]),np.array(s["load_forecast_kw"])
        pc, ec = s["max_charge_kw"],s["charge_efficiency"]
        expected_energy = np.r_[cap*s["soc_percent"]/100,energy[:-1]]+ec*charge*dt-discharge*dt
        checks = [np.max(abs(pv+discharge+gi-load-charge-go))<=TOL,np.max(abs(energy-expected_energy))<=TOL,
            min(energy)>=cap*s["soc_min_percent"]/100-TOL,max(energy)<=cap*s["soc_max_percent"]/100+TOL,
            min(charge)>=-TOL,min(discharge)>=-TOL,max(charge)<=pc+TOL,max(discharge)<=pc+TOL,
            min(gi)>=-TOL,min(go)>=-TOL,np.max(np.minimum(charge,discharge))<=TOL,np.max(np.minimum(gi,go))<=TOL,
            np.all(gi<=np.maximum(0,load-pv+pc)+TOL),np.all(go<=np.maximum(0,pv-load+pc)+TOL)]
        if command_contract == "sop":
            active = discharge>TOL
            checks.extend([np.all(gi[active]<=TOL),np.all(go[active]<=pc+TOL)])
        if first_charge_kw is not None: checks.append(abs(charge[0]-first_charge_kw)<=TOL)
        if first_step:
            checks.extend(abs(values[0]-first_step[key])<=TOL for key,values in
                (("charge_kw",charge),("discharge_kw",discharge),("grid_import_kw",gi),("grid_export_kw",go)))
        if not all(checks): raise ModelOutcome("failed", "重放计划未通过独立物理、边界或干预校验。")
        purchase, revenue = float(gi@np.array(s["import_eur_kwh"])*dt),float(go@np.array(s["export_eur_kwh"])*dt)
        wear, terminal = float(np.sum(charge+discharge)*s["degradation_eur_kwh"]*dt),float(energy[-1]*s["terminal_eur_kwh"])
        objective = purchase-revenue+wear-terminal
        if abs(objective-result.objective)>TOL: raise ModelOutcome("failed", "重放费用与原求解器目标值对账失败。")
        cost = Cost(purchase_eur=purchase,export_revenue_eur=revenue,degradation_eur=wear,terminal_credit_eur=terminal,objective_eur=objective)
        return {"status":"optimal","solve_ms":round(ms,3),"first_charge_kw":float(charge[0]),"cost":cost.model_dump(),
            "plan":[{"step":t,"charge_kw":float(charge[t]),"discharge_kw":float(discharge[t]),"grid_import_kw":float(gi[t]),
                "grid_export_kw":float(go[t]),"soc_end_percent":float(energy[t]/cap*100)} for t in range(n)]}

    def compare(self, decision: dict, first_charge_kw: float) -> dict:
        if not self.supports(decision): raise ModelOutcome("unsupported", "记录与恢复模型的版本、参数、首步或完整输入不匹配。")
        r, s = decision["replay_context"],decision["snapshot"]
        try:
            baseline = self.solve(s,r["command_contract"],first_step=r["first_step"])
        except ModelOutcome as exc:
            raise ModelOutcome("timeout" if exc.status=="timeout" else "failed",f"原候选重放失败：{exc}") from exc
        if any(abs(baseline["cost"][k]-decision["cost"][k])>TOL for k in baseline["cost"]):
            raise ModelOutcome("failed", "原候选费用分项与保存的策略记录不一致。")
        alternative = self.solve(s,r["command_contract"],first_charge_kw=first_charge_kw)
        provenance = decision.get("provenance") or {}
        artificial = provenance.get("origin") == "model_validation" or provenance.get("input_kind") == "synthetic"
        return {"baseline":baseline,"alternative":alternative,
            "delta_eur":alternative["cost"]["objective_eur"]-baseline["cost"]["objective_eur"],
            "assumptions":[self.source_description,
                "人工输入用于模型接入验证；不是实际遥测。" if artificial else "使用导入时保存的完整输入；未查询新状态替换旧输入。",
                f"{len(s['pv_forecast_kw'])} 步 × {s['step_hours']:g} 小时；控制模型契约 {r['command_contract']}。",
                "基准固定保存的首步四项功率；替代仅固定首步电池充电，后续重新优化。",
                "同一 SOC、效率、边界和终端估值；差额是替代费用减基准费用，允许负值。",
                self.deployment_note]}
