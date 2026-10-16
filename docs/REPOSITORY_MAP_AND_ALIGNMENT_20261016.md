# 仓库地图与 worktree 对齐基线（2026-10-16）

当前 Git 仓库位于实验环境，上级目录只是工作容器。下表保留初次盘点快照；当前进度以文末更新和 `WORKTREE_ALIGNMENT_CHECKLIST.md` 为准。

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

## 2026-10-16 集成更新

主工作区现为 `c994ddd`，有未跟踪 worker 文件；独立集成候选 `integration-worktree` 位于 `integration/online-milestone` @ `7cc9b68`，工作区干净。W2 `a228f4b` 已在集成候选中通过 merge commit `bc94545` 纳入；主工作区尚未快进，因此“已合入集成候选”不等于“主工作区已对齐”或“已部署”。W3 `7cd64c4` 与 E4 `f6ff5df` 仍须选择性审查，不能整分支覆盖较新的在线采集或求解契约。run2/run3 的三站事故证据仍在主工作区忽略目录，未归档或删除；在线里程碑 O7/O9 未通过。

后续结构更新：主工作区与集成 worktree 现均为 `b4034a2`。W2 的 dashboard 快照工具已迁到 `scripts/online/capture_dashboard_snapshot.py`，根目录保留同名兼容入口；该入口和整套核心测试通过。上段是历史时间点的审计记录，并非当前 HEAD。主工作区仍有未归属的 worker 文件；未获归属前不批量移动或删除。

产物清单：`archive/OUTPUT_INVENTORY_20261016.json` 是较早时间点的 161 项快照；`archive/OUTPUT_INVENTORY_20261016_POSTREFACTOR.json` 是本轮 162 项复核清单，包含只读复核目录。run3 压缩包在两份清单之间由既有分析/打包流程重建，旧哈希保留作历史对照，新清单记录当前 SHA；不能把两份清单的 SHA 差异解释为重构代码改写比赛证据。

## 当前权威快照（2026-10-16）

此快照优先于上面的历史状态段落：主工作区 `D:/Anker黑客松/实验环境`（`feat/explainability-integration`）与 `D:/Anker黑客松/integration-worktree`（`integration/online-milestone`）现均为 `1b1c9f7` 且工作区干净；核心测试均 373 passed。`outputs/deployment_preflight_20261016/report.json` 对该固定提交给出本地 browser-terminal dry-run 准备通过。它不代表远端已部署或在线运行。

参考 worktree 保持原样：E4 `f6ff5df`（有未提交测试与 `.agents/`）、W2 `a228f4b`、W3 `7cd64c4`、W2 E5 detached `8362854`。不以旧分支覆盖主线；后续若吸收其新工作，须重新审查差异。run2/run3 三站 reconciliation pending 和 O7 失败证据继续保留。

2026-10-16 产物清单复核：`archive/OUTPUT_INVENTORY_20261016_CURRENT.json` 记录 165 个 outputs 顶层条目，其中 5 个在线事故/恢复证据目录保护了 25 个文件 SHA；与上次清单中原有受保护文件的 SHA 全部一致。其余 160 个条目一律标为 `review_required`，不据此自动删除。新开模拟窗口的临时只读快照只作该窗口状态观察，不归入本项目 run2/run3 里程碑证据。

分析脚本归属复核：`audit_w3_cache.py`、`analyze_d3_causal_pairs.py`、`analyze_irradiance_shadow.py` 等根目录脚本仍被测试、报告脚本或现场 checklist 按根路径导入；它们暂不迁移到 `scripts/analysis/`，以免破坏可复现入口。已迁入 `scripts/analysis/` 的脚本继续作为新目录正文，根目录兼容入口只在引用已验证时保留。

随后完成一项安全迁移：`audit_strategy_results.py` 的实现现位于 `scripts/analysis/audit_strategy_results.py`，根目录文件为兼容入口；旧 import 和 CLI 已有定向测试覆盖。当前主线与集成 worktree 基线为 `1624803`，核心测试 377 passed，Markdown 链接检查 0 条断链。

## 当前权威快照（归档后）

主工作区 `feat/explainability-integration` 与 `integration/online-milestone` 已同步至 `4089b491`，均无未提交改动；核心测试为 **378 passed**，Markdown 本地链接检查为 **0**。本轮仅将用户确认无关的模型使用说明原样归档至 `archive/docs/`，并以 `archive/docs/archive-manifest-20261016.json` 记录来源和 SHA；未改写任何 outputs、HA reconciliation pending 或比赛证据。

参考 worktree 仍保持独立以供追溯：E4 `f6ff5df`（有未提交测试改动与 `.agents/`）、W2 `a228f4b`（其已交付提交已在主线祖先中）、W3 `7cd64c4`、W2 E5 detached `8362854`。当前没有证据支持将 W3/E4/E5 整分支覆盖主线；需逐文件审查后再决定是否摘取。O7 三站 pending、O9 远程部署和完整在线流程仍未通过。
