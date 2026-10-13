# 后续实验与比赛接入检查（2026-10-10）

> 架构口径已更新：以 [SYSTEM_ARCHITECTURE_GUIDELINE.md](SYSTEM_ARCHITECTURE_GUIDELINE.md) 为 baseline。API 价格/天气和 HA 实时状态是主策略输入；预测模型与 168 小时历史仅为辅助校准，不再是策略启动门槛。此前关于“先积累 168 小时再启用线上策略”的表述按本条修正。

`src/energy_lab/system_model.py` 已新增纯函数物理模型：功率平衡、SOC 递推、PV-辐照度比例校准、负载分位数区间和功率残差诊断；当前仅完成单元测试，尚未接入 HA 写入路径。批准的中间产物清理因当前执行策略拒绝删除，目标目录仍保留，不能将其记录为已清理。

本轮按“压力闭环 → 自用基线 → 站点留出与参数稳定性 → 比赛接口检查”推进。前三阶段为内部离线实验；随后新增了真实环境机只读 shadow-run。没有下发 HA 控制，所有经济结果均不是官方成绩。重新完整核对 SOP 后，已将设备时钟、价格/天气公开 API、HA 唯一控制边界、0%~100% SOC 规则、充电侧 0.95 损耗、期末库存结算和模拟/正式比赛历史接口纳入基线。

## 1. 连续压力实验

最终产物：`outputs/stress_closed_loop_v2/summary.json`、`trace.csv`、`plans.csv`、`console.log`、`process_status.jsonl`。

8 个场景共执行 408 步，审计 9,807 行未来计划，检查非负功率、上下限、充放电与购售电互斥、SOC 递推、能量平衡和目标成本复算。执行只取第一步；计划首步与实际执行另外核对。不是仅检查执行结果中单个 flow 字段。

| 场景 | 步数 | 观察结果 |
|---|---:|---|
| 满电且持续负外送价格 | 48 | 全部 hold，但外送仍造成 48 EUR 损失；没有弃光控制 |
| 接近满电 | 48 | 按剩余容量执行，能量守恒 |
| 接近软件 SOC 保底 | 48 | 从 11% 降至 10%，不再透支 |
| 同时刻售电价高于购电价 | 48 | 36 次最优、12 次真实超时，回退保持物理约束 |
| 注入一次求解超时 | 48 | 正常→超时实际执行回退→下一步恢复优化 |
| 注入一次损坏预测 | 48 | 当前观测有效，回退自用→下一步恢复优化 |
| 初始 SOC 0%、5 分钟步长 | 24 | 前 3 次真实不可行，逐步充电恢复，随后正常；没有伪造初始有效 SOC |
| 15 分钟合成数据 | 96 | 96 次最优，能量记账随步长正确缩放 |

买电价始终为正。价格倒挂是超出 SOP 常规正买卖价差的故障压力输入，不代表比赛存在补贴或允许无限套利。该场景真实求解 P95 约 5 秒，已触及单次上限；三站在线不能默认接受每站各等待 5 秒，需要整体截止时间与降级策略。

求解器功率可有约 `0.000219 W` 的数值误差。最终完整计划容差：功率与电网平衡 0.001 W，能量平衡 `1e-6 kWh`，首步 SOC `1e-5` 个百分点；目标精度与求解超时没有放宽。实际执行能量残差最大约 `1.8e-15 kWh`。完整日志保留了调试断言失败和最终退出码 0。

预测错误回退逻辑位于 `src/energy_lab/decision.py`，与离线回测共用；`_policy_step` 新增显式时间长度。当前观测本身无效时拒绝生成决策，不以缺失观测假造功率。此处并不证明现有在线程序已经使用该路径。

## 2. 更强的固定自用基线

产物：`outputs/self_consumption_100site/`。复用已审计 MILP 轨迹，只新增自用策略，逐站核对输入 SHA-256、时间范围、初始 SOC、容量、功率、效率、软件保底和成本参数。132,600 行三策略轨迹均通过独立物理与财务复算。

自用策略只读取当前光伏和负载：有余量就充电，有缺口就放电，容量/功率不足时剩余部分由电网承担。不读取未来电价；满电时允许光伏余量外送，绝不称为“零外送”。双方初始 SOC 50%、软件保底 10%、期末估值 0.10 EUR/kWh、损耗代理 0.02 EUR/kWh。官方设备允许 0% SOC，本次是相同内部约束下的对照，不是官方评分基线精确复刻。

| 100 站点聚合 | 自用策略 | MILP |
|---|---:|---:|
| 运行成本 / EUR | -5.92 | -1312.29 |
| 库存校正成本 / EUR | -95.91 | -1326.29 |
| 吞吐 / kWh | 22518.79 | 34349.97 |
| 库存校正并含损耗代理成本 / EUR | 354.46 | -639.29 |

MILP 相对自用的运行成本节省 1306.37 EUR，库存校正后节省 1230.38 EUR，再计入损耗后节省 **993.76 EUR**；100/100 站点改善，最低 3.99 EUR，中位 9.62 EUR。相对旧规则的 1940.51 EUR 不再作为唯一效果证据。吞吐仍更高，不能据此声称寿命延长。

## 3. 站点留出与参数稳定性

协议和结果目录：`outputs/holdout80_20_frozen/`。在读取结果前写入 `protocol.json`，按站点编号能否被 5 整除固定留出 20 站；共享模型只用另外 80 站前 80% 时间训练。评估 20 站最后 442 小时，允许使用这些站点截至当前时刻的观测作为滞后输入，不用它们拟合模型。检查训练最晚时刻早于评估最早时刻。

预先固定三组：原参数（SOC 10%、损耗 0.02）、SOC 20%（损耗仍 0.02）、损耗 0.05（SOC 仍 10%）；各 20 站，共 60 任务。所有组共用同一个模型和输入，使用 8 进程、每进程 1 线程，24 小时窗口、小时步长、persistence 天气。每组各自采用同 SOC 下限的自用基线，同时额外用统一 0.02 损耗系数报告结果，避免把损耗估值变化与动作变化混为一谈。

最终任务已完成，真实退出码为 0；独立审计 `outputs/holdout80_20_frozen/audit_report.json` 通过。共 79,560 行、26,520 个 MILP 决策，全部 optimal、回退 0，最大能量残差 `1.78e-15 kWh`。模型重新拟合哈希、保存模型和 20 个留出站点首个预测窗口逐项一致，三组参数的预测指标完全一致。

| 20 站点留出聚合 | SOC 10% / 损耗 0.02 | SOC 20% / 损耗 0.02 | SOC 10% / 损耗 0.05 |
|---|---:|---:|---:|
| 相对自用运行成本节省 / EUR | 267.34 | 247.46 | 194.36 |
| 相对自用库存校正节省 / EUR | 251.98 | 234.02 | 179.00 |
| 相对自用含损耗节省 / EUR | **204.21** | 190.96 | 162.53 |
| 含损耗为正的站点 | 20/20 | 20/20 | 20/20 |
| 单站最小节省 / EUR | 4.52 | 3.88 | 3.51 |
| 单站节省中位数 / EUR | 10.63 | 10.13 | 8.75 |

三组按各自设置的损耗系数报告时，相对自用均保持 20/20 站点为正；其中 SOC 20% 组使用同样提高到 20% 的自用基线。若统一用 0.02 EUR/kWh 评价损耗，第三组节省为 172.41 EUR，仍低于原参数的 204.21 EUR，其电池吞吐则由 7092.27 降至 5032.72 kWh，约减少 29%。这显示了收益与吞吐的权衡，并非寿命已经得到验证。该稳定性只支持候选参数的敏感性判断，不支持冻结比赛参数。

总运行时间约 738.4 秒（12.3 分钟），使用 8 个进程、每进程 1 个预测线程；这不是与旧 100 站点任务相同工作量的性能比较。训练数据截止 2026-09-14 13:00，评估自 14:00 开始。小时平均光伏/负载仍被视为当前时刻已知，未来电价窗口假设可得；这些粗粒度假设与真实 tick 反馈的差距尚未消除。

这些站点和时段此前均曾被用于实验，不能称为“从未看过的测试集”。本轮证明的是重新训练后对训练外站点的回顾性泛化表现。真正的新时段/新站点确认仍需新的数据或后续模拟赛。不会根据本轮结果静默挑选最佳参数或冻结比赛配置。

## 4. 比赛接口与执行可行性

本地参赛 SOP 第 5.1、5.2、6、7.2、7.3 节提供设备时钟、小时价格/天气、指令和 tick 语义。`review_interface_readiness.py` 对 44,200 个历史 MILP 动作做了纯本地映射检查，结果在 `outputs/interface_readiness/`，网络请求和控制写入均为 0。随后 `run_online_policy_replay.py` 使用 SOP 约束重新求解 100 站点各 3 个真实功率窗口，结果在 `outputs/online_policy_replay/`。

| 动作分类 | 数量 | 结论 |
|---|---:|---|
| 充电功率指令 | 11044 | 可形成候选设定值，仍需设备回读 |
| 放电转为并网点目标 | 18536 | 使用目标 = 电池放电 + 光伏 − 负载，不能原样发送电池功率 |
| hold/零功率 | 12413 | 候选为第三方 charge=0；需设备回读确认，当前 writer 会改为自用 |
| 仅覆盖部分负载缺口的放电 | 1471 | 需要负并网点目标，当前指令范围无法精确表达 |
| 换算后并网点目标超过 5 kW | 736 | 无法直接表达，不能简单截断后沿用原收益 |

合计 **2,207/44,200（约 4.99%）** 动作无法直接映射。其余 41,993 个也只是候选映射，不代表全部已经通过设备验证。最优计划的可执行约束必须前置到优化模型，或单独评估安全投影后的实际轨迹。零并网点目标也可能要求电池供给全部负载缺口，不能等同于电池待机。

新的 SOP 约束求解器在 300 个窗口、7,200 个计划步中实现 0 个不可映射步；原始归档轨迹的 41,993 个可映射动作通过了模拟回读，仍是软件模拟，不是设备验收。约束模式保守地排除了设备饱和模式和无法表达的部分负载缺口，不应把这 7,200 步的已实现动作直接替换成全量经济结果。

已新增纯函数 `interface_contract.py`，验证跨日按完整时间戳对齐、拒绝缺失明日窗口、功率映射与不可表达动作。没有接入现有写入路径。

当前在线代码的实际差距：`run_live.py` 仍调用规则 dispatch，没有载入预测模型/MILP；价格按小时索引且可能复用当日；只检查时间字段存在，没有实现真正的超龄判断和下发后 tick 回读；`HAClient.set_dispatch` 把 hold/零值映射为自用，与离线 hold 不同。新增 `run_shadow_dry_run.py` 只生成候选动作、检查状态新鲜度和模型字段兼容性，不发送控制；新增 `verify_readback` 要求命令已发送、进入下一设备 tick、模式/方向/设定值和功率回读均匹配。此前 README 关于“已实现过期停止和回读确认”的表述需要收回，不能当成现有能力。

SOP 的示例加速 ×180 下，5 秒真实 tick = 15 分钟设备时间。因此“24 小时窗口／15 分钟时间步”可继续作为待验证的设计，“5 分钟设备时间重规划”不能直接作为该环境的可实现承诺。应先读取实际倍率并按设备 tick 重规划；小时数据展开成 15 分钟不能当成新增真实观测。

`run_shadow_dry_run.py` 已在环境机执行只读 shadow-run，产物为 `outputs/shadow_remote_final_20261010/`：3/3 站点生成候选，`writes_performed=0`，没有调用 HA 服务写接口。价格响应的 `start` UTC 时间戳和 today/tomorrow 已保存；HA `device_time` 虽无时区后缀，但与 API 同一设备时钟，API 时间戳提供了对齐坐标。API-first 功率路径已接入 shadow：天气字段完整时按 API 小时覆盖 15 分钟路径；没有在线 PV 校准参数时明确使用当前 PV/负载 persistence，不加载不兼容的历史模型。

2026-10-11 再次使用当前本地配置完成动态只读验证，产物为 `outputs/shadow_api_clock_inferred_20261011_v2/`：三站均 `candidate_only`、求解器 `optimal`、`writes_performed=0`，API 天气快照和 HA 实时状态已进入 API-first 路径；未传入人工时区参数，程序从 API 时间戳推导设备时钟坐标；当前仍因缺少在线 PV 校准参数而采用显式 persistence fallback。

### 本轮新增：API 兼容模型与观测缓存

已新增 `online_contract.py`、`capture_observations.py`、`check_online_readiness.py` 和 `online_inference.py`。天气契约只使用 API 与训练数据中同名且可验证的 `temperature_c`、`cloud_cover_percent`；`solar_irradiance_wm2` 没有被强行映射到历史 `shortwave_radiation_wm2`。三站只读快照可写入按站点 JSONL 观测缓存，缓存不包含令牌；HA 的 `device_time` 虽可能没有时区后缀，但 API 的带时区时间戳提供了设备时钟的对齐坐标，不使用 HA/本机墙钟来判断设备小时。

基于 100 站点历史数据训练的 API 兼容模型保存在 `models/online_api/`，PV/负载验证 MAE 分别约 199.7 W / 333.7 W，均优于同一留出集的 24 小时朴素基线。这是离线模型筛选结果，不是线上或官方成绩。模型推理受 168 个连续小时观测门禁保护，并按小时聚合 15 分钟设备 tick；当前三站各只有 1 个小时点，`outputs/online_readiness_20261011.json` 明确为 `ready=false`，因此没有启用线上模型。

观测缓存现已同时保存 `pv/load/soc/temperature_c/cloud_cover_percent`，天气点按设备小时严格对齐；缺少天气点或历史小时不连续时直接拒绝进入推理，不使用 0 填充或未经验证的字段别名。

训练集边界已进一步核对：`forecast_data/` 是 100 个离线站点、每站 2,208 个小时点（2026-07-03 至 2026-10-02），可用于模型训练和 warm-start 管线测试；它不含当前三台比赛设备的身份映射。SOP 规定 `/api/contest/history?site=N` 只在模拟/正式比赛窗口启动后提供当前窗口前 7 天历史；赛前调用返回 404 不能推导为接口永久不存在。已用 `forecast_data/1.csv` 的 168 小时历史离线生成 24 小时预测，证明模型管线可用，但不将该结果标记为线上 readiness 或设备历史。

新增 `collect_online_history.py` 作为周期性只读采集器，默认每小时抓取一次，支持 `--iterations 0` 持续运行；它只调用状态和天气读取接口。使用 `--snapshot` 可离线回放，不会产生网络或 HA 控制写入。

本轮新增的 `outputs/online_policy_replay_rerun_20261011/summary.json` 重新验证了 SOP 约束与模拟回读：44,200 条归档动作中 41,993 条可模拟确认，300 个受约束窗口共 7,200 个计划步全部可映射，网络请求和控制写入均为 0。该结果只证明软件契约和理想化回读路径一致，不代表真实设备接受指令，也不产生新的线上经济收益结论。

## 当前下一步

只读路径的约束优化、跨日对齐、状态新鲜度、API-first 快照接入和环境机连通性已经落实；实际控制回读仍只有模拟测试。下一步是从 HA 观测建立 PV 校准与电网-电池-负载参数，增加 API 动态变化/缺失/延迟回归场景，再以新设备约束重跑收益实验。`solar_irradiance_wm2` 不应未经验证就当成训练 CSV 的全部辐射特征，也不应将缺失的 24 个天气字段一律填 0 后宣称训练模型已接通。

本次远程最终检查耗时约 4.20 秒，包含认证与快照抓取，不能据此承诺三站持续控制循环满足每轮 5 秒。新鲜度保持最大 15 秒；未来墙钟偏差只用于传输新鲜度校验，超过范围拒绝。没有用设备的加速时钟与真实墙钟直接相减。策略小时/档位只按 `device_time` 或 API 当前档位判定；墙钟仅用于判断 HTTP/HA 快照是否新鲜和维持秒级轮询。

## 复核与复现

本轮 41 项单元/回归测试通过，包含真实回退后恢复、子小时能量记账、跨日缺数拒绝、SOP 约束计划、状态新鲜度和回读条件检查，以及天气契约、观测缓存、小时聚合、周期采集器和线上 readiness 门禁。原留出审计已保存在 `outputs/holdout80_20_frozen/audit_report.json`；`review_followup_results.py` 严格检查当时源码哈希，当前新增 SOP/接口代码后重新运行会拒绝源码不一致，不能把该拒绝解释为旧轨迹损坏，也不能静默更新旧协议指纹。

重新执行实验必须指定新输出目录，避免覆盖已完成结果：

```powershell
python run_logged_experiment.py --script run_stress_scenarios.py --output outputs/stress_rerun
python run_logged_experiment.py --script run_self_consumption_comparison.py --output outputs/self_use_rerun
python run_logged_experiment.py --script run_holdout_validation.py --output outputs/holdout_rerun --workers 8
python run_online_policy_replay.py --output outputs/replay_rerun
python run_shadow_dry_run.py --config local_online_config.json --output outputs/shadow_rerun
python -m unittest discover -s tests -v
```

留出专用 runner 在每个站点完成后保存检查点，但尚未实现续跑入口；这与已有 `run_strategy_comparison.py --resume` 不同。shadow-run 会保存完整价格/天气/状态快照，凭据和令牌不写入快照。本轮没有提交 Git、上传模型或删除原有实验产物。

进程处理记录：结束了旧的本地 `parity_parallel` Python 进程（PID 16852）。检查时未找到其完成摘要或站点检查点，因此不能认定该任务已经完成，也未将它计入本轮成功结果；“已完成的残留进程”这一先前表述没有得到产物证据支持。未删除日志或已完成实验的结果文件。

## API-first 策略 replay（2026-10-11）

为落实“API 优先、预测辅助”的新口径，新增 `run_api_first_replay.py`，在不访问网络、不调用 HA 的条件下完成了 5 站点 pilot 和 100 站点全量 replay。每站评估 168 个小时，滚动优化视域 24 小时，使用 SOP MILP、0%~100% SOC 和充电侧 0.95 效率。策略决策只使用当前 PV/负载持久化、当前 SOC 和未来价格窗口；训练 CSV 中的未来 PV/负载只用于事后模拟状态推进及 oracle 诊断，未泄漏到策略输入。

全量产物：`outputs/api_first_replay_100site_20261011/`。

- 100 站、16,800 个决策步全部为 `optimal`，策略 SOC 无越界。
- 聚合 API-first 成本为 `-99.49 EUR`，自发自用基线为 `168.90 EUR`，代理实验节省 `268.39 EUR`。
- 61/100 站点相对基线节省，39/100 站点亏损；站点节省中位数 `5.43 EUR`，第 10 百分位 `-9.81 EUR`。
- 使用未来真实 PV/负载的 oracle 诊断节省 `552.23 EUR`，与 API-first 的差距说明当前持久化假设在部分站点会造成明显误调度。

该结果是 100 个练习站点上的机制和泛化基线，不是比赛成绩，也不是 `contest/history` 的替代品。下一实验应使用真实 API 天气字段和 HA 观测建立在线 PV-辐照度校准，并对未来负载使用区间/风险约束；在此之前不应把当前策略直接切换为 live。

## API-first 严格执行口径 replay（2026-10-11）

在上一轮基线之上，新增了三项约束并重新运行 100 站点：

- 评估时刻之前 168 小时的 `shortwave_radiation_wm2` 与 PV 仅作为离线天气字段代理，使用中位比例做 PV 校准；结果明确标记为 `shortwave_radiation_proxy`，不等同于线上 API 的 `solar_irradiance_wm2` 字段映射。
- 未来负载不看未来真实值，使用当前负载与过去 168 小时 P75 的较大值，作为保守负载输入；SOC 内部下限设为 10%，比赛规则的可用范围仍是 0%~100%。
- 期末库存按该 168 小时评估窗口平均买电价结算，分别报告 operating cost 和 settled cost。

全量产物：`outputs/api_first_replay_calibrated_100site_20261011/`。

结果：100 站、16,800 步全部求解 `optimal`，聚合运行期 API-first 成本 `2.78 EUR`，自用基线 `168.90 EUR`，运行期节省 `166.13 EUR`；加入期末库存结算后 API-first settled cost `-138.07 EUR`，基线 `-70.64 EUR`，settled 节省 `67.43 EUR`。本轮共发现 92 个动作在实际 PV/负载下不能表达为 SOP `discharge` 并网点目标，均显式拒绝并转为 `hold`，没有静默截断；因此该结果比上一轮更接近真实执行约束，但仍不能作为 live 就绪证明。

该失败模式说明：当前滚动优化仍以电池侧 `discharge_w` 为决策变量，执行层再尝试映射到并网点目标；实际设备的 `discharge` 设定值是并网点输出目标，未来必须把 SOP 指令空间直接写进优化模型，或在计划阶段使用预测 PV/负载验证可表达性，而不能等到下发前处理。天气代理和 P75 负载也仍需替换为真实 API/HA 数据后再做最终泛化评估。

### 修正：按计划并网目标执行的最终 replay

上一段 92 个不可表达动作的结果是中间版本：它错误地用实际 PV/负载重新检查电池侧放电功率。现已修正为：计划阶段将电池侧放电转换为 SOP 并网点目标；执行阶段保持该并网目标，并依据实际 PV/负载反解实际电池放电功率。这样既遵守 `discharge=并网点输出目标`，又正确模拟预测误差。中间产物 `outputs/api_first_replay_calibrated_100site_20261011/` 不再作为最终结果。

最终产物：`outputs/api_first_replay_final_100site_20261011/`。

- 100 站、16,800 个决策步全部 `optimal`；0 个不可表达动作；0 个求解失败/回退步。
- 运行期 API-first 成本 `-17.51 EUR`，自用基线 `168.90 EUR`，运行期节省 `186.41 EUR`。
- 按评估窗口平均买电价结算期末库存后，API-first settled cost `-172.06 EUR`，基线 `-70.64 EUR`，settled 节省 `101.42 EUR`。
- 运行期节省为正的站点 60/100，settled 节省为正的站点 59/100；settled 站点节省中位数 `3.46 EUR`，第 10 百分位 `-10.34 EUR`。
- 天气字段仍使用离线 `shortwave_radiation_wm2` 作为 API `solar_irradiance_wm2` 的代理，负载使用过去 168 小时 P75 与当前负载的较大值；这两个假设必须在真实 API/HA 数据上重新校准。

本结果是当前最接近 SOP 执行语义的离线代理基线，但不是官方成绩，也不代表可以直接 live。下一步应将同样的并网目标执行逻辑接入真实 shadow，并用比赛窗口的 `contest/history` 和 HA 观测替换离线字段代理。
## Read-only shadow verification (2026-10-11 13:43 Asia/Shanghai)

The current local configuration was used for a fresh network read-only run. The
artifact is `outputs/shadow_readonly_20261011_134256/`.

- All three stations produced `candidate_only` actions; all solver statuses were
  `optimal`; `writes_performed=0`.
- Prices contained complete 24-point `today` and `tomorrow` windows with UTC
  timestamps. HA `device_time` was `2027-01-24 03:00:00` for all three stations.
- The weather API supplied 24 hourly points per day. Each point contained a
  timestamp plus four weather variables: `temperature_c`,
  `cloud_cover_percent`, `solar_irradiance_wm2`, and `condition`; the response
  also included daily summary fields. The run used current-PV/current-load
  persistence because no online PV calibration was available. The historical
  full-weather models were not loaded because their feature contract does not
  match the forecast API.
- `/api/contest/history` returned HTTP 404 for sites 1, 2, and 3. This is
  recorded as `window_not_started`, not treated as missing or fabricated data.
- Read-only snapshots were appended to `data/online_history/site_{1,2,3}.jsonl`.
  The cache currently contains five samples per site spanning four device-hours,
  with large gaps between captures. `outputs/online_readiness_latest.json`
  correctly reports `ready=false`; these samples are not 168 continuous hours.

This confirms the safety gates and API/HA clock alignment. It does not authorize
HA writes, live control, or an official score.

### Accelerated-clock collector cadence check (2026-10-12)

`collect_online_history.py` now targets a 5-second wall-clock cadence by
default, omits the price request, fetches independent weather endpoints
concurrently, and reports cycle duration plus each station's `device_time`.
Transient capture errors are reported by type and do not terminate a continuous
collection run. A real read-only probe completed in 3.65 seconds; two additional
cycles completed in 2.67 and 2.12 seconds. Between those two cycles all stations
advanced by one 15-minute device tick. No HA writes occurred. This is a short
cadence check, not a continuous-history result; the 168-hour auxiliary-model
gate remains closed. Full test suite: 47 passed.

### 100-cycle collector audit (2026-10-12)

The bounded 100-cycle run completed with no remaining collector process and no
HA writes. One cycle (iteration 46) reported a `ValueError` and was caught so
collection continued; because station files are written sequentially, that
cycle left a partial capture for site 1 only. The run therefore contains 100
recent rows for site 1 and 99 for sites 2 and 3.

After deduplication, the run covers approximately 25 device-hours. Most steps
are 15 minutes, but there are several 30-minute skips and one site-specific
45-minute skip. The three stations are not point-by-point aligned. The
readiness report remains `ready=false` with only 30 unique device-hours and
must not be used to enable model inference. Before collecting a clean 168-hour
window, the collector should stage all three normalized observations in memory
and append them atomically only when the complete station set passes validation.

### Staged-write collector retest (2026-10-12)

The collector was rerun for 100 bounded cycles into a separate cache,
`data/online_history_retest_20261012/`, with its log in
`outputs/collector_retest_20261012.jsonl`. The updated collector validates and
normalizes every station observation before appending any of the three, and the
whole run exited with code 0.

- 100/100 cycles returned three observations; zero capture or validation
  errors. Each station cache has exactly 100 raw rows, so no partial-station
  cycle was written.
- Two cycles exceeded the 5-second target (iterations 70 and 91); the maximum
  observed request cycle was 6.476 seconds. This is a wall-clock cadence
  overrun, not a device-clock measurement.
- Device time advanced mostly in 15-minute increments, but the three stations
  were not synchronized in iterations 92-100. Their rows therefore cannot be
  treated as a point-aligned multi-station sample set. Per-station deduplicated
  data contains 98, 100, and 100 observations for sites 1-3, respectively;
  the difference is due to repeated device timestamps, not missing writes.
- Each site spans only about 25 device-hours and has 26 unique device-hour
  buckets. Readiness was rerun against this isolated cache and correctly
  returned `ready=false`: the required 168 continuous hourly observations are
  absent for all sites. The trained models are API-weather compatible, but that
  does not waive the history gate.

Conclusion: the staged validation fix addresses the prior partial-write defect,
but it does not solve device-clock synchronization between stations. Keep this
cache as a collector audit only. Before using multi-station observations for
joint analysis, add an explicit cross-station device-time alignment check and
discard or separately mark cycles whose station clocks disagree. Continue
read-only collection only; do not enable model inference or HA writes from this
retest.

### Cross-station time-offset review (2026-10-12)

A follow-up review corrected the temporary decision to reject a whole collector
batch when station `device_time` values differ. The 168-hour auxiliary history
and its readiness gate are per station, and each station's observations can be
validated on its own device clock. Cross-station identity of timestamps is not
a prerequisite for collecting those separate histories. Rejecting all sites
would unnecessarily discard otherwise valid observations. The collector
therefore keeps the all-sites validation-before-write behavior, records a
`device_times_aligned` diagnostic, and does not reject a valid batch solely due
to a cross-site offset.

The three-cycle probe under the temporary gate found offsets of one 15-minute
tick in all three captures; this confirms the observed offset but does not by
itself determine whether the simulator clocks are intentionally phase-shifted
or asynchronously advanced. Since `/api/states` supplies the stations in one
HA state-list response, the collector is not making separate per-station HA
requests. For per-site readiness, retain observations independently and check
each station for its own gaps/continuity. Any future joint cross-site analysis
must explicitly join on device timestamps and report unmatched rows rather
than assuming the stations share a tick.

The temporary strict-alignment rejection has been removed. Regression tests
now verify both that a station validation failure writes no partial batch and
that valid observations from differently timed stations are retained. Full
suite: 49 tests. A follow-up 3-cycle read-only capture retained all 9 station
observations; the per-cycle alignment diagnostic was `true`, `true`, then
`false` (one station one tick behind). No capture errors occurred and each
cycle completed within the 5-second target. This validates the collection
behavior, not the root cause or long-term frequency of the offset.

Readiness against this short cache is still `ready=false` for all three sites
(one unique device-hour per site). The API-weather feature contract remains
compatible, but the auxiliary 168-hour history gate is intentionally unmet.

### Device-time offset diagnosis (2026-10-12)

A five-snapshot read-only probe fetched one HA `/api/states` list per sample
and did not issue per-station requests. Site 1 and site 3 advanced together;
site 2 stayed exactly one 15-minute device tick behind in all five samples.
Each station's `device_time_last_changed` advanced on its own roughly
5-second wall-clock cadence, with fixed phase offsets of a few seconds. A later
snapshot showed all three stations aligned again. This pattern is consistent
with independently scheduled Modbus/HA update loops (or per-device simulator
tick phases): the state-list response is a point-in-time read of values that
may have been updated at different instants, not a barrier synchronized to one
global simulator tick.

The mismatch is therefore a sampling-phase effect at a device-tick boundary,
not evidence that the collector is reading stations sequentially, and not a
timezone conversion error. The collector already reads one HA state list and
uses each station's own `device_time` as its clock. Strategy decisions remain
per-station; any joint report must join on exact device timestamps and mark
unmatched rows. A bounded retry-until-aligned diagnostic could be added for
joint snapshots, but it must not block independent station history collection.

### Bounded device-clock alignment audit (2026-10-12)

Added `audit_device_time_alignment.py`, a bounded, read-only diagnostic that
fetches one HA state-list snapshot per sample and records each site's
`device_time` and `device_time_last_changed`. It does not request prices or
write to HA. The 12-sample run is saved at
`outputs/device_time_alignment_audit_20261012.json`.

- 10/12 snapshots had all three stations on the same device tick; 2/12 had one
  station 15 device minutes behind the latest station.
- The offset was transient: all stations aligned again on subsequent samples.
- Per-station `device_time_last_changed` updates retained stable phase offsets
  of roughly 2-3 real seconds, consistent with independent HA/device update
  schedules and occasional snapshots straddling a tick boundary.
- All 12 requests returned successfully. One request took 5.062 seconds and
  the other 11 took 1.814-3.158 seconds. HA writes: zero; price requests: zero.

This strengthens the sampling-phase explanation but does not prove the
simulator's internal scheduling mechanism. The safe operational rule remains:
keep per-site histories independently, use each station's own device clock,
and exact-time join only for cross-station analysis. Do not wait for alignment
as a prerequisite for ordinary per-site collection.

### Continuous per-site history collection started (2026-10-12)

Started an unbounded read-only collector in a fresh cache to build the
168-hour per-site readiness window:

- Cache: `data/online_history_continuous_20261012/`
- Standard output: `outputs/collector_continuous_20261012.log`
- Standard error: `outputs/collector_continuous_20261012.err.log`
- Wall-clock target interval: 5 seconds; no price endpoint and no HA writes.
- At the first health check: process PID 86892 was running, 16 cycles had
  completed, and each site had 16 observations. All cycles had three valid
  station observations with no capture errors. One sample briefly had site 2
  one tick behind; its per-site observation was retained as intended.

At the documented x180 example rate, collecting 168 device-hours requires
about 56 wall-clock minutes. This run is still in progress and readiness must
not be evaluated as complete until each station independently has 168
continuous hourly buckets. Recheck process health, per-site continuity and
readiness after the window has elapsed; do not use this cache for model
inference before the gate passes.

### Per-site 168-hour readiness passed (2026-10-12)

The continuous cache was rechecked after extended collection. All three sites
now pass the auxiliary history gate and the API-weather model contract:

- Site 1: 6,206 deduplicated observations, 1,578 device-hour buckets.
- Site 2: 6,236 deduplicated observations, 1,578 device-hour buckets.
- Site 3: 6,208 deduplicated observations, 1,578 device-hour buckets.
- Each site's latest 168 hourly buckets are continuous and include the required
  `temperature_c` and `cloud_cover_percent` fields.
- `outputs/readiness_continuous_20261012.json` reports `ready=true` for all
  sites and both shared models.

The cache was sufficient for the auxiliary model's lag/rolling features at the
time of the check and was archived as a reproducible baseline. This does not
replace API-first prices/weather or current HA state, and it does not authorize
HA writes or formal-contest execution. For live inference, the collector must
remain running in a separate `live_history` cache; stopping it makes the cache
tail stale as the accelerated device clock advances.

### Frozen-cache inference check (2026-10-12)

The collector was stopped at iteration 6402 and the cache was preserved as the
historical baseline. A read-only weather snapshot was then fetched and passed
to `run_online_inference_dry_run.py`. All three sites were correctly blocked
with `weather forecast does not cover the complete hourly horizon`: the frozen
cache ended at device time around 2027-06-30 04:00, while the fresh snapshot
was already around 2027-07-01 15:00. The API weather window began at the latter
date, so filling the gap would have violated the time contract.

This is an expected accelerated-clock lifecycle issue, not a model failure:
readiness is a historical gate at a particular cache tail, while live
inference additionally requires the cache tail and current API/HA device clock
to be contiguous. The dry-run produced no control actions and no HA writes.
The frozen cache remains valid for reproducible historical evaluation only. A
new current-window cache must be collected and maintained continuously before
claiming an online forecast is ready. This is a lifecycle requirement, not a
reason to block the API-first strategy itself: when the live model gate is not
ready, use the current API/HA fallback path.

### Correction: live cache must remain active (2026-10-12)

The previous wording that suggested stopping the collector after passing the
168-hour gate could be read as an online operating procedure. That was wrong
for the accelerated simulator. The 168-hour check is a point-in-time readiness
assessment; it does not make a frozen cache current. The production design now
distinguishes:

- `frozen_baseline`: immutable cache and reports for replay/audit;
- `live_history`: continuously appended, per-site observations used only when
  its latest 168-hour tail is both continuous and adjacent to the current HA
  `device_time`.

If the live tail falls behind or develops a gap, only the auxiliary forecast is
disabled. API-first prices/weather plus current HA PV/load/SOC remain the
primary strategy path, with no fabricated history and no HA write as a
workaround.

The gated runner now enforces this rule in code: before invoking the forecast
model it compares each station's current HA `device_time` with that station's
cache tail. A stale tail is reported as `model_unready` together with the
`api_first_current_pv_load_persistence` fallback. Replaying the stale frozen
cache reported `cache_tail_stale_by_35.00h` for all three sites, generated no
control actions and performed zero HA writes. The full regression suite passes
55 tests. The runner now distinguishes a short tail lag within a configurable
three-device-hour tolerance as `forecast_degraded`; longer gaps still disable
the model. This tolerance affects forecast quality only and never suppresses
the API-first strategy path.

### Operational priority correction (2026-10-12)

The original single `ready=true` gate was too strict for an accelerated live
loop: a transient collector error must not stop the strategy from producing
decisions. The operational tiers are now explicit:

- `full_168h`: at least 168 continuous device hours and a current cache tail;
- `degraded_minimum`: at least 24 continuous device hours, with a short tail
  lag (up to three device hours by default) allowed as a quality downgrade;
- `model_unready`: fewer than 24 continuous hours, a long tail gap, missing
  API weather coverage, or invalid observations.

In the last tier only the auxiliary PV/load model is disabled. The API-first
strategy continues with current HA PV/load/SOC and persistence or conservative
fallback inputs, and each model-unready site records its fallback explicitly.
This prioritizes a valid, safe strategy output over occasionally unavailable
prediction accuracy. It does not relax current-state, price-window,
control-safety or HA-write validation.

Implementation: `predict_online_power` now accepts an explicit minimum
history threshold, readiness reports `model_tier` per site, and the runner
distinguishes a tolerated short lag from a long gap. Full regression suite:
58 tests passed.

### Basic-continuity tolerance (2026-10-12)

The model gate was further relaxed to match the operational priority of always
producing a safe strategy output. Tolerant hourly reconstruction now preserves
the real time axis and exposes coverage metrics instead of shifting lag
positions when a sample is missing:

- default model window: at least 24 device hours;
- allowed missing coverage: at most 5% of the evaluated window;
- allowed largest single gap: at most 3 device hours;
- allowed cache-tail lag: at most 3 device hours, reported as degraded;
- beyond those limits: auxiliary model disabled, API-first persistence fallback
  remains active.

The full 168-hour, zero-gap path is still reported as `full_168h`; tolerated
losses are `forecast_degraded`, not a strategy failure. A missing sample is
represented as NaN on its actual hourly timestamp, never silently removed and
never replaced with a fabricated value. The implementation and regression
tests pass 59 cases. The current continuous cache still reports full quality:
all three sites have 1,603 hourly buckets, 100% coverage and zero-hour maximum
gap in `outputs/readiness_continuous_tiered_20261012.json`.

### Gap-tolerance fault-injection experiment (2026-10-12)

Ran `run_gap_tolerance_experiment.py` against the frozen three-site history.
The weather input was explicitly synthetic and the report is not scoreable; it
was used only to exercise the operational gate. Results are in
`outputs/gap_tolerance_experiment_20261012_v2/result.json` and were identical
in all three sites:

- Clean 168-hour window: `forecast_ready`, 100% coverage, 24 forecast points.
- Two isolated missing hours: `forecast_degraded`, 98.81% coverage, one-hour
  maximum gap, 24 forecast points still produced.
- Six-hour contiguous gap: `model_unready`, rejected as over tolerance, with
  explicit `api_first_current_pv_load_persistence` fallback.

This confirms the intended priority: small collection errors lower model
quality but do not interrupt forecast assembly; only materially unreliable
history disables the auxiliary model. The strategy output path remains
available in every case. The experiment used no HA writes.

### Live-history continuation and degraded forecast (2026-10-12)

The second live-history collection phase appended 110 read-only polling cycles
to `data/live_history_experiment_20261012/`. Every cycle except one completed
with three station observations; one cycle was rejected as a whole because the
short API weather window temporarily did not cover the observed device hour.
No HA control writes were performed.

The final cache contains 200, 208 and 195 raw deduplicated observations for
sites 1, 2 and 3, respectively, spanning 55, 55 and 56 device-hour buckets.
The latest 24-hour tolerant windows have 100% coverage and zero maximum gap.
Readiness therefore reports `ready=false` but
`degraded_model_usable=true`: all sites are in the `degraded_minimum` tier
because the full 168-hour auxiliary-history gate is not yet met.

The gated inference run is archived at
`outputs/live_history_inference_20261012_final/result.json`. It generated 24
forecast points per site with no control actions. Sites 1 and 2 were
`forecast_degraded` because their cache tails were two device-hours behind the
current API/HA device clock but within the configured three-hour tolerance;
site 3 was also `forecast_degraded` because it only met the 24-hour minimum
tier, despite a contiguous tail. The model remained usable in all three cases.

In parallel, the live API-first shadow run at
`outputs/shadow_api_first_live_history_20261012_final/result.json` produced a
candidate-only strategy for all three sites, each with `solver_status=optimal`,
`horizon_steps=96`, and `writes_performed=0`. The forecast mode explicitly
remained current-PV/current-load persistence fallback, confirming that
auxiliary-model degradation does not interrupt the primary strategy path.

This is a successful operational-continuity result, not an official contest
score. The next online milestone is to keep the collector active until each
site has a fresh continuous 168-hour tail, then rerun the same checks to
promote the model tier to `full_168h`.

### Full-tier live inference and clock-race correction (2026-10-13)

The continuously running collector reached a stable full-history state. The
readiness report `outputs/readiness_live_history_20261013.json` shows all three
sites with 1,259--1,261 device-hour buckets, 100% coverage, zero maximum gap,
compatible weather features, and `model_tier=full_168h`.

The first full-tier inference attempt exposed a race condition: while the
read-only API/HA snapshot was being fetched, the accelerated collector advanced
one device hour, so the cache tail was newer than the snapshot's `device_time`.
The runner now trims each station cache at the snapshot clock before building
features. It therefore never uses future observations, while retaining the
latest valid historical window.

After this correction, the automatic-tier run
`outputs/online_inference_auto_tier_20261013/result.json` completed for all
three sites. Each site selected the preferred 168-hour window, produced 24
finite non-negative hourly forecast points, had a contiguous tail, and was
reported as `forecast_ready`. The API-first shadow run
`outputs/shadow_api_first_full_20261013_v2/result.json` also produced three
`candidate_only` plans with `solver_status=optimal` and zero HA writes.

The online runner now attempts the 168-hour tier by default and falls back to
the 24-hour tolerant tier only when the full window is unavailable. New clock
boundary, tier-selection and gap-handling tests bring the full regression suite
to 66 passing tests. Forecast readiness and API-first strategy execution remain
separate: this run proves the auxiliary model's full-tier plumbing, while the
shadow strategy intentionally continues to use current PV/load persistence
until a calibrated forecast is explicitly integrated and compared.

### Paired persistence-vs-auxiliary strategy replay (2026-10-13)

To decide whether the full-tier model should enter the main strategy, a new
offline paired replay was run on sites 1--20 for a chronological 96-hour
holdout. Both arms received the same known price window, current observed
PV/load/SOC, and identical MILP/SOP constraints; the persistence arm held
current PV/load, while the auxiliary arm recursively predicted PV/load from the
frozen API-compatible model. Future realized PV/load were used only to score
execution after the first action. No network request or HA write was made.

Results are archived at
`outputs/persistence_aux_20site_96h_20261013/summary.json`. All 3,840 paired
decision steps solved successfully, with zero unrepresentable actions and all
SOC/power bounds satisfied. An independent trace audit passed for both the
observed-weather-proxy and weather-persistence sensitivity runs; the maximum
energy-balance residual was `4.97e-15 kWh`, and frozen model hashes were
unchanged. A two-site serial run matched the parallel traces exactly.

Aggregate diagnostic results for the observed-weather-proxy arm:

- PV one-step MAE: 328.46 W (persistence) vs 185.29 W (auxiliary), a 43.6%
  reduction.
- Load one-step MAE: 355.25 W vs 384.37 W, an 8.2% increase.
- Operating cost improvement: 3.89 EUR across 20 sites.
- Settled cost improvement: 44.93 EUR, of which approximately 41.04 EUR is
  due to the auxiliary arm ending with more battery inventory.
- After adding the configured throughput degradation cost, improvement is
  38.48 EUR.
- The auxiliary arm is better in settled cost for 20/20 sites, but only 9/20
  sites have lower operating cost before terminal inventory valuation.

The weather-persistence sensitivity run produced nearly the same aggregate
settled improvement (45.25 EUR), so this experiment does not establish that
future weather fields caused the gain. It is also not unseen-site validation:
the frozen shared models were trained using the first 80% of all 100 supplied
sites. The result supports keeping the auxiliary model as a guarded candidate,
not replacing API-first current-state persistence unconditionally. Before
production integration, run longer multi-window holdouts, report operating and
settled metrics separately, and require a conservative selection rule when
load uncertainty or terminal SOC effects erase the operating-cost benefit.

### 四臂消融与收益归因复核（2026-10-13）

已完成 `outputs/ablation_20site_96h_20261013/`：20 站、每站 96 小时，
分别测试 persistence、仅 PV 辅助、仅负载辅助、PV + 负载辅助。
四臂共 7,680 个决策步全部 optimal；独立逐步审计通过，SOC/功率边界、
能量平衡和冻结模型哈希通过。这里的审计只证明回放物理及账目一致，
不证明预测泛化、真实设备执行或官方得分。

下表均为相对 persistence 的 20 站合计改善，单位 EUR；正数为改善。
寿命成本按本实验设置的 0.02 EUR/kWh 吞吐量计算，不是已核实的官方扣分项。

| 指标 | 仅 PV 辅助 | 仅负载辅助 | PV + 负载辅助 |
| --- | ---: | ---: | ---: |
| 运行期电费改善 | 2.484 | -0.779 | 3.886 |
| 期末库存价值差贡献 | 37.939 | 3.206 | 41.042 |
| 结算成本改善 | 40.423 | 2.428 | 44.928 |
| 额外寿命成本 | 5.352 | -0.012 | 6.449 |
| 运行期改善减额外寿命成本 | -2.868 | -0.767 | -2.563 |
| 结算改善减额外寿命成本 | 35.072 | 2.439 | 38.479 |
| 运行期电费改善站数 | 9/20 | 8/20 | 9/20 |
| 结算成本改善站数 | 20/20 | 15/20 | 20/20 |

结果的正确解释：

1. **库存收益有效，但不能与运行期收益混称。** SOP 第 9.1 节明确按
   `saving_settled_percent` 排名，期末电量按整场平均购电价计价。
   因此约 91.4% 的辅助模型结算改善来自库存差，并不意味着它是虚假收益。
   同样，不能把 44.93 EUR 表述成已经节省的电费。当前比较对象是另一种
   MILP 输入方式，不是官方自发自用 baseline，也没有计算官方 K 分母。
2. **PV 是主要贡献来源，但没有证明必须使用机器学习。** 仅 PV 辅助已
   得到约 90% 的组合模型结算改善。persistence 把当前 PV 外推 24 小时，
   存在白天功率延续到夜间、夜间零功率延续到白天的结构性弱点。
   下一步应与 API 辐照度校准路径比较，而不是仅凭战胜此弱基线推广旧模型。
3. **负载模型不宜单独无条件接管，也不能直接认定应删除。** 一步负载
   MAE 从 355.25 W 升到 384.37 W，单独使用运行期亏 0.779 EUR；但在
   PV 辅助之上加入它，运行期再改善 1.402 EUR、结算再改善 4.505 EUR，
   多计寿命成本后两者增益分别约 0.305 和 3.408 EUR。预测误差与策略收益
   并非单调关系，存在输入组合及 SOC 轨迹交互。
4. **短期和远期误差应分别处理。** 本次全部回放点的负载 MAE 在
   6/12/23 小时为 596.74/570.44/571.78 W，低于 persistence 的
   751.52/765.56/686.27 W；这与一步误差变差可以同时成立。
   本次全量统计优先于此前抽样数值，不应以一步指标概括整条预测路径。
5. **损耗约束已有，但经济标定尚未完成。** 优化目标已含吞吐成本，不能
   说优化器完全忽略寿命损耗。组合模型相对基线多约 322.44 kWh 吞吐量，
   对应额外 6.45 EUR；需要区分官方结算目标与内部寿命风险指标，随后做
   系数敏感性验证，而不是以任一人工系数直接决定比赛方案。

### 期末估值敏感性及求解时间限制复核（2026-10-13）

`--terminal-price 0` 只取消 **每次滚动优化视域末端** 的库存奖励，
不取消 **回放结束后的结算计价**。两者已在命令行帮助中明确区分。
因此该实验不是“官方不计期末电量”的假设，也不是建议生产策略将库存价值设零。

- 5 秒求解上限：`outputs/persistence_aux_terminal0_20site_96h_20261013/`，
  persistence 出现 12 步 limit_reached，全部回退 hold；辅助臂没有超时。
- 30 秒求解上限：`outputs/persistence_aux_terminal0_20site_96h_20261013_t30/`，
  persistence 仍有 1 步 limit_reached（站点 19，设备时间 2026-09-17 08:00），
  辅助臂没有超时。全套 20 站耗时约 140.3 秒，物理和账目审计通过。
- 30 秒组相对改善为：运行期 15.872 EUR，库存差 21.505 EUR，结算
  37.377 EUR；扣除额外寿命成本后分别为 7.949 和 29.455 EUR。
  运行期改善站数为 17/20，但仍包含一处安全回退，不能声明纯最优对照。

这表明优化器末端估值会显著改变收益的组成；并不表明取消库存价值更适合
比赛——本组结算改善反而低于默认组，而且尚有求解时限干扰。
默认四臂全部最优，应作为本轮主要证据；terminal0 两组仅保留为敏感性证据。
审计报告现显式记录 `nonoptimal_steps_by_arm` 和 `all_steps_optimal`，
避免将账目审计通过误读成每步均最优。

### 下一轮主线实验优先级

1. 对齐官方评估：完整 168 设备小时、自发自用基线、K 分母、结算收益及
   运行期成本分列；内部寿命成本另列。滚动视域末端不等于真实比赛终点，
   策略必须知道窗口剩余时间，不能读取尚未公开的未来价格估值。
2. 验证 API 辐照度 + 历史 HA PV 校准主线，与 persistence 及本轮 PV-only
   诊断臂比较。校准只用决策前可见数据；历史观测天气不是归档 API 预报，
   离线代理与真实只读 API 快照结果分开报告。继续保留模型不可用时的回退。
3. 用负载区间/候选场景和预期净收益阈值验证是否值得改变动作；不得因一组
   结果就将点预测、风险系数或阈值固定为比赛配置。
4. 在多个未调参窗口及更多站点重复测试，补上负售电价、API 缺失/延迟、
   真实设备 tick 和指令回读验证。旧共享模型见过所有站点训练段，且 clip_max
   使用过完整序列最大值，这批结果仍属于探索性诊断，不能称作无泄漏泛化认证。

本轮未重训模型、未改变在线主策略、未下发 HA 控制，未删除旧结果。
新增离线求解时限参数默认仍为 5 秒，30 秒只用于上述诊断重跑，
不能直接照搬到加速比赛控制循环。回归测试：74 passed。

### 168 小时评分口径、辐照度及负载风险实验（2026-10-13）

新增 `run_contest_strategy_replay.py`、`src/energy_lab/contest_replay.py` 与
`audit_contest_strategy_replay.py`。本轮不训练或加载共享机器学习模型；
100 站练习集仅用作策略回放，本次固定取前 20 站，逐站使用过去 168 小时
做 PV 比例及负载分位统计。尚未接入真实 API 预报快照或执行 HA 控制。

#### 评估口径与旧产物隔离

- 每场严格 168 小时，起始 SOC 统一假设 50%，物理边界 0%~100%，14 kWh、
  5 kW，充电效率 0.95。50% 是配对实验假设，不宣称由本次 SOP 核实。
- 自发自用 baseline 与各候选策略面对同一实际 PV、负载和电价。
- `K = Σ(实际负载 kW × 1h × 购电价)`；配对结算改善等于运行期电费差
  加期末库存价值差，库存按本场完整 168 小时平均购电价计价。
- `saving_settled_percent_estimate = 100 × Σ结算改善 / ΣK`，不平均站点百分比。
  这是官方公式风格的离线估算，尚未核对官方模拟器库存成本台账，不是正式得分。
- 优化器视域为 `min(24, 比赛剩余小时)`；末端估价只看本场已经历的价格
  与当前可见的未来价格窗口，不提前读取尚未公开的全场价格。
- 内部吞吐寿命成本固定 0.02 EUR/kWh，进入优化但不从估算排名百分比中扣除；
  含寿命成本的收益另列。该系数及 0.02 EUR 风险门槛均未按两周结果调参。

检查旧 `run_api_first_replay.py` 时确认：其自发自用和动作执行路径调用
`Battery.step` 截断 SOC 后，仍可能用原请求功率计算并网电量，在满/空电时
产生虚假能流。因此旧 `outputs/api_first_replay_*20261011/` 的收益和 baseline
结果不再作为可靠财务证据（包括曾标注 final 的版本），保留作历史记录，
不与本轮结果直接比较。此问题不等于此前通过 `_policy_step` 审计的其他
实验全部失效；本轮统一复用能量限幅后的实际功率并独立复算。

另一个执行口径修正：根据当前 HA 客户端，`hold` 及求解失败回退执行
`self_consumption`，不再假设电池功率一定为零。新轨迹同时记录请求动作、
并网目标和实际电池功率。但优化器内部未来 hold 仍表示零电池功率，
其规划模型与执行模式的差异尚未消除；“全部 optimal”只证明数学模型求解完成，
不表示整个真实闭环已达到最优。

#### 顺序与产物

1. 阶段 1：2 站 × 168h，自发自用与 persistence，产物
   `outputs/contest_replay_stage1_2site_20261013/`，672 行审计通过。
2. 阶段 2：5 站加入辐照度路径，产物
   `outputs/contest_replay_stage2_5site_20261013/`，2,520 行审计通过。
3. 阶段 3：5 站加入负载上分位及风险门槛，产物
   `outputs/contest_replay_stage3_5site_20261013/`，4,200 行审计通过。
4. 冻结参数后，20 站运行两个不重叠的 168 小时评估窗口，各站每场独立重置
   SOC；第二场历史允许包含第一场已发生的原始观测，不使用第一场策略 SOC。
   两场不是两组独立未见站点，也不能当作统计显著性证明。

正式样本产物（“正式样本”不表示正式比赛）：

- `outputs/contest_replay_stage3_20site_20261013/`：2026-09-14 14:00 至
  2026-09-21 13:00，K 合计 634.1714 EUR。
- `outputs/contest_replay_stage3_20site_window2_20261013/`：2026-09-21 14:00 至
  2026-09-28 13:00，K 合计 531.0258 EUR。

所有时间为 CSV 原始设备小时坐标，不由本机墙钟转换或补猜时区。

#### 两个窗口的结果

下表百分比均相对自发自用，为按 K 加权的离线结算估算：

| 策略 | 窗口 1 | 窗口 2 | 正收益站数：窗口 1 / 2 |
| --- | ---: | ---: | ---: |
| 当前 PV / 负载持久化 | 9.476% | 4.042% | 20 / 17 |
| 辐照度校准 PV + 当前负载 | 14.612% | 6.441% | 20 / 17 |
| 辐照度 PV + 负载上分位 | 13.510% | 4.898% | 20 / 16 |
| 辐照度 PV + 负载风险门槛 | 14.723% | 6.513% | 20 / 18 |

辐照度臂的运行期改善分别为 75.474 / 16.051 EUR，库存差贡献为
17.190 / 18.153 EUR，结算改善为 92.664 / 34.204 EUR。因此第一周改善主要
来自运行期，第二周期末库存占比明显增大。两周各优化臂期末 SOC 均为 100%，
而自发自用不全为 100%；优化臂之间的收益差并非库存差造成。

风险门槛相比辐照度 + 当前负载，仅多 0.706 / 0.383 EUR 结算改善，约
0.111 / 0.072 个百分点；扣除额外寿命成本后增量约 0.011 / 0.448 EUR。
第一周 3,360 次 guarded 决策中接受上分位计划 322 次；经济增益很小，
尚不能证明增加此层复杂度值得。第二周辐照度臂在站点 12、17、20 为负，
guarded 仅使站点 20 转正，不能称为“不亏损保证”。

负载风险臂的含义：从决策前一周按相同设备小时估计 P10/P50/P90 场景，
候选负载取 `max(当前负载, 同小时 P75)`，第一步始终覆盖为当前真实负载。
仅当候选计划在三种固定未来场景下均比当前负载计划好超过 0.02 EUR 才替换。
这是整条计划的开环压力门槛，执行时仍只落第一步，既不是概率校准区间，
也不是保证多步重规划收益的鲁棒 MPC。直接使用负载上分位两周都更差，
不能把“负载更保守”自动等同于“策略更安全或更赚钱”。

#### 验证结果与下一步边界

两场主样本共 33,600 行轨迹、全部求解最优、无求解回退；独立审计复算了
输入哈希、指令执行、SOC 连续性、功率限幅、能量/并网平衡、电费、库存、K
及合并百分比，最大能量残差约 4.97e-15 kWh。全套测试 **89 passed**，
新增覆盖了满/空电、hold 自用、负售电价、禁止未来目标输入、按设备小时
负载场景、比赛终点截断及连续 168 步求解超时后仍输出安全回退。

本轮建议优先保留“辐照度 PV + 当前负载”作为进一步验证主线，而不是默认
启用负载上界或风险门槛；这只是离线候选判断，没有更换线上策略。
下一步先消除优化器 hold 与执行 self_consumption 的差异，再对接真实只读
API 辐照度及 HA PV 校准，验证预测误差/刷新/设备 tick 与延迟，之后再扩站。

天气输入目前仍是历史 shortwave 实测值代理，不能声称已经验证真实 API
天气预报质量。两个窗口均无负售电价自然样本；单元测试覆盖负价记账，不等于
已完成负价长周期策略压力测试。每小时均值当作当前读数也不覆盖小时内变化。
SOP 对常见分数量级的描述不是上限，但本轮较高结果不应用作比赛成绩承诺。

复现命令（输出目录必须不存在）：

```powershell
python run_contest_strategy_replay.py --output outputs/contest_replay_repeat_w1 --stage 3 --max-sites 20 --workers 4
python run_contest_strategy_replay.py --output outputs/contest_replay_repeat_w2 --stage 3 --max-sites 20 --offset 168 --workers 4
python audit_contest_strategy_replay.py outputs/contest_replay_repeat_w1 outputs/contest_replay_repeat_w2
python -m pytest -q
```

### Hold 语义隔离与模型一致性验证（2026-10-13）

成功计划中的零电池流量不能再与安全回退共用一个隐含解释。本轮新增显式
`--hold-mode self_consumption|zero_flow`，只影响成功优化计划的 idle 时段：

- 自发自用 baseline 和求解失败回退始终 self_consumption，不受诊断参数影响；
- self_consumption 模式保留旧执行行为，仍与优化器零流量 idle 有差异；
- zero_flow 模式只在离线模拟/只读候选中把 idle 映射为 charge=0 W。
  discharge=0 W 是零外送目标，可能放电供负载，不能替代它。
- `shadow.make_candidate` 可显式选择 `zero_charge_unverified`，输出
  `hardware_verified=false`、`writes_performed=0`；默认旧行为及 HA 客户端
  未修改。未把此映射开放为自动 live 控制，尚需受控真实回读才能确认。

新增一致性测试：在已知相同未来功率下，zero-charge 候选的执行 SOC、并网
功率和含损耗/末端库存的目标值逐步吻合优化计划。这里只解决了离线命令模型
的一致性，不表示设备已经验证，也不表示 self_consumption 分支的 MILP
已经重建。失败回退即使有连续 168 步超时仍输出 self_consumption。

有效成对实验：`outputs/contest_hold_pair_self_v2_20261013/` 与
`outputs/contest_hold_pair_zero_v2_20261013/`，均阶段 2、5 站 × 168h，
只有成功计划 idle 语义不同。5 站自发自用 baseline 的逐行轨迹完全一致。
两组各 2,520 行通过独立审计，全部求解最优；均直接记录 command_flow，
没有从旧格式推断指令。估算分数仍不是官方成绩：

| 策略 | idle 执行自发自用 | idle 执行零流量候选 |
| --- | ---: | ---: |
| 当前 PV / 负载持久化 | 6.238% | -2.115% |
| 辐照度 PV + 当前负载 | 11.167% | 9.204% |

辐照度臂运行期改善由 15.000 降至 11.215 EUR，吞吐量由 811.02 降至
605.33 kWh。两个语义都是在各自执行假设下进行模拟，并非数字高者就更正确。
该对照说明旧收益包含自发自用在 idle 时段的实际贡献，不能当作优化器本身
已预测到的收益，也不能将它外推到 charge=0 W 的另一套执行方案。

中间目录 `outputs/contest_hold_zero_5site_20261013/` 不可用：早期参数误影响
baseline，且风险场景的不可表达指令导致中断，已添加 INVALID_RUN.md。
后续固定 baseline 后重跑，失败目录保留但不纳入结论。风险场景映射失败时
现在只拒绝 guarded 候选，保留主计划，不中断整场实验；不静默修改指令目标。

### 真实 API 辐照度 / HA PV 只读试采与冻结预报检验（2026-10-13）

已新增 `run_irradiance_shadow.py` 与 `analyze_irradiance_shadow.py`。只使用既有
认证和白名单读接口，不调用 HA service 或启动比赛。采样以设备时间配对，
真实墙钟只负责网络新鲜度与试采间隔。

旧 `live_history_experiment_20261012` 缓存没有 solar_irradiance_wm2，不能被
事后补成 API 辐照度记录。本轮为 `collect_online_history.py` 增加该字段及来源，
并让缓存读取器兼容它；没有重启现有旧采集进程，因此其已加载的旧代码不会
自动保存新字段。新增试采单独存储完整脱敏快照和 API/HA 配对，不改变旧缓存。

试采记录：

1. `outputs/irradiance_live_readonly_20261013/`：12 轮、36 个站点配对，处于
   设备夜间，全部辐照度为零。36 次站点决策均产出持久化只读候选，未强行拟合。
2. `outputs/irradiance_live_daylight_20261013/`：继承上述 36 对，只读再采 24 轮，
   新增 72 对、合计 108 对；本次约从设备 2027-10-25 09:00 至 20:45。
   三站设备时刻分别保留，不强制伪造一致时钟。72 次均产出 candidate_only，
   单轮最长约 3.49 秒；10 秒试采间隔不是正式 5 秒控制周期达标证明。

从第 6 轮起，每站有至少 3 个历史有效日照小时，试验性启用比例校准。
按设备小时取配对中位值，每小时等权；只使用更早的 device_time 和 received_at，
不将当前/未来 PV 用来拟合当前预测，首个策略时间步仍用当前 HA 实测。
独立分析对 57 次校准重算系数，因果时间边界审计通过。3 小时门槛只用于
试验管线，明确不是生产质量验收标准。

除 concurrent 配对误差外，本轮新增真正的冻结预报检验：保存较早时刻的
API today/tomorrow 响应及当时校准系数，预测未来 1/2/4 个设备小时，再与
后来 HA 完全相同时间戳的 PV 对照。不得用后来更新的天气响应重算旧预测；
不插值错位点；同站同起始 tick 不重复计数。下面是未来 1 小时的结果：

| 站点 | 配对样本数 | 辐照度校准 MAE | 当前 PV 持久化 MAE |
| --- | ---: | ---: | ---: |
| 1 | 13 | 1085.84 W | 386.38 W |
| 2 | 17 | 136.47 W | 167.88 W |
| 3 | 17 | 958.38 W | 672.59 W |

未来 4 小时的 MAE（校准 / 持久化）为站点 1：1038.91 / 1392.20 W（10 点）、
站点 2：103.95 / 114.64 W（11 点）、站点 3：923.82 / 2762.09 W（11 点）。
站点 1 的 1 小时有明显正偏差，短样本比例标定不稳，不能宣称准确性通过。
并发配对 MAE 为 796.58 / 119.18 / 796.86 W（各 19 点），与未来误差单列。
详细记录见试采目录 `analysis.json`；这是单日、相关小样本，不用于显著性
或长期泛化结论，也不是线上策略收益。

对同一冻结预报又做了纯诊断收缩，不改变任何候选控制：
`PV_mix = α × PV_irradiance + (1−α) × PV_persistence`。全站合并 MAE 如下，
仅用于判断保守混合是否值得继续，不能当成按时距自动切换规则：

| 预测提前量 | α=0 | α=0.25 | α=0.5 | α=0.75 | α=1 |
| --- | ---: | ---: | ---: | ---: | ---: |
| 1 小时 | 410.87 W | 456.40 W | 525.41 W | 609.80 W | 696.35 W |
| 2 小时 | 732.69 W | 727.99 W | 728.39 W | 732.00 W | 738.82 W |
| 4 小时 | 1423.94 W | 1229.13 W | 1038.50 W | 854.22 W | 677.96 W |

当前小样本建议仅作为后续候选：短时距保持 persistence；更远时距才测试
较高 α。由于 1 小时和 4 小时的最优方向相反，不能用一个固定 α 覆盖全天，
也不能直接把 MAE 改善换算成策略收益。后续应在更多昼夜和天气日重复冻结
预报验证，并以动作收益/损耗门槛而非预测误差单独决定是否切换。

结论与下一步：

- API-first 的优先级不变。问题出在“辐照度转换为本站 PV”的少量样本校准，
  不能据此认为 API 不可靠或用旧预测模型取代 API。
- 当前实测仍是短时锚点；未来可检验按时距的校准修正和收益门槛，不能仅凭
  本次几个点就固定 1h/4h 分界或自动启用。校准差/不可用不得阻塞策略输出。
- 下一轮先扩大跨昼夜的只读冻结预报样本并按站点、时距、偏差评估，再验证
  有限幅修正的 PV 路径；尚未授权或执行 charge=0 W 的真实写入验证。
- 本轮只读试采两批均已正常结束，没有新增无限后台任务；旧只读采集器未停止。
  回归测试 **100 passed**。包括显式 idle 命令、失败回退独立、整条计划能量
  一致性、因果校准、冻结预报评分、篡改系数拒绝与缺历史持续输出测试。

### 主机重启后恢复采集与独立冻结预报批次（2026-10-13 13:05）

重启后进程检查未发现 Python 采集器；旧日志停在本机 02:30:38，设备时间为
2027-10-27 02:00。只读探测恢复时设备已到 2028-01-13，不能把停机缺口伪装成
连续历史，也没有用旧缓存补造辐照度。原结果、配置和旧缓存均保留。

- 新历史缓存：`data/live_history_post_reboot_20261013/`，已确认包含
  `solar_irradiance_wm2` 和 `irradiance_source`。前面包含一次探测和 8 轮有限采集，
  随后在同一新缓存中启动后台持续采集；分析时应按设备时间去重并保留真实缺口。
- 后台采集器：启动时间本机 12:57:06，PID 66988（仅本次进程标识，不可跨重启复用），
  5 秒目标轮询，`--iterations 0`；日志在 `outputs/collector_post_reboot_20261013/`。
  截至本机 13:05:52 检查仍存活；104 轮中 1 轮跨日天气/HA 小时不匹配，下一轮恢复，
  8 轮耗时超过 5 秒。采集器会继续运行，但未配置开机自启。
- 独立试采：`outputs/irradiance_post_reboot_20261013/`，24 轮、20 秒轮询，已正常结束，
  72 条三站配对，没有继承旧批次校准数据；设备时间约从 2028-01-13 09:30 至
  2028-01-14 08:30（站点 3 末点 08:45）。这是跨午夜约 23 小时样本，不是多日验收。
- 两条进程均仅使用既有只读接口和认证接口，未执行 HA 控制写入、比赛启动或重置。

冻结预报仍要求完全相同的目标设备时间，不插值补齐错位；63 次历史校准系数重新计算
通过因果审计。下表为同一批次中三站配对点合并的 MAE，不能直接解释成策略收益：

| 提前量 | 配对数 | 当前 PV 持久化 MAE | 辐照度校准 MAE |
| --- | ---: | ---: | ---: |
| 1 小时 | 47 | 157.09 W | 308.01 W |
| 2 小时 | 49 | 320.47 W | 293.21 W |
| 4 小时 | 45 | 544.22 W | 196.41 W |

结果方向与前批小样本相近，但包含夜间低功率时段、站点匹配数不同；不能把跨批次 MAE
降低当作模型已经改进，也不能直接冻结 1/4 小时切换或混合系数。结果详见该目录
`analysis.json`；没有修改线上 PV 路径或训练共享预测模型。

**可用性问题：**72 次站点候选请求中 65 次 `candidate_only`，7 次 `limit_reached`
（站点 1 一次、站点 3 六次）；完整三站轮次平均 4.61 秒、最长 8.81 秒。这里的
`shadow.make_candidate` 在非 optimal 时返回 `candidate=None` 和
`no_new_command_requires_supervised_policy`，并非自动自发自用候选。此前“失败回退
固定 self_consumption”的结论适用于离线 contest replay，不能扩大到此只读入口。
只读试采未发指令，故没有产生真实执行影响；但当前候选生成可用率 90.28%，不能声称
已经满足持续输出策略或 5 秒控制周期。下一项优先验证有限时限下的可执行回退，区分
真实 SOC 边界与可选软件保底；候选及回退验证仍只读，真实写入需另外授权。

本轮回归测试 100 passed；两个有效 hold 配对回放各 2,520 行重新独立审计通过，
最大能量残差约 4.97e-15 kWh。上述回放复核不是本轮真实 API 闭环收益。

补充诊断：`outputs/irradiance_post_reboot_20261013/solver_budget_sweep.json`
对 24 张冻结快照调用 `shadow.evaluate_snapshot`，按记录的接收时间检查新鲜度，
依次扫描 0.2/0.5/1/2/5 秒单站时限；没有网络请求或 HA 写入。这是**未注入历史
校准系数的持久化路径**，默认 legacy_hold，不是上述 irradiance pilot 的同路径重跑。
只测本地计算时间、不含网络，不可与实采 7 次超时或 8.81 秒端到端时延直接比较。

| 单站求解时限 | 候选成功 / 72 | 超时 | 平均三站本地计算 | 最长三站本地计算 |
| ---: | ---: | ---: | ---: | ---: |
| 0.2 s | 67 | 5 | 0.153 s | 0.293 s |
| 0.5 s | 67 | 5 | 0.222 s | 0.592 s |
| 1.0 s | 68 | 4 | 0.304 s | 1.091 s |
| 2.0 s | 68 | 4 | 0.469 s | 2.093 s |
| 5.0 s | 68 | 4 | 0.971 s | 5.093 s |

从 1 秒增至 5 秒，本次成功数未增加。该单次诊断不用于选定生产预算；应继续做
相同校准输入、相同 idle 语义下的预算和显式回退配对验证，优先确保持续输出可用
策略，再评估收益。短预算本身不保证安全，尚未实现或验证新的线上回退。

### C1 limit_reached 病例库与根因分类（2026-10-13）

线 C 第一项：对只读试采 7 次 `limit_reached`（轮次 4/5/9/10/11/12 站点 3、
轮次 18 站点 1）建立病例库。工具 `analyze_limit_reached_cases.py`（只读、无网络），
产物 `outputs/irradiance_post_reboot_20261013/limit_reached_case_library.json`。
三种视角：shadow 路径多预算重放、live 路径（因果校准逐轮累积）精确复现、
scipy 原始结果探针（gap/节点数/LP 松弛，仅诊断用途）。

**被排除的假设：**

- SOC 边界：7 次失败 SOC 全部 100%，但 65 次成功对照也全部 100%（试采期间三站
  满电静置，soc_last_changed=2026-10-08）。SOC 不是区分特征。
- 价格形态：失败与对照的价格窗口极差/负价/平台段几乎一致（同一日价格表），
  不是区分特征。
- 机器争抢或预算不足：本地精确复现全部 7 次失败（live 路径、2 秒预算、
  因果校准）；0.2→120 秒预算扫描成功数不变。不是环境问题。

**根因分类：**

- R-A 认证失败（7/7 共同根因）：`mip_rel_gap=1e-9` 过严。HiGHS 在 0.02~0.12 秒
  内找到的可行 incumbent 与 300 秒长跑的 incumbent 目标值完全一致；全部求解时间
  消耗在把 0.2~0.3% 的整数间隙证明到 1e-9。300 秒 / 49~64 万 B&B 节点后 gap 仍
  0.18~0.27%。LP 松弛 0.008 秒可解且界紧（距 incumbent 约 0.3%）。
- R-B 校准诱导的实例退化（5/7 附加因素）：轮次 4/5/9/10/11 在未校准 persistence
  路径下 0.1 秒可解；注入历史校准系数后 PV 曲线形态改变、间隙结构变差，2 秒预算
  不足以完成认证。轮次 12/18 两条路径均难（属纯 R-A 硬实例）。
- 已确认非不可行：超时返回中 scipy 仍给出可行 incumbent（fun 已赋值），
  满足全部 SOP 互斥与 SOC 边界约束；当前 `optimize_window` 将其直接丢弃。

**对 C2 的输入（诊断性证据，非生产决策）：** 诊断 gap=1e-2 下 7/7 在 0.02~0.09
秒返回，incumbent gap 0.12~0.81%，决策物理合理（净功率为正时 idle、为负时放电
精确匹配缺口）。这指向 C2 的回退分级：超时但 incumbent 存在 → 返回 incumbent 并
标注 mip_gap（非最优归因）；无 incumbent → 显式自用回退（Q1 已决策）。生产 gap
策略属 C4 冻结事项，须遵守 R8（不得以放宽精度冒充最优可用率）。

### C2 有限时限可执行回退实现与验证（2026-10-13）

按 C1 根因实现分级回退，全程未改动 `mip_rel_gap=1e-9`（R8）：

- `rolling.py`：MILP 超时（status=1）时提取 HiGHS 返回的 incumbent，但只在
  **独立复检**通过后才接受——变量边界（含 SOC 上下限）、全部等式/不等式约束行
  （含互斥与 SOP 契约）、整数性，容差 1e-6；复检不过则维持原空 idle 占位计划。
  `RollingPlan` 新增 `suboptimal` / `mip_gap` 字段，status 语义不变
  （`limit_reached` 仍是求解器结果，suboptimal 标记 incumbent 被采用）。
- `shadow.make_candidate`：suboptimal 计划照常产出 candidate，附加
  `suboptimal_incumbent=true`、`mip_gap`、`attribution=optimizer-suboptimal-incumbent`；
  无 incumbent 时返回 `candidate=None`、`fallback=explicit_self_use_mode`、
  `attribution=non-optimizer-attributed`（Q1 决策落地，替换旧
  `no_new_command_requires_supervised_policy` 标签）。这是与 A2 `execute_candidate`
  决策表对接的字段契约。
- `explainability_evidence.py` 的 rolling.py 哈希 pin 同步更新
  （`0acf526f`，MODEL_VERSION `shared-milp-0acf526f-cf1`）；sidecar 仅在
  optimal 时导出，suboptimal 候选不导出证据，E 线契约不变。
- 离线 `decide()` 路径**未改动**：规则回退与压力测试行为保持不变（刻意控制
  变更面，离线口径属 A5/F 线）。

**验证（只读，无网络无写入）：**

- 72 次病例重放（`run_limit_fallback_verification.py`，冻结快照 + 因果校准
  精确 live 路径、单站 2 秒预算）：候选产出 **72/72**（65 optimal + 7 suboptimal
  incumbent），显式自用回退 0 次（7 次失败全部有 incumbent 可用），
  suboptimal mip_gap 最大 0.76%、均值 0.40%；对全部 72 张 96 步计划做独立物理
  审计（互斥、功率/SOC 边界、SOP 放电禁进口、能量动力学、首步电网平衡），
  **越界 0 次**。产物 `outputs/limit_fallback_verification_20261013_v2/`。
- 压力场景重跑（新目录 `outputs/stress_closed_loop_post_c2_20261013/`）：
  8 场景 408 步通过，与改动前基线逐项一致（注入超时时 decide 仍走规则回退，
  闭环物理约束保持）。
- 回归测试 **170 passed**（新增 `tests/test_suboptimal_incumbent.py` 10 例：
  incumbent 接受、SOC/功率/互斥/整数性/NaN 五类拒绝、无 incumbent 占位、
  shadow 两分支契约）。

**不能声称的部分：** 可用率提升目前只在 72 次病例重放上验证（真实在线争抢环境
未复测）；suboptimal incumbent 的收益影响未评估（属 F2 冻结参数全量回测范围）；
生产 gap/预算参数未冻结（C4）；回退路径真实设备写入仍未授权（线 B）。

### C3 真实 SOC 边界与软件保底区分及 soc_min=0 冻结建议（2026-10-14）

**两类边界的定义与来源：**

- 真实 SOC 边界 = 设备/比赛物理边界 `0%~100%`。[SOP_RECONCILIATION.md](SOP_RECONCILIATION.md)
  §1 规定"比赛 SOC 空间固定为 `0%~100%`，充电上限、放电下限和备用预留不可修改"；
  设备自用模式只认此边界（HOLD_SEMANTICS_DESIGN §8.1 B4）。该边界不由我方配置，
  是回退路径唯一可依赖的保底。
- 软件保底 = 优化器 `soc_min` 参数（rolling.py 的 energy 变量下界
  `capacity*soc_min/100`），仅存在于规划层，默认 0。SOP_RECONCILIATION §2 已定调：
  "shadow 默认使用比赛完整 0%~100% SOC 边界；内部保守保底只能显式作为实验参数"。

**软件保底 >0 进入比赛配置的三类代价（不得外推的理由）：**

1. 结构性 infeasible 风险。初始 SOC 低于保底、且单步充能上限（5 kW × 步长 × 0.95）
   不足以补回时，MILP 直接无解。压力场景 `infeasible_initial_soc_recovery` 实证：
   初始 0%、保底 10%（1.4 kWh）、5 分钟步长（最多充 0.396 kWh），前 3 步 infeasible
   走规则回退。反之 `soc_min=0` 时恒等 idle 计划（全程零流量、电网 big-M 平衡）
   恒可行——SOC 下界永不可能致 infeasible，这是候选可用率的结构性保证，
   与 C2 的回退分级互补（C2 解决超时，本条款排除边界致无解）。
2. 回退语义冲突。自用回退只认设备 0~100%：若优化器按 `soc_min=10` 规划，回退步
   设备可放到 10% 以下，下一轮重规划初始 SOC 低于保底，又触发第 1 类 infeasible，
   形成"回退→越软件边界→再无解"的循环。Q1 条件 2 已决策冻结 `soc_min=0`，
   从根上消除该循环（R4 关闭的依据）。
3. 收益口径。保底 10% 削去 1.4 kWh 可用吞吐；排名按三站合并
   `saving_settled_percent`，SOP 全量程是收益最大化口径。任何 `soc_min>0` 的
   收益数字（如离线实验的保底臂）不得外推为比赛口径。

**冻结前事实核查（2026-10-14）：**

- 比赛在线链路已为 0：`local_online_config.json` `device_defaults.soc_min_percent=0.0`
  （无 experiment 覆盖段）→ config 映射 `soc_min=0.0` → run_live.py
  `make_candidate(soc_min=...)` → `optimize_window`。example 配置
  （config.example.json、remote_experiment_config.example.json）同为 0。
- 比赛 replay（run_contest_strategy_replay.py `soc_min=0.`）、链路测试
  （test_run_live_chain.py `soc_min: 0.0`）、解释器 schemas（默认 0，桥接测试断言
  `soc_min_percent == 0`）均一致。
- 非零值仅存于离线实验脚本与单元测试（test_rolling_milp、test_shadow、
  run_solver_comparison、run_stress_scenarios 的 decide 调用等，`soc_min=10`），
  属显式实验参数，符合 SOP_RECONCILIATION §2，予以保留。

**冻结建议（供 F 线并入比赛参数冻结清单）：**

1. 比赛配置冻结 `soc_min=0` / `soc_max=100`；配置加载的 experiment 覆盖段
   不得设置非零 `soc_min_percent`（建议 F 线在冻结清单中显式列出该键）。
2. 软件保底仅作离线实验参数；含保底实验的产物须标注"非比赛口径"。
3. C2 回退链路中"满足真实 SOC 边界"即 0~100% 设备边界，incumbent 独立复检的
   变量边界项已按此验证；冻结后优化器边界 = 设备边界 = 回退边界，三者一致。

**不能声称的部分：** 设备 BMS 是否存在内部不可观测保底未实测（只读试采期间三站
SOC=100 满电静置，无低位观测，soc_last_changed=2026-10-08）；即便存在，自用回退
天然遵守设备边界，不影响上述冻结建议。

### D2 跨昼夜扩样：分时距 PV 预测切换规则达到可拍板样本量（2026-10-13 晚）

新增 `run_d2_expansion.ps1`（20 个有限批链式受监督执行，零代码改动复用
`run_irradiance_shadow.py` 24 轮 × 20 秒）与 `analyze_dayblock_bootstrap.py`
（日块 bootstrap 决策分析，新增测试 7 个）。全程只读，无 HA 写入、无比赛启动。

**采样**：批 01–20 链式 `--history-result` 继承，墙钟 2026-10-13 20:21–23:00，
覆盖设备 2028-03-08 至 2028-03-28 共 19 个有效设备日；批 20 因两次连续
ConnectTimeout 按设计提前停止（14/24 轮，损失 2028-03-28 凌晨时段，真实缺口
未补造）。因果校准审计 20/20 通过；链起点代码 provenance 哈希落盘
`outputs/irradiance_d2_expansion_20261013/code_provenance_chainstart.json`。

**异常率**：blocked 合计 43/1401 站点轮（批 02 跨午夜天气/HA 小时错位 26 次
为最大单一事件，批 11–20 仅 6 次）；limit_reached 批 1–5 共 13 次、批 6–10
共 2 次、批 11–20 为 0（该改善仅见于本采样链，不等于在线可用率结论，属线 C）。

**校准系数**：三站 scale 在 19 天内呈完整波动周期（站 1 中位 3.6→5.1→3.6，
站 3 4.5→5.0→4.6，站 2 0.34–0.69），批内极差小（±0.1 量级）、日间漂移是
主信号。结论：系数必须因果滚动重估，**不可冻结为常数**；现有 168h 滚动
因果设计得到支持。

**预注册判定**（日照目标点：truth>50W 或 predicted>50W；设备日块 bootstrap
B=10000，95% CI；门槛 ≥15 设备日且 ≥200 日照对/时距，全部满足）：
`outputs/irradiance_d2_expansion_20261013/final_bootstrap_all20.json`。

| 时距 | 日照对 | MAE 比（校准/persistence） | 95% CI | verdict |
| ---: | ---: | ---: | --- | --- |
| 1h | 508 | 1.10 | [0.95, 1.34] | inconclusive → 维持 persistence |
| 2h | 468 | 0.63 | [0.54, 0.76] | 辐照度校准 |
| 4h | 410 | 0.41 | [0.36, 0.50] | 辐照度校准 |

收缩诊断：2h/4h 最优 α=1.0（纯校准，CI 不含 1），1h α=0.5 的 CI
[0.87, 1.06] 跨 1，不支持混合层。分时站描述：1h 站 3 方向相反（0.85），
2h/4h 站 2 接近中性（0.97/0.86），均只作标记不作分站规则。
含旧试采 3 天的敏感性（22 设备日）verdict 不变，见
`sensitivity_bootstrap_with_pilots.json`。

**写入比赛配置的建议（待线 F 收益门槛复核后冻结）**：PV 预测切换规则 =
1 设备小时用当前 PV persistence，≥2 设备小时用滚动因果辐照度校准
（168h 窗、≥3 日照小时、校准不足时回退 persistence），不引入固定收缩系数。

**不能声称的部分**：样本为模拟器连续 19 设备日，天气 regime 多样性有限；
1h 结论为"无显著差异"而非"persistence 显著更优"；MAE 层结论未经策略
replay 收益门槛复核（线 F）；未做任何真实写入验证（线 B）。
