# 连续只读采集与外部执行证据接入

本入口持续读取三站 HA 状态及公开价格/天气信息，调用共同策略的 shadow 求解，把同次候选证据导入本地解释库。它不调用 HA 控制服务，不加载预测 pickle，也不生成提交凭证、执行确认或官方结算。现场观测与未来预测/持久假设分别保存来源；候选费用和反事实差额均为模型结果。

下面的启动步骤面向本机运行。执行凭证接口的 JSON 是**人工结构示例**，不表示实际下发或设备执行；现场读取不应使用 `synthetic` 标签。既有共同源码与导出契约见[协作说明](../../EXPLAINABILITY_COLLABORATION.md)，此前单次真实只读验收见[现场验收](live-readonly-acceptance.md)。

## 启动三站连续采集

从共同仓库根目录执行。使用 Python 3.12；若已有安装完整依赖的环境，可直接激活它并设置相同 `PYTHONPATH`。新环境按以下方式准备：

```bash
python3.12 -m venv explainability-app/.venv
source explainability-app/.venv/bin/activate
python -m pip install -r explainability-app/requirements.lock -r explainability-app/requirements.shared.lock
python -m pip install --no-deps -e explainability-app
export PYTHONPATH="$PWD/explainability-app/src:$PWD/src"
npm ci --prefix explainability-app/frontend
npm run build --prefix explainability-app/frontend
```

本工作区也可复用相邻独立框架的既有依赖环境：从共同仓库根目录执行 `source ../explainability-app/.venv/bin/activate`，然后设置上述共同仓库 `PYTHONPATH`。这仅复用依赖，解释包与策略源码仍加载共同仓库文件。Windows PowerShell 的路径分隔符改为 `;`，环境启动与依赖步骤见[协作说明](../../EXPLAINABILITY_COLLABORATION.md#安装与启动)。

在三个终端分别运行；每个终端均使用上述环境与 `PYTHONPATH`：

```bash
# 终端一：API 与构建后的同源前端
python -m energy_explain serve --db explainability-app/data/explainability.sqlite --host 127.0.0.1 --port 8080
```

```bash
# 终端二：独立反事实工作进程
python -m energy_explain.worker --db explainability-app/data/explainability.sqlite --timeout 30
```

```bash
# 终端三：连续只读采集；使用自己已准备的私有运行配置
python run_explainability_stream.py --config local_online_config.json --run-id readonly-run-001 --db explainability-app/data/explainability.sqlite --checkpoint outputs/explainability-stream/checkpoint.sqlite
```

前端为 `http://127.0.0.1:8080/`，接口文档为 `http://127.0.0.1:8080/docs`。无需预先 seed 人工数据。采集器直接写指定的本地解释数据库，不需要通过 HTTP 导入；API、采集器与 worker 必须指向同一个解释数据库。

`local_online_config.json` 是既有私有配置的文件名；配置与凭据不放进本报告或 Git。入口强制 `dry-run`，即便配置含 live 开关也不会下发设备控制。认证交换仅在内存保留 token，checkpoint 不保存运行配置、认证响应或请求头。

先做有限轮次时，在采集命令末尾添加 `--once` 或 `--cycles 3`，两者互斥。正常跑完有限轮次会记录 `state=stopped, reason=cycles_completed`；仅当最后一轮完整成功时退出码为 0。无这两个参数时持续运行；Ctrl+C 记录 `stopped/interrupted` 并退出，当前 CLI 退出码为 1，不能仅凭退出码判断是否发生数据损坏。

| 参数/时限 | 默认值 | 实际含义 |
| --- | --- | --- |
| `--poll` | 5 秒 | 每轮完成后等待的墙钟时长；实际两轮间隔还包含 HTTP 与求解时间 |
| `--max-age` | 15 秒 | 采集观测及设备时钟实体更新时间的新鲜度门限；求解后写证据前再检查 |
| `--solver-seconds` | 1 秒 | 每站 shadow 的求解预算；超时/不可行不导出成功候选 |
| 心跳过期 | `max(30秒, 3×poll)` | 超期后 API 展示采集状态未知；不是设备离线的直接证明 |
| worker | 总超时 30 秒 | 反事实在独立进程计算；共享提供器每次模型调用另有 10 秒求解上限 |
| 总览自动刷新 | 10 秒 | 页面可见且自动刷新开启时，在请求完成后等待 10 秒再请求 |

`observed_at` 在 HA `/api/states` 返回后立即记录，不等待天气请求完成；天气响应较慢时，旧 HA 输入仍按原采集墙钟检查新鲜度，不冒充新观测。

`--device-timezone` 仅在已知设备时间坐标时显式设置。缺时区时优先按 API 时间戳推断；坐标无法确定就阻止该站候选，不能把本机时区强加给设备。默认 `--input-kind mixed`：当前实测与未来假设混合；`synthetic` 仅用于人工测试。

## 恢复 outbox 并保持证据不变

采集器使用两个独立 SQLite 文件：解释库保存不可变决策及追加事件；checkpoint 保存待投递业务记录、原字节 SHA-256、材料指纹、各站设备 tick 和采集轮次。记录先进入 outbox，再向解释库投递。若进程中断或解释库暂时不可用，使用**同一个 `run_id`、checkpoint、解释库和站点映射**重启；旧待投递项会重新校验哈希并按原始内容幂等投递。

| 情况 | 处理与操作 |
| --- | --- |
| 同 run/site/tick 内容相同 | 保留首份原字节、hash 与采集墙钟，重复投递不增加决策 |
| 同 tick 只重新收到观测 | 采集器材料指纹忽略新的 `observed_at` 及显式 `capture:`/`snapshot:` 路径；导入仍使用首份原记录 |
| 同 tick 输入、动作、费用、模型或来源实质变化 | `evidence_conflict`；拒绝覆盖原记录，不用新的抓取时间掩盖变化 |
| outbox 原字节与保存 hash 不符 | `PayloadIntegrityError`；拒绝恢复该条，不自动修复或重新解释为有效记录 |
| 站点映射改变 | 拒绝同 run 继续，改用新 `run_id` |
| 任一新鲜设备时钟回退 | `stopped/clock_regression` 并持久阻止旧 run 继续；确认复位后使用新 `run_id` |
| 单站无效、求解失败或超时 | 隔离该站；其余有效站可以留档，整轮为 `degraded`，不伪造缺失候选 |

采集器 outbox 的材料去重规则只用于决定是否投递首份原记录；直接 `POST /api/decisions` 仍按完整业务记录幂等、冲突拒绝。同 tick 暂停时，即使采集心跳持续更新，首份决策也可能已过新鲜度门限；API 会显示其记录过期，不用新心跳刷新旧证据的时间。

新的 run 可以使用同一 checkpoint 文件，记录按 run 隔离；旧 run 的未投递项与证据保留，不自动归入新场次。设备复位、模拟比赛重开与时间坐标变化都需明确换 run。不要删除旧数据库来消除冲突。

## 检查采集是否仍在运行

每个进程生成独立 `owner_id`。采集器在认证、读取和恢复待投递记录前向解释库声明 owner，之后持续更新心跳；同 run 已有新鲜 owner 时，第二个进程被拒绝并停止，不继续读取或投递。正常停止会发布 `stopped`；异常强制结束时，心跳过期后显示 `unknown/heartbeat_expired`。重启可在旧进程已发布停止后立即接管，或等待旧心跳过期再接管；不能以第二个进程覆盖活跃 owner。

慢请求返回后及修改共享设备时钟、outbox 前再次核验 owner。已经失去所有权的旧进程会停止，不能用旧快照将新进程的 run 标记为设备时间回退。独立模块若不提供 `status_sink`，不会取得外部 owner lease；调用方必须单实例运行或提供所有权校验。正式 CLI 始终使用解释库的状态校验。

瞬时读取异常记录为 `degraded/capture_failed` 并按退避重试，最长等待 60 秒。认证失败终止，不每五秒重复错误密码尝试。状态存储拒绝 owner 或无法接收心跳时，采集器以 `status_sink_failed` 停止投递；之前 owner 的已保存状态仍是 API 的依据。

总览页面显示采集状态、三站整轮最近成功墙钟、最近错误与每条记录的新鲜度，并允许手动刷新和关闭自动刷新。切换场次不会把旧请求内容显示为新场次数据；隐藏页面暂停自动请求，重新可见时刷新。网页刷新只更新展示，不触发设备控制或重新生成决策。

```bash
# 本地只读查询当前场次
curl --fail --silent 'http://127.0.0.1:8080/api/stations?run_id=readonly-run-001'
```

`last_success_at` 表示三站完整采集轮次最近成功的墙钟时间。它不是设备 ACK，也不证明此前候选已执行。记录过期、心跳过期、执行待确认和未结算分别展示。

## 外部 sender 如何提交执行证据

只有真正负责 HA 下发的外部模块才可以提供提交回执。连续只读采集器不会生成这些回执。HA 当前状态恰好符合候选、导入了候选、或存在普通 `submitted` 事件，都不足以确认执行。

`decision_key` 是 `POST /api/decisions` 返回的服务端 `id`；不是业务 `decision_id`。两条新接口均为本地追加接口：

| 接口 | 输入与结果 |
| --- | --- |
| `POST /api/decisions/{decision_key}/submission-receipts` | `SubmissionReceipt`；完整身份、实际命令与候选相符后保存 receipt 和 submitted 事件 |
| `POST /api/decisions/{decision_key}/readbacks` | `TelemetryReadback`；服务端寻找同 instruction 的 receipt 并验证，返回 `verification` 与 `execution_status` |
| `GET /api/decisions/{decision_key}` | 包含 `execution_events`、`execution_evidence.submission_receipts/readbacks` 与各回读验证结果 |

receipt 的 `sender_status` 只接受 `accepted`。部分 HA 服务完成、非 2xx receipt、超时后接受状态未知，不能作为 accepted。`raw_receipt` 仅允许 `request_id/http_status/services_expected/services_completed`；不保存 headers、配置、认证响应或任意原始响应体。实际命令若与候选不同，应保存单独的对应决策，不能把它附在原候选上。

以下两个 JSON 是同一条**人工充电证据**的结构示例，假设隔离测试库中已有匹配身份及 `charge/battery_charge/2 kW` 候选。静态时间不适用于当前现场请求；实际 sender 使用当前墙钟、真实设备时钟，并在新鲜度门限内提交回读。

```json
{
  "run_id": "synthetic-execution-example",
  "site_id": "1",
  "decision_id": "tick-example",
  "instruction_id": "example-instruction",
  "event_id": "example-submission",
  "issued_device_time": "2031-01-01T00:00:00Z",
  "issued_wall_time": "2026-10-14T00:00:00Z",
  "completed_wall_time": "2026-10-14T00:00:01Z",
  "mode": "third_party_control",
  "flow": "charge",
  "target_kw": 2.0,
  "semantics": "battery_charge_target",
  "source_ref": "synthetic-test-sender-no-ha-write",
  "sender_status": "accepted"
}
```

```json
{
  "run_id": "synthetic-execution-example",
  "site_id": "1",
  "decision_id": "tick-example",
  "instruction_id": "example-instruction",
  "observation_id": "example-observation",
  "observed_at": "2026-10-14T00:00:03Z",
  "updated_at": "2026-10-14T00:00:02Z",
  "device_time": "2031-01-01T00:15:00Z",
  "mode": "third_party_control",
  "flow": "charge",
  "setpoint_kw": 2.0,
  "pv_kw": 0.4,
  "load_kw": 1.0,
  "charge_kw": 2.0,
  "discharge_kw": 0.0,
  "grid_import_kw": 2.6,
  "grid_export_kw": 0.0
}
```

单位统一为 kW。receipt 与 readback 的 `run_id/site_id/decision_id/instruction_id` 必须逐项一致。所有时间带时区，两个时间坐标不可互相比较：

- `issued_device_time/device_time` 是设备或加速模拟器时间；回读 tick 必须严格晚于提交 tick。
- `issued_wall_time/completed_wall_time/updated_at/observed_at` 是实际墙钟；回读更新时间必须晚于提交完成时间，捕获时间不能早于更新时间，更新时间及捕获时间均须新鲜。墙钟超过服务端未来两秒视为 `clock_unverified`。
- `updated_at` 必须代表外部观察器取得的一致遥测。不能把重新抓取时间写作实体更新时间，或拼接不同 tick 的缓存传感器。

控制语义按实际下发目标验证：

| flow | receipt semantics | 目标与观测 |
| --- | --- | --- |
| charge | `battery_charge_target` | 目标为电池充电功率；候选 action 仍使用 `control_semantics=battery_charge`，验证 `charge_kw≈target_kw` |
| discharge | `grid_export_target` | 目标为并网点输出，验证 `grid_export_kw≈target_kw`；电池放电功率可能还要供给家庭负载 |
| hold | 首版不支持 receipt | 不能把 self-consumption 或零功率猜测成已执行确认 |

放电并网目标为零仍可能需要电池放电满足负载。不能把零并网出口解释为电池不工作；若负载仍由电网进口供给，仅仅 `grid_export=0` 不满足确认条件。验证还要求：模式为第三方控制、flow 和设定值相符；电池及电网流向互斥；`PV+进口+电池放电≈负载+电池充电+出口`。默认测量容差 0.1 kW，设定值容差 0.000001 kW；放电时进口与反向充电均不得超过测量容差。

返回 `verification.status` 有 `verified/pending/mismatch/unsupported`，同时给出原因 codes。无 receipt 为 `unsupported/missing_submission`；旧 tick、旧更新及过期回读保持 pending；控制或物理不匹配为 mismatch。只有 verified 才原子追加 confirmed。重复 observation 保留首次验证结果；新遥测使用新 `observation_id`，不能重用旧 ID 刷新时间或改变内容。

如果回读早于 receipt 导入，首次结果仍保留 `unsupported`；receipt 导入后取得新的遥测并追加新的 observation，旧验证结果不重写。receipt 与 submitted、readback 与 confirmed 分别在解释库的一次事务内追加；解释库与 checkpoint 是两个独立数据库，通过 outbox 幂等补投恢复，不声称跨库事务原子性。

错误身份、候选/实际命令不符、无时区、额外字段等返回 422；重复身份内容改变返回 409。原 `POST /execution-events` 仍可记录提交和失败，但外部记录直接传 `kind=confirmed` 会返回 **422**，不能绕过结构化 receipt/readback 桥接。人工 demo 的旧行为保留，不能将其结果用于现场确认。

以前版本已存的外部 raw confirmed 保留原始记录，但没有结构化验证证据时显示 `confirmation_unverified`（回读记录待核验），不计作已确认执行。

确认的证据范围是 `post_submission_telemetry_match`，即“提交后的遥测回读一致”。HA 没有原样回显本地 `instruction_id`，因此它**不是 hardware ACK**。独立反事实也不下发命令；确认执行不等于官方收益已经结算。

## 官方 dashboard 已观察到什么

本轮通过页面 `app.js` 定位到只读 `/api/dashboard`，实际返回 HTTP 200。页面请求观察到的摘录如下；这是现场响应证据，不是本框架生成的成绩：

```json
{
  "contest": {"mode": "idle", "now": "2026-10-13T16:05:18Z", "speed": 180},
  "results": {
    "count": 0,
    "official_count": 0,
    "mock_count": 0,
    "runs": [],
    "verify": {
      "verified": true,
      "signing_enabled": true,
      "key_fingerprint": "601e6a530494658c"
    }
  }
}
```

当前无最终成绩，不导入参考零值行、不把当前 score 当完整窗口的最终结算。官方服务器页面同时显示“不可达”；这是观察到的页面状态，尚未定位根因，不推定为账号、区域或客户端问题。

`results.verify` 是现场服务返回的验证信息。本框架尚未实现独立公钥验签，不能把该字段描述为“我们已验签”。待实际出现最终记录后，需按官方来源、contest/epoch、mock 或 official 类型、完整七天设备窗口、三站范围和是否计入排名绑定到本地 run。保留 `saving_settled_percent` 的官方口径；缺少基准电费 K 时不能从百分比臆算 EUR。官方结算接入尚待最终 payload 与来源验证，未取得结果继续显示未结算。

## 本轮持续采集实测

场次 `live-stream-20261013T160959Z` 使用真实 HA 三站输入及共同源码的 96 步 MILP，执行一次有限轮采集后以同 run、同 checkpoint 重启，继续采集三轮。采集墙钟区间为 `2026-10-13T16:10:03.607624Z` 至 `16:10:20.290927Z`，对应香港时间 2026-10-14 00:10；采用默认 `solver=1秒/poll=5秒/max-age=15秒`。

| 实测项 | 结果 |
| --- | --- |
| 不可变决策 | 共 10 条；站 1、2 各 3 个 tick，站 3 为 4 个 tick |
| 站点时序 | 三站独立且不同步，未把它们拼成同一设备 tick |
| 执行与结算 | 全部 `candidate_only`、未结算，`writes_performed=0` |
| 模型与未来输入 | 使用共同 96 步模型；未来 PV/load 仍为 `assumption`，没有加载预测 pickle |
| 丢失投递确认模拟 | 第一次 Store 成功入库后故意抛 `RuntimeError`；重启前 outbox pending=1 |
| 同 checkpoint 恢复 | 重启后 pending=0；幂等恢复未增加重复决策 |
| 最近完整成功轮次 | 重启后三轮三站均成功，`last_success_at=2026-10-13T16:10:20.587219Z` |

丢失确认异常是本地恢复验收的故意注入，不是现场故障归因。该实测证明读取、同次求解、不可变投递与重启恢复链路；没有验证 HA 下发、真实预测准确率或官方比赛收益。运行证据保存在 Git 忽略的 `outputs/explainability-stream/live-stream-20261013T160959Z/` 与本地解释数据库中，报告不收录原始私有采集资料。

浏览器联调再次恢复同一场次采集四轮，累计 **22 条决策**，pending 仍为 0；最近完整成功为 `2026-10-13T16:13:40.452192Z`。页面暂停后手动刷新、再开启 10 秒自动刷新，均读到真实新记录及 `running/fresh` 状态；采集停止后总览显示停止及记录过期。固定决策详情进入反事实比较后完成 96 步＋96 步计划，费用分项对账，首步不充的差额在 `1e-9 EUR` 精度内为零。截图和浏览器检查保存在本地忽略的 `explainability-app/artifacts/stream/`；全程未写入 HA。

## 信任边界与后续接入

当前 API 默认绑定 `127.0.0.1`，面向本机可信进程，没有生产 sender 身份认证。`source_ref`、`sender_status=accepted` 和调用者填写的 `source_kind=official` 都是业务声明，不是认证证明。不要直接将本地接口暴露到公共网络并把所有来请求者当可信 sender。

生产接入需要补 sender 认证、站点/场次权限、凭证来源校验、重放保护及传输保护；执行模块还需提供实际 HA 服务完成凭证和一致遥测，再做真实下发验收。最终成绩应使用确认的只读官方来源并匹配比赛窗口，独立公钥校验完成前只保留服务器声明。连续读取与解释 UI 可先使用，真实控制闭环及官方成绩仍是独立验收项。

## 核查依据

本地策略回归 **140 项通过**，解释服务 **173 项通过，另有 49 个 subtests 通过**，前端 **23 项通过，TypeScript 与 Vite 构建通过**。覆盖慢天气观测时钟、旧 owner 恢复、同 tick 冲突、队列哈希、双时钟与物理回读校验、旧外部确认兼容，以及崩溃重启后最近成功/错误时间不回退。三页浏览器流程及服务重启恢复已通过；远端结果以协作 PR 的实际 Checks 为准。

| 证据 | 结论 | 可复核路径 |
| --- | --- | --- |
| [连续 CLI](../../run_explainability_stream.py)、[stream](../../src/energy_lab/explainability_stream.py) | 只读、默认时限、owner 声明、原字节恢复与同 tick 冲突拒绝 | `python run_explainability_stream.py --help`；[离线 stream 测试](../../tests/test_explainability_stream.py) |
| [执行纯适配器](../src/energy_explain/execution.py)、[Store](../src/energy_explain/store.py)、[API](../src/energy_explain/api.py) | 双时钟及物理校验通过后才能确认，外部 raw confirmed 拒绝 | [纯适配器测试](../tests/test_execution_bridge.py)、[接口测试](../tests/test_stream_api.py) |
| [总览页面](../frontend/src/App.tsx) | 心跳与记录状态分别显示，页面可见时自动刷新 | [前端测试](../frontend/src/App.test.tsx) |
| 本轮浏览器 `/api/dashboard` HTTP 200 摘录 | idle、没有最终记录；服务器验签字段不等于本框架验签 | 需要自己的授权只读现场环境；报告保留必要字段，不包含认证材料 |

离线回归从各自目录执行，避免两个 `tests` 包名称碰撞：

```bash
# 已激活同一依赖环境，从共同仓库根目录执行
PYTHONPATH=src python -m unittest discover -s tests -v
cd explainability-app
PYTHONPATH=src:../src python -m pytest -q
cd frontend
npm test
npm run build
```

运行数据库、checkpoint、私有配置、原始采集资料与日志位于 Git 忽略路径，不作为报告正文或源代码提交。本文件记录实现逻辑、操作及本轮已观察到的结果；页面和回归验收另记录各自实际检查项，不由只读采集成功推定。
