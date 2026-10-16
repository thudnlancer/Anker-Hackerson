# F1 逐项签核包

状态：**待集成负责人签核；F2 未授权**

本包只整理冻结参数、来源、限制和验收证据，不代填签核字段，不启动正式 100 站回测，不发布正式收益。

## 1. 签核项对照

| 签核项 | 冻结值/结论 | 来源与证据 | 限制 |
|---|---|---|---|
| 求解预算 | `solver_seconds=2.0`，`mip_rel_gap=1e-9` | C4：72/72 输出，65 optimal + 7 verified incumbent，0 物理违规 | 仍需现场 API/设备粒度验收 |
| SOC 边界 | `0.0% <= SOC <= 100.0%` | C3、C4、配置快照 | 非硬件备用电量；官方冲突参数需重做 F1 |
| 轮询 | `poll_seconds=5.0` | `outputs/w1_readiness_actual_20261014/config_snapshot.json` | 整体在线 readiness 仍 false，历史仅 30 小时 |
| 充/放电效率 | `0.95 / 1.0` | SOP/共享模型约定，C4/A5 使用 | 非设备实测；官方参数变更需重跑证据 |
| degradation cost | `0.02 EUR/kWh` | A5 replay 显式传入 | 不等于官方分数扣减 |
| terminal price | `current_visible_mean` | F1 terminal-price 敏感性 | 结算期末库存估值独立列报 |
| 缺价/非法价格 | 当前 tick 不进优化器，`self_consumption` 回退，`non-optimizer-attributed` | `F1_ONLINE_PRICE_ANOMALY_EVIDENCE.md` | 当前为模拟在线链路，不是远程机 M3 |
| 成功 idle | `zero_flow` | A5 同输入、zero-flow 语义验收 | live 仍需 `zero_flow_verified` |
| 失败 idle | `self_consumption` | A2/A5 归因规则 | 回退收益不得归因优化器 |
| 超时可行解 | 接受独立复验通过的 `limit_reached` incumbent；保留 `mip_gap`、`accepted_incumbent=true` | `rolling.py`、replay/online 定向测试 | 不得标为 optimal；未验证或 infeasible 必须回退 |
| PV | 离线候选三臂；线上 persistence-only | D2、F1 PV 证据 | 未完成 D3/API forecast 验收 |

## 2. 冻结 manifest

- 文件：`outputs/f1_parameter_manifest_20261014.json`
- 当前 SHA-256：`6ab83c5ac66af61b2362007c96ebffcad653b704ab48680776299c0b47cee45a`
- `f2_authorized=false`
- `signature.f2_authorization=false`
- `signature.integration_owner`、`signed_at` 留空

manifest 内 13 项源码、19 项证据哈希由 `build_f1_manifest.py` 生成并逐项复核。源码或证据任何变化都必须重建 manifest 并替换本包中的哈希引用。

主要证据定位与 SHA-256：

| 证据 | 路径 | SHA-256 |
|---|---|---|
| A5 同输入基线对比 | `outputs/w1_a5_compare_20261014_1/a5_same_input_comparison.json` | `6e72be160d83e759622e48573449904917df0f7fdffd653f2d2a82e81faaa5d0` |
| C4 72 例复验 | `outputs/w1_c4_integration_20261015/verification_report.json` | `96e91ef18da35a1e431fa48e8f38bd2553f2bf205da19a1a633e8e8578f6d2e8` |
| 控制配置快照 | `outputs/w1_readiness_actual_20261014/config_snapshot.json` | `103f32a460489b852f6c5dc457f75bc5f4e60570de0c989ee01627d5f082f8b6` |
| terminal/PV 敏感性 | `F1_TERMINAL_PV_EVIDENCE.md` | `fb9c750f7d7b07a7c6cc8df01cd6bae65f9a70322d61a6f5560dcb388c304650` |
| 在线价格异常/incumbent | `F1_ONLINE_PRICE_ANOMALY_EVIDENCE.md` | `aba7763d0921f6d87956ce0eef34af6b5575c7330ce0e8c2253f56947a3c1ce0` |
| D2 PV 候选 | `outputs/irradiance_d2_expansion_20261013/final_bootstrap_all20.json` | `eb37d2683ab9783e5e520d1617f147bcc43aed5834939a184e47fe92cddc1d7b` |

C4 集成复验文件与 manifest 内早期 C4 验证报告是不同路径，但两者当前字节哈希相同。历史五站诊断仍由旧源码生成，是方向性证据；本轮不重标为新口径正式收益。

## 3. 单站 smoke

运行范围：`forecast_data/1.csv`，`stage=4`，`max_sites=1`，168 小时，四臂，`hold_mode=zero_flow`，`current_visible_mean`，2 秒求解预算，所有冻结参数显式传入。

- 672 行物理审计通过。
- 每臂 168 行；`self_consumption` 为 168 行 `non-optimizer-attributed`，其余优化臂各 168 行 `optimizer-attributed`。
- `fallback_steps=0`、`accepted_incumbent_steps=0`（该单站 smoke 未触发超时 incumbent）。
- `official_score_claim=false`，`formal_result_authorized=false`。

产物目录：`outputs/w1_f1_signoff_smoke_20261015_v3/`。目录内 `validation.json` SHA-256：`4deebfbabe4edbbc7721e1f3ecead3b4c309fcd62caa34108e91ac6471bf3f68`。这是字段/统计/审计 smoke，不是正式收益结果。

## 4. F2 输出校验器

`validate_f2_output.py` 默认要求：准确 F1 manifest 哈希、签核及独立 F2 授权、100 个冻结输入、每站 168 小时、四臂、672 行 trace、审计字段、fallback/incumbent/归因统计、独立物理审计和 `official_score_claim=false`。

`--allow-smoke` 只允许 1 站字段验证，返回 `formal_result_authorized=false`，不能绕过正式授权。

签核完成后，先用 `prepare_f2_command.py` 生成显式参数命令，再运行 F2；签核前两个工具都不会启动正式 100 站回测。

## 5. 签核栏

- [ ] A5-1 归因报告
- [ ] A5-2 同输入基线
- [ ] C4 预算与物理审计
- [ ] 效率 `0.95/1.0` 与 degradation `0.02`
- [ ] terminal price 与缺价回退
- [ ] zero-flow / self-consumption 语义
- [ ] incumbent 接受/拒绝口径
- [ ] 线上 persistence-only PV 边界
- [ ] 当前 manifest 准确 SHA-256
- [ ] 单独填写 F2 授权

签核人：________________  日期：________________  备注：________________
