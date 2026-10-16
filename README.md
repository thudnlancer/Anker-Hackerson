# 充电储能赛道实验环境

本目录是预赛方案的可复现实验骨架，默认只做离线回测和 `dry-run`，不会向现场设备下发控制指令。系统架构基线见 [系统架构 Guideline](SYSTEM_ARCHITECTURE_GUIDELINE.md)，SOP 逐条复核见 [SOP_RECONCILIATION.md](SOP_RECONCILIATION.md)：API 优先、设备时间驱动、HA 实时状态、在线物理校准、SOP 约束滚动策略和回读闭环。

解释框架与中文前端位于 `explainability-app/`。只读 shadow 可显式导出同次 MILP 完整决策证据，反事实工作进程直接调用本仓库策略源码。安装、导出、导入与团队分工见 [协作接入说明](EXPLAINABILITY_COLLABORATION.md)；本次本地验收使用人工输入，HA 闭环与官方结算仍需单独验收。

最新阶段结果见 [后续实验报告](NEXT_STAGE_RESULTS.md)：连续压力审计、100 站点自用基线、80/20 站点留出、SOP 约束重放及真实只读 shadow-run 已完成。API-first 功率路径已接入 shadow；在线主路径仍需完成 HA 物理校准和真实设备回读。辅助预测模型不作为策略启动门槛，当前不能直接将离线动作下发比赛设备。

## 目录

- `src/energy_lab/sim_client.py`：`:45678` 模拟器登录、电价、天气和历史数据客户端。
- `src/energy_lab/ha_client.py`：Home Assistant 状态读取与控制服务客户端。
- `src/energy_lab/model.py`：14 kWh / 5 kW / SOP 充电侧 0.95 效率的规则调度器和电池仿真。
- `src/energy_lab/backtest.py`：读取训练集 CSV，输出逐时轨迹和成本摘要。
- `run_backtest.py`：离线回测入口。
- `run_live.py`：本地或远程在线入口；默认只读，使用 `--mode live` 且三个安全开关同时打开才允许写 HA。
- `run_online_dry_run.py`：一次性只读在线检查，自动用 HA 账号换取临时令牌并发现三个设备后缀；不会调用任何控制服务。
- `run_stress_scenarios.py`：连续滚动压力场景、超时/坏预测回退与 15 分钟合成步长审计；最终结果见 `outputs/stress_closed_loop_v2/`。
- `run_self_consumption_comparison.py`：在已审计 MILP 轨迹上增加固定自用基线，统一期末库存和损耗口径。
- `run_holdout_validation.py`：固定 80/20 站点留出和预声明参数敏感性实验；结果不得解读为全新盲测。
- `review_interface_readiness.py`：离线检查电池功率到 HA 并网点目标的映射，不执行网络请求或写入。
- `run_online_policy_replay.py`：历史轨迹的 SOP 约束重放、候选命令映射和模拟回读；不访问网络。
- `run_shadow_dry_run.py`：读取价格/天气/HA 状态生成候选动作，默认只读；不会调用 HA 服务写接口。
- `src/energy_lab/decision.py`：共享的优化决策、失败回退和坏观测拒绝路径。
- `src/energy_lab/interface_contract.py`：SOP 约束下的纯函数接口映射与跨日时间对齐检查。
- `src/energy_lab/system_model.py`：在线电网-电池-负载功率平衡、SOC 递推、PV 辐照度校准、负载区间和损耗诊断。
- `config.example.json`：现场地址、站点后缀和安全参数模板。
- `remote_experiment_config.example.json`：参赛机 SSH、环境机接口、数据路径与实验安全开关的集中配置模板。
- `remote_runtime_config.example.json`：放在参赛机上的最小运行配置模板；凭据只保存在参赛机，不进入代码同步包。
- `deploy_remote.ps1` / `run_remote.ps1`：从 Windows 本地同步代码和模型、安装依赖、启动/查看/停止远程策略。

## 离线回测

```powershell
cd D:\Anker黑客松\实验环境
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python run_backtest.py --data .\data
```

把仪表盘“下载训练数据”得到的 `training-data.zip` 解压到 `data`，或直接传 `--zip`；脚本会递归读取其中的 CSV。当前训练集为 100 个站点、2026-07-03 至 2026-10-02、小时粒度、每站 2208 条记录，包含 31 列：光伏、负载、温度、湿度、露点、降水、气压、云量、风速、短波/直射/散射辐照度、太阳高度角/方位角、`is_day` 及买卖电价。读取器兼容 `local_hour_time`、`pv_power_avg_w`、`load_power_avg_w`、`import_price_eur_kwh`、`export_price_eur_kwh`，缺少必需列会明确报错。

## 现场配置

复制 `config.example.json` 为 `config.json`，填写环境机地址、HA 长期令牌、看板账号密码和 3 个设备后缀。令牌只放在本地 `config.json`，不要提交到版本库。

示例：`python run_backtest.py --zip ..\\solarbank-training-data.zip --data data --output outputs`。天气字段会随标准化数据保留；当前基础策略使用光伏、负载和价格，后续预测模型可直接使用辐照度、云量、太阳高度角和 `is_day` 等特征。SOP 说购电价恒正，但更新 CSV 中发现非正 `import_price_eur_kwh` 行时，汇总会写出数据质量警告；核清字段映射前，不应据此训练“负价买电有利”的策略。

训练站点预测基线：

```powershell
python run_forecast.py --zip ..\\solarbank-training-data.zip --data forecast_data --models models --metrics forecast_metrics.json
```

该命令将 100 站点按站点内时间顺序切分，汇总训练样本训练两套共享模型（光伏、负载），以前 80% 训练、后 20% 验证；输出 MAE/RMSE，并与“前一天同一小时”基线比较。验证口径是一小时滚动重规划的一步预测，历史滞后量来自该时点可观察到的历史数据。24 小时预测按递归方式生成，并会累积误差；每小时读入新实测后重新预测。指标不能直接当作现场 API 预测精度。

### 规则策略与预测 + LP 对比

```powershell
python run_strategy_comparison.py --data forecast_data --output outputs/strategy_comparison_pilot --max-sites 1
```

先用少量站点跑通，再移除 `--max-sites` 评估完整数据集。该实验在每站前 80% 时间段训练共享 PV/负载模型，后 20% 做逐小时滚动回测；规则策略和 LP 使用相同的留出段、初始 SOC 和电价，LP 只执行 24 小时计划的首步，并在下一小时读入实测后重算。输出逐步 trace 与 summary，含策略成本、期末 SOC、购/售电量、电池吞吐、负外送电价损失、LP 回退次数和约束检查。

天气敏感性分为两种，均不能直接代表线上天气预报效果：`oracle` 使用未来真实天气，只作理想上界；`persistence` 将天气特征维持为最近一次观测，只作简单敏感性测试。真实可部署的结果需要拿到决策时点可获得的天气预报快照后再评估。训练/验证时间严格分开；可用的买卖电价被假设为决策时已知的未来价格，需以正式 API 粒度和发布时间确认该假设。

当前 5 站点代表性批次结果保存在 `outputs/strategy_comparison_5site/`（该目录被 `.gitignore` 排除）：两种天气情景下 LP 均无回退，SOC/功率约束均通过，共 8,840 条轨迹。预测 LP 的总成本低于规则基线，但电池吞吐量明显更高，且部分站点 SOC 到达 0%；这说明必须同时报告循环寿命成本、SOC 保底和期末库存结算，不能直接把成本改善宣称为比赛收益。

敏感性参数已经加入对比脚本：`--soc-min` 默认 10%，`--degradation-cost` 默认 0.002 EUR/kWh，`--terminal-price` 可设置留出段末电池库存结算价值。例如：

```powershell
python run_strategy_comparison.py --data forecast_data --output outputs/strategy_sensitivity --max-sites 5 --weather-modes persistence --soc-min 10 --degradation-cost 0.05 --terminal-price 0.10
```

`total_cost_eur` 是运行期间购电成本减外送结算收入；设置 `--terminal-price` 后，`total_settled_cost_eur` 会扣除留出段末 SOC 对应的库存价值。该库存价值不是官方结算规则的替代品，正式比赛仍以看板结算为准。高循环成本应显著降低吞吐，SOC 保底应保证所有轨迹不低于设定下限；二者都必须在 summary 中核对，而不能只看总成本。

已完成的 5 站点高寿命成本批次在 `outputs/strategy_sensitivity_5site_deg050/`：`soc_min=10%`、`degradation_cost=0.05 EUR/kWh`、`terminal_price=0.10 EUR/kWh`、天气情景为 `persistence`。LP 无回退，最低 SOC 约 10%，总吞吐约 960 kWh，结算后总成本约 15.22 EUR；该结果仅用于参数方向验证，不代表官方成绩或完整 100 站点统计。

补充的 5 站点保守 SOC 批次在 `outputs/grid_5site_soc20_deg002/`：`soc_min=20%`、`degradation_cost=0.02 EUR/kWh`、`terminal_price=0.10 EUR/kWh`。LP 无回退，最低 SOC 20%，总吞吐约 1,480 kWh，结算后总成本约 -5.55 EUR。与 `soc_min=10%`、`degradation_cost=0.05` 的批次相比，20% 保底保留了更大安全余量，但成本和吞吐的权衡仍需在官方结算口径下确认。

完整 100 站点 `persistence` 回测已完成；复核发现原规则基线有 927 步满电附近充电量记账错误，已修复并重新执行规则基线。后续使用 `outputs/grid_100site_soc10_deg002_persistence_corrected/`，旧目录仅供追溯。口径为每站前 80% 训练、后 20% 留出，24 小时滚动窗口、每小时重规划，`soc_min=10%`、循环代理成本 `0.02 EUR/kWh`、期末库存价值 `0.10 EUR/kWh`。LP 运行成本为 `-1308.42 EUR`，期末库存校正后为 `-1322.42 EUR`，修正规则基线分别为 `1333.32 EUR` 与 `1203.12 EUR`；LP 总吞吐 `34276.09 kWh`，规则为 `4904.57 kWh`。LP 回退为 0；两种策略执行轨迹的能量平衡、SOC/功率边界与成本复算通过审计。LP 原轨迹在数值容差内原样复用，没有重复训练和求解。该结果是内部离线敏感性结果，不是官方成绩。

配对的 `oracle` 修正版在 `outputs/grid_100site_soc10_deg002_oracle_corrected/`：LP 运行成本为 `-1339.73 EUR`，期末库存校正后为 `-1353.73 EUR`，修正规则基线与 persistence 一致；LP 吞吐 `33986.57 kWh`。oracle 聚合运行成本低 `31.31 EUR`，但这不是严格收益上界，也不能与 5 站点参数实验直接比较影响大小。两种情景均为 100/100 站点 LP 成本低于修正规则基线；运行成本单站节省中位数分别为 `27.20 EUR` 和 `27.27 EUR`。同时计入期末库存与损耗代理后，聚合节省分别为 `1938.11 EUR` 和 `1975.21 EUR`，仍为 100/100 站点改善。

详细核验及下一阶段检查见 [EXPERIMENT_REVIEW.md](EXPERIMENT_REVIEW.md)。原批次没有独立完整控制台日志，审计依据是源 CSV、trace 和 summary；下一批需保存日志及完整配置。`python audit_strategy_results.py` 可复核修正版；`python -m unittest discover -s tests -v` 检查满电和保底边界。当前 CSV 无负购电价，旧模拟材料的问题与此批次分开处理。旧 5 站点批次的规则对照也受同一缺陷影响，下表仅报告 LP 指标，不使用旧规则收益比较。候选参数尚未冻结为比赛配置。

参数对比（5 站点、`persistence`、期末库存价值 `0.10 EUR/kWh`）：

| 批次 | SOC 下限 | 循环成本 (EUR/kWh) | 运行成本 (EUR) | 期末校正成本 (EUR) | 吞吐 (kWh) | 最低 SOC | LP 回退 |
|---|---:|---:|---:|---:|---:|---:|---:|
| `grid_5site_soc10_deg002` | 10% | 0.02 | -11.61 | -12.31 | 1554.68 | 10% | 0 |
| `grid_5site_soc20_deg002` | 20% | 0.02 | -4.15 | -5.55 | 1480.44 | 20% | 0 |
| `strategy_sensitivity_5site_deg050` | 10% | 0.05 | 15.92 | 15.22 | 960.07 | 10% | 0 |

`total_cost_eur` 是运行期间购电成本减外送结算收入；`total_settled_cost_eur` 在此基础上扣除期末电池库存估值。二者都不是官方结算字段。

滚动优化器位于 `src/energy_lab/rolling.py`。默认原型是线性规划（LP）；传入 `--solver milp` 可启用 SciPy `milp` 的显式充放电互斥和同时购售电互斥。两种求解器都接收 24 小时预测、当前 SOC 和价格，约束能量平衡、SOC、5 kW 充放电上限和 0.95 充电效率；目标包含购电成本、售电收入、循环代理成本和期末电量价值。MILP 的二进制约束已在 300 个 24 小时真实数据窗口和 100 站点全量滚动回测中通过检查。全量 LP/MILP 使用相同 100 站点训练模型和输入数据，配对审计见 [并行实验报告](PARALLEL_EXPERIMENT.md)；两者成本差异仅是当前内部样本结果，不构成官方成绩或普遍优势结论。每轮只执行返回计划的第一行，失败/超时由共享决策层记录并回退；当前在线 `run_live.py` 尚未接入预测模型/MILP，不能把离线回测动作直接下发。由于设备的 `discharge` 指令语义是并网点输出目标，接入 HA 前必须把优化器的电池功率转换为并网点目标并用回读验证，不能直接把 `discharge_w` 原样写入 `power_control`。

MILP 参考窗口检查：`python run_solver_comparison.py`。它覆盖 100 站点各 3 个 24 小时窗口，验证 LP/MILP 目标、首步动作、互斥、SOC、功率和能量守恒；结果在 `outputs/solver_comparison_100site/`，只用于求解器机制诊断，不代表预测线上性能。

10 站点 `persistence` MILP 批次在 `outputs/grid_10site_soc10_deg002_milp_persistence/`：4,420 个优化决策全部 optimal、回退 0，运行成本 `22.92 EUR`，库存校正成本 `21.52 EUR`；同批修正规则基线为 `287.97 EUR` 和 `275.39 EUR`，吞吐分别为 `3247.14 kWh` 和 `564.69 kWh`。该批次仅使用 10 站点训练模型，不能与 100 站点训练模型的 LP 截取结果作求解器因果比较；此前“差异来自互斥约束”的归因撤回。此为内部结果，非官方成绩。

并行与全量运行的最新状态见 [并行实验报告](PARALLEL_EXPERIMENT.md)。通过 `--workers` 设置独立站点/天气进程数；单站时间顺序不变，共享预测模型只在父进程训练一次，子进程接收同一模型。默认 `--worker-threads 1` 限制预测用 BLAS/OpenMP 线程；它不代表直接配置了 HiGHS 的全部内部线程。模型、时域、求解精度和超时不因并行数而改变。

MILP 分块入口为 `run_milp_chunk.ps1`，例如：

```powershell
.\run_milp_chunk.ps1 -StartSite 1 -SiteCount 10 -WeatherMode persistence
```

脚本使用全部站点训练共享模型，只评估指定的站点区间，并保存 `console.log`。区间是 1-based；`-Workers 4` 设置进程数。每个任务完成后保存 `site_results/` 和 `progress.jsonl`，最终按站点顺序合并。中断后用相同命令加 `-Resume`；代码、数据、模型和参数指纹不一致时拒绝续跑，已完成批次也拒绝覆盖。续跑粒度为站点/天气任务，未完成的单站从头运行。各分块仍需单独审计，尚未自动合并不同目录。

在线前先完成 SOP 的三站添加和自检：HA 中确认三个设备在线；逐站执行一次充电 2000 W、回读功率和 SOC，再切回 `self_consumption`。初次运行保持 `live_enabled=false`，观察 dry-run 日志无误后，才在受控模拟赛中开启写入。

现有在线入口只认 HA 的 `device_time`，不使用本机墙钟决定设备小时；默认按 5 秒真实时间轮询并隔离单站异常。API 时间戳用于给无时区后缀的 `device_time` 提供同一设备时钟坐标。只读 shadow 已接入 API-first 15 分钟功率路径，但仍需要补齐 HA 物理校准、真正的下发后回读确认和动态快照审计；不能把这些待办描述为已完成能力。`review_interface_readiness.py` 对历史动作的纯离线映射结果见 [后续实验报告](NEXT_STAGE_RESULTS.md)。

在线只读检查：`python run_online_dry_run.py --config local_online_config.json --output online_dry_run_latest.json`。该命令会读取 HA 三站实体和模拟器电价/天气，并把结果保存到本地；`writes_performed` 应保持为 `0`。

## 本地到远程的切换（旧 SSH 私钥方案，当前不适用）

当前只有 EC2 Instance Connect 浏览器终端，没有本地 SSH 私钥。下列 `deploy_remote.ps1` / `run_remote.ps1` 命令保留作历史方案，不用于当前验收；实际部署与常驻 dry-run 只按 [远程 dry-run runbook](docs/operations/REMOTE_DRY_RUN_RUNBOOK.md) 的 Git 固定提交路径执行，且须另行授权远端操作。

本地里程碑完成后，远程参赛机只需要接收代码、模型和自己的运行配置。私钥仍保存在本机，HA 令牌和看板密码不写入命令行，也不随代码同步。

1. 将 `remote_experiment_config.example.json` 另存为不提交的 `remote_experiment_config.json`，填写 SSH 私钥绝对路径；将设备后缀和环境机凭据填写完整。
2. 首次部署（明确同意把运行凭据上传到参赛机时才加 `-UploadRuntimeConfig`）：

```powershell
.\deploy_remote.ps1 -ConfigPath .\remote_experiment_config.json
```

   若需要同步运行配置：

```powershell
.\deploy_remote.ps1 -ConfigPath .\remote_experiment_config.json -RuntimeConfigPath .\remote_runtime_config.json -UploadRuntimeConfig
```

   远程配置应命名为 `/home/ec2-user/solarbank-experiment/remote_runtime_config.json`，默认 `mode` 和三个写入开关均为关闭。
3. 先在远程机执行一次只读检查，再启动策略：

```powershell
.\run_remote.ps1 -ConfigPath .\remote_experiment_config.json -Action status
.\run_remote.ps1 -ConfigPath .\remote_experiment_config.json -Action logs
.\run_remote.ps1 -ConfigPath .\remote_experiment_config.json -Action dry-run
```

   `dry-run` 会在远程 `nohup` 进程中持续运行，并用 `policy.pid` 管理，即使本地电脑离线也不会停止。查看日志使用 `-Action logs`，停止使用 `-Action stop`。
4. 只有在正式比赛人工确认后，才在远程 `remote_runtime_config.json` 中同时设置 `mode=live`、`live_enabled=true`、`allow_ha_write=true`、`allow_formal_contest=true`，然后执行 `.\run_remote.ps1 -ConfigPath .\remote_experiment_config.json -Action live`。任何一个开关关闭都会拒绝写入；结束比赛使用 `-Action stop`。

远程运行命令使用远程配置中的 `device_time` 和环境机接口，不依赖本地时钟或本地文件。若私钥未填写、远程配置缺失或包含占位符，部署/运行会直接失败。

## 仅使用 EC2 Instance Connect 浏览器

比赛机固定提交、只读常驻、断线/重启恢复及 `status/logs/stop` 演练以 [远程 dry-run runbook](docs/operations/REMOTE_DRY_RUN_RUNBOOK.md) 为准。下文旧的裸 `nohup` 示例不含固定提交与显式 `--strategy milp`，不得用于当前验收。

当前参赛机连接信息为：实例 `i-0217afcd24bf6e9da`，可用区 `us-west-2a`，区域 `us-west-2`，公网地址 `35.92.220.3`，登录用户 `ec2-user`。浏览器连接路径是 AWS 控制台的 **EC2 → us-west-2 → Instances → Connect → EC2 Instance Connect**。

这种连接方式不提供本机 `ssh/scp` 私钥，因此 `deploy_remote.ps1` 和 `run_remote.ps1` 不能直接从本地调用。进入浏览器终端后，仍需先把代码包和模型传入实例；推荐顺序是：

1. 将部署包放到团队可访问的 Git 仓库，远程执行 `git clone` 或下载固定版本压缩包。
2. 或使用 S3 对象/预签名 URL，远程执行 `curl` 下载后解压。
3. 若允许创建密钥，可在 AWS 中创建临时 Key Pair，并将 `.pem` 私钥安全保存到本机，再恢复使用自动部署脚本。

浏览器终端中的最小启动命令如下（代码已经传入 `/home/ec2-user/solarbank-experiment` 且运行配置已单独保存后）：

```bash
cd /home/ec2-user/solarbank-experiment
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python run_online_dry_run.py --config remote_runtime_config.json --output online_dry_run_latest.json
nohup .venv/bin/python run_live.py --config remote_runtime_config.json --mode dry-run >> logs/live.log 2>&1 </dev/null &
echo $! > policy.pid
```

`remote_runtime_config.json` 不要放入公开仓库。当前模板已记录实例 ID 和 `us-west-2`，不应继续使用旧的 `ap-southeast-2` 区域值。

### 使用 Git 传输

建议创建私有 Git 仓库，只提交策略源码、模型、依赖和模板配置。实验目录已提供 `.gitignore`，会排除 HA/看板凭据、训练数据、运行结果和本地环境。

本地首次发布（将 `<PRIVATE_REPO_URL>` 替换为私有仓库地址）：

```powershell
cd D:\Anker黑客松\实验环境
git init
git add .
git commit -m "prepare remote experiment runtime"
git branch -M main
git remote add origin <PRIVATE_REPO_URL>
git push -u origin main
```

在 AWS 浏览器终端中：

```bash
cd /home/ec2-user
rm -rf solarbank-experiment
git clone <PRIVATE_REPO_URL> solarbank-experiment
cd solarbank-experiment
mkdir -p logs
cp remote_runtime_config.example.json remote_runtime_config.json
chmod 600 remote_runtime_config.json
```

如果仓库是私有仓库，浏览器终端需要先配置 GitHub Deploy Key、SSH key 或使用短期访问令牌；访问令牌不要写入 shell 历史。比赛期间更新代码时执行 `git pull --ff-only`，先停止旧进程，再重新执行 dry-run。

模型文件 `models/*.joblib` 体积较小，可以直接提交；训练数据 ZIP 和 `forecast_data/` 不建议提交，远程在线运行不需要它们。

### 获取 HA 长期令牌

1. 在浏览器打开 `http://52.41.23.84:8123` 并登录 HA。
2. 点击左下角用户名/头像，进入个人资料（Profile）。
3. 滚动到 **Long-Lived Access Tokens（长期访问令牌）**。
4. 点击 **Create Token**，名称填写例如 `solarbank-contestant-remote`。
5. 创建后立即复制令牌；HA 通常只显示一次，丢失后只能撤销并重新创建。
6. 在远程实例中写入未提交的 `remote_runtime_config.json`：

```json
{
  "mode": "dry-run",
  "ha_base": "http://52.41.23.84:8123",
  "ha_token": "粘贴刚创建的令牌",
  "sim_base": "http://52.41.23.84:45678",
  "sim_username": "contestant",
  "sim_password": "看板密码",
  "stations": {
    "1": {"suffix": "1502"},
    "2": {"suffix": "1503"},
    "3": {"suffix": "1504"}
  },
  "live_enabled": false,
  "allow_ha_write": false,
  "allow_formal_contest": false
}
```

令牌只放在远程 `remote_runtime_config.json`，不要提交 Git、不要放在命令行参数中，也不要粘贴到聊天记录。先执行 `run_online_dry_run.py`，确认 `writes_performed=0` 后再启动长期 dry-run。

## 当前边界

本地骨架不代替官方结算，也不把模拟回测结果写成比赛成绩。正式成绩以仪表盘的 `saving_settled_percent` 为准；期末库存按 SOP 结算价计入，负 `export_price` 只表示向电网送电会产生损失，不表示购电补贴。
