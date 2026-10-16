# 策略对比里程碑计划（Strategy Comparison Milestone Plan）

版本：2026-10-17 定稿。本计划由「MILP 现状 + 拟引入梯度下降与负载预测模型」的需求驱动，目标是在**可复现的离线试验场**中对多种策略进行公平对比，并把策略模块解耦以便替换与选择。

## 0. 核心架构结论：嵌合还是选其一？

**不是二选一，而是三层架构，层间关系不同：**

| 层 | 回答的问题 | 现状代码锚点 | 计划新增 |
|---|---|---|---|
| 预测层 Forecast | 未来 24h 的 PV/负载会怎样 | `src/energy_lab/forecast.py`（`ForecastModel`，HistGB，已存在但**决策链未使用**）；`src/energy_lab/api_policy.py` `build_api_power_path`（PV=天气标定，**负载=当前值持久化**） | 负载预测模型 |
| 优化层 Optimizer | 给定预测+电价，本 15min 充/放/闲置多少瓦 | `src/energy_lab/rolling.py` `optimize_window`（唯一，`solver="milp"` 硬编码于 `shadow.py:119`、`contest_replay.py:108`） | 梯度下降求解器 |
| 元决策层 Meta | 本轮用哪个优化器/策略 | 不存在（硬编码 milp） | LLM 定期选择 + 规则兜底 |

- **预测层 ↔ 优化层：嵌合（级联）**。负载预测的输出喂给优化器；MILP 与梯度下降消费同一份预测，不冲突。
- **优化层内部（MILP vs 梯度下降）：选其一/对比**。同一 `Optimizer` 接口下的两个实现，是真正需要对比实验裁决的位置。
- **LLM 元决策：第三层裁判**，在优化器之间做最优选择，输出可审计理由。

一句话：负载预测是「给优化器喂更好的输入」，梯度下降是「换一个求解器」，LLM 是「在上面当裁判选人」，三者可以同时存在。

## 1. 已确认决策基线

1. **梯度下降定位：优化层的替代调度求解器**（与 MILP 同接口，对比/按场景选择）。
2. **离线试验场粒度：15 分钟**（比赛真实节奏，device_time tick 粒度）。
3. **LLM 触发频率：每窗口/每天定期选 + 规则兜底**（单 tick 不调 LLM，避免 poll=5 延迟预算问题）。

## 2. 现状关键差距

1. **策略未解耦**：优化器是裸函数，调用点硬编码 `solver="milp"`，无 Strategy/Optimizer 接口。
2. **预测模型离线**：`ForecastModel`（`fit_shared_model` 等）已训练但 `make_candidate` 不使用（走 persistence）。
3. **离线试验场不完整**：
   - 现有 replay（`scripts/online/run_contest_strategy_replay.py` + `src/energy_lab/contest_replay.py`）是 **168h、小时粒度**（`step_hours=1`）、基于 training CSV，非「比赛抓取的真实负载/电价序列」。
   - 在线是 **15 分钟粒度**（`step_hours=0.25`），步长不一致。
   - 抓取侧 `scripts/online/collect_online_history.py` **不含电价**（`include_prices=False`）；电价由 `run_local_mock.py` 单独落盘 `price_series.jsonl`。**无统一的比赛全程回放数据集格式**。
4. 梯度下降优化器、LLM 元决策：完全空白。

## 3. 里程碑总览与依赖

```
即刻启动: 抓取器(补电价+标准格式) ─────────────┐
                                                ├─> 阶段1(回放引擎) ─> 阶段2(A/B) ─┬─> 阶段3(预测) ──┐
阶段0: 策略解耦(纯代码, 无依赖) ─────────────────┘                                   └─> 阶段4(梯度下降) ├─> 阶段5(LLM) ─> 阶段6(收敛)
```

- 阶段 0 与抓取器**完全并行**（一个纯代码、一个纯数据）。
- 阶段 3/4 依赖阶段 2 的 A/B 脚手架（同一评估口径）。
- 阶段 5 依赖阶段 2（多个可对比策略）+ 最好有阶段 3/4 成果。

## 4. 各阶段交付物与验收

### 即刻启动 — 比赛数据抓取器（时效性最高）

别人正在跑模拟比赛，数据有时效性，必须现在接住。

- **改动**：`collect_online_history.py` 补上电价采集（当前 `include_prices=False`），输出统一为「15min 状态序列 + 小时电价」的标准回放格式；复用其已有的去重/心跳/多节点分片机制。
- **数据落盘**：`data/replay/<epoch>/`。
- **回放数据集 schema（草案）**：
  - 每 tick 一行：`device_time`、`site`、`soc`、`pv_w`、`load_w`、`import_price`、`export_price`、（可选）`weather` 字段。
  - 元数据 `manifest.json`：站点清单、赛程 epoch、初始 SOC、结算规则、采集版本、数据 SHA-256。
  - 电价小时粒度，15min tick 映射到所属小时。
- **验收**：一个完整 epoch 抓取后，数据集可独立加载、无断档（device_time 连续 15min 步进）、电价覆盖全程。

### 阶段 0 — 策略解耦（Strategy 抽象，纯代码）

- 定义两个接口：
  - `ForecastProvider`：`persistence` / `api_power_path` / `load_model`（新）→ 产出 `pp/ll` frame。
  - `Optimizer`：`milp` / `gradient_descent`（新）→ 产出 plan。
- `make_candidate`（`shadow.py`）改为组合二者。
- **验收（回归）**：MILP+persistence 路径行为与现状**逐字节一致**；`tests/test_price_anchor.py`、`tests/test_run_live_chain.py` 全绿。

### 阶段 1 — 15min 回放引擎

- 实现因果 replay 引擎：每 tick 喂「当前状态 + 当前可见电价窗口」给策略，动作作用于**真实负载/PV**（来自数据集），更新 SOC，绝不使用未来信息。
- 评估层复用 `contest_replay.execute_command / window_metrics / aggregate_sites`，步长 1h→15min。
- **验收**：用回放数据集重放 MILP，结果与真实比赛 audit 对齐（同输入下同决策）。

### 阶段 2 — A/B 对比脚手架

- 新增 `run_strategy_benchmark.py` + 策略注册表：一份数据集、一组策略并排跑，产出统一指标。
- 指标：`settled_saving_percent`、throughput、fallback 率、求解时间分布。
- **验收**：MILP vs 基线（self-consumption / persistence）出可比报告。

### 阶段 3 — 负载预测接入（预测层）

- 用回放数据集训练/微调 `forecast.py` 的负载模型，接入 `ForecastProvider`，替换「负载持久化」。
- **验收**：「负载持久化 MILP」vs「负载预测 MILP」在试验场上量化预测增益。

### 阶段 4 — 梯度下降优化器（优化层）

- 用 autodiff（torch/jax）解同一 SOP 调度问题，作为第二个 `Optimizer` 实现。
- ⚠️ **核心技术风险**：SOP 带二进制变量（充/放互斥、导入/导出互斥），不可微。先做可行性 spike，候选方案：
  1. 二元松弛为连续 + 正则化/投影；
  2. 可微 penalty-Lagrangian 软化约束为损失；
  3. softmax 门控离散决策、连续部分用梯度。
- **验收**：试验场上 MILP vs 梯度下降（解质量 vs 求解时间）。

### 阶段 5 — LLM 元决策（Meta 层）

- 特征输入：电价波动、SOC、预测不确定性、时间预算、各策略近期表现。
- LLM 定期（窗口/每天）选策略，输出可审计理由；**默认 MILP 兜底**，切换需「不比默认差」的验证机制。
- **验收**：LLM 选择 vs 单策略最优 vs 默认 MILP 三方对比。

### 阶段 6 — 持续抓取 + 结论收敛

- 对比实验进行中继续抓更多比赛数据集，扩充试验场多样性，汇总最终结论。

## 5. 两个提前想清楚的点

1. **「最优选择」判据**：以 **settled saving（利润）为主指标**，同时硬约束「求解时间必须 fit poll 预算」——梯度下降再快，若解质量差则无意义；MILP 再准，若超时也白搭。LLM 切换验证围绕同一判据。
2. **梯度下降约束可行性**：阶段 4 必须先 spike 确认「可微地满足 SOP 互斥约束」在质量上追得上 MILP，否则退化为「梯度下降仅在连续松弛下辅助 warm-start MILP」。

## 6. 与现有里程碑的关系

本计划不替代既有 O1–O9 在线流程里程碑（`docs/milestones/ONLINE_FLOW_MILESTONE.md`）；它是**策略层的独立对比实验里程碑**，产出的是「哪个优化器/预测/元决策组合在离线试验场上最优」的结论，供后续在线切换参考。抓取器复用 B4 collector 基础设施（`collect_online_history.py`）。
