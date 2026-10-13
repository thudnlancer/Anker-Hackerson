"""Same-origin HTTP interface; ingestion never issues device commands."""
from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .facts import explain
from .providers import get_provider
from .schemas import DecisionIn, ExecutionEvent, Intervention, Question, SettlementIn
from .store import Conflict, NotFound, Store


def create_app(db_path: str | Path | None = None) -> FastAPI:
    app = FastAPI(title="储能可解释性框架",version="0.1.0",description="输入证据、执行事件与结算由外部模块导入；独立工作进程负责反事实。")
    store = Store(db_path or os.environ.get("ENERGY_EXPLAIN_DB","data/energy.sqlite"))
    app.state.store = store

    @app.exception_handler(NotFound)
    async def missing(request: Request, exc: NotFound):
        return JSONResponse(status_code=404,content={"detail":str(exc)})

    @app.exception_handler(ValueError)
    async def invalid(request: Request, exc: ValueError):
        return JSONResponse(status_code=409 if isinstance(exc,Conflict) else 422,content={"detail":str(exc)})

    def detail(key):
        d = store.get_decision(key)
        provider = get_provider(d)
        return d | {"execution_status":store.execution_status(key),"execution_events":store.execution_events(key),
                    "capabilities":{"counterfactual":provider is not None,
                    "supported_interventions":["first_charge_kw"] if provider else []},
                    "settlement":store.get_settlement(d["run_id"])}

    @app.get("/api/health",tags=["运行"])
    def health(): return {"status":"ok","worker":"independent","schema_version":1}

    @app.post("/api/decisions",tags=["接入"])
    def import_decision(record: DecisionIn):
        saved = store.add_decision(record)
        return detail(saved["id"]) | {"created":saved["created"]}

    @app.get("/api/decisions",tags=["证据"])
    def list_decisions(run_id: str | None = None,site_id: str | None = None):
        return {"decisions":store.decisions(run_id,site_id)}

    @app.get("/api/stations",tags=["证据"])
    def stations(run_id: str | None = None): return store.stations(run_id)

    @app.get("/api/decisions/{key}",tags=["证据"])
    def decision_detail(key: str): return detail(key)

    @app.get("/api/decisions/{key}/explanation",tags=["解释"])
    def explanation(key: str): return store.explanation(key)

    @app.post("/api/decisions/{key}/execution-events",tags=["接入"])
    def append_execution(key: str,event: ExecutionEvent):
        return store.add_execution(key,event) | {"execution_status":store.execution_status(key)}

    @app.post("/api/runs/{run_id}/settlements",tags=["接入"])
    def import_settlement(run_id: str,event: SettlementIn):
        return store.add_settlement(run_id,event) | {"settlement":store.get_settlement(run_id)}

    @app.get("/api/runs/{run_id}/settlements",tags=["证据"])
    def settlement(run_id: str): return {"run_id":run_id,"settlement":store.get_settlement(run_id)}

    @app.post("/api/decisions/{key}/counterfactuals",status_code=202,tags=["反事实"])
    def comparison(key: str,intervention: Intervention): return store.create_job(key,intervention)

    @app.get("/api/counterfactuals/{job_id}",tags=["反事实"])
    def task(job_id: str): return store.get_job(job_id)

    @app.post("/api/questions",tags=["解释"])
    def ask(query: Question):
        d = store.get_decision(query.decision_id)
        e = store.explanation(d["id"])
        q = query.question.strip().rstrip("？?。!")
        why = {"为什么这样做","为什么这样建议","为什么充电","为什么不充电","为什么放电","为什么这样做的"}
        compare = {"首步不充会怎样","首步不充电会怎样","如果首步不充电会怎样","不充电会怎样","现在不充电会怎样"}
        limit = {"为什么不能增加功率","为什么不能增加充电功率","能增加功率吗","为什么不能充更多"}
        result = {"intent":"unsupported","answer":"支持的追问：为什么这样做；首步不充会怎样；为什么不能增加功率。请选用这些问题。","evidence_refs":[],"job":None}
        if q in why:
            result.update(intent="why",answer=e["summary"]+" "+e["scope"],
                          evidence_refs=[ref for f in e["facts"] for ref in f["evidence_refs"]])
        elif q in compare:
            if get_provider(d):
                result.update(intent="compare_no_charge",answer="已提交首步充电功率为 0 kW 的比较任务；后续步骤将重新优化。",
                    evidence_refs=[d["id"]+"#/snapshot",d["id"]+"#/action"],job=store.create_job(d["id"],Intervention(first_charge_kw=0)))
            else:
                result.update(intent="compare_no_charge",answer="该决策未注册匹配的重放模型，当前无法计算反事实。",
                              evidence_refs=[d["id"]+"#/model_version"])
        elif q in limit:
            relevant = [f for f in e["facts"] if f["code"] in {"soc","power_limit"}]
            result.update(intent="power_limit",answer=" ".join(f["text"] for f in relevant)+" 这些是保存的边界；其他控制约束需由策略模块提供。",
                          evidence_refs=[ref for f in relevant for ref in f["evidence_refs"]])
        return result

    dist = Path(__file__).resolve().parents[2]/"frontend"/"dist"
    if dist.exists(): app.mount("/assets",StaticFiles(directory=dist/"assets"),name="assets")

    @app.get("/",include_in_schema=False)
    def index():
        if (dist/"index.html").exists(): return FileResponse(dist/"index.html")
        return JSONResponse(status_code=503,content={"detail":"请先在 frontend 中运行 npm ci && npm run build。API 已可用。"})

    return app
