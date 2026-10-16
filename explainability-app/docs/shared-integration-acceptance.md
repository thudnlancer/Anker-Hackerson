# 共同策略仓库接入验收

验收日期：2026-10-13。共同仓库基线：`8ac78e5dafadebb9909f3b3760760df74dc2e844`。本轮接入在本地 `feat/explainability-integration` 分支完成；本文件记录本地实际执行结果，GitHub CI 尚未运行。

安装与接入命令见 [协作说明](../../docs/integration/EXPLAINABILITY_COLLABORATION.md)。使用 Python 3.12.14、Node 24.18.0、npm 11.16.0；运行与测试依赖记录在 `requirements.lock`、`requirements.shared.lock` 和 `frontend/package-lock.json`。本轮复用已有 Python 环境提供依赖，但显式设置共同仓库的 `PYTHONPATH`；测试验证了实际加载的解释包和策略源码均来自共同仓库。

## 实际结果

| 检查 | 本轮执行结果 |
| --- | --- |
| 原策略及桥接 unittest | 109 项通过，包含 8 项新增桥接测试 |
| 解释 pytest | 83 项与 49 个子测试通过，包含 8 项新增共享提供器测试 |
| 前端 | 12 项测试通过；TypeScript 检查及 Vite 生产构建通过 |
| Python 依赖 | `pip check` 通过 |
| 前端依赖审计 | Vitest 4.1.11、Vite 7.3.7，完整 `npm audit` 为 0 |
| 源码与单位 | 共同策略哈希与记录匹配；实际优化输入 96 点完整保存，W → kW 转换、0.25 h、效率、均价终端及 0.02 EUR/kWh 损耗对账通过 |
| 隔离及来源 | 模型版本/哈希/控制契约不符时不支持；共同源码缺失不会转用历史副本；敏感额外键不进入导出 |
| 行为保持 | 无 hook 的返回保持原样；坏站不导出；回调异常保留候选，仅报告异常类型 |
| 离线 CLI | 三站同次记录导出成功；首次导入 `created=3`，重复导入 `created=0` |
| 浏览器 | 总览、详情、比较完整流程 5 项检查通过；三个 96 步反事实完成，无脚本异常 |
| 重启 | API 和 worker 关闭后使用原数据库重启；已排队的 2 kW 干预从 queued 恢复到 ready，首步为 2 kW；解释模板版本为 v2 |

Python 测试出现既有 Starlette/httpx 测试客户端弃用警告；它不影响本轮检查通过。测试套件从两个各自目录执行，避免同名 `tests` 包碰撞。

```bash
# 共同仓库根目录，已激活依赖环境
PYTHONPATH=src python -m unittest discover -s tests -v
cd explainability-app
PYTHONPATH=src:../src python -m pytest -q
cd frontend
npm test
npm run build
npm audit
```

Windows 将 Python 路径分隔符改为 `;`，并在对应终端设置 `$env:PYTHONPATH`。

## 端到端模型结果

通过 `scripts/create-shadow-fixture.py` 创建人工快照，随后调用真正的共同 shadow、共同 `rolling.py`、证据导出、CLI 导入和独立工作进程。页面三站均注明“模型接入验证 · 人工输入”；执行保持候选、没有回读与结算。

| 人工站点 | 基准模型目标 (EUR) | 首步充电为 0 的替代目标 (EUR) | 替代 − 基准 (EUR) |
| --- | ---: | ---: | ---: |
| 1 | -3.203289474 | -3.165789474 | +0.037500000 |
| 2 | -3.731184211 | -3.731184211 | 0 |
| 3 | -3.937500000 | -3.937500000 | 0 |

负目标包含期末库存估值，不等于实际盈利。重放固定保存的首步四项功率，费用各分项与同次原候选对账到 `1e-6`；替代只固定首步充电，后续重新优化。两侧计划各 96 步。

可选浏览器工具依赖 Playwright 1.62.1 和 Chrome。在解释目录运行：

```bash
npm install --prefix .cache/browser-qa --no-save --package-lock=false playwright@1.62.1
BROWSER_CHANNEL=chrome PLAYWRIGHT_MODULE="$PWD/.cache/browser-qa/node_modules/playwright" node scripts/shared-strategy-smoke.cjs
```

运行前须按协作说明导入人工 JSONL 并启动 API 和 worker。可用 `SHADOW_RECORDS` 指定其他三站**人工**导出文件；脚本拒绝将真实输入作为该人工验收。

本地证据均位于 Git 忽略目录：

- `outputs/explainability-smoke-v2/`：人工原始快照、shadow 结果、完整业务 JSONL。
- `explainability-app/artifacts/shared-browser/`：三页截图、`result.json`、重启前排队记录和 `restart-result.json`。

浏览器截图已人工视觉检查；96 点价格图使用最多 8 个刻度，折线仍保留全部输入。没有将运行数据库或截图纳入提交。

## 遇到的限制与未证明项

第一次较复杂人工窗口在默认 1 秒 shadow 求解预算下有一站 `limit_reached`，未为它导出成功决策；该预算没有被接入代码修改。最终三站输入简化了净负载，用来确认接口链路，不代表所有真实 96 步窗口都能在 1 秒内求解。反事实仍有独立 worker 30 秒总超时，每次模型调用的求解上限为 10 秒，超时与不可行分别展示。

首次 runner 在检查仓库已有预测 pickle 时因缺少原训练模块而 blocked；已有成功求解的证据仍写入。最终人工流程显式指定空预测模型目录，未来功率以持久假设并标记 fallback。本轮未修改预测模型，也没有确认其现场加载能力。

导入按条目幂等、身份冲突拒绝，整批不是事务原子导入。真实读取和设备操作没有执行；真实预测准确率、硬件控制、下发后回读、AWS 部署、官方结算及比赛收益均未验收。历史四步算例与此前独立框架验收保留在对应文档，不用本轮人工结果替代现场证据。
