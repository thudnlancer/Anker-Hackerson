"""Explicit, idempotent synthetic seed; never run on service startup."""
from .providers import DemoMilpProvider
from .schemas import DecisionIn, ExecutionEvent
from .store import Store


def demo_records() -> list[DecisionIn]:
    snapshot = {"device_time":"2026-10-13T00:00:00Z","observed_at":"2026-10-13T00:00:00Z",
        "soc_percent":20,"pv_kw":0,"load_kw":1,"capacity_kwh":14,"max_charge_kw":5,"max_discharge_kw":5,
        "step_hours":1,"soc_min_percent":0,"soc_max_percent":100,"charge_efficiency":.95,
        "discharge_efficiency":1,"pv_forecast_kw":[0]*4,"load_forecast_kw":[1,3,3,1],
        "import_eur_kwh":[.1,.35,.4,.15],"export_eur_kwh":[.04]*4,"terminal_eur_kwh":.1,
        "degradation_eur_kwh":.002,"sources":dict.fromkeys(("pv","load","pv_forecast","load_forecast"),"synthetic"),
        "history_mode":"none","cache_tail":None,"forecast_status":"ready","fallback_reason":None}
    scenarios = [("site-01",20,5,False),("site-02",100,0,False),("site-03",35,2,True)]
    records = []
    model = DemoMilpProvider()
    for site,soc,power,fallback in scenarios:
        s = snapshot | {"soc_percent":soc}
        if fallback:
            s = s | {"history_mode":"frozen_baseline","cache_tail":"2026-10-11T00:00:00Z",
                "forecast_status":"fallback","fallback_reason":"人工演示：历史尾部滞后 48 小时，使用固定负荷假设。",
                "sources":snapshot["sources"] | {"load_forecast":"assumption"}}
        d = DecisionIn(run_id="demo-four-step-v1",site_id=site,decision_id="decision-0001",source_kind="demo",
            snapshot=s,action={"kind":"charge","power_kw":power,"control_semantics":"battery_charge"},
            model_version=model.version,policy_version="synthetic-candidate-v1",solver_status="optimal",
            cost=model.solve(s,power)["cost"])
        records.append(d)
    return records


def seed_demo(store: Store) -> list[str]:
    keys = [store.add_decision(d)["id"] for d in demo_records()]
    submitted = {"event_id":"demo-submit-01","instruction_id":"demo-instruction-01","kind":"submitted",
                 "issued_at":"2026-10-13T00:00:00Z","note":"人工执行时间线示例；没有连接或写入设备。"}
    store.add_execution(keys[0],ExecutionEvent(**submitted))
    store.add_execution(keys[0],ExecutionEvent(**(submitted | {"event_id":"demo-confirm-01","kind":"confirmed",
        "observed_at":"2026-10-13T00:00:02Z","device_time":"2026-10-13T00:00:02Z",
        "observed_mode":"synthetic-charge","observed_power_kw":5})))
    # No settlement result is fabricated: all demo sites remain unsettled.
    return keys
