# 仓库地图与 worktree 对齐基线（2026-10-16）

当前 Git 仓库位于实验环境，上级目录只是工作容器。五个 worktree 的状态：

| 目录 | 提交 | 职责 | 状态 |
|---|---|---|---|
| 实验环境 | 6b72ff3 | 集成基线、本机在线模拟、HA 写入 | 大量未提交改动；run3 三站 journal pending |
| e4-worktree | f6ff5df | energy-floor 求解约束 | 有未提交测试改动 |
| w2-worktree | a228f4b | explainability/E4-E5 产品与看板 | 尚未合入主工作区 |
| w2-e5-integration-check | 8362854 | E5 选取性集成验证 | detached，不作发布源 |
| w3-worktree | 7cd64c4 | 在线采集、watchdog、D1/D3 | 尚未合入主工作区 |

没有 worktree 与主工作区完全一致。远端集成分支仍在 2a49d23，本地改动不能视为已部署。

目标结构：src/energy_lab 放在线策略核心；explainability-app 放产品服务；docs 分架构、运维、里程碑、决策；scripts 分在线、分析、维护；outputs 按 run ID 留证；archive 只读保留过时资料。根目录只保留入口、README 与配置模板。

对齐规则：先固定每个 worktree 的 SHA、变更清单和测试证据，再按在线安全链路、W3 采集器、W2 产品、E4 求解约束逐一集成。禁止整体重置或删除脏工作区。run2 的 reconciliation journal 和 run3 证据包在三站 pending 解除前不得迁移或删除。

W3 逐文件比对结论见 `WORKTREE_DIFF_AUDIT_20261016.md`：W3 分支核心采集器比主线天气降级/epoch 契约更旧，不可直接覆盖。第一批目录化已经把本机 mock 工具移入 `scripts/online/`、分析工具移入 `scripts/analysis/`，并保留根目录兼容入口。`archive/OUTPUT_INVENTORY_20261016.json` 是 161 个 output 顶层条目的只读盘点，21 个 run2/run3 受保护文件记录 SHA；没有执行删除或移动。
