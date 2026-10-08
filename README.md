# 充电储能赛道实验环境

本目录是预赛方案的可复现实验骨架，默认只做离线回测和 `dry-run`，不会向现场设备下发控制指令。

## 目录

- `src/energy_lab/sim_client.py`：`:45678` 模拟器登录、电价、天气和历史数据客户端。
- `src/energy_lab/ha_client.py`：Home Assistant 状态读取与控制服务客户端。
- `src/energy_lab/model.py`：14 kWh / 5 kW / 0.95 充电效率的规则调度器和电池仿真。
- `src/energy_lab/backtest.py`：读取训练集 CSV，输出逐时轨迹和成本摘要。
- `run_backtest.py`：离线回测入口。
- `run_live.py`：本地或远程在线入口；默认只读，使用 `--mode live` 且三个安全开关同时打开才允许写 HA。
- `run_online_dry_run.py`：一次性只读在线检查，自动用 HA 账号换取临时令牌并发现三个设备后缀；不会调用任何控制服务。
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

滚动优化器位于 `src/energy_lab/rolling.py`。`optimize_window` 接收 24 小时预测、当前 SOC 和价格，约束能量平衡、0--100% SOC、5 kW 充放电上限和 0.95 充电效率；目标包含购电成本、售电收入、循环代理成本和期末电量价值。每轮只执行返回计划的第一行，LP 不可行时由调用方回退规则策略。由于设备的 `discharge` 指令语义是并网点输出目标，接入 HA 前必须把优化器的电池功率转换为并网点目标并用回读验证，不能直接把 `discharge_w` 原样写入 `power_control`。

在线前先完成 SOP 的三站添加和自检：HA 中确认三个设备在线；逐站执行一次充电 2000 W、回读功率和 SOC，再切回 `self_consumption`。初次运行保持 `live_enabled=false`，观察 dry-run 日志无误后，才在受控模拟赛中开启写入。

在线策略只认 HA 的 `device_time`，不使用本机墙钟。每轮读取三站状态和模拟器电价，默认按 5 秒轮询；控制写入后等待下一个 tick 并回读确认。单站异常会隔离，状态过期时停止生成新指令。

在线只读检查：`python run_online_dry_run.py --config local_online_config.json --output online_dry_run_latest.json`。该命令会读取 HA 三站实体和模拟器电价/天气，并把结果保存到本地；`writes_performed` 应保持为 `0`。

## 本地到远程的切换

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
