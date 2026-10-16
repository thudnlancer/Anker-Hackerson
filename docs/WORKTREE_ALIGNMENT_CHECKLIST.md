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

快速测试（2026-10-16，本机 Windows）：主集成 355 passed；W2 170 passed；W3 194 passed；E4 123 passed、1 failed。E4 失败项是解释性 JSONL 测试用系统默认 GBK 读取 UTF-8 中文，设置 `PYTHONUTF8=1` 后该项 1 passed；这是测试环境/编码兼容问题，不是 energy-floor 数学约束已被否定。E4 整套测试仍需在 UTF-8 环境下重跑并记录，不能写成“全绿”。

补充：W2 `explainability-app` 后端测试在当前根环境无法收集（缺少 `fastapi`，且未安装 package，`energy_explain` 不在 import path）；这不是代码通过。其 frontend Vitest 为 16 passed。后续集成门禁必须使用 `explainability-app` 的锁定依赖和 package 安装步骤重跑后端测试。

进一步复核：借用现有 W3 `.venv` 且显式设置 W2 产品 `src` 与 W2 根 `src` 为 `PYTHONPATH`，W2 后端得到 98 passed + 49 subtests（1 条 Starlette/httpx deprecation warning）。这证明 W2 代码在现有依赖环境可执行，但借用 W3 虚拟环境不等价于 W2 自己的锁定依赖可复现；合入前仍需独立环境门禁。E4 全套设置 `PYTHONUTF8=1` 后 124 passed；W2 E5 detached 验证树核心测试 200 passed。
