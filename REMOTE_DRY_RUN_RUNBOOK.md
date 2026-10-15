# 比赛机固定版本与常驻 dry-run 演练

本 runbook 适用于 AWS EC2 Instance Connect 浏览器终端。默认不写 HA；不要把 `remote_runtime_config.json` 提交到 Git。

## 固定代码版本

在本地确认包含 `remote_dry_run_ctl.sh` 的最终提交，将下面的 `<PINNED_COMMIT>` 替换为其完整 SHA；在浏览器终端执行：

```bash
cd /home/ec2-user/solarbank-experiment
git fetch --all --tags
git checkout --detach <PINNED_COMMIT>
git status --short
git rev-parse HEAD
```

工作区必须干净，`git rev-parse HEAD` 必须等于批准的提交号。若仓库需要认证，使用实例已有 Git 凭据；不要把 token 写入命令行历史。

## 配置与依赖

```bash
chmod 600 remote_runtime_config.json
python3.11 --version  # 本项目要求 Python >=3.10；比赛机旧 python3/.venv 可能是 3.9
python3.11 -m venv .venv311
.venv311/bin/python -m pip install -r requirements.txt
printf '%s\n' "$PWD/.venv311" > .runtime_venv
export PINNED_COMMIT=<PINNED_COMMIT>
printf '%s\n' "$PINNED_COMMIT" > .pinned_commit
export VENV=$PWD/.venv311
export CONFIG=$PWD/remote_runtime_config.json
```

配置必须满足 `mode=dry-run`，且 `live_enabled=false`、`allow_ha_write=false`、`allow_formal_contest=false`。控制脚本会拒绝任何不满足条件的配置。

若 `python3.11` 不存在，先在 Amazon Linux 2023 上执行 `sudo dnf install -y python3.11`，再重建新的 `.venv311`。不要在旧的 3.9 虚拟环境上运行 `pip install` 来尝试升级解释器；保留旧 `.venv` 和已有日志以便审计。

## 常驻、断线、状态/日志/停止

```bash
chmod 700 remote_dry_run_ctl.sh install_remote_dry_run_recovery.sh
./remote_dry_run_ctl.sh start
./remote_dry_run_ctl.sh status
LINES=120 ./remote_dry_run_ctl.sh logs
```

`start` 使用 `nohup`、独立 PID 和日志文件；关闭本地浏览器终端后进程应继续运行。重新打开浏览器终端后执行 `status` 和 `logs`，确认日志中的 `mode=dry-run; write_enabled=False; strategy=milp`，并检查每个结果 `writes_performed=0`。

## 断线存活与重启恢复演练

1. 启动后记录 `policy.pid`、`logs/runtime_meta.json`、`sha256sum remote_runtime_config.json` 和 `git rev-parse HEAD`。
2. 关闭浏览器终端，等待至少 30 秒；重新连接后执行 `status`、`logs`。不得因为本地断线而停止。
3. 仅在确认比赛窗口未运行或负责人允许的演练时执行：

```bash
./remote_dry_run_ctl.sh stop
./remote_dry_run_ctl.sh start
./remote_dry_run_ctl.sh status
```

记录停止前后 PID、启动时间、首条日志、错误计数和 `writes_performed=0`。不得用此步骤重开已结束模拟窗口。

为主机重启恢复安装用户级 systemd。安装后 `start/status/stop` 会委托给 systemd，避免与手动 PID 管理相互冲突：

```bash
./install_remote_dry_run_recovery.sh
systemctl --user --no-pager status solarbank-dry-run.service
```

若实例允许，执行一次 `sudo loginctl enable-linger ec2-user`，然后在非比赛窗口 `sudo reboot` 演练；重连后检查 `systemctl --user status`、`remote_dry_run_ctl.sh status` 和日志。若未授权主机重启，只保留 systemd 配置，不执行 reboot。

## 停止并留证

```bash
./remote_dry_run_ctl.sh stop
./remote_dry_run_ctl.sh status
sha256sum remote_runtime_config.json
git rev-parse HEAD
```

保留 `logs/live.log`、`logs/runtime_meta.json`、`logs/run_live_audit.csv` 和命令输出。此 runbook 不包含 live 启动命令；HA 写入开关在整个演练中保持关闭。
