# W1 单站真实策略回测记录：只读接入

本接入读取 W1 `outputs/w1_a5_zero_flow_smoke_20261014_1/site_1_trace.csv`，期望 SHA-256 `c0a7ff9479f2f8faffc6791cd4c0670f38107beba22599fcfd2c4a5479e99fbf`。数据为 1 站 × 168 个原始本地小时 × `self_consumption`、`persistence`、`irradiance` 三臂，共 504 行。只读解析源 CSV，既不复制到决策表，也不创建执行事件/结算记录；文件哈希不符时服务拒绝配置。

## 映射口径

| trace 原字段 | 解释视图 | 边界 |
|---|---|---|
| `time` | `local_hour`，`time_basis=dataset_local_hour` | 原始文本，不解析成 UTC/设备时刻；不能用于在线 deadline 或跨时区比较 |
| `pv_w`、`load_w`、`power_w` | `pv_kw`、`load_kw`、`power_kw` | W → kW；`power_kw` 是离线物理流，不是控制目标 |
| `planned_flow`、`planned_power_w` | `planned_flow`、`planned_power_kw` | 计划动作 |
| `command_mode`、`command_flow`、`command_target_w` | 同名语义、`command_target_kw` | 回测指令表达，不是现场已执行指令 |
| `flow`、`soc_before`、`soc_after` | 同名 | 离线物理回放结果，不是 HA 回读 |
| `buy`、`sell`、`cost_eur` | 同名 | 回测输入价与逐小时模型成本，不是官方结算 |
| `solver_status`、`fallback`、`optimizer_attributed`、`attribution` | 同名 | 原样保留归因，不推断未知策略内部原因 |

`GET /api/offline-replay` 返回一站概览；`GET /api/offline-replay/steps/{0..167}?arm=irradiance` 返回逐小时字段与证据引用。页面为 `/#/replay`，不混入三站在线/人工决策列表。反事实、计划截止检查在此类记录上禁用；数据不含构造这两项能力所需的完整逐步预测/约束快照。

这些是家庭历史数据的离线练习回放，不是现场比赛设备。天气为未来实测短波代理，不是归档 API 预报；`zero_flow` 是离线语义，不是硬件验证。所选窗口没有负价样本，不能声称验证了负电价应对。PV 去向和弃光若另见 E5 页面，也必须遵循估算、`not_measured` 边界。

## 应用独立环境复现（PowerShell）

从 `D:\Anker黑客松\w2-worktree\explainability-app` 运行，Python 与 Node 依赖安装在应用自身目录，不复用主仓虚拟环境。`--trace` 源文件须由 W1 提供且通过上述哈希检查；源 trace 不纳入此代码提交。

```powershell
python -m venv .venv
& .\.venv\Scripts\python.exe -m pip install -r requirements.lock
& .\.venv\Scripts\python.exe -m pip install --no-deps -e .
$env:PYTHONPATH = 'src;../src'
& .\.venv\Scripts\python.exe -m pytest -q tests/test_offline_replay.py tests/test_api.py
Set-Location frontend
npm ci
npm test -- --run
npm run build
Set-Location ..
$env:ENERGY_EXPLAIN_OFFLINE_TRACE = 'D:\Anker黑客松\实验环境\outputs\w1_a5_zero_flow_smoke_20261014_1\site_1_trace.csv'
$env:ENERGY_EXPLAIN_OFFLINE_TRACE_SHA256 = 'c0a7ff9479f2f8faffc6791cd4c0670f38107beba22599fcfd2c4a5479e99fbf'
& .\.venv\Scripts\python.exe -m energy_explain serve --db data/w1-readonly-demo.sqlite --host 127.0.0.1 --port 8080
```

另开终端，从同一应用目录运行浏览器 smoke；仅 GET 请求，不需要 worker，也不触发 HA 写入：

```powershell
npm install --prefix .cache/browser-qa --no-package-lock playwright@1.58.2
$env:PLAYWRIGHT_MODULE = "$PWD/.cache/browser-qa/node_modules/playwright"
$env:EXPLAINABILITY_URL = 'http://127.0.0.1:8080'
node scripts/offline-replay-smoke.cjs
```

本机复验使用 Python 3.13.5（未安装 3.12）；依赖仍严格来自 `requirements.lock`。有 Python 3.12 的集成环境应优先使用 3.12 复跑。

注意：应用全套测试中的 `test_shared_provider.py::test_offline_runner_sidecar_import_cli_is_repeatable_and_replayable` 会跨出应用目录运行仓库根部的 `run_shadow_dry_run.py`，还需根项目依赖 `joblib`。它不属于上述应用自身的锁定依赖。独立 `.venv` 下全套测试因缺少 `joblib` 为 96 passed、1 failed（环境依赖失败）；有根项目依赖的实验环境全套回归另行记录。不要把该失败解释为 W1 映射失败或把跨仓额外依赖悄悄加入应用锁文件。

手工演示：打开 `/#/replay`，确认“真实策略回测记录”标题和 `dataset_local_hour` 边界；先展示 `irradiance` 第 0 小时计划/指令/物理流，切到下一小时，再切 `persistence` 臂。展示源 SHA、证据引用、无官方结算。若源 CSV 不可用或哈希不符，直接说明无法复现，不用人工样例冒充真实 W1 trace。
