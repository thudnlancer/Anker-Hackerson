# 文档入口

文档按用途分层，根目录历史文档在迁移完成前保持原路径可读：

- `REPOSITORY_MAP_AND_ALIGNMENT_20261016.md`：仓库与 worktree 基线。
- `RESTRUCTURE_AND_ONLINE_MILESTONE_PLAN.md`：重构阶段和在线流程验收条件。
- `WORKTREE_ALIGNMENT_CHECKLIST.md`：五个 worktree 的合入门禁和测试记录。
- `WORKTREE_DIFF_AUDIT_20261016.md`：W3/W2/E4 相对主线的冲突审计。
- `PROJECT_STATUS_AND_WORKSTREAMS.md`：集成负责人共享状态与当前进度。
- `SYSTEM_ARCHITECTURE_GUIDELINE.md`：系统架构规范。
- `operations/SOP_RECONCILIATION.md`：早期 SOP 对齐记录；当前验收以在线流程里程碑为准。
- `operations/REMOTE_DRY_RUN_RUNBOOK.md`：浏览器终端 + Git 固定提交的远程 dry-run 操作手册。
- `M3_*`、`F1_*`、`F2_*`：比赛阶段验收材料。
- `decisions/HOLD_SEMANTICS_DESIGN.md`：hold/idle 与设备执行语义的历史评审稿；根目录保留兼容链接。
- `milestones/B2_ZERO_FLOW_REVIEW_DEMO.md`：B1 零流量离线演示；不代表硬件验收。
- `milestones/ONLINE_FLOW_MILESTONE.md`：本机模拟赛、HA 回读、看板与部署的最终门槛。
- `WORKER_UNTRACKED_INVENTORY_20261016.md`：尚未纳入发布的 B1/B3/D2 worker 脚本归属与安全门槛。

后续迁移目标：

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
