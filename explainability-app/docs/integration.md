# 接入真实策略与执行记录

解释服务可以在没有 Windows 策略源码的情况下独立启动。真实策略通过 HTTP 导入保存的决策，控制模块追加提交和回读事件，结算模块导入结果。只有反事实重新求解依赖对应版本的策略模型。

当前已把用户日志中的历史 MILP 原代码注册为独立提供器，并使用完整人工输入验证接入链路。版本锁定、新增字段和真实运行记录缺口见 [恢复模型接入说明](recovered-model-integration.md)。

## 接口一览

服务启动后，`/docs` 提供交互式 API 文档，`/openapi.json` 提供字段、枚举、必填项和错误响应的机器可读定义。

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | `/api/stations?run_id=...` | 按场次查看各站当前决策 |
| POST / GET | `/api/decisions` | 导入或查询决策 |
| GET | `/api/decisions/{id}` | 查看不可变记录和追加记录 |
| GET | `/api/decisions/{id}/explanation` | 查看有证据引用的原因卡 |
| POST | `/api/decisions/{id}/execution-events` | 追加指令提交、回读确认或失败事件 |
| POST / GET | `/api/runs/{run_id}/settlements` | 导入或查询该场次结算 |
| POST | `/api/decisions/{id}/counterfactuals` | 提交首步充电干预任务 |
| GET | `/api/counterfactuals/{id}` | 查询任务状态与比较结果 |
| POST | `/api/questions` | 对指定决策提出有限意图追问 |

路径中的 `id` 使用导入响应返回的服务记录身份，不能仅使用策略侧 `decision_id`；业务身份由 `run_id + site_id + decision_id` 联合确定，避免不同场次和站点互相覆盖。

## 导入时保留的证据

策略适配器需要保存决策时使用的实际输入，不能在事后查询最新状态来拼装旧决策。以下信息分别承担可复现和用户解释用途：

| 信息 | 接入要求 | 用户用途 |
| --- | --- | --- |
| 场次、站点、决策身份 | 同一次导入保持稳定 | 查询与隔离 |
| 设备时间、观测时间 | 带时区；`device_time` 是设备时钟，`observed_at` 是墙上时钟 | 判断新鲜度、执行关联 |
| 输入与单位 | 功率 kW、能量 kWh、SOC %、电价 EUR/kWh、费用 EUR、步长小时 | 防止比较口径混用 |
| 模型和预测来源 | 保存模型版本和各预测输入来源 | 明确使用 API、持久假设或辅助模型 |
| 历史尾部与模型状态 | 保存缓存最新设备时间以及降级原因 | 解释历史过期等回退 |
| 候选动作与控制语义 | 明确电池动作与并网点指令的含义 | 避免把建议和实际下发混为一谈 |
| 费用分项与期末估值 | 同步保存购电、售电、损耗与期末信用 | 解释预计费用和反事实差额 |
| 支持能力 | 明确是否存在匹配的反事实模型 | 控制界面可用操作 |

相同业务身份且相同内容再次导入为幂等成功。同一身份但任一不可变内容不同返回 HTTP 409，调用方应修正身份或核对上游记录，不能覆盖旧证据。非法输入返回 HTTP 422。

仓库提供 [外部导入契约示例](examples/external-decision.json)。其中全部数值是接口示例，来源标为假设或未知，不是实际遥测。启动本地服务后，在仓库根目录运行：

```bash
curl --fail-with-body -sS http://127.0.0.1:8080/api/decisions \
  -H 'Content-Type: application/json' \
  --data-binary @docs/examples/external-decision.json
```

响应的 `id` 可用于详情、解释和事件路径。该示例的反事实能力不可用，因为没有对应真实模型。示例运行与人工演示使用不同场次，保持来源隔离。

## 把真实模型加入反事实

人工四步 MILP 只接受演示来源及匹配版本。即使真实记录声明相同模型标识，也不能使用人工模型得出比较结论。

接入模型时，应实现 `energy_explain.providers.ComparisonProvider` 接口的 `supports(decision)` 和 `compare(decision, first_charge_kw)`，并把提供器注册到服务和工作进程使用的同一注册表。模块加载方法见 [运行说明](operations.md)。提供器必须拒绝不匹配的来源和版本，并在同一快照下计算：

1. 固定原首步动作后的后续最优续行。
2. 固定替代首步充电功率后的后续最优续行。
3. 两侧功率计划、SOC、费用分项和“替代净成本减原净成本”。

差额为负表示在该模型假设下替代方案费用更低，必须原样保留。无法表达的动作返回不支持，证明无可行解返回不可行，超时和技术异常分别返回对应状态。模型最优不等于预测准确，费用比较不等于真实运行收益。

`compare` 返回可 JSON 序列化的字典，包含 `baseline`、`alternative`、`delta_eur`、`assumptions`。两侧 rollout 包含 `status`、`first_charge_kw`、`cost` 和 `plan`：

| 结果 | 字段 |
| --- | --- |
| `cost` | `purchase_eur`、`export_revenue_eur`、`degradation_eur`、`terminal_credit_eur`、`objective_eur` |
| `plan` 的每一步 | `step`、`charge_kw`、`discharge_kw`、`grid_import_kw`、`grid_export_kw`、`soc_end_percent` |
| `delta_eur` | `alternative.cost.objective_eur - baseline.cost.objective_eur` |
| `assumptions` | 比较口径与局限的字符串数组 |

业务计算无法完成时抛出 `ModelOutcome`，状态使用 `infeasible`、`timeout` 或 `unsupported`；其他异常由工作进程记录为 `failed`。基准重放应校验保存的各成本分项，不能静默替换原证据。

## 执行和结算的关联

外部控制模块在提交时记录指令身份与发出设备时间 `issued_at`；回读事件引用同一指令并记录设备时间 `device_time`、墙上时钟观测时间 `observed_at`、模式和功率。确认的设备时间不能早于指令发出设备时间。加速模拟中设备时间和墙上时钟可能相差数月，两种时钟不能直接比较。

确认事件的 `issued_at` 必须等于对应提交的设备时间，且必须先存在对应提交。当前决策存在新提交时，旧指令的有效回读仍可留在时间线，但不能确认新指令。

没有提交记录时，用户看到候选建议；没有匹配回读时，保持提交待确认；没有结算记录时，保持未结算。真实提交、真实回读和官方结算均由对应外部模块提供，本服务不会根据求解成功自行补造。

演示场次不能导入标为官方来源的结算；外部场次不能导入演示来源的结算。场次结算按 `captured_at` 的最新记录呈现，迟到的旧记录保留但不覆盖新状态；收益数值可以为负。

## 共同仓库接入

同次 shadow 证据导出、直接调用共同 MILP 的提供器及完整运行命令见 [共同仓库协作接入说明](../../EXPLAINABILITY_COLLABORATION.md)。新的共享模型记录使用 `shared-milp-603de45c-cf1`；历史恢复模型与人工四步模型保留各自版本，缺少共享源码时不会互相替代。
