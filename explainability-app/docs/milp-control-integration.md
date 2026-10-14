# 有限 MILP 下发、回读与恢复接入

`run_milp_live.py` 将同次真实共享 MILP 求解的候选接到 HA 服务，再把实际服务回执与遥测回读追加到解释库。本入口按站点顺序各尝试一次，结束后恢复本入口触碰的站点为自发自用；它是三站有限验收入口，尚不是整场持续控制器。默认只生成候选，不下发 HA 服务，不启动或复位比赛。

本次用户选择“三站有限下发与回读，验证后恢复自发自用”。现场预检看到上一场已经 `ended`，尚未真正下发；不能据此称三站现场控制验收通过。此前完整模拟保留的 MILP 记录属于 shadow 候选，实际模拟成绩不能归功于这些候选。后续验收必须记录实际下发、回读及恢复结果，任一环节缺证据就保留未确认状态。

## 先确定运行范围

| 运行范围 | 实际前置条件 | 本入口的行为 |
| --- | --- | --- |
| 默认候选模式 | 可读取授权 HA、价格和天气 | 不要求启动比赛；保存同次求解证据，`writes_performed=0` |
| `--live --scope mock` | dashboard 为 `track=mock` 且 `mode=running`，完整 start/end/date_range | 绑定当前窗口，每项 HA 服务前复核场次、设备 tick、新鲜度及模式 |
| `--live --scope free-run` | dashboard 的 `mode=idle` | 有限自由运行验收，每项服务前仍要求 idle |
| 正式赛 / preparing / ended / 身份不完整 | 不满足对应 live 条件 | 阻止控制；不自动开赛、复位或转为另一运行范围 |

`allow_formal_contest` 必须为 `false`；该入口没有正式赛控制能力。`mock` 与 `free-run` 不能互相降级。现场为 `ended` 时，不能用 `free-run` 绕过检查，也不能由本入口启动新模拟赛。

live 下发同时需要 CLI `--live`、私有配置 `live_enabled=true`、`allow_ha_write=true`。只有两个配置开关而没有 `--live` 仍是候选模式；默认配置三个权限开关均为 false。配置中的 `mode` 字段不会代替 CLI 的显式写入许可。本入口按当前三个不同站点映射运行，各站初始模式必须是 `self_consumption`，避免接管已经存在的第三方控制器。

## 在共同仓库根目录启动

下面沿用本机已验证的相邻 Python 3.12 依赖环境；运行源码加载共同仓库。所有命令从 `Anker-Hackerson` 根目录开始，各终端使用同一环境和路径：

```bash
source ../explainability-app/.venv/bin/activate
export PYTHONPATH=src:explainability-app/src
```

使用已有私有 `local_online_config.json`，或仅在该文件尚不存在时以 [配置模板](../../milp_live_config.example.json) 创建它，再在本机填入授权连接值。模板仅包含占位符，不能直接连接服务。HA 可使用 token 或既有登录账户；使用登录方式时将 `ha_token` 设为 `null`，填写用户名与密码。不要把真实值加入示例、报告或 Git，也不要覆盖已有私有配置。

模板中的三个站点 suffix 要对应 HA 实体前缀，且互不重复。base URL 只填服务地址，禁止用户信息、查询参数及 fragment，不使用 `http://user:password@host/`。运行配置仍支持既有容量等字段，但本控制器实际复用共享 MILP 的模型参数；修改配置数字不能代替模型校准或单位检查。

先构建前端（依赖安装与全新环境步骤见[协作说明](../../EXPLAINABILITY_COLLABORATION.md#安装与启动)）：

```bash
cd explainability-app/frontend
npm ci
npm run build
cd ../..
```

终端一启动本地 API 与同源前端：

```bash
python -m energy_explain serve --db explainability-app/data/milp-control.sqlite --host 127.0.0.1 --port 8080
```

终端二启动反事实 worker：

```bash
python -m energy_explain.worker --db explainability-app/data/milp-control.sqlite --timeout 30
```

终端三先只读生成一次三站候选。每次使用新的输出目录，避免覆盖旧报告；需要续查控制历史时沿用同一个 journal：

```bash
python run_milp_live.py --config local_online_config.json --db explainability-app/data/milp-control.sqlite --journal explainability-app/data/milp-control-journal.sqlite --run-id milp-control-preflight-001 --output outputs/milp-control/preflight-001
```

API、runner 与 worker 必须使用**同一个解释数据库**。控制 journal 为另一个 SQLite 文件，记录发送意图和回执；不能把它当解释库传给 API。打开 `http://127.0.0.1:8080/`，选择当前 run，在三站总览进入决策详情，查看费用、输入来源、模型状态以及执行证据，再进入替代方案比较。OpenAPI 位于 `http://127.0.0.1:8080/docs`。网页刷新、追问和反事实比较均不触发 HA 下发。

只读预检通过、现场运行范围满足条件后，本次授权的有限 mock 验收使用：

```bash
python run_milp_live.py --config local_online_config.json --db explainability-app/data/milp-control.sqlite --journal explainability-app/data/milp-control-journal.sqlite --run-id milp-control-mock-001 --live --scope mock --readback-seconds 12 --solver-seconds 1 --output outputs/milp-control/mock-001
```

如果明确选择自由运行验收，使用新的 run/output，并把 scope 改为 `free-run`；仍必须先读到 simulator `mode=idle`。上面的命令不会启动比赛，也不会等待一场新比赛自动开始。`--device-timezone` 只在已确认设备时钟坐标时填写；无法由价格时间戳推断时区且没有显式坐标时停止，不把本机时区当设备时区。

## 同次求解如何变成实际命令

每站下发前重新抓取快照并求解，共享模型仍是 96 个 15 分钟步。当前 PV/load 来自实际观测，未来输入保留 `assumption` / fallback 标记；没有加载预测 pickle。候选动作、输入、模型版本与成本在首次保存时即固定，HA 回执及回读作为追加证据关联 `run_id/site_id/decision_id/instruction_id`。

| 模型动作 | HA 操作与目标语义 | 当前边界 |
| --- | --- | --- |
| charge | 先切 `third_party_control`，再设 `grid_flow=charge`，最后设置 W 目标 | 目标为电池充电功率 |
| discharge | 先切第三方控制，设 discharge，再设置 W 目标 | 目标为并网点出口功率；电池可能同时承担家庭负载 |
| 零电池功率 / hold | 同次求解显式导出 `charge=0` 候选 | 这是待校准的控制假设，需现场遥测一致才可确认；不能声称已证明等同原模型 hold |

MILP 的 kW 目标换算成 W 后必须符合 HA number 实体的单位、min/max/step。若实体只支持 1 W 而求解得到小数 W，入口拒绝下发；不会暗中舍入后把它称为原计划。后续若需要量化功率，应在模型或显式重新求解中处理，并保存相应新的证据。

每项服务前复核同一比赛绑定、设备时间实体墙钟更新时间 ≤15 秒、设备 tick 等于候选 tick，以及模式仍属于本入口的控制序列。候选观测墙钟也须在 15 秒内。期间 tick 改变、已有控制器接管、场次变化或读取失败会阻止后续服务，不能复用旧候选跨 tick 下发。

## 回执、回读和前端状态

HA 三项服务全部成功才生成 `sender_status=accepted` 的结构化回执，保存 receipt 与 submitted 事件；部分成功、超时或未知 HTTP 结果不会变成 accepted。HTTP 成功只表示服务接受请求。随后读取晚于提交的设备 tick，并检查模式、flow、设定值、四路功率、守恒及互斥。

一致遥测还要求可靠的采集 tick、设备时钟坐标、各实体墙钟更新时间，以及能将这些读数归到同次更新的 coherence 证据。缺少一致性依据时回读为 unavailable/pending；不能把一次 `/api/states` 中不同更新时间的缓存实体拼成 confirmed。实际校验细节见[连续接入文档](continuous-readonly-integration.md#外部-sender-如何提交执行证据)。

| 状态 | 可以说明什么 |
| --- | --- |
| candidate_only / not_sent | 求解已留档，还没有实际发送 |
| submitted | 所有服务收到成功回执，正在等待遥测 |
| pending / unavailable / mismatch | 回读不足、旧 tick、实体不一致或功率不匹配；不确认执行 |
| confirmed | 当前 instruction 的提交后遥测一致，证据范围为 `post_submission_telemetry_match` |
| unsettled | 没有匹配的完整最终成绩，模型费用不会自动变成结算 |

`confirmed` 不是硬件 ACK，HA 没有原样回显本地 instruction ID。恢复自发自用记录也独立于原命令回读；恢复成功不能代替原候选执行确认。前端仍分别展示求解、预测、执行和结算状态。有限 runner 不发布连续采集心跳，采集状态未知不等于没有候选或回执；执行证据以固定决策详情为准。

## journal、单实例和异常恢复

控制器先在 SQLite journal 保存 claimed 意图，再发网络请求。相同 run/site/decision 不重发；站点存在 claimed、accepted、awaiting_readback、unknown 或 partial_failure 时，新命令也被阻止。按 HA 资源地址派生的进程锁避免本机两个控制器同时接管，注册信息要求继续使用原 journal。锁为本机 Unix `fcntl`，不是跨机器分布式锁；未协调的其他进程或远端规则 sender 仍需在现场验收前停用。

正常完成、可捕获异常或 Ctrl+C 后，入口只对本进程实际尝试过服务的站点执行恢复，读取模式核对是否回到 `self_consumption`，并保存 cleanup。没有触碰的站点不复位。mock 场次改变且当前站点仍需恢复时会停止写入，以免影响新场控制器。报告中的 `cleanup.restored=false` 必须继续处理，不能仅因进程结束就宣称已恢复。

强制杀进程、断电或崩溃可能在“服务已生效、证据尚未入库”之间留下 pending。此时重启**不会自动盲重发，也没有自动恢复此前 pending 的 CLI**。保留原 journal、解释库和报告，先只读核对相关站点现状、当前场次与旧命令是否仍影响设备；在明确恢复授权内只恢复原 journal 所记录的相关站点，并把真实回读与恢复结果补记。未完成核对前不要删除 journal、换空 journal 或手动把状态改成 verified/restored 来继续发送。

解释库与 journal 为两个独立数据库，不存在跨库原子事务。服务真实回执已保存在 journal 而解释库追加失败时，以原回执核对并补写，不能重新发送来制造第二份回执。当前入口未实现整场循环、自动崩溃恢复调度或长期 lease 接管，这些应另行验收。

## 认证及数据边界

`LiveSource` 的业务读取只允许 HA `/api/states` 和模拟器 dashboard、price、today/tomorrow weather。模拟器登录只使用固定 `/api/auth/login`；HA 登录交换使用既有 auth flow。登录是认证请求，不是比赛或设备控制写入。业务 GET 禁止跟随重定向，任意控制服务、开赛/复位路径、外部 URL 或带查询的 path 均不在白名单。

串行白名单读取遇 401 时，每个来源在当前进程内最多重新认证一次，仅重试同一个 GET；再次 401 或任意 403 即停止。并发天气请求携带不可变 token 副本，遇 401/403 直接停止，不独立重试。**HA 控制服务不自动认证重试或重复发送**；写结果不明时保留 journal 待核对。token 仅在内存，关闭 source 时清除；运行输出只保留错误类别和可用 HTTP 状态，不保留错误正文、配置、认证响应或 Authorization headers。

HA 的 `received_at` 在状态读取完成时立即固定，随后等待天气完成不会刷新该时间。慢天气导致候选过期时按真实观测时间阻止控制。

私有配置、运行 SQLite、控制锁、日志和 outputs 均保留在 Git 忽略路径。API 默认只绑定本机 `127.0.0.1`；生产远端 sender 认证、跨机器控制锁、长期控制调度、真实预测校准及官方独立验签尚不属于本轮有限验收。

## 离线验证与验收记录

HTTP 与认证测试在纯离线环境运行；不调用真实 HA、模拟器或比赛接口。从 app 目录运行，避免与根目录 tests 包碰撞：

```bash
cd explainability-app
PYTHONPATH=src:../src ../../explainability-app/.venv/bin/python -m pytest -q tests/test_live_source.py tests/test_live_control.py tests/test_ha_observation.py
```

[source 测试](../tests/test_live_source.py)覆盖 401 单次刷新及失败终止、403 禁止刷新、错误与认证材料不输出、URL 用户信息及 endpoint 拒绝、token 仅内存、慢天气不改变 HA 观测墙钟。[控制测试](../tests/test_live_control.py)与[回读测试](../tests/test_ha_observation.py)验证默认不写、精度拒绝、意图幂等、部分写入、崩溃 pending 和遥测一致性边界。

现场报告需要逐站列出候选 ID、实际 instruction、服务数量与接受状态、回读结果及原因、恢复后的模式；保留输出目录与 journal。三站有一次有效下发、提交后遥测一致、恢复核对均成功后，才能称三站有限验收通过。本轮截至预检仅确定 ended 阻止写入，尚无这份现场通过证据。
