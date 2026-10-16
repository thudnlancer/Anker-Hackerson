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

快速测试（2026-10-16，本机 Windows）：主集成 355 passed；W2 170 passed；W3 194 passed；E4 123 passed、1 failed。E4 失败项是解释性 JSONL 测试用系统默认 GBK 读取 UTF-8 中文，设置 `PYTHONUTF8=1` 后该项 1 passed；这是测试环境/编码兼容问题，不是 energy-floor 数学约束已被否定。E4 整套测试仍需在 UTF-8 环境下重跑并记录，不能写成“全绿”。

补充：W2 `explainability-app` 后端测试在当前根环境无法收集（缺少 `fastapi`，且未安装 package，`energy_explain` 不在 import path）；这不是代码通过。其 frontend Vitest 为 16 passed。后续集成门禁必须使用 `explainability-app` 的锁定依赖和 package 安装步骤重跑后端测试。

进一步复核：借用现有 W3 `.venv` 且显式设置 W2 产品 `src` 与 W2 根 `src` 为 `PYTHONPATH`，W2 后端得到 98 passed + 49 subtests（1 条 Starlette/httpx deprecation warning）。这证明 W2 代码在现有依赖环境可执行，但借用 W3 虚拟环境不等价于 W2 自己的锁定依赖可复现；合入前仍需独立环境门禁。E4 全套设置 `PYTHONUTF8=1` 后 124 passed；W2 E5 detached 验证树核心测试 200 passed。
