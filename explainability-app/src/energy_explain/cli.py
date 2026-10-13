import argparse
import json
from pathlib import Path
from pydantic import ValidationError

from .demo import seed_demo
from .store import Store


def main(argv=None):
    parser = argparse.ArgumentParser(description="独立储能可解释性服务")
    sub = parser.add_subparsers(dest="command",required=True)
    seed = sub.add_parser("seed",help="显式导入三站人工演示")
    seed.add_argument("--db",default="data/energy.sqlite")
    recovered = sub.add_parser("verify-model",help="导入恢复的真实 MILP 源码＋人工验证输入（不连接设备）")
    recovered.add_argument("--db",default="data/recovered.sqlite")
    ingest = sub.add_parser("import-records",help="将策略导出的业务 JSONL 导入本地证据库（不连接设备）")
    ingest.add_argument("--file",type=Path,required=True)
    ingest.add_argument("--db",default="data/energy.sqlite")
    serve = sub.add_parser("serve",help="启动同源 API 与构建后的前端")
    serve.add_argument("--db",default="data/energy.sqlite")
    serve.add_argument("--host",default="127.0.0.1")
    serve.add_argument("--port",type=int,default=8080)
    args = parser.parse_args(argv)
    if args.command == "seed":
        print(json.dumps({"source_kind":"demo","decision_keys":seed_demo(Store(args.db))},ensure_ascii=False))
    elif args.command == "verify-model":
        from .recovered_validation import seed_validation
        print(json.dumps({"provenance":"recovered_source_synthetic_inputs","decision_keys":seed_validation(Store(args.db))},ensure_ascii=False))
    elif args.command == "import-records":
        from .schemas import DecisionIn
        records = []
        for index,line in enumerate(args.file.read_text(encoding="utf-8").splitlines(),1):
            if not line.strip(): continue
            try: records.append(DecisionIn.model_validate_json(line))
            except (ValidationError,ValueError): parser.error(f"第 {index} 行不符合决策契约；未开始导入。")
        if not records: parser.error("文件中没有决策记录。")
        store = Store(args.db)
        saved = [store.add_decision(d) for d in records]
        print(json.dumps({"records":len(saved),"created":sum(r["created"] for r in saved),"decision_keys":[r["id"] for r in saved]},ensure_ascii=False))
    else:
        import uvicorn
        from .api import create_app
        uvicorn.run(create_app(args.db),host=args.host,port=args.port)
