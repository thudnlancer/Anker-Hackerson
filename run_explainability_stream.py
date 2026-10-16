"""Read-only three-site collection into the local explainability database."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
from energy_lab.config import load_runtime_config
from energy_lab.explainability_stream import ReadOnlyStream, StreamCheckpoint


def parser():
    result = argparse.ArgumentParser(description="持续只读采集三站候选证据；不下发控制、不加载预测 pickle。")
    result.add_argument("--config", type=Path, default=Path("local_online_config.json"))
    result.add_argument("--run-id", required=True, help="重启复用此 ID；设备时间回退必须更换 ID")
    result.add_argument("--db", type=Path, default=Path("explainability-app/data/energy.sqlite"))
    result.add_argument("--checkpoint", type=Path, default=Path("outputs/explainability-stream/checkpoint.sqlite"))
    bounded = result.add_mutually_exclusive_group()
    bounded.add_argument("--once", action="store_true")
    bounded.add_argument("--cycles", type=int, help="本次最多采集轮数")
    result.add_argument("--poll", type=float, default=5., help="轮次间隔秒数，默认 5")
    result.add_argument("--max-age", type=float, default=15., help="实际观测墙钟新鲜度门限秒数")
    result.add_argument("--solver-seconds", type=float, default=1.)
    result.add_argument("--device-timezone", help="只在已知设备时间坐标时显式覆盖；否则使用 API 时间戳")
    result.add_argument("--input-kind", choices=("mixed", "synthetic"), default="mixed")
    return result


def main(argv=None):
    options = parser().parse_args(argv)
    # These imports are local to the integration entry point. The stream module
    # remains usable by the shared strategy without installing the web package.
    sys.path.insert(0, str(Path(__file__).resolve().parent / "explainability-app" / "src"))
    from energy_explain.schemas import DecisionIn
    from energy_explain.store import Store

    try:
        cfg = load_runtime_config(options.config, mode_override="dry-run")
        store = Store(options.db)

        def status_sink(status):
            store.update_collection_status(status)
            print(json.dumps(status, ensure_ascii=False, allow_nan=False, separators=(",", ":")), flush=True)

        collector = ReadOnlyStream(cfg, run_id=options.run_id, checkpoint=StreamCheckpoint(options.checkpoint),
            record_sink=lambda record: store.add_decision(DecisionIn.model_validate(record)), status_sink=status_sink,
            poll_interval_seconds=options.poll, freshness_threshold_seconds=options.max_age,
            solver_seconds=options.solver_seconds, device_timezone=options.device_timezone, input_kind=options.input_kind)
        status = collector.run(cycles=1 if options.once else options.cycles)
        if status.get("reason") == "status_sink_failed":
            print(json.dumps(status, ensure_ascii=False, allow_nan=False, separators=(",", ":")), flush=True)
        return 0 if collector.last_round_success and status.get("reason") == "cycles_completed" else 1
    except Exception as exc:
        # Never print request objects, configuration, auth responses or exception text.
        print(json.dumps({"state": "stopped", "reason": "startup_failed", "error_type": type(exc).__name__,
                          "writes_performed": 0}), flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
