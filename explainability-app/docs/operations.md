# 启动、恢复和复现

运行要求 Python 3.12、Node.js 22.12 或以上以及 npm。以下命令从仓库根目录执行；生产构建的网页和 API 使用同一地址。

## 安装锁定的依赖

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.lock
python -m pip install --no-deps -e .
cd frontend
npm ci
npm run build
cd ..
```

后端通过 `requirements.lock` 固定本轮运行和测试依赖；前端通过 `package-lock.json` 固定依赖。首次安装需要访问对应包仓库。

## 启动人工演示

服务启动不会自动导入演示。先显式导入，重复执行不会覆盖已有记录：

```bash
source .venv/bin/activate
python -m energy_explain seed --db data/energy.sqlite
python -m energy_explain serve --db data/energy.sqlite --host 127.0.0.1 --port 8080
```

另开终端，在仓库根目录启动独立工作进程：

```bash
source .venv/bin/activate
python -m energy_explain.worker --db data/energy.sqlite --poll 0.5 --timeout 30
```

访问 `http://127.0.0.1:8080/` 查看前端，`http://127.0.0.1:8080/docs` 查看 OpenAPI。可用以下命令检查 API：

```bash
curl --fail-with-body -sS http://127.0.0.1:8080/api/health
```

三站分别覆盖低价充电、满电边界、历史过期，运行身份为 `demo-four-step-v1`，站点身份为 `site-01`、`site-02`、`site-03`。演示执行时间线也是人工示例，未连接实际设备；没有生成结算结果。

## 开发前端

API 与工作进程继续使用前述命令运行。第三个终端执行：

```bash
cd frontend
npm run dev
```

Vite 默认在 `http://127.0.0.1:5173/` 提供网页，把 `/api` 代理到本机 8080 端口。生产构建后，后端直接提供 `frontend/dist` 的文件。API 可独立运行；未构建前端时首页返回 503 并提示构建命令。

## 重启与任务恢复

使用相同 `--db` 路径重启服务和工作进程，即可保留决策、追加事件和任务。默认一次只运行一个反事实任务，重复启动工作进程仍通过数据库租约约束全局并发。

中断工作进程后，运行中任务在租约到期后重新领取；默认 30 秒计算上限对应 40 秒租约。连续两次进程失联的任务标记失败。超时、技术失败或缺少模型的同一干预可以重新提交，以便修复环境或注册真实模型后重试；已完成或不可行的相同任务复用已有结果。关闭 API 不会停止单独运行的工作进程，两个终端分别按 Ctrl+C 结束。

SQLite 路径由 `--db` 指定；应用工厂还支持 `ENERGY_EXPLAIN_DB`。工作进程须使用相同数据库路径。独立部署或接入不同数据时，使用另一数据库并省略 seed 命令。

## 加载真实反事实模型

设置 `ENERGY_EXPLAIN_PROVIDER_MODULE` 指向可导入 Python 模块。该模块暴露 `register(register_provider)`，注册模型版本与实现 `supports(decision)`、`compare(decision, first_charge_kw)` 的提供器。

API 和工作进程均需要设置相同环境变量、安装同一模型代码；模块加载错误不能被人工模型替代。模型接口和结果口径见 [接入契约](integration.md)。

## 验证代码

```bash
source .venv/bin/activate
python -m pytest -q
cd frontend
npm test
npm run build
```

浏览器验收基于生产构建的同源服务，检查三页和任务状态，结果见 [验收记录](acceptance.md)。

浏览器脚本是可选验收工具，要求另外安装 Playwright 1.62.1 和 Chrome，不属于应用运行依赖。可把 QA 工具安装到忽略目录：

```bash
npm install --prefix .cache/browser-qa --no-save --package-lock=false playwright@1.62.1
BROWSER_CHANNEL=chrome \
  PLAYWRIGHT_MODULE="$PWD/.cache/browser-qa/node_modules/playwright" \
  node scripts/browser-smoke.cjs
```

执行时 API、工作进程与人工演示数据库应按前述命令运行。脚本通过默认本机 8080 端口检查，使用 `EXPLAINABILITY_URL` 可指定另一个服务地址。结果与截图写入 `artifacts/browser`。

## 文件与数据

`data/` 数据库、`.env`、缓存、前端构建、运行日志、截图和原始 JSONL 均被 Git 忽略。备份 SQLite 时先停止 API 和工作进程或使用 SQLite 在线备份接口，避免只复制主文件而遗漏 WAL 中的数据。
