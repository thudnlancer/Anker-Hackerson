# Worktree 对齐清单

每个 worker worktree 合并前必须填写：

- [ ] worktree 路径、分支、HEAD SHA、是否 detached。
- [ ] 工作区是否干净；未提交文件逐项列出。
- [ ] 相对集成基线的提交范围和文件清单。
- [ ] 核心测试、产品测试、静态检查命令与结果。
- [ ] 配置 SHA、是否包含凭证、是否包含远端部署动作。
- [ ] 运行产物目录、日志、dashboard 前后快照和 manifest。
- [ ] 与现有 W1/W2/W3/E4 变更的冲突及处理决定。
- [ ] 合入后的回滚提交和验收负责人。

当前登记：

| worker | HEAD | 是否可直接合入 | 下一步 |
|---|---|---|---|
| W1/主集成 | `6b72ff3` + dirty | 否 | 先保存本机在线与 HA 修复变更，再形成集成提交 |
| E4 | `f6ff5df` + dirty | 否 | 提交 energy-floor 变更并运行定向测试 |
| W2 | `a228f4b` | 暂不 | 提供 E4/E5 变更清单和产品测试结果 |
| W2 E5 check | `8362854` detached | 否 | 仅作为验证参考，不直接发布 |
| W3 | `7cd64c4` | 暂不 | 提交采集器变更并提供离线/在线证据 |

## 2026-10-16 集成复核（以 `integration/online-milestone` 为准）

集成 worktree 为 `D:\Anker黑客松\integration-worktree`，HEAD `bc94545`，工作区干净。它从主工作区已提交的 `c994ddd` 出发，以 merge commit 纳入 W2 `a228f4b` 的离线解释与看板只读审计增量；七处冲突逐项解决。主工作区 `feat/explainability-integration` 尚停在 `c994ddd`，因此**分支尚未对齐**；主工作区另有未跟踪的 worker 文件，不在这次合并范围，不能批量清理。

| 工作树 | 当前状态 | 集成处置 |
|---|---|---|
| 主工作区 | `c994ddd`，有未跟踪文件 | 在线 HA 修复和仓库地图已作为集成基线；未跟踪文件逐项归属前保持原位。待集成分支复验及归属完成后才移动主工作区指针。 |
| W2 | `a228f4b`，干净 | 已通过 `bc94545` 合入；保留主线在线状态和 HA 只读说明，吸收旧 trace 缺字段为 `null` 的兼容逻辑及审计工具。 |
| W3 | `7cd64c4`，干净 | 不整分支合入。其采集器与当前主线的天气降级、epoch、单站容错契约有差异；仅在逐文件审查和定向测试后摘取净增量。 |
| E4 | `f6ff5df`，仍有未提交测试改动 | 不整分支合入。`energy_floor_kwh` 已在当前 `rolling.py`，直接覆盖会回退 time-limit incumbent、idle/零流与 provenance 契约；仅补足不冲突测试或证据。 |
| W2 E5 check | detached `8362854` | 保留验证参考，不作为发布分支。 |

`bc94545` 回归：核心 `315 passed`；解释性后端 `99 passed + 49 subtests`（借用主工作区应用 `.venv`，非独立锁定环境）；前端 `18 passed` 且 build 通过。核心用例数比主工作区的 `355 passed` 少，是因为主工作区若干未跟踪测试尚未归属/纳入，不代表这些测试被删除。集成 worktree 未包含本机私有 `local_online_config.json`；离线 snapshot 模式现允许缺席配置，联网读取仍必须有配置，原先由缺文件导致的三个测试失败已修复。

合并差异未触及 `src/energy_lab/{config,ha_client,reconciliation,rolling}.py`。run2 三站 pending journal 与 run3 比赛证据保留在主工作区的忽略目录，没有复制、修改或清理；本轮未进行远端控制、HA 写入或新模拟赛。发布前仍需独立锁定依赖回归、W3/E4 差异结案与完整在线闭环复验。

2026-10-16 后续更新：主工作区已安全快进到 `564cf75`，与当时的集成候选对齐；原未跟踪的 W2 dashboard 文档按 SHA 原样归档到 `archive/worktree_preintegration_20261016/`，其余未跟踪 worker 文件未动。主工作区核心测试 **358 passed**。独立集成 worktree 的 `explainability-app/.venv` 从 `requirements.lock` 和 `requirements.shared.lock` 新建，`pip check` 通过，后端完整测试 **99 passed + 49 subtests**；此前“借用既有环境”的限制已解除。W3/E4 仍未整分支合入，O7/O9 仍未通过。

快速测试（2026-10-16，本机 Windows）：主集成 355 passed；W2 170 passed；W3 194 passed；E4 123 passed、1 failed。E4 失败项是解释性 JSONL 测试用系统默认 GBK 读取 UTF-8 中文，设置 `PYTHONUTF8=1` 后该项 1 passed；这是测试环境/编码兼容问题，不是 energy-floor 数学约束已被否定。E4 整套测试仍需在 UTF-8 环境下重跑并记录，不能写成“全绿”。

补充：W2 `explainability-app` 后端测试在当前根环境无法收集（缺少 `fastapi`，且未安装 package，`energy_explain` 不在 import path）；这不是代码通过。其 frontend Vitest 为 16 passed。后续集成门禁必须使用 `explainability-app` 的锁定依赖和 package 安装步骤重跑后端测试。

进一步复核：借用现有 W3 `.venv` 且显式设置 W2 产品 `src` 与 W2 根 `src` 为 `PYTHONPATH`，W2 后端得到 98 passed + 49 subtests（1 条 Starlette/httpx deprecation warning）。这证明 W2 代码在现有依赖环境可执行，但借用 W3 虚拟环境不等价于 W2 自己的锁定依赖可复现；合入前仍需独立环境门禁。E4 全套设置 `PYTHONUTF8=1` 后 124 passed；W2 E5 detached 验证树核心测试 200 passed。

2026-10-16 追加归属：`src/energy_lab/calibration.py`、`src/energy_lab/zero_flow.py` 及其 34 项定向测试已从 worker 未跟踪文件纳入主线（提交 `751f8c6`）。它们是无 I/O 的物理一致性/零流量判定模块；`charge=0 W` 仍标记为 `hardware_verified=false`，没有因此授权或执行真实 HA 写入。D1/D2 分析脚本、B1 runner 和其余 worker 文件仍待逐项归属。

后续收敛：B1/B3 runner 已在 `7202ff9` 纳入并通过核心测试；B1 默认 dry-run 且 pending journal 会在联网前阻断 live。W3 启动/重启脚本和 D2 批处理仍保留未跟踪状态，不纳入当前发布门禁。

最新复核（2026-10-16）：主工作区 `feat/explainability-integration` 与集成 worktree `integration/online-milestone` 均为 `b706fd2`，集成树干净。核心全套 **365 passed**；产品独立锁定环境 **99 passed + 49 subtests**，`pip check` 通过；前端 **18 passed** 且 build 通过。主工作区只剩未跟踪 `restart_w3_collector.ps1`（进程停止/重启动作）、`run_d2_expansion.ps1`（旧入口依赖）和 `archive/.gitkeep`，状态详见 worker 清单；W2/W3/E4 worktree 仍分别在其原分支，不做无必要的 merge，功能差异审计结论见 `WORKTREE_DIFF_AUDIT_20261016.md`。run2 journal 三站 pending 保留，O7 未通过。

再后续更新：D2 expansion 已移入 `scripts/analysis/`、默认 plan-only；collector restart 已移入 `scripts/maintenance/`、默认只读 PID/heartbeat/命令行预检。只对脚本做了离线测试，没有启动 D2 采集，也没有停止/启动 collector。详见 `WORKER_UNTRACKED_INVENTORY_20261016.md`。

最终结构复验（当前 `2e2f0a2`）：主工作区与 `integration/online-milestone` 同 SHA 且均无未提交文件；全套核心测试 **370 passed**。W2/W3/E4 保持独立原始 worktree 以供追溯，不冒险覆盖主线。run2 journal pending 站点仍为 `1,2,3`，run3 ZIP 当前 SHA `A9E622EEF8C26C6C0D2A2FCAB3364B255B58AE7595D4F5F61C5E56A4C8172656`，未被重构流程改写。本轮没有启动 D2、停止/启动 collector、HA 写入或远程操作。

2026-10-16 归档后复核：主工作区与 `integration/online-milestone` 已对齐到 `52370a5`，均无未提交文件；核心测试 **378 passed**，产品锁定环境 **99 passed + 49 subtests**、`pip check` 通过，前端 **18 passed** 且 production build 通过。E4/W2/W3/E5 参考 worktree 仍保持原始状态；run2 三站 pending、run3 证据和受保护 SHA 未改写。本轮未启动模拟赛、collector、HA 写入或远程动作。

## 历史快照（2026-10-16，`1b1c9f7`）

主工作区 `feat/explainability-integration` 与集成 worktree `integration/online-milestone` 已对齐到 `1b1c9f7`，均干净；核心测试均为 373 passed，本地 dry-run 部署预检通过。E4/W2/W3/E5 参考 worktree 仍分别位于 `f6ff5df`、`a228f4b`、`7cd64c4`、`8362854`，不直接覆盖主线。三站 pending 未解除，O7 与完整在线流程均未通过。

产物清单工具现保护 run2/run3、其基线与已完成的赛后只读复核目录。2026-10-16 当前清单记录 165 个 outputs 顶层条目、25 个受保护文件 SHA；保护范围外条目仍需人工审查，不执行批量清理。

## 当前复验入口（2026-10-16）

产品后端需在 `explainability-app/` 中使用其 `.venv` 安装锁定依赖和 editable package，并将仓库根目录 `src/` 加入 `PYTHONPATH`，然后运行 `python -m pytest tests -q`；本次结果为 **99 passed + 49 subtests**，`pip check` 无损坏依赖。直接从仓库根目录用系统 Python 跑产品测试会因缺少 FastAPI/包安装而在收集阶段失败，不能视作代码回归。前端在 `explainability-app/frontend/` 执行 `npm test` 与 `npm run build`，本次分别为 **18 passed** 与构建成功。

CI 门禁已同步采用同一安装顺序：先安装两个 lock 文件，再执行 `pip install --no-deps -e explainability-app`，避免工作流遗漏产品包安装而在测试收集阶段失败。

这些是 O8 的本地代码门禁证据，不替代 O7 的人工安全复核、HA 真机回读或 O9 的实际部署运行。
