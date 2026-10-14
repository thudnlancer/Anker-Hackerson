# E5 三站演示讲稿与复现

## 演示边界

本演示把两类独立证据并列展示，不把它们拼接成一条伪造的完整策略回放：E1–E3 使用内置的三站人工决策样例；E4 使用冻结的共享 MILP 做截止约束验收；E5 使用三站家庭历史数据做离线 PV 核算。PV 数据不是这些决策样例的输入，也没有证明策略曾在这些历史时刻作出相应决策。

三站 PV 数据是家庭历史数据的离线练习回放，不是比赛现场三台设备或官方结算。天气字段是未来实测短波辐照度代理，不是归档的天气 API 预报。弃光状态为 `not_measured`。所选窗口没有负价样本，不能据此声称负电价应对能力。`dataset_local_hour` 只用于按 CSV 原始本地日期核算，时区未知，不能用于在线 deadline 或跨时区比较。PV 去向是估算，不是设备实测；不宣称已完成弃光测量。

## 启动

以下 PowerShell 命令在 `explainability-app` 目录执行。数据库仅用于演示，可从人工样例和重建的 JSONL 生成；不需要提交 SQLite。源 trace 目录为本地输入，不随 Git 交付，需按交接文档验证源哈希；没有源数据时只能回退到已有本地产物，不能宣称复现成功。

```powershell
$env:PYTHONPATH = 'src;../src'
$python = 'D:\Anker黑客松\实验环境\explainability-app\.venv\Scripts\python.exe'
& $python scripts/import_replay_demo.py --trace-root 'D:\Anker黑客松\实验环境\outputs\contest_hold_pair_zero_v2_20261013' --db artifacts/e5-real-replay/demo-verified.sqlite --output artifacts/e5-real-replay
& $python scripts/prepare_e5_demo.py --db data/e5-demo.sqlite
```

分别开两个终端，以同一个 `data/e5-demo.sqlite` 启动 API 与 worker：

```powershell
$env:PYTHONPATH = 'src;../src'
& 'D:\Anker黑客松\实验环境\explainability-app\.venv\Scripts\python.exe' -m energy_explain serve --db data/e5-demo.sqlite --host 127.0.0.1 --port 8080
```

```powershell
$env:PYTHONPATH = 'src;../src'
& 'D:\Anker黑客松\实验环境\explainability-app\.venv\Scripts\python.exe' -m energy_explain.worker --db data/e5-demo.sqlite --poll 0.5 --timeout 30
```

API 和 worker 属于解释性应用；不连接 HA，不下发设备控制。共享 MILP 五类截止约束验收单独执行：

```powershell
$env:PYTHONPATH = 'src;../src'
& 'D:\Anker黑客松\实验环境\explainability-app\.venv\Scripts\python.exe' scripts/verify_plan_floor.py --output artifacts/e4-floor-acceptance/acceptance.json
```

## 逐步讲解

| 步骤 | 输入 | 页面或 API | 讲解与预期 | 失败时回退 |
|---|---|---|---|---|
| E1 总览 | `demo-four-step-v1` 三个人工样例 | 首页 `/`，`GET /api/stations?run_id=demo-four-step-v1` | 展示低价充电、满电边界、历史过期三个不同状态；标注人工样例、候选建议、未连接控制、未结算 | 使用 `docs/examples/demo-decisions.json` 对照记录；不得称为真实设备决策 |
| E2 决策依据/反事实 | 选 `site-01` 人工决策及首步功率干预 | 决策依据、替代方案；`GET /api/decisions/{id}/explanation`、`POST /api/decisions/{id}/counterfactuals` | 查看引用证据、相同快照下的替代成本；worker 完成后展示结果和输入假设 | 回到决策详情与证据引用，说明反事实任务不可用；不编造成本结果 |
| E3 追问 | 同一人工决策与限定问题 | 决策依据内追问；`POST /api/questions` | 展示“为什么这样做”等支持问题如何引用事实；越界问题明确回答 unsupported | 展示 OpenAPI 返回的 unsupported 响应；不得把解释说成未来保证 |
| E4 截止约束 | `verify_plan_floor.py` 中的固定人工快照、96 步输入和冻结模型哈希 | 终端验收 JSON；页面计划检查可另用匹配模型决策 | 终端应出现 feasible、infeasible、timeout、unsupported、rejected 五类；这是共享优化器能力验收，不是三站 PV 历史策略重放 | 以保存的验收 JSON 展示五类结果，并明确报告回退不是本次运行；若文件不存在则跳过结果宣称 |
| E5 三站 PV 核算 | `irradiance` 臂 JSONL，站点 1/2/3，各 8 个原始本地日期 | 光伏分析页选择 `irradiance`，逐站填写 1、2、3；`GET /api/pv/{site}/daily?arm=irradiance` | 每站展示本地日期、PV 去向估算、trace 外送量/负价损失和醒目的证据边界。三站所选窗口负价计数均为 0 | 回退到 `pv-accounting.jsonl` 中的单日记录与 `import-report.json` 汇总；只能说明离线核算数据，不能声称页面实时查询成功 |

最后明确收束：演示证明 E1–E4 的产品链路和优化器约束能力可单独检查，E5 展示另一组历史 PV 核算证据；不代表比赛现场运行、现场三台设备、官方结算或完整历史决策重放。

## 集成复验命令

在 `explainability-app` 目录执行：

```powershell
$env:PYTHONPATH = 'src;../src'
& 'D:\Anker黑客松\实验环境\explainability-app\.venv\Scripts\python.exe' -m pytest -q
Set-Location frontend
npm test -- --run
npm run build
Set-Location ..
$env:EXPLAINABILITY_URL = 'http://127.0.0.1:8080'
npm install --prefix .cache/browser-qa --no-package-lock playwright@1.58.2
$env:PLAYWRIGHT_MODULE = "$PWD/.cache/browser-qa/node_modules/playwright"
$env:BROWSER_CHANNEL = 'chrome'
node scripts/browser-smoke.cjs
```

浏览器走查依赖已构建前端、上面准备好的演示数据库、API/worker 进程以及可供 Node `require` 的 Playwright。浏览器测试会走 E1–E3 样例并逐一打开三站 E5 数据；E4 的真实共享优化器验收由前一条 Python 命令执行。集成负责人需在这些命令实际通过后更新共享状态；此前 W2 的 E5 状态保持“待集成”。
