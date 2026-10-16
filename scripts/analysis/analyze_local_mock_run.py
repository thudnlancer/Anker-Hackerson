"""Analyze and package a completed local mock run without credentials."""
from __future__ import annotations

from collections import Counter
import argparse
import hashlib
import json
from pathlib import Path
import zipfile


ROOT = Path(__file__).resolve().parents[2]
RUN = ROOT / "outputs" / "local_mock_20261016_run3"
JOURNAL = ROOT / "outputs" / "local_mock_20261016_run2" / "ha_reconciliation_state.json"


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--overwrite", action="store_true",
                        help="Explicitly replace generated analysis and evidence archive")
    args = parser.parse_args()
    generated = [RUN / name for name in (
        "analysis_summary.json", "analysis_summary.md", "evidence_manifest.json",
        "local_mock_run3_evidence.zip")]
    existing = [path for path in generated if path.exists()]
    if existing and not args.overwrite:
        raise SystemExit("refusing to overwrite existing evidence; pass --overwrite explicitly: "
                         + ", ".join(path.name for path in existing))
    events = [json.loads(line) for line in (RUN / "console.log").read_text(
        encoding="utf-8").splitlines() if line.strip()]
    ticks = [event for event in events if event.get("event") == "site_result"]
    pre = read_json(RUN / "prelaunch_snapshot.json")
    ended = read_json(RUN / "ended_snapshot.json")
    recovered = read_json(RUN / "post_end_recovery.json")
    journal = read_json(JOURNAL)

    # Analysis is read-only with respect to the durable HA journal. A malformed
    # historical evidence path is reported, never silently repaired here.

    result_pre = pre["dashboard"]["results"]
    result_end = ended["dashboard"]["results"]
    failures = [{key: row.get(key) for key in (
        "site", "received_at", "status", "reason", "delivery_error", "delivery_steps",
        "round_elapsed_seconds", "round_deadline_seconds")}
        for row in ticks if row.get("status") == "delivery_failed"]
    summary = {
        "contest": next((event for event in reversed(events)
                         if event.get("event") == "mock_ended"), None),
        "site_tick_count": len(ticks),
        "status_counts": dict(Counter(row.get("status") for row in ticks)),
        "reason_counts": dict(Counter(row.get("reason") for row in ticks if row.get("reason"))),
        "write_requests_attempted": sum(row.get("write_requests_attempted", 0) for row in ticks),
        "write_requests_acknowledged": sum(row.get("write_requests_acknowledged", 0) for row in ticks),
        "delivery_failures": failures,
        "reconciliation_rows": sum(row.get("status") == "reconciliation_required" for row in ticks),
        "dashboard": {
            "pre_sha256": pre["dashboard_sha256"],
            "end_sha256": ended["dashboard_sha256"],
            "pre_count": result_pre.get("count"), "end_count": result_end.get("count"),
            "pre_mock_count": result_pre.get("mock_count"),
            "end_mock_count": result_end.get("mock_count"),
            "pre_run_count": len(result_pre.get("runs", [])),
            "end_run_count": len(result_end.get("runs", [])),
            "end_total": ended["dashboard"].get("total"),
        },
        "post_end_recovery": {
            site: {"before_mode": item["before"]["mode"]["state"],
                   "after_mode": item["after"]["mode"]["state"],
                   "mode_echo_verified": item["mode_echo_verified"],
                   "recovery_post": item["recovery_post"]}
            for site, item in recovered["sites"].items()
        },
        "journal_pending_sites": sorted(journal.get("pending", {})),
        "journal_clearance_count": len(journal.get("clearances", [])),
    }
    (RUN / "analysis_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    statuses = summary["status_counts"]
    report = [
        "# 本机模拟赛结果分析（run3）", "",
        "## 闭环结果", "",
        "- 模拟器阶段从 `running/mock` 到 `ended/mock`，runner 和只读终结器均已退出。",
        f"- 看板 run 数 {summary['dashboard']['pre_run_count']} → {summary['dashboard']['end_run_count']}，",
        f"  `mock_count` {summary['dashboard']['pre_mock_count']} → {summary['dashboard']['end_mock_count']}。",
        "- 证明本机完整模拟窗口及看板成绩落库；不代表三站 HA 写入全程成功。", "",
        "## 写入与安全", "",
        f"- 共 {len(ticks)} 条 site tick；状态计数：`{statuses}`。",
        f"- HA POST attempted={summary['write_requests_attempted']}，HTTP acknowledged={summary['write_requests_acknowledged']}。",
        f"- {len(failures)} 次 `delivery_failed`，{summary['reconciliation_rows']} 条后续隔离记录。",
        "- 比赛结束时三站均为 `third_party_control`；赛后恢复 POST 仅获 HTTP 响应，模式没有回显。",
        "- 因模拟器结束后设备状态冻结，三站 journal 必须保留，不能人工清除或声称已恢复。", "",
        "## 成绩解读", "",
        f"- dashboard 总 saving={summary['dashboard']['end_total'].get('saving')}，",
        f"  settled saving={summary['dashboard']['end_total'].get('saving_settled')}，",
        f"  shortage_count={summary['dashboard']['end_total'].get('shortage_count')}。",
        "- 该数值包含本轮策略运行前已有历史和模拟器结算，不是本轮增量收益；后续应从 prelaunch/end runs 做窗口级归因。",
        "- 本轮包含设备时间/API 档位冲突回退和下发失败，不能用于策略优劣结论。", "",
        "## HA 写入修复", "",
        "- 每站一旦出现未知下发，本 runner 立即熔断其他站点后续写入，只保留只读观察。",
        "- 对 HTTP 错误记录状态码与 `http_rejected_not_device_verified`，不记录响应体/凭证、不自动重试。",
        "- 全套测试 `355 passed`；修复尚未在下一场本机 mock 窗口经真实 HA 行为验收。", "",
    ]
    (RUN / "analysis_summary.md").write_text("\n".join(report), encoding="utf-8")

    files = [RUN / name for name in (
        "prelaunch_snapshot.json", "ended_snapshot.json", "post_end_readonly_snapshot.json",
        "post_end_recovery.json", "post_recovery_readonly_snapshot.json", "console.log",
        "console.err", "finalizer.log", "finalizer.err", "audit.csv", "runtime_meta.json",
        "analysis_summary.json", "analysis_summary.md")]
    files.extend([JOURNAL, ROOT / "outputs" / "local_mock_20261016_run2" / "manual_recovery.json"])
    manifest = {str(path.relative_to(ROOT)): {"sha256": sha256(path), "bytes": path.stat().st_size}
                for path in files}
    manifest_path = RUN / "evidence_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                             encoding="utf-8")
    archive = RUN / "local_mock_run3_evidence.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for path in [*files, manifest_path]:
            bundle.write(path, path.relative_to(ROOT).as_posix())
    print(json.dumps({"summary": summary, "archive": str(archive),
                      "archive_sha256": sha256(archive), "manifest": str(manifest_path)},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
