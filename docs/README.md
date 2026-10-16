# 文档入口

文档按用途分层。根目录保留 README、配置/运行入口、历史报告及少量兼容链接；当前维护文档以本目录路径为准。

- `REPOSITORY_MAP_AND_ALIGNMENT_20261016.md`：仓库与 worktree 基线。
- `RESTRUCTURE_AND_ONLINE_MILESTONE_PLAN.md`：重构阶段和在线流程验收条件。
- `WORKTREE_ALIGNMENT_CHECKLIST.md`：五个 worktree 的合入门禁和测试记录。
- `WORKTREE_DIFF_AUDIT_20261016.md`：W3/W2/E4 相对主线的冲突审计。
- `PROJECT_STATUS_AND_WORKSTREAMS.md`：集成负责人共享状态与当前进度。
- `architecture/SYSTEM_ARCHITECTURE_GUIDELINE.md`：系统架构规范。
- `operations/SOP_RECONCILIATION.md`：早期 SOP 对齐记录；当前验收以在线流程里程碑为准。
- `operations/REMOTE_DRY_RUN_RUNBOOK.md`：浏览器终端 + Git 固定提交的远程 dry-run 操作手册。
- `operations/O7_RECONCILIATION_CLEARANCE_RUNBOOK.md`：O7 三站 reconciliation 结案前的只读审查与逐站清除门禁。
- `M3_*`、`F1_*`、`F2_*`：比赛阶段验收材料。
- `decisions/HOLD_SEMANTICS_DESIGN.md`：hold/idle 与设备执行语义的历史评审稿；根目录保留兼容链接。
- `milestones/B2_ZERO_FLOW_REVIEW_DEMO.md`：B1 零流量离线演示；不代表硬件验收。
- `milestones/ONLINE_FLOW_MILESTONE.md`：本机模拟赛、HA 回读、看板与部署的最终门槛。
- `milestones/ONLINE_FLOW_EVIDENCE_INDEX_20261016.md`：run3 证据到 O1–O9 门槛的逐项映射。
- `milestones/STRATEGY_COMPARISON_MILESTONE_PLAN.md`：策略解耦 + 15min 离线回放试验场 + MILP/梯度下降/负载预测/LLM 对比的里程碑计划。
- `WORKER_UNTRACKED_INVENTORY_20261016.md`：尚未纳入发布的 B1/B3/D2 worker 脚本归属与安全门槛。
- `CLEANUP_CANDIDATES_20261016.md`：文档、脚本与本地输出的保留/归档/删除候选及执行门禁；不代表清理已执行。

分析脚本迁移采用“实现归档到 `scripts/analysis/`、根目录保留兼容入口”的方式；只有经过引用扫描和定向测试的脚本才可迁移。

当前主要结构与后续归档方向：

迁移文档后运行 `python scripts/maintenance/check_doc_links.py`；它只检查 Git 跟踪的 Markdown 文件中的本地相对链接，不访问外网或 `outputs/`。兼容入口可保留旧路径，但新引用应指向 `docs/` 中的正文。

```text
docs/architecture/   架构与接口
docs/operations/     运行、恢复、部署
docs/milestones/     F1/F2/M3/E4/E5 验收
docs/decisions/      关键决策和取舍
scripts/online/      在线采集、模拟、回放
scripts/analysis/    结果分析和审计
scripts/maintenance/ 清理、manifest、配置检查
archive/             只读历史资料和旧产物
```
