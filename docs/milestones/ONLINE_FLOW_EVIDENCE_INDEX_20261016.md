# 在线流程证据索引（run3）

本索引把现有本机模拟赛证据映射到验收门槛。它是证据导航，不把未完成的 O7/O9 写成通过；所有路径均相对于仓库根目录。

## 已有证据

| 门槛 | 证据 | 结论 |
|---|---|---|
| O1 | `outputs/local_mock_20261016_run3/console.log`、`ended_snapshot.json`、`runtime_meta.json` | `mock_ended` 已记录，窗口从 running/mock 结束，runner 退出证据已保存。 |
| O2 | `outputs/local_mock_20261016_run3/audit.csv`、`analysis_summary.json` | 设备时间/API 冲突被显式分类并回退；存在 180 条 `device_api_date_hour_conflict`，因此是部分通过，不是无冲突通过。 |
| O3 | `audit.csv`、`console.log`、`evidence_manifest.json` | 981 次写请求、978 次确认和失败步骤均有事务审计。 |
| O4 | `audit.csv`、`analysis_summary.json` | `reconciliation_required` 及 `previous_delivery_unknown` 被记录，可区分未知/陈旧路径。 |
| O5 | `analysis_summary.json`、`outputs/local_mock_20261016_run2/ha_reconciliation_state.json` | 3 次 delivery failure 被隔离，unknown incident 持久化；下一专用窗口仍需复验。 |
| O6 | `prelaunch_snapshot.json`、`ended_snapshot.json`、`analysis_summary.json` | dashboard 前后快照由 SHA 对账，mock count 从 6 增至 7。 |

## 明确未通过

| 门槛 | 当前证据 | 未通过原因 |
|---|---|---|
| O7 | `post_end_recovery.json`、`post_recovery_readonly_snapshot.json`、run2 journal | 三站仍为 `third_party_control`，`mode_echo_verified=false`；当前 pending incident 为站点 1/2/3。不得自动清除。 |
| O9 | `outputs/deployment_preflight_<commit>/report.json` | 仅证明固定提交的本地 browser-terminal dry-run 前置条件；未证明比赛机部署、PID 存活或不间断运行。 |

## O8 与复核规则

- 主线核心测试、产品锁定环境、前端测试/构建和 `pip check` 的具体结果记录在 [`WORKTREE_ALIGNMENT_CHECKLIST.md`](../WORKTREE_ALIGNMENT_CHECKLIST.md)。
- `evidence_manifest.json` 记录 run3 文件 SHA；受保护文件 SHA 由 `archive/OUTPUT_INVENTORY_20261016_CURRENT.json` 复核。
- 下一专用模拟窗口必须在操作者完成三站安全核验后再启动；新证据应使用新的 run ID，不覆盖本索引引用的 run3 文件。
