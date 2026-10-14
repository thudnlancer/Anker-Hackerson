# 项目共享进度与并行工作分工（2026-10-14）

> 用途：此后各 worker 与集成负责人交流进度的唯一共享看板。本文档区分已验证事实、进行中工作和计划，不做时间估算；排序以里程碑门槛表示。
> 关联文档：[预赛方案_更新版.md](../预赛方案_更新版.md)（产品方案）、[SYSTEM_ARCHITECTURE_GUIDELINE.md](SYSTEM_ARCHITECTURE_GUIDELINE.md)（架构基线）、[NEXT_STAGE_RESULTS.md](NEXT_STAGE_RESULTS.md)（实验证据）、[HOLD_SEMANTICS_DESIGN.md](HOLD_SEMANTICS_DESIGN.md)（语义对齐设计，Q1 已决策）。
> 状态核验：2026-10-14；当前集成分支 `feat/explainability-integration`，E4-1、W3 选择性集成和 W2 E4 产品分别落在 `82ba72c`、`a632a56`、`76399f0`。工作区仍有其他实验/设计改动，未纳入上述提交。

## 0. 共享协作规则（所有 worker 必读）

- 本文档是**进度与交接的共同入口**，不是把未提交文件视为已交付的依据。状态只能写成 `待开始`、`进行中`、`待集成`、`已验收`、`阻塞`；`已验收`须附测试/报告路径或提交号。口头“完成”不自动改变状态。
- 多 worker 同时开工前由集成负责人盘点并归属当前未提交改动，确保本文档已纳入 Git 跟踪，再为每个 worker 建独立分支/worktree。**W1 在主工作树推进；W2 的 E4 已集成；W3 的 B4 本地基线已选择性集成并继续做 D1。** 不允许在当前脏工作区上并发编辑，也不允许整枝合并旧 `feat/energy-floor`。
- 每个 worker 只修改自己的文件域；共享的 `src/energy_lab/rolling.py`、`src/energy_lab/config.py`、`src/energy_lab/online_contract.py`、`src/energy_lab/explainability_evidence.py` 和跨线样例配置由集成负责人协调。跨域需求先在本文档 §9“接口/阻塞记录”登记，由所有者或集成负责人处理。
- 更新节奏：开始任务、完成可验收增量、发现阻塞或接口变化时，更新 §8 一行，并在 §9 追加一条记录；集成负责人验收后才修改 §2/§4 的总状态。不要让多 worker 在同一工作树同时编辑本文档；各分支可提出文档补丁，由集成负责人串行合并。
- 每次交接至少包含：任务编号、分支/提交、变更文件、验证命令及结果、证据产物、已知限制、下一步/阻塞。所有对外收益数字只使用 A5/F2 冻结后的零流量口径；自用回退标记 `non-optimizer-attributed`。
- 所有线默认只读或离线。采集恢复和 M3 只读 dry-run 不等于授权 HA 写入；真实控制写入、远程部署/重装、故障切换须单独确认执行范围与窗口。

## 1. 一句话定位

Anker 黑客松充电储能赛道场景三："会解释自己的 AI"——基于 LP/MILP 滚动调度引擎的家庭能源管家，调度与解释共享同一份状态/决策记录。当前处于**离线证据充分、线上闭环未通**的阶段。

## 2. 当前状态

### 2.1 已验证（离线，可对外声称）

| 项 | 证据 |
|---|---|
| 压力闭环回退安全 | 8 场景 408 步，超时/坏预测回退均保持物理约束 |
| 历史探索性优化器对照 | 曾有 100 站 MILP vs 自用基线的探索性结果；不属于当前冻结参数、零流量口径或正式收益，W1 完成前不对外发布 |
| 跨站点泛化 | 80/20 留出，三组参数均 20/20 站点为正（未冻结比赛参数） |
| 比赛口径策略主线 | 20 站 × 2×168h：辐照度 PV + 当前负载最优（+14.612% / +6.441%），第三窗口可加但非门槛 |
| SOP 控制语义 | 充放/购售互斥、SOC 全量程、第三方模式两条指令、约 1 秒间隔、下一 tick 生效 |
| Hold 语义差异量化 | pair 实验：自用 idle vs 零流量 idle，persistence 臂 6.238% vs -2.115%，辐照度臂 11.167% vs 9.204% |
| 回归测试 | 2026-10-14 集成后主仓 `pytest tests -q`：241 passed；Explainability 应用后端：95 passed、49 subtests passed（1 个 Starlette/httpx 弃用警告）；前端 14 passed，生产构建通过 |
| 只读采集链路 | D1 首轮恢复报告确认 PID/heartbeat 匹配、三站覆盖、零写入；设备时钟推进到 2026-10-20 08:15，但该报告窗口内只有一个唯一 tick，旧缓存是独立 epoch，尚不能合并为连续历史 |
| HA 物理校准（B3） | 两轮真实只读审计（夜 12×5s + 日光 18×10s，[报告](outputs/ha_physical_calibration_20261013/report_daylight.json)）：三站 PV/负载符号、充放互斥、电网进出口互斥零违例；三向功率平衡残差 ≤1W；日光轮 PV 激励峰值 1232/1150/720W；30/30 样本可用 |
| 零流量硬件验证（B1/B2） | 三站真实 `charge=0` 受监管写入全部 verified（live_readback，[证据目录](outputs/zero_flow_verification)）：设定回显正确、电池 0/0W、电网被动结算（进口 150≈158W）、写入后下一 tick 回读、模式恢复回读确认无残留；`stations.{1,2,3}.zero_flow_verified=true` 已落配置；测试后三开关已收回 false |

### 2.2 已完成或进行中

- 线 A：A1-A4 已提交；A5-1/A5-2 的离线证据已由主控验收（2026-10-14，范围仅限归因与同输入对比）；A6 定向回归 89 passed，V1/V5 清单验收仍待完成。
- 线 B：B0-B3 已完成；B4 本地分片、merge view、监控、watchdog、离线故障演练已选择性集成（`a632a56`）。远程 Tailscale/双节点部署未完成。
- 线 C：C1-C3 已提交；C4 的 `solver_seconds=2.0`、readiness 校验及 72 个冻结病例复验已由主控验收（2026-10-14，72/72 可输出、0 物理违规），仅表示求解预算/配置子项通过，不表示在线整体 readiness 通过。
- 线 D：D2 批 1-20 分析已完成；batch 20 因 ConnectTimeout 仅 14/24 轮，缺口保留。另有 batch 21 完整 24 轮补采（零写入，继承 batch 20），尚未并入 20 批主分析。D3、D4 未完成。
- 线 E：E1-E3 已完成；E4-1、E4-2 和 E4-4 已集成（`82ba72c`、`76399f0`）。真实共享优化器截止约束测试通过；E5 三站 replay 数据导入与演示叙事未完成。旧 `feat/energy-floor` worktree 保留，不整枝合并。
- 采集器：D1 首轮报告确认只读进程存活、heartbeat 新鲜、三站覆盖且零写入；设备时间在报告窗口内仍只有一个唯一 tick，旧 2028 缓存与新 2026 epoch 不连续，必须分段处理。进程持续健康需继续观察，不能由一次报告推定。

### 2.3 能力缺口（不可声称，按优先级）

1. A5/C4 已通过对应离线验收；F1 仍有 terminal price 窗口和 PV 策略收益门槛未决，正式参数未冻结，F2 尚未启动。
2. `run_live.py` 的 MILP dry-run 接线已完成，但当前工作区未完成端到端 M3 在线只读验收；真实持续写入控制及恢复流程未验收。
3. 天气预报质量尚未完成 D3；D2 的 PV 误差统计只支持校准方向判断，仍需线 F 用策略收益门槛复核后才能冻结切换规则。
4. self_consumption 回退分支的 MILP 一致性模型未重建；回退收益需标为非优化器归因。
5. E4 产品功能已集成；E5 三站 replay 数据导入和演示叙事仍未交付。
6. 远程双节点冗余及 Tailscale 部署尚未完成；本地只读采集已恢复，当前 heartbeat 有存活进程证据，但设备时间仍停在单一 tick，旧 2028 缓存与新 2026 epoch 不连续。

### 2.4 决策状态

- 已决策（2026-10-13）：Q1 未验证站点回退 = 显式 `self_use_mode` + 三条件（见 HOLD_SEMANTICS_DESIGN §8.1）。
- 已决策（2026-10-13）：Q4 设备验证执行窗口——B0 已授权并已执行，三站验证完成（见 §2.1）。
- 已决策：Q2 SOC 发散阈值采用 2 个 SOC 百分点（已实现于 A3）。
- 待评审：Q3 自用 idle 建模立项条件。

## 3. 总体路线（里程碑门槛）

```
M0 设计评审收尾 ✅（Q1/Q2/Q4 已决策，Q3 待定）
  → M1 并行实施（A/C/D/E/F 部分完成，仍有未提交改动和未交付项）
  → M2 设备语义验证 ✅ 2026-10-13（三站 charge=0 受控验证完成；比赛写入开关已关闭）
  → M3 线上只读 dry-run 验收 ⏳（A4 代码已接线，当前需复核配置、运行状态和完整链路）
  → M4 比赛参数冻结、零流量口径回测和赛前检查 ⏳
```

原则：任何真实 HA 写入需单独授权；所有线默认只读/dry-run；每条线独立可回退。

## 4. 并行工作线

### 线 A：契约与执行层重构（hold 语义对齐实施）

**目标**：落地 HOLD_SEMANTICS_DESIGN 步骤 0/2/3/4/5，消除计划-执行语义差异。
**来源**：HOLD_SEMANTICS_DESIGN §4、§6。
**需要设备/授权**：否（A4 的 live 分支除外，依赖线 B 结果）。

- [x] A1（步骤 0）契约去歧义：`rolling.py` `hold→idle`、执行映射显式化（`fec9f35`）
- [x] A2（步骤 2）`execute.py` 统一执行决策表和审计字段（`ecac401`）
- [x] A3（步骤 3）SOC 发散守卫，阈值 2pp（`780fe08`）
- [x] A4（步骤 4）`run_live.py` dry-run 链路和第四道安全开关（`d11bc87`）；实时链路验收仍待 M3
- [x] A5-1（步骤 5a）归因报告：逐步归因字段、汇总计数、旧字段未知处理和离线报告由主控验收（2026-10-14；见 `A5_ATTRIBUTION_AND_BASELINE_REPORT.md`）；不包含在线 fallback 收益验证
- [x] A5-2（步骤 5b）同输入基线对比：旧 self-consumption idle vs 新 zero-flow idle 的收益、fallback、归因步数和物理审计由主控验收（2026-10-14；见 `outputs/w1_a5_compare_20261014_1/a5_same_input_comparison.json`）；非官方结算
- [ ] A6 核对 V1 清单剩余测试；hold/zero-flow、执行层、回放、配置、shadow、rolling 与时间链路定向回归 89 passed；本轮 `pytest tests -q` 全量回归 241 passed。A6 仍待集成负责人按 V1/V5 清单验收

**验收**：HOLD_SEMANTICS_DESIGN §6 V5 表步骤 0/2/3/4/5 行。
**依赖**：A3 需 Q2 拍板；A4 需 B2 产物；A2 审计 schema 冻结后线 E 可对接。

### 线 B：设备验证与 HA 写入（授权门槛）

**目标**：三站 `zero_flow_verified` 结论落配置；建立下发→回读闭环能力。
**来源**：HOLD_SEMANTICS_DESIGN §4 步骤 1、§6 V3。
**需要设备/授权**：**是**，本线是唯一真实写入路径，需单独授权与执行窗口（Q4）。

- [x] B0 授权与窗口确认（Q4 评审）+ 单站先行协议。**完成（2026-10-13）**：用户会话授权，按单站先行协议执行
- [x] B1 单站 `charge=0` 受监管写入测试。**完成（2026-10-13）**：站1 verdict=true（live_readback）——设定回显正确、电池 0/0W、电网被动结算（进口 150≈158W）、下一 tick 回读、模式恢复确认无残留（[证据](outputs/zero_flow_verification/site1_live_20261013)，写入 6 次=下发 3+恢复 3）
- [x] B2 三站扩展 + 证据产物存档 + `zero_flow_verified` 落配置。**完成（2026-10-13）**：站2、站3 相继通过（同站1口径），三站 `zero_flow_verified=true` 落配置；六件 artifact/站 + SHA-256 哈希链存档（[站2](outputs/zero_flow_verification/site2_live_20261013)、[站3](outputs/zero_flow_verification/site3_live_20261013)）；测试后三开关收回 false 恢复默认只读
- [x] B3 HA 物理校准：PV 传感器额定、负载符号/方向、电池功率实体三向一致性。**完成（2026-10-13）**：两轮真实只读审计三站全部 calibrated，平衡残差 ≤1W，日光轮 PV 峰值 1232/1150/720W（[报告目录](outputs/ha_physical_calibration_20261013)，[audit_physical_calibration.py](audit_physical_calibration.py)，15 单测）；注：被测为模拟器环境，真机需复跑确认
- [x] B4 本地采集分片/合并/监控改造与 watchdog 离线故障演练已选择性集成；远程电脑 Tailscale 重装及双节点回传未完成（远程动作未授权）

**验收**：三站结论落配置、无模式残留；B3 校准报告。
**阻塞他人**：无（B0~B3 已完成，A4 与线 F 线上口径验证已解锁）。**被阻塞**：B4 需远程机访问。

### 线 C：求解可用性与时限回退

**目标**：把 90.28% 候选可用率提升到满足控制周期，且回退可执行、可验证。
**来源**：NEXT_STAGE_RESULTS §4 第 1 条。
**需要设备/授权**：否。

- [x] C1 `limit_reached` 病例根因分类（`31b85af`）
- [x] C2 超时时提取并复核可行 incumbent，供只读候选和回退链路使用（`82325b3`）；生产时限仍待 C4/F 冻结
- [x] C3 区分真实 SOC 边界与可选软件保底：配置冻结建议（比赛 `soc_min=0`，呼应 Q1 条件 2）。**完成（2026-10-14，commit c223894）**：分析与核查见 NEXT_STAGE_RESULTS "C3" 节——SOP 固定比赛 SOC 空间 0~100% 且备用预留不可改；`soc_min>0` 可致结构性 infeasible 并与自用回退冲突；比赛在线链路核查已全程 `soc_min=0`。冻结建议已递交线 F（见 F1）
- [x] C4 预算扫描结论固化：`solver_seconds=2.0` 映射、readiness 校验、脱敏配置快照和 72 病例复验由主控验收（2026-10-14）；不放宽 `mip_rel_gap`（R8）。整体在线 `ready=false` 仍然成立

**验收**：重放 72 次病例 + 压力场景，可用率与回退越界次数有量化报告；回退路径与 A2 决策表对接。
**依赖**：与 A2 接口对齐（回退candidate 的字段契约）。

### 线 D：预测校准与数据采集

**目标**：PV/负载校准在真实 API 数据上下结论；天气预报质量完成验证。
**来源**：NEXT_STAGE_RESULTS 2026-10-13 辐照度试采、§4 第 4 条。
**需要设备/授权**：否（只读）。

- [ ] D1 采集器监护：只读采集已恢复并确认三站覆盖、heartbeat/PID 存活和零写入；连续观察仍为单一设备 tick，和旧缓存尾部相差 -778530 分钟，不得拼接为连续历史；需等待设备时间推进后再验收
- [x] D2 PV 校准扩样主分析已完成：20 批有 19 个有效设备日；1h inconclusive/维持 persistence，2h/4h 校准 MAE 优；batch 20 真实缺口保留。batch 21 已补采完整设备日，未并入主 bootstrap
- [ ] D3 天气预报质量验证：API 预报 vs 后续实测的误差分布（替代历史实测代理的缺口）
- [ ] D4 负载预测在当前负载主线下的误差画像（服务线 C 的病例分析）

**验收**：D2/D3 各自产出可写入比赛配置的结论或"样本不足"的明确声明。
**依赖**：采集持续运行；与线 F 共享数据口径。

### 线 E：场景三解释器与演示产品（比赛交付物）

**目标**：预赛方案五大功能中面向评委的解释体验：决策解释卡片、自然语言反事实追问、用电计划可行性检查、光伏消纳分析。
**来源**：[预赛方案_更新版.md](../预赛方案_更新版.md)。
**需要设备/授权**：否，**可完全基于离线 replay 数据与审计 schema 开发**。

- [x] E1 审计证据消费层与 schema
- [x] E2 决策解释卡片
- [x] E3 反事实追问与异步重算
- [x] E4-1 优化器支持逐步 `energy_floor_kwh` 截止电量约束；已提交 `82ba72c`，模型指纹与解释证据层一致，集成后完整回归见 §2.1
- [x] E4-2/E4-4 可行性检查、光伏日核算 API/导入和页面已集成；真实共享优化器 deadline 测试通过（`76399f0`）
- [ ] E5 演示叙事：三站点真实数据驱动的讲解脚本与界面

**验收**：在离线数据上跑通四大功能演示；语义诚实（区分优化归因/非优化归因，口径同 A5）。
**依赖**：A2 审计 schema（可先按 HOLD_SEMANTICS_DESIGN §3 字段定义开工）；线 F 的 replay 产物做数据源。
**备注**：这是场景三评分本体；E1-E4 已有可运行基座，E5 演示数据与叙事仍需优先推进。

### 线 F：离线验证收尾与比赛参数冻结

**目标**：把"已验证"与"可声称"收口为比赛配置。
**来源**：NEXT_STAGE_RESULTS §4、README 退出条件。
**需要设备/授权**：否。

- [ ] F1 比赛参数冻结：已生成 [`F1_PARAMETER_FREEZE_CHECKLIST.md`](F1_PARAMETER_FREEZE_CHECKLIST.md)，含求解预算、SOC、轮询、效率、degradation、terminal price、hold 和 PV 切换规则；清单已区分已验证值与待确认候选，**待集成负责人逐项确认并签核**。当前 readiness 为 `control_config.ready=true`、整体 `ready=false`（历史仅 30 小时），不得混同为离线 F2 通过。
- [ ] F2 全量回测重跑：**阻塞于 F1 签核**；签核前不得启动冻结参数下的 100 站正式零流量回测或发布 100 站收益数字
- [ ] F3 第三窗口补充评估（可选，非门槛）
- [ ] F4 赛前检查清单：`check_online_readiness.py` 已覆盖 `solver_seconds`、`soc_min_percent`、`poll_seconds` 并保存脱敏配置快照，待集成负责人验收

**验收**：冻结配置文件 + 可复现的正式收益报告（零流量口径）。
**依赖**：A5 集成验收、C4 集成验收、D2 校准结论；F1 清单签核是 F2 的硬门槛。

## 5. 依赖关系速查

| 线 | 阻塞于 | 阻塞谁 |
|---|---|---|
| A | 无（A1-A4 已完成） | F、E（口径） |
| B | B4 的远程机访问；本地 B4.1-B4.4 不需远程授权 | D1；M3（线上链路） |
| C | A2 接口契约 | F |
| D | 采集器持续运行 | F |
| E | A2 schema（可先 mock）、F 数据源 | 无（纯交付） |
| F | A5/C4 集成验收、D2 | F1 签核后方可开始 F2 |

**关键路径**：A5/C4 完成 → F1 参数冻结 → F2 零流量口径回测 → M3 线上只读验收 → M4 赛前检查。E4/E5 可并行开发，不应阻塞回测口径工作。
**可立即推进（无设备写入）**：A5/C4 集成评审、A6、F1 清单签核准备、E5 演示、D1 多 tick 观察与 D3 数据准备。F2 在 F1 签核前不得启动。

## 6. 跨线风险

- 授权边界：M3 是只读 dry-run，不以真实 HA 写入授权为前提；持续写入控制、远程部署或真实故障切换需要单独确认权限与窗口。
- 口径蔓延：自用 idle 曾抬高表观收益（pair 实验），所有线的对外数字统一走 A5 零流量口径，E 线演示同样遵守。
- 采集单点：D 线全部结论与 B3 校准依赖采集器；建议采集产物每日备份（可并入 B4）。
- 文档-代码漂移：A 线每步单独提交并回归（R7），禁止多步合并提交。

## 7. Worker 并行分工

拟采用“3 个 worker + 1 名集成负责人”。worker 只在独立 worktree 工作；集成负责人负责接口冻结、文档串行更新、合并和最终验收。

### W1：核心策略、口径与正式回测

- **范围**：A5、A6 → C4 → F1 → F2。先收口零流量归因，再固化 `solver_seconds=2.0`、SOC 边界、损耗/吞吐成本、hold 语义，最后运行 100 站正式回测和比赛回放。
- **文件域**：`contest_replay.py`、核心回测脚本、C/F 测试与报告；不得擅自改动共享优化器、线上契约和解释 schema。
- **输入依赖**：A2 审计字段、C3 `soc_min=0` 建议、D2 已有结论。D3 未完成不阻塞 C4，但天气切换规则必须在 F1 标注“已证实/样本不足”。
- **交付门槛**：每个子任务独立提交；有可复现命令、正式零流量数字、失败/回退统计和配置快照。W1 的 F1/F2 完成后才能声称正式收益。

### W2：场景三产品与演示

- **范围**：E4-2 可行性检查、光伏消纳分析 → E5 演示叙事和界面。
- **文件域**：独占 `explainability-app/`；优化器需求只能登记接口契约，不直接修改共享 `src/energy_lab/rolling.py`。
- **输入依赖**：E1-E3/E4-1、A2 schema。F2 正式 replay 未完成前可以使用明确标记的 mock/历史样例，但不能把样例收益作为正式数字。
- **交付门槛**：API、前端页面、错误/空数据状态、测试和三站点演示脚本齐全；解释必须区分优化归因与 `non-optimizer-attributed` 回退。

### W3：数据可靠性、采集与预报证据

- **范围**：B4.1-B4.4 本地分片/合并/监控/watchdog → D1 采集恢复 → D3 天气预报质量；可附带合并 D2 batch 21，但不阻塞 W1。
- **文件域**：采集器、监控器、watchdog、采集/预报分析脚本及对应测试；不得修改优化器、产品前端或 HA 写入开关。
- **授权边界**：本地故障演练和只读采集无需设备写入；远程 Tailscale、远程部署、HA 写入和故障切换须单独授权与窗口确认。
- **交付门槛**：给出心跳/熔断/恢复证据、缓存新鲜度与缺口报告、天气 API 预测误差分布；不能用历史实测代理冒充 API 预测验证。

### 集成负责人（主控）

- 负责当前脏工作区归属、建立 worktree、冻结跨线接口、串行更新本文档、审查合并。
- 负责 M3 线上只读 dry-run、最终全量回归、F4 赛前检查和发布材料。
- 不代替 worker 重写其工作域；只在共享文件或跨线契约上做最小必要改动。

## 8. Worker 状态表（共享更新区）

| Worker | 当前状态 | 当前任务 | 分支/提交 | 下一验收 |
|---|---|---|---|---|
| W1 | 进行中 | A5-1/A5-2、C4 已由主控验收；F1 候选清单仍待 terminal price/PV 决策 | `feat/explainability-integration` @ `852abb1` | 固化 terminal price 唯一窗口规则；复核 PV 切换的策略收益门槛；F1 签核前不启动 F2 |
| W2 | 已集成 | E4-1/E4-2/E4-4 已在主集成分支，完整回归通过 | 集成提交 `82ba72c`、`76399f0`；W2 基线更新 `07140ea` | 导入三站 replay trace 并完成 E5；明确标注练习数据与估算边界 |
| W3 | 进行中 | B4.1-B4.4 已选择性集成；D1 只读恢复首轮报告完成；D3 未开始 | 主仓 `a632a56`；离线演练 `5586550`；D1 `outputs/d1_recovery_20261014/` | 继续采集并统计多 tick 新鲜度/缺口；满足 API forecast 与实测对齐后启动 D3 |
| 集成负责人 | 进行中 | E4-1、W3 基线、W2 E4 已完成集成；负责验收 A5/C4/F1 和最终回归 | `feat/explainability-integration` @ `76399f0` | 签核 F1 门槛，再决定是否启动 F2 |

## 9. 进度交接记录

| 日期 | 角色 | 事项 | 结果/阻塞 | 证据 |
|---|---|---|---|---|
| 2026-10-14 | 集成负责人 | 建立并行分工基线 | W1 在主工作树推进；W2 待启动；W3 已转为待集成；当前工作区仍存在跨线未提交改动，禁止直接并发编辑 | 本节 §0、§7、§8 |
| 2026-10-14 | W1 | A5 初步落地 | trace 新增字符串归因标签；跨站归因计数对 legacy `null` 保持未知，不再求和崩溃；回放摘要显式记录 idle 核算臂，非官方分数仍明确否定 | `tests/test_contest_replay.py`；当时阶段性回归 231 passed（不是当前总数） |
| 2026-10-14 | W1 | C4 预算复验 | 同一冻结快照/因果校准路径、每站 2 秒预算复跑；65 optimal + 7 verified suboptimal incumbent，72/72 输出；最大/平均 gap 0.755%/0.403%，72 计划物理审计 0 违规；不改 `mip_rel_gap=1e-9` | `outputs/w1_c4_verification_20261014_1/verification_report.json` |
| 2026-10-14 | W3 | 完成 B4 本地基线与消费者接线 | 分片/heartbeat、merge 视图、监控状态机、watchdog 过滤与过期状态保护；未启动真实采集、未执行远程部署或 HA 写入 | `2eb92ef`、`905a40f`；W3 定向测试 33 passed |
| 2026-10-14 | 集成负责人/W3 | 选择性集成与 D1 首轮恢复 | 仅集成采集可靠性、watchdog、监控状态机、离线演练和对应测试；明确未合并 W3 的 explainability-app/旧模型哈希修改。主仓回归 241 passed；应用回归 83 passed + 49 subtests。只读采集 PID 与 heartbeat 匹配、三站覆盖、零写入；新设备时间单 tick，旧缓存 2028 与新 2026 epoch 不连续。D3 未启动 | 主仓集成提交 `a632a56`（来源 `905a40f`）；演练证据来源 W3 `5586550`；`outputs/d1_recovery_20261014/d1_live_report.json`；`outputs/w3_integration_20261014/` |
| 2026-10-14 | W1 | A5 产物 smoke 与 C4 配置门槛 | 1 站 × 168h × 3 臂零流量回放：504 行物理审计通过；optimizer-attributed 计数为辐照度臂 168、持久化臂 168、自用基线 0；`official_score_claim=false`。运行配置新增预算必须为正且短于轮询周期；当时阶段性回归 232 passed | `outputs/w1_a5_zero_flow_smoke_20261014_1/`；`tests/test_config_solver_seconds.py` |
| 2026-10-14 | W1 | A5 拆分验收 | 归因报告与同输入基线对比均生成；5 站 × 168h 两口径输入等价，旧/新各 2520 行物理审计通过；收益、fallback、optimizer/non-optimizer 步数均列明；`official_score_claim=false` | `A5_ATTRIBUTION_AND_BASELINE_REPORT.md`；`outputs/w1_a5_compare_20261014_1/a5_same_input_comparison.json` |
| 2026-10-14 | W1 | readiness 三参数与快照 | `check_online_readiness.py` 检查 `solver_seconds=2.0`、`poll_seconds=5.0`、`soc_min_percent=0.0`；配置检查通过，历史仅 30 小时所以整体 `ready=false`；保存脱敏快照 | `outputs/w1_readiness_actual_20261014/readiness.json`；`config_snapshot.json`；235 全仓测试通过 |
| 2026-10-14 | W1 | 提交并复核 F1 参数冻结清单 | A5、C4 状态为待集成验收；清单列明指定参数、hold/zero-flow、PV 候选规则及签核条件；明确 `degradation_cost=0.02` 必须显式传入（不可依赖 rolling 默认 `0.002`），readiness 仅控制配置子项通过；F2 明确阻塞于集成负责人签核 | `F1_PARAMETER_FREEZE_CHECKLIST.md`；`outputs/w1_readiness_actual_20261014/readiness.json`；当前 235 passed |
| 2026-10-14 | W1 | A6 定向回归复核 | hold/zero-flow 契约、执行决策表、A5 回放、solver 配置、shadow、rolling MILP、在线时间链路定向测试共 89 passed；未改变 A5/C4 的待集成验收状态，也未启动 F2 | `pytest -q tests/test_idle_contract.py tests/test_execute.py tests/test_config_solver_seconds.py tests/test_contest_replay.py tests/test_run_live_chain.py tests/test_run_live_time.py tests/test_shadow.py tests/test_rolling_milp.py tests/test_system_model.py` |
| 2026-10-14 | W1 | 集成后全量回归复核 | 在当前共享工作区（含 W3 选择性集成）运行 `pytest tests -q`，241 passed；未改变 A5/C4 待集成验收状态，未启动 F2 | `pytest tests -q` |
| 2026-10-14 | W2 | 完成 E4-2/E4-4 第一版 | 持久计划任务队列、基准重放对账、截止 SOC 约束接口、明确状态与调整建议；实现 trace 日核算导入/查询、JSONL CLI 与两页 UI。真实共享优化器调用待 E4-1 进入基线；暂无三站正式 trace 导入 | `feat/w2-e4-product` @ `9bb5d2a`; 94 后端测试、49 subtests、14 前端测试通过，build 通过 |
| 2026-10-14 | 集成负责人 | 集成 E4-1、W3 B4 与 W2 E4 | E4-1 与 W2 页面/服务以独立提交集成；W3 仅选择性集成采集域，排除其应用哈希修改。主仓 241 passed、应用 95 passed + 49 subtests、前端 14 passed/build 通过；真实 shared MILP deadline 约束测试通过 | `82ba72c`、`a632a56`、`76399f0`；`explainability-app/tests/test_e4_shared_integration.py` |
| 2026-10-14 | 集成负责人 | A5/C4 离线证据签核 | A5 5 站同输入两口径各 2520 行物理审计通过，归因和收益差异可对账；C4 72/72 输出、0 物理违规，配置子项通过。F1 未签核：terminal price 规则与 PV 收益门槛仍待收口 | `A5_ATTRIBUTION_AND_BASELINE_REPORT.md`；`outputs/w1_a5_compare_20261014_1/a5_same_input_comparison.json`；`outputs/w1_c4_verification_20261014_1/verification_report.json` |

### W2 下一轮验收清单（E4 集成后的剩余项）

2026-10-14 集成更新：E4-1 已在 `82ba72c`，E4-2/E4-4 已在 `76399f0`；真实共享 MILP 截止约束测试通过。此前预检定位 `outputs/contest_hold_pair_zero_v2_20261013/` 三站练习站点 replay（每站 504 行、选定 `irradiance` 臂 168 行），但原 trace 无时区，被核算接口正确拒绝；不得悄悄补时区并冒充原始数据或官方比赛三站。来源说明见 [W2 E4/E5 预检](../w2-worktree/explainability-app/docs/w2-e4-e5-preflight.md)。

1. 已完成：共享模型版本/哈希及证据导出层对齐；真实共享优化器可达/不可达 deadline 测试通过。超时、缺失约束能力和设备时间步不对齐已有单元测试，仍可补端到端负例。
2. 待完成：为三站 trace 找到带明确时区的权威来源或记录可审计的时区转换依据，再做导入/物理核算；无依据时保持拒绝，不生成伪“真实”报告。
3. 待完成：E5 三站演示叙事；练习站点、预测代理、非官方结算和 `curtailment_status=not_measured` 必须在界面/讲解中明示。

### 接口/阻塞记录

| 日期 | 提出者 → 接收者 | 需求或阻塞 | 决策/状态 |
|---|---|---|---|
| 2026-10-14 | W2 → 集成负责人 | E4-2 需要 E4-1 `energy_floor_kwh` 已提交基线及模型哈希 | 已解决：`82ba72c` / SHA-256 `603de45ca089b13665abd91fe2f58aa12e576909dfcba2ffb481cff2aedd13a4`；真实集成测试见 `76399f0` |

## 10. 评审会待办

1. 确认当前未提交改动归属，并建立 W1/W2/W3 独立 worktree；
2. 确认 W1/W2/W3 负责人和交接节奏；
3. 确认远程采集机访问、远程部署和 HA 写入的授权责任人与执行窗口；
4. 确认 F1/F2 正式数字发布前的评审人。
