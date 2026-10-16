# Worktree 差异审计（2026-10-16）

## W3

W3 `7cd64c4` 相对主集成 `6b72ff3` 的采集器、在线契约、缓存审计和测试并非单向增强。主工作区已包含天气降级（天气缺失不丢有效 HA 样本）、显式设备时区和 epoch、单站拒绝隔离与 PV 校准；W3 版本会移除这些能力。

决定：不直接 cherry-pick W3 核心文件。其重复缓存去重逻辑已在主工作区存在；完全相同的离线 drill 和健康测试可作为参考。W3 独有诊断文档另行审查后迁移。

## W2

W2 `a228f4b` 包含 dashboard 前后快照对账、E5 回放和解释性产品代码，但与主线在线安全文件重叠。后端借现有 W3 虚拟环境、显式双 PYTHONPATH 得到 98 passed + 49 subtests；前端 Vitest 为 16 passed。根环境缺少依赖，不能将此视为锁定依赖可复现。

决定：先建 W2 独立锁定依赖环境，再按提交合并；不可覆盖主线 config、HA client、reconciliation 和 runner。

## E4

E4 `f6ff5df` 的 energy-floor 约束与主线滚动求解器重叠，UTF-8 环境全套 124 passed。先提取最小补丁和定向测试，再运行主线全套测试；不直接合并脏 worktree。

## 合入顺序

1. 主工作区在线安全改动形成可回滚提交。
2. W3 仅选择不冲突的采集器/文档补丁。
3. E4 最小 energy-floor 补丁。
4. W2 E4/E5 产品与看板对账。
5. 核心、产品、前端与在线流程证据全量复验。

## 2026-10-16 收敛复核（主线 `cd1d13d`）

上述为初次审计时的计划，不应被读作当前待执行顺序。W2 已并入主线；主工作区和独立集成 worktree 均为 `cd1d13d`。W3 `7cd64c4` 与 E4 `f6ff5df` 仍保持原工作树以便溯源，但“分支 HEAD 不同”本身不代表必要功能未交付。

- W3：主线已具有重复 tick 去重、分片缓存审计、watchdog 离线演练，并额外保留天气缺失降级、epoch、显式设备时区与单站隔离。反向取 W3 的 `collect_online_history.py` 或 `audit_w3_cache.py` 会移除这些契约；因此此时不整分支合并。集成树定向测试 `test_w3_offline_tools.py`、`test_history_collector.py`、`test_online_contract.py` 与 E4 测试合计 38 passed。真正待验的是下一模拟窗口的 D1/D3 现场证据，而非把旧分支内容机械复制过来。
- E4：主线 `rolling.py` 已有 `energy_floor_kwh`，并保留已验证的 time-limit incumbent、`idle`/零流和 provenance；E4 分支的同名实现更旧。当前 `test_energy_floor.py` 定向通过。E4 工作树仍有未提交测试改动，按原工作树保留，不覆盖主线。
- 未跟踪 worker 文件：纯计算模块 `calibration.py`、`zero_flow.py` 和 34 项测试已纳入主线；D1/D2 只读分析脚本移入 `scripts/analysis/` 并保留根兼容入口，9 项测试通过。B1 硬件 runner、现场采集/重启脚本和评审稿仍未纳入在线执行入口，等待安全/归属审查。

此复核只说明代码层功能覆盖，不替代 O7 三站安全模式回显、O9 部署准备或完整在线里程碑验收；run2/run3 pending 仍原样保留。
