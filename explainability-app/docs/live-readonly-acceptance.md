# 真实环境只读接入验收

2026-10-13 已完成一轮真实环境采集：从 HA 获取三站观测，从模拟器获取当日/次日电价和天气，使用共同 MILP 在本地生成 shadow 候选，然后导入解释服务并完成反事实和三页展示。没有把未知的线上控制动作当作本地 MILP 决策；本次记录是使用现场输入生成的本地只读候选。

## 采集事实

- HA：`http://52.41.23.84:8123/`，已连接，发现 89 个实体；三站后缀为 `1502 / 1503 / 1504`，与 SOP 的三站映射一致。
- 模拟器：`http://52.41.23.84:45678/`。观察时处于自由运行模式，页面无已完成的完整比赛成绩；本轮未启动、停止或复位比赛。
- 采集墙钟：**2026-10-13 20:53:42，Asia/Hong_Kong**（保存值 `2026-10-13T12:53:42.048781Z`）。设备时钟是加速的比赛时钟，不能与采集墙钟直接比较。
- 场次 ID：`live-readonly-20261013T125339Z`。三站 SOC 都为 100%；当前光伏/负载均来自 HA，而非模拟器设备状态接口或人工填数。
- 电价 API 覆盖 `2028-03-12 / 2028-03-13`，两天各 24 点；三站天气两天各 24 点。模型按设备 tick 对齐为 96 步 × 0.25 h。

| 站点 / 后缀 | 设备时间（UTC） | 当前光伏 (kW) | 当前负载 (kW) | 模型首步电池放电 (kW) | 候选并网输出目标 (kW) |
| --- | --- | ---: | ---: | ---: | ---: |
| 1 / 1502 | 2028-03-12 23:00 | 0 | 0.185 | 0.185 | 约 0 |
| 2 / 1503 | 2028-03-12 23:00 | 0 | 0.049 | 0.049 | 0 |
| 3 / 1504 | 2028-03-12 23:15 | 0 | 1.043 | 1.043 | 0 |

该例直接验证了 SOP 控制语义：**并网输出目标为 0，不等于电池不放电**。当前负载可由电池供应，家庭与电网净交换为 0。因此总览新增中文功率含义，详情保留首步四项流量证据。

未来光伏转换尚无现场校准，未来负载也未启用训练模型。本次明确使用当前功率持久假设，记录为 `mixed`：当前 PV/load 为 observed、未来序列为 assumption，预测状态为 fallback。真实天气已读取，但不冒充校准后的功率预测；本轮未加载已有预测 pickle。

## 解释与重放结果

三站初始求解均为 optimal，在原默认 1 秒求解预算下成功导出；没有调整真实输入来换取成功。记录包含完整输入、有效参数、源码哈希、首步四项功率及费用分项，均通过 Pydantic 契约。首次导入 3 条，随后 API 重复导入全部幂等。

| 站点 | 基准模型目标 (EUR) | 首步充电为 0 的替代目标 (EUR) | 比较结果 |
| --- | ---: | ---: | --- |
| 1 | -3.177585513 | -3.177585513 | 计算精度内相同 |
| 2 | -3.943398685 | -3.943398685 | 相同 |
| 3 | 1.701996668 | 1.701996668 | 相同 |

工作进程使用共同仓库源码重放，三站每一费用分项与原候选对账到 `1e-6`，两侧计划各 96 步。原候选本已首步不充电，因此 0 kW 干预没有带来可分辨的费用改变。

站点 1 的原始差额为 `-1.7763568394002505e-15 EUR`，属于浮点计算精度。本次只修正页面展示：绝对值 ≤ `1e-9 EUR` 时显示约零及费用相同，原 API/任务结果保留原值；正常负差额仍显示费用更低。满电时强制首步电池充电 1 kW，独立任务返回 infeasible，页面展示“约束不可行”，未混同为超时。

费用包含购电、售电、循环代理成本和期末库存估值。负模型目标不代表实际盈利，所有数值依赖本次持久假设，不能替代官方成绩。

## 验证与修正

| 验证 | 结果 |
| --- | --- |
| 策略与只读认证 unittest | 117 项通过；新增 8 项纯 mock 认证/采集测试 |
| 解释服务 pytest | 83 项及 49 个子测试通过 |
| 前端 | 14 项测试通过，TypeScript/Vite 生产构建通过 |
| 真实记录 API 验收 | 三站导入幂等、证据引用、96 步费用重放、满电不可行、有限追问通过 |
| 真实记录浏览器 | 总览 → 详情 → 比较通过；来源为外部记录，功率语义明确，观测与假设分开；不可行页面通过 |

修复了 `is_placeholder(None)` 原来返回 False 导致的 `Bearer None`：缺省/null token 现在正确进入账号密码认证，缺省必填字段在请求前拒绝。新增 `fetch_snapshot_from_config(cfg)`，允许从规范化内存配置采集；原文件入口兼容并委托同一逻辑。端点白名单和 `write_enabled=False` 保留，测试证明即使 caller 给出 live flags 也不调用控制端点。

## 复现与证据

现场凭据通过已提供 SOP 在内存使用；未写入运行配置、导出、测试或 Git。采集只涉及认证与允许的读取端点，HA 控制服务、比赛按钮、云部署和官方结算导入均未调用。

标准文件入口继续可用，使用自己的私有运行配置：

```bash
# 从共同仓库根目录执行，依赖安装见协作说明
export PYTHONPATH="$PWD/explainability-app/src:$PWD/src"
python run_shadow_dry_run.py --config local_online_config.json --model-directory outputs/live-run-001/no-models --output outputs/live-run-001 --explainability-run-id live-run-001 --explainability-input-kind mixed
python -m energy_explain import-records --file outputs/live-run-001/explainability-decisions.jsonl --db explainability-app/data/live-readonly.sqlite
python -m energy_explain serve --db explainability-app/data/live-readonly.sqlite --port 8080
```

另一终端启动相同数据库的 worker。验证脚本只访问本机解释 API，不访问 HA：

```bash
python -m energy_explain.worker --db explainability-app/data/live-readonly.sqlite --timeout 30
# 在 explainability-app/ 目录，另一个终端运行
LIVE_RECORDS=../outputs/live-run-001/explainability-decisions.jsonl node scripts/live-record-smoke.cjs
```

本轮本地证据位于以下 Git 忽略目录：

- `outputs/live-explainability/attempt-01/`：真实业务快照、3 条决策 JSONL、采集结果与单位/来源校验。
- `explainability-app/artifacts/live-readonly/`：API 验收 JSON、浏览器验收 JSON、总览/详情/比较/不可行截图；截图已视觉检查。
- `explainability-app/data/live-readonly.sqlite`：保存的不可变记录和比较任务。

这是一轮采集与接入验收，并未部署持续采集服务。设备时钟仍在运行，页面证据对应保存的采集时刻。执行保持候选，结算保持未结算；HA 控制闭环、未来预测校准及官方结算仍需对应阶段验收。
