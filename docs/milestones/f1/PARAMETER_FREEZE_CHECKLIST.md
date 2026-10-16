# F1 比赛参数冻结清单

状态：**F1 建议值与限制已逐项明确；待集成负责人签核。签核前 F2 暂停**

本清单汇总 A5 归因/同输入对比、C3 SOC 边界结论、C4 2 秒预算复验及现有模型配置，供集成验收和参数冻结评审。表中数值不因写入清单而自动成为冻结值：标注“待确认”的项目须由集成负责人明确裁定。签核前 F1 不得标记完成；签核前不得开始 F2 的 100 站正式零流量回测，也不得发布正式收益数字。

## 冻结项

| 参数 | 冻结值/规则 | 来源与验收口径 |
|---|---|---|
| `solver_seconds` | `2.0 s` 每站每次求解 | C4：72 个冻结病例 72/72 输出，65 optimal + 7 verified suboptimal incumbent，物理违规 0；`mip_rel_gap=1e-9` 不放宽 |
| `soc_min_percent` | `0.0%` | C3：比赛物理边界固定 0%~100%，非零软件保底会造成结构性 infeasible 风险 |
| `soc_max_percent` | `100.0%` | SOP/设备比赛空间；不可将软件上限收窄为比赛边界替代 |
| `poll_seconds` | `5.0 s` | 实际配置快照；控制配置子项通过（`solver_seconds < poll_seconds`），整体 readiness 因历史仅 30 小时仍为 `ready=false`，不代表全项通过 |
| 充电效率 | **建议 `0.95`** | 与 SOP/当前共享优化器的能量平衡一致：储能增加 `0.95 × AC 充电能量`。这是比赛模型约定，不是设备实测效率；如官方电池参数明确给出不同值，修改版本并重跑 C4/A5/F1 后再启动 F2 |
| 放电效率 | **建议 `1.0`** | 与 SOP 当前结算抽象一致：放电侧不再施加第二次效率折减，避免重复计入损耗。该值也是模型约定、非设备实测；若官方参数明示放电效率，更新模型及 C4/A5/F1 |
| `degradation_cost_eur_kwh` | **建议 `0.02 EUR/kWh`** | A5 manifest 明确传入 `0.02` 到求解器目标与回放核算，按充放电吞吐计。通用 `rolling.py` 默认值为 `0.002`，F2 必须显式传入 `0.02`，不能依赖默认值 |
| `terminal_price` | **建议 `current_visible_mean`**：当前决策时、优化时域内 API 可见购电价的算术均值，显式传给优化器 | 敏感性诊断见 [F1_TERMINAL_PV_EVIDENCE.md](TERMINAL_PV_EVIDENCE.md)。A5 评估起点均值在后续 tick 混入过去价格，不再使用。优化窗口价格必须完整、有限、与设备时间对齐；任何缺失/非法价格时该 tick 跳过优化，执行显式 self-consumption 回退，标 `non-optimizer-attributed`，不得填 0 或沿用旧价格。评估端期末库存估值独立列报，不混同 optimizer terminal estimate |
| 成功计划 idle 语义 | `zero_flow`：第三方模式下 `charge=0 W`，仅用于已验证站点/离线零流量核算 | A5 同输入对比；optimizer-attributed；硬件 live 使用仍受 `zero_flow_verified` 开关约束 |
| 失败回退 idle 语义 | 显式 `self_consumption`，标记 `non-optimizer-attributed` | A5 归因报告、A2 执行决策表；不得把回退收益归因于优化器 |
| PV 预测切换规则 | **F2 离线候选**：当前点 HA 实测优先；未来第 1 个设备小时 persistence；时距 `>=2 h` 使用过去 168 h 因果辐照度校准且至少 3 个合格日照样本；任一条件不满足时整个预测时域 persistence。**线上运行在 D3/API 字段、时间对齐、缺失/延迟验收通过前保持 persistence-only** | D2：1h inconclusive；2h MAE 比 0.630（95% CI [0.541, 0.763]）。五个练习站的策略诊断为方向性收益证据，不证明真实 API forecast 泛化；限制见诊断报告 |
| 负载预测规则 | API-first 主线优先使用当前 HA 负载；未来负载不可得时使用当前负载 persistence/已声明区间假设 | 当前 API-first 约束；不把练习站训练标签当成线上未来负载真值 |
| 时间步 | 当前离线比赛回放 `1 h`；线上以官方 API 实际粒度为准，不能把离线小时步长冒充官方结算粒度 | SOP/API 约束；F2 报告必须注明粒度 |

## 配置快照

完整 F1 建议 manifest 见 [`outputs/f1_parameter_manifest_20261014.json`](../../../outputs/f1_parameter_manifest_20261014.json)；terminal/PV 证据见 [`F1_TERMINAL_PV_EVIDENCE.md`](TERMINAL_PV_EVIDENCE.md)。当前实际配置快照见 [`outputs/w1_readiness_actual_20261014/config_snapshot.json`](../../../outputs/w1_readiness_actual_20261014/config_snapshot.json)：

```json
{
  "solver_seconds": 2.0,
  "soc_min_percent": 0.0,
  "poll_seconds": 5.0
}
```

readiness 结果见 [`outputs/w1_readiness_actual_20261014/readiness.json`](../../../outputs/w1_readiness_actual_20261014/readiness.json)：控制配置检查通过，但整体 `ready=false`，三站各只有 30 小时历史。此项不是离线 F2 的自动阻断条件，也不得声称线上模型整体 ready。manifest 是完整签核候选；签核后须由集成负责人确认其哈希/签署版本，再以该版本执行 F2。

## F2 启动门槛

F2 只有在以下条件全部满足后才能开始：

1. 集成负责人确认 A5-1 归因报告和 A5-2 同输入基线对比。
2. 集成负责人确认本清单每个冻结项，补充签核日期、提交号和完整配置 manifest。
3. 确认相关控制配置子项通过（`solver_seconds < poll_seconds`、`soc_min_percent == 0`）；不得把整体在线 readiness 的 168 小时历史门槛混同为离线 F2 条件，也不得将当前整体 `ready=false` 描述为通过。
4. 明确 F2 使用 `hold_mode=zero_flow`，失败回退仍标记 `non-optimizer-attributed`。
5. F2 产物继续标明 `official_score_claim=false`，直到比赛官方接口/结算口径完成现场验证。

## 集成负责人签核

- [x] A5-1 归因报告已由集成负责人验收（仅离线证据，见共享进度 2026-10-14 签核记录）
- [x] A5-2 同输入基线对比已由集成负责人验收（仅离线证据）
- [x] C4 预算与控制配置子项已由集成负责人验收（不含整体在线 readiness）
- [ ] 确认建议值：效率 `0.95/1.0`、degradation cost `0.02`，并接受其模型约定/非设备实测限制
- [ ] 确认 terminal 缺价时跳过该 tick 优化并显式 self-consumption 回退，不插补价格
- [ ] 确认 PV 混合规则仅用于 F2 离线候选；线上 D3/API 验收前 persistence-only
- [ ] F2 使用已签核 manifest 的准确哈希，所有参数显式传入且 `f2_authorized` 经签核后另行授权
- [ ] 上述冻结参数无未登记例外
- [ ] 允许启动 F2：是 / 否

签核人：________________  日期：________________  备注：________________
