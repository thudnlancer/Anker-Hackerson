"""Deterministic statements about recorded evidence, never invented causes."""
from datetime import datetime


def explain(d: dict, execution_status: str = "candidate_only") -> dict:
    s, a, facts = d["snapshot"], d["action"], []
    def fact(code, text, *paths):
        facts.append({"id": f"{d['id']}:{code}", "code": code, "text": text,
                      "evidence_refs": [f"{d['id']}#{p}" for p in paths]})
    names = {"charge": "充电", "discharge": "放电", "hold": "自用保持"}
    fact("action", f"保存的候选建议：{names[a['kind']]} {a['power_kw']:g} kW；控制语义为 {a['control_semantics']}。",
         "/action", "/policy_version", "/model_version")
    prices = s["import_eur_kwh"]
    if prices:
        suffix = "，为已保存窗口的最低值" if prices[0] <= min(prices) else ""
        fact("price", f"首步购电价 €{prices[0]:.4f}/kWh{suffix}；窗口最高价 €{max(prices):.4f}/kWh。",
             "/snapshot/import_eur_kwh")
    else:
        fact("price_unavailable", "记录未提供购电价格，无法判断价格因素。", "/snapshot/import_eur_kwh")
    full = s["soc_percent"] >= s["soc_max_percent"] - 1e-6
    fact("soc", f"当前 SOC {s['soc_percent']:g}%，保存的允许范围 {s['soc_min_percent']:g}%–{s['soc_max_percent']:g}%。"
         + ("初始电量已达上界，首步电池继续充电受到能量边界限制。" if full else ""),
         "/snapshot/soc_percent", "/snapshot/soc_min_percent", "/snapshot/soc_max_percent")
    fact("power_limit", f"电池充电上限 {s['max_charge_kw']:g} kW，电池放电上限 {s['max_discharge_kw']:g} kW。"
         + ("当前电池充电建议已达到充电功率上限。" if a['kind'] == "charge" and a['control_semantics'] == "battery_charge" and a['power_kw'] >= s['max_charge_kw'] else ""),
         "/snapshot/max_charge_kw", "/snapshot/max_discharge_kw", "/action")
    labels = {"observed":"观测", "predicted":"预测", "synthetic":"人工演示", "assumption":"假设", "unknown":"未知"}
    fact("sources", "光伏、负荷、光伏预测、负荷预测来源分别为：" + "、".join(labels[s['sources'][k]] for k in
         ("pv", "load", "pv_forecast", "load_forecast")) + "。", "/snapshot/sources")
    if s["cache_tail"]:
        hours = (datetime.fromisoformat(s["device_time"].replace("Z", "+00:00")) -
                 datetime.fromisoformat(s["cache_tail"].replace("Z", "+00:00"))).total_seconds()/3600
        fact("history", f"历史尾部比本次设备时间早 {hours:g} 小时；历史模式为 {s['history_mode']}。",
             "/snapshot/cache_tail", "/snapshot/device_time", "/snapshot/history_mode")
    else:
        fact("history", "未提供历史尾部，无法验证历史新鲜度。", "/snapshot/cache_tail", "/snapshot/history_mode")
    if s["forecast_status"] != "ready":
        fact("fallback", f"预测状态为 {s['forecast_status']}；记录的原因：{s['fallback_reason']}。",
             "/snapshot/forecast_status", "/snapshot/fallback_reason")
    fact("solver", f"外部记录声明的求解状态为 {d['solver_status']}，模型版本 {d['model_version']}。",
         "/solver_status", "/model_version")
    if d.get("replay_context"):
        r = d["replay_context"]
        f = r["first_step"]
        fact("replay_context", f"保存的模型首步：电池充电 {f['charge_kw']:g} kW、放电 {f['discharge_kw']:g} kW，"
             f"并网购电 {f['grid_import_kw']:g} kW、输出 {f['grid_export_kw']:g} kW；模型契约 {r['command_contract']}。",
             "/replay_context", "/action")
    if d["cost"]:
        fact("cost", f"保存的模型目标成本为 €{d['cost']['objective_eur']:.6f}，包含购电、售电、损耗和终端电量估值。",
             "/cost")
    if s["forecast_status"] != "ready":
        summary = "历史或预测已降级；请结合假设查看候选建议。"
    elif full:
        summary = "SOC 已达保存的上界，首步电池继续充电受到限制。"
    elif prices and prices[0] <= min(prices):
        summary = f"首步价格处于窗口低位，当前 SOC 为 {s['soc_percent']:g}%。"
    else:
        summary = "建议依据已保存输入；完整策略原因需由策略模块提供。"
    scope = ("人工四步演示记录；价格、负荷与设备状态均为合成数据。" if d["source_kind"] == "demo" else
             "外部导入记录；此服务陈述保存的证据，不重建未知策略的内部原因。")
    provenance = d.get("provenance")
    if provenance:
        fact("provenance", provenance["note"] or f"输入类型为 {provenance['input_kind']}，来源为 {provenance['origin']}。", "/provenance")
        if provenance["origin"] == "model_validation" or provenance["input_kind"] == "synthetic":
            model_source = "共同策略 MILP 源码已接入" if provenance["origin"] == "external_adapter" else "恢复的历史策略 MILP 源码已接入"
            scope = model_source + "；本记录使用人工输入验证，不是实际遥测或历史决策重放。"
    elif d["source_kind"] == "external" and all(v in {"synthetic","assumption"} for v in s["sources"].values()):
        scope = "外部模型记录使用人工或假设输入，不是实际遥测。"
    return {"title": "为什么这样建议", "summary": summary, "facts": facts,
            "scope": scope + "模型成本和替代方案均不等于官方结算收益。",
            "execution_status": execution_status, "source_kind": d["source_kind"]}
