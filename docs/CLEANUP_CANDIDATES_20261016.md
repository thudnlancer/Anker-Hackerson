# 清理候选清单（2026-10-16）

本清单是审阅与执行的边界，不代表已删除或移动任何文件。清理前重新生成 `archive/OUTPUT_INVENTORY_20261016_CURRENT.json`、核对 Git 状态与引用；受保护证据 SHA 不匹配时停止。私有配置、令牌和环境文件不进入此清单。

## 保留

| 范围 | 理由 |
|---|---|
| `outputs/local_mock_20261016_run2/`、`outputs/local_mock_20261016_run3/`、`outputs/local_mock_20261016/` 及既有赛后只读复核目录 | 模拟比赛、HA 事务、pending reconciliation 与看板证据；已纳入受保护 SHA 清单。 |
| 当前 O7 incident journal、恢复记录和相关审查结果 | 事故仍未由负责人结案；不得改写或清空。 |
| `F1_*`、`F2_RUNBOOK.md`、`M3_*`、`W1_DELIVERY_TRANSACTION_CANDIDATE.md`、`W3_NEXT_WINDOW_D1_D3_CHECKLIST.md` | 与当前签核、模拟在线准备、人工恢复或现场下一窗口步骤相关；不能因日期旧而删除。 |
| `PROJECT_STATUS_AND_WORKSTREAMS.md`、`NEXT_STAGE_RESULTS.md`、`EXPERIMENT_REVIEW.md`、`PARALLEL_EXPERIMENT.md`、`EXPLAINABILITY_COLLABORATION.md` | 仍是阶段事实/实验沿革，且被 README、产品文档、manifest 或其他证据引用。后续如拆分，保留原始版本与链接映射。 |
| 其余 `outputs/` 项目 | 当前清单统一标记 `review_required`；没有逐项审查前不做批量删除。 |

## 归档候选

| 路径 | 证据与建议 | 状态 |
|---|---|---|
| `HAtJdgo45otz2Px31bYcu3PDnRf.md` | 已下载的通用大模型使用说明，正文称图片/超链接附件为 0；未发现仓库内引用。用户已授权原样归档。 | 已归档至 `archive/docs/HAtJdgo45otz2Px31bYcu3PDnRf.md`；原始字节 SHA-256 记录于 `archive/docs/archive-manifest-20261016.json`。 |
| `B4_COLLECTION_REDUNDANCY_DESIGN.md` | 设计稿标记待实施，正文引用当前采集代码及共享工作流；可能被后续 W3 采集器工作使用。 | 暂不归档，先由 W3 负责人确认是否已替代。 |

## 可删除候选

以下均为根目录被 `.gitignore` 忽略、未被 Git 跟踪的可再生输出；已有基线 SHA 用于确认审阅期间未变。它们仍可能被命令默认读取，**本清单不执行删除**。

| 路径 | 字节 | SHA-256 | 依赖/处置 |
|---|---:|---|---|
| `forecast_run.log` | 4,226 | `C853160502EA7F5A0DA494C8E67CF4B2C9AF72FD535FB5EF95C34F6C8DD7400E` | 未发现代码读取；可由重新训练生成。可在确认不需保留训练日志后删除。 |
| `forecast_metrics.json` | 2,826 | `3E48CE1D8781D58EEA19A0B02CB4ABD41F37508ADC2760A371799670637D958B` | `run_forecast.py` 默认重建；远程示例配置仍提及该路径。先核对无运行进程及不需留存旧指标，再删除。 |

## 需人工确认

| 路径 | 当前证据 | 要求 |
|---|---|---|
| `online_dry_run_latest.json` | 未跟踪、被忽略，SHA-256 `D2390DFB438BB99829023AE87F1B5EF22C32976AFC918D1552D1FF3EAB5818F9`；`review_interface_readiness.py` 会读取此文件。 | 保留至对应 readiness 复核完成，或先修改消费者为显式输入路径并归档本文件。 |
| `PROJECT_STATUS_AND_WORKSTREAMS.md` | 活跃共享工作流日志且包含多 Worker 状态；文件较大、跨阶段叙述。 | 只能在所有 Worker 同意后拆分/归档旧日志段；不得覆盖其他成员的未提交内容。 |
| `NEXT_STAGE_RESULTS.md` | 大型按时间记录的实验报告，仍由 README 与 F1 文档引用。 | 先建立章节级导航和引用映射，再考虑拆分；不删历史实验结论。 |
| `EXPLAINABILITY_COLLABORATION.md` | 被 explainability-app 文档直接引用。 | 新协作说明完整替代且更新全部链接前，不移动或删除。 |

## 执行门禁

1. 审阅者确认候选的业务用途、消费者和留存期限。
2. 归档时保持原始字节，写入来源路径、时间、SHA-256 的 manifest，并修复链接。
3. 删除仅限明确可再生且无消费者的 ignored 文件；先确认相关进程已结束，并保留删除前 SHA 记录。
4. 每批操作后运行 `python scripts/maintenance/check_doc_links.py` 和 `python -m pytest tests -q`，复算受保护证据 SHA。
5. 不对 `outputs/` 执行递归清理，不触碰 pending reconciliation journal。
