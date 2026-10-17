# SOLIX v13 前端接入

以团队提供的 `solix-v13.html` 为视觉参考，将页面接入当前 React/Vite 前端和同源解释 API。原件保存在 [design/solix-v13.html](design/solix-v13.html)，仅供设计比对；页面不执行原件脚本。运行入口为 `frontend/src/SolixApp.tsx`，装饰电池与作用域样式来自原件，旧证据工作台样式不受新页面影响。

## 页面与接口

| 页面 | 接口 | 展示与边界 |
|---|---|---|
| HOME、总览 | `GET /api/stations?run_id=...`、`GET /api/decisions/{id}` | 场次与站点使用服务返回的身份；每 10 秒刷新，失败保留快照并提示；显示采集状态、新鲜度和来源 |
| 调度时间轴 | 决策的 `snapshot` | 使用完整设备时间与实际 `step_hours`，分别展示购电价、外送价、光伏、负载；独立纵轴，支持查看时刻及时间窗；不伪造完整 dispatch/SOC 轨迹 |
| 决策、追问 | `GET /api/decisions/{id}/explanation`、`POST /api/questions` | 只使用该记录的解释与证据引用；有限意图问答，不声称调用通用 LLM |
| 替代方案 | `POST /api/decisions/{id}/counterfactuals`、`GET /api/counterfactuals/{id}` | 能力不可用时禁用提交，轮询独立 worker；费用差保留负号 |
| 储能计划 | `POST /api/plans/feasibility`、`GET /api/plans/{id}` | `decision_id` 使用服务记录 ID；截止时间由设备快照步长生成，明确时区；展示可行、不可行、能力缺失、超时和失败 |
| 光伏日报 | `GET /api/pv/{site_id}/daily?arm=...` | 站点身份原样使用，不猜测映射；选择回放方案和原始日期，按实际日报绘制去向；空数据不生成占位比例 |
| 能源费用 | 决策 `cost`、同场次 `settlement` | 使用 EUR，不做未经授权的汇率换算；模型目标与同站点结算分别展示，旧结算响应不覆盖更新记录 |
| 生活计划、家庭偏好 | `localStorage` | 按站点隔离，本地生活计划仅做时间/功率检查；存储失败明确提示，禁止把保存偏好描述为修改设备或调度器 |

`/#/evidence`、`/#/decisions/{id}`、`/#/comparisons/{id}` 继续由原证据工作台处理。旧家庭路由 `today/plans/solar/costs/settings` 分别映射到新页面。无记录时显示空状态，不默认播种人工数据。

AI 与储能计划工作区固定使用进入时的不可变决策，避免后台连续采集在填写或计算期间替换场景；同站点新记录到达后提供显式切换按钮。切换场次或站点会取消旧界面请求并创建新工作区，旧详情和解释不会落到新站点。

## 协议口径

- 功率 kW、能量 kWh、SOC %、价格 EUR/kWh、费用 EUR。
- 设备时间展示接口原时区；预测/截止时间转换为明确标记的 UTC 瞬间，不按浏览器墙钟安排设备动作，不固定假设 96 步或 24 小时。
- `grid_export_target` 是并网点输出目标；`self_consumption` 是模式建议。两者均不转换成电池实测功率。当前快照没有电网实测时保持缺失。
- 完整预测数组必须同长且数值有效，否则不绘制。负外送价原样显示，不解释为购电补贴。
- 日报是历史回放估算，展示后端 `source_kind`、`curtailment_status` 与 `caveat`。不把估算去向写成实测弃光。
- 建议、提交、回读和结算分别核对。所有浏览器 POST 仅用于解释、反事实或模型计划任务，未增加 HA 控制、执行事件或结算写接口。

## 验证与本地启动

前端沿用现有锁文件和构建流程：

```powershell
Set-Location explainability-app/frontend
npm ci
npm test
npm run build
```

本次本机 Node 为 26.4.0，执行测试时需关闭 Node 自带的实验性存储，使用 jsdom 浏览器存储：

```powershell
$env:NODE_OPTIONS = '--no-experimental-webstorage'
npm test
```

前端全套 **84 项测试通过**，生产构建通过。新增回归覆盖站点身份隔离、延迟响应、负反事实差额、设备时间截止约束、异步任务、自动刷新固定场景、PV 空状态与本地偏好。生产页面另用真实 API 和独立 worker 的人工演示库验收；不访问比赛设备。浏览器脚本为 `scripts/solix-smoke.cjs`，检查桌面 1440×1050、手机 390×844、原证据深链和所有页面手机横向溢出。浏览器走查通过，无页面运行异常；截图与报告输出至忽略目录 `artifacts/solix-v13/`。

启动服务与工作进程按 [operations.md](operations.md) 安装应用环境，并使用独立测试数据库。以下命令分别在应用目录的终端执行：

```powershell
$env:PYTHONPATH = 'src;..\src'
python -m energy_explain seed --db .cache/solix-v13-qa.sqlite
python -m energy_explain serve --db .cache/solix-v13-qa.sqlite --port 8093
```

```powershell
$env:PYTHONPATH = 'src;..\src'
python -m energy_explain.worker --db .cache/solix-v13-qa.sqlite --poll 0.2 --timeout 15
```

```powershell
npm install --prefix .cache/browser-qa --no-save --package-lock=false playwright@1.58.2
node scripts/solix-smoke.cjs
```

脚本默认使用已安装的 Chrome；可设置 `BROWSER_CHANNEL`、`EXPLAINABILITY_URL` 和 `PLAYWRIGHT_MODULE`。人工四步提供器只验证反事实链路；E4 任务应返回 `unsupported`，不把它当作真实共享 MILP 的 E4 验收。

本机 Python 锁文件安装因包源 SSL/连接失败未完成，浏览器 API 验收使用现有 Python 3.12 环境（FastAPI 0.141.1、Pydantic 2.13.5、SciPy 1.18.1）。本次不声称完成锁定环境下的后端全套回归；后端源码和 Python 依赖锁均未修改。
