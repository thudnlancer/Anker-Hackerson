# F1 terminal-price 与 PV 候选规则诊断（2026-10-14）

状态：**离线证据已交付，待集成负责人评审；不是 F1 签核或 F2 正式回测**。

## 1. Terminal price 口径

原 A5 回放每个 tick 取 `raw.iloc[start:min(index+24,end)].buy.mean()`，其中 `start` 是 168 小时评估窗口起点。该价格虽不包含当前 API 尚未发布的未来价格，却在后续 tick 继续混入过去价格。优化器未显式收到 terminal price 时则取当前求解窗口 `import_price` 的均值。这两者并不一致。建议 F1 统一为 `current_visible_mean`：**当前决策 tick API 可见购电价窗口的算术均值**，显式传给优化器；缺价不得静默填值，须按缺输入回退。正式窗口末端按比赛剩余时段裁切。

下表是相同 5 个练习站、每站 168 小时、相同输入与零流量执行语义的敏感性诊断。收益是相对自用基线的内部公式估计，分母为合计 `K=192.7311 EUR`，不是官方分数；三组只改变求解目标的 terminal price 规则。`zero` 为去除优化终值的消融，不是推荐值。结算端的期末 SOC 库存估值仍按评估窗口实际购电价均值计算，保持可比。

| 求解终值口径 | persistence | 全辐照度 | 保守 PV 混合候选 | fallback（持久化/辐照度/混合） | 物理审计 |
|---|---:|---:|---:|---:|---|
| A5 起点至可见末端均值 | -2.115% | +9.204% | +9.049% | 0/0/0 | 5/5 站通过 |
| 当前可见窗口均值（建议） | +2.469% | +9.036% | +8.970% | 0/0/0 | 5/5 站通过 |
| 0（消融） | -3.627% | +4.087% | +3.642% | 4/0/0 | 5/5 站通过 |

因果口径相对 A5 口径，persistence 变化 +4.584 个百分点，而混合候选变化 -0.079 个百分点；说明终值口径对策略结果有显著敏感性，不能将旧 A5 数字沿用到新冻结配置。建议 F2 只用签核后的因果口径重新回测；A5 旧数字保留为诊断对照。求解时限为 2 秒，少数压力口径/臂可能触发 incumbent 不可用的显式回退（零终值 persistence 4 步）；这不影响物理审计通过，但应把 fallback 计数与收益一起看。

## 2. PV 保守候选与证据边界

候选规则：当前点使用 HA 实测 PV；未来第 1 个设备小时使用当前 PV persistence；从第 2 个未来设备小时起，只有在历史 168 小时内至少 3 个合格日照样本、API 辐照度可用且校准成功时，才用因果辐照度校准；否则该预测窗口回退 persistence。不得把离线 `shortwave_radiation_wm2` 当成已验证的在线 API 预报。

D2 真实只读采样的误差层证据：1h MAE 比（校准/persistence）1.100，95% CI [0.948, 1.337]，不确定，故不切换；2h 比值 0.630，95% CI [0.541, 0.763]，支持更远时距校准。5 站回放的收益层证据：在建议的因果 terminal-price 口径下，混合候选 +8.970%，全辐照度 +9.036%，persistence +2.469%；混合候选 5/5 站为正，840 个优化归因步、0 fallback、5/5 物理审计通过。A5 旧口径及零终值消融下也分别为 +9.049%、+3.642%，但零终值下有 1/5 站为负，不能声称稳健占优。

结论仅支持**待签核的 F2 离线保守候选**。该回放用练习站未来实测短波辐射代理“已发布天气预报”，不能测量真实 API 预报误差/更新频率；当前混合规则只加入了离线 replay 诊断臂，尚未验证线上实时策略接线。明确建议：F2 离线验证混合规则；线上在 D3 完成真实 API 预报字段、设备时间对齐、预测质量以及缺失/延迟更新验收前，继续使用 persistence-only，不自动启用混合规则。`+8.970%` 只是五个练习站点的诊断性离线估计，不是正式收益。

## 3. 可复现证据

- 机器可读完整 F1 最终建议与哈希：[manifest](../../../outputs/f1_parameter_manifest_20261014.json)。状态为 `final_proposal_pending_integration_signature`，`f2_authorized=false`；签核字段留空。
- 三组最终复核回放目录：`outputs/f1_terminal_a5_v2_20261014/`、`outputs/f1_terminal_current_v2_20261014/`、`outputs/f1_terminal_zero_v2_20261014/`，各自含 `manifest.json`、`summary.json`、逐站 trace/summary。早期无 `_v2` 目录为开发诊断，不用于最终 manifest。
- 命令模板：`python scripts/online/run_contest_strategy_replay.py --data forecast_data --output <dir> --stage 4 --max-sites 5 --workers 2 --wear 0.02 --solver-time-limit 2 --hold-mode zero_flow --terminal-price-mode <a5_eval_start_mean|current_visible_mean|zero>`。
- 历史 A5 同输入报告：[A5_ATTRIBUTION_AND_BASELINE_REPORT.md](../../experiments/A5_ATTRIBUTION_AND_BASELINE_REPORT.md)；D2：[final_bootstrap_all20.json](../../../outputs/irradiance_d2_expansion_20261013/final_bootstrap_all20.json)。

F1 签核前不运行 100 站 F2，不发布正式收益数字。
