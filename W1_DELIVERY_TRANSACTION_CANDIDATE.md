# W1 HA 下发事务候选交付（2026-10-16）

状态：仅本地候选，待集成负责人验收；不部署 W3，不开启 HA 写入，不授权 F2。

## 代理配置增量（2026-10-16）

- HA 登录、HA 状态读取和模拟器读取现在共用显式 `proxy_mode`：`environment` 使用 `requests` 环境代理，`direct` 明确绕过代理。默认保守取 `environment`；远程比赛机模板取 `direct`，由比赛机网络实际可达性核实后才能使用。代理模式不写入 token，也不通过命令行传凭证。
- 代理可达只证明 HTTP/鉴权链路可访问，不代表 current/未来价格完整、三站状态新鲜、价格对齐、求解成功或 10 tick M3 验收通过。
- 修复后的只读预检（原 `alignment_budget_seconds=2.5`、`round_deadline_seconds=5.0` 未改）在代理下取得 current 档位，但在 `sim/api/price` 请求处于预算内超时：总采集 `2.504s`，三站 `eligible_for_milp_tick=false`，`writes_performed=0`。产物：`outputs/m3_prestart_20261016/preflight_proxy_fixed.json`。这不是 10 tick 证据，也不授权放宽截止时间。
- 后续本地连续 dry-run 的审计文件 `outputs/m3_proxy_10tick_20261016/audit.csv` 至少包含 37 个完整三站轮次；所有完整轮次都因 `alignment_deadline_exceeded` 走零写入回退，求解器未被调用。文件另有一组轮次编号缺失的 3 行，不计入完整轮次。**因此虽然采样轮次数超过 10，10 tick 有效 M3 仍为 0/10，验收未通过。** 本地进程已停止，未启用 live。
- 代理增量后全量 `python -m pytest tests -q`：354 passed；`git diff --check` 无错误。该增量需独立复核，不覆盖原候选提交的集成验收结论。

## 安全边界

- `run_live.py --strategy milp --mode live` 使用 `--reconciliation-state` 指向持久化 JSON；默认路径 `outputs/ha_reconciliation_state.json`。真实写入前，HAClient 在每个 POST **之前**原子替换并同步 journal。模式、流向、功率三次 POST 无法原子化；崩溃或超时后的 journal 保留 `reconciliation_required`，重启后仍阻断该站写入。
- HTTP 成功仅是服务确认，不是设备回读确认；异常后不得自动重试、补发或自动回滚。journal 无法读取或格式错误时启动失败。当前设计只支持一个 live runner 独占此文件；多进程共同写入尚未实现锁保护，禁止共享启动。
- `writes_performed=0` 的失败审计不证明设备零动作，应看 `write_requests_attempted`、`write_requests_acknowledged` 和 `delivery_steps`。完成整条 HTTP 命令后清除 journal checkpoint，但下一 tick 设备回读验证仍按原审计执行。

## 人工解隔离

1. 停止所有使用该 journal 的 live runner，确认进程已退出；备份 journal 和相关日志。
2. 用只读 HA 检查并保存该站模式、流向、功率、SOC、设备时间及相关 `last_changed`，判断部分命令是否残留；如设备状态不安全，按经授权的设备恢复 SOP 处理，本脚本不会发 HA 命令。
3. 从 journal 读取 `pending.<site>.incident_id`，核对站点和事故；保存检查证据为本地文件。
4. 执行 `python clear_ha_reconciliation.py --state <journal> --site <site> --incident-id <id> --operator <name> --evidence <file> --reason <review> --confirm-runner-stopped`。脚本检查事故 ID、操作人、理由和证据文件存在，留下 `clearances` 审计条目，然后才解除该站隔离。它不能自动判断证据内容是否正确。
5. 重启前复核 journal 与设备状态。任何源代码、参数或远端版本切换仍须走独立审批。

## 本地验收摘要

- `python -m pytest tests -q`：351 passed。包含 POST 超时/5xx、中途失败、慢响应、截止与跨轮拒发、真正并发占用单求解槽、事故持久化、重启恢复、人工清除、dry-run 不解隔离和坏 journal 拒绝。
- 三站 10 轮离线新鲜状态注入：30 站轮次跳过 0；20 ms 截止/80 ms 慢求解注入：30/30 站轮次跳过；两组 HA 写入 0。产物：`outputs/m3_schedule_20261016/offline_three_site_multiround.json`（本地被 gitignore，需单独交接；不是新窗口 M3）。
- 配置摘要：本地配置 3 站，`mode=online_dry_run`、`proxy_mode=environment`、`live_enabled=false`、`allow_ha_write=false`、`allow_formal_contest=false`、`solver_seconds=2.0`、`poll_seconds=5.0`；整轮截止仍为 5 秒，采集预算仍为 2.5 秒。`local_online_config.json` 字节 SHA-256 为 `51096f1a6dfb060de95cd90a3ca2cd1fc780f609d7827f242de7953f34904546`；不提交配置或凭证。
- 代理增量后的未签名 F1 manifest SHA-256：`5058b1dd277543462edd376f6e81c77860d73198ed24d3fffc553ea25b43f513`，`f2_authorized=false`。此前 SHA 对应旧代码，不能用于本增量签核；manifest 引用的部分历史和本轮离线证据在本地 `outputs/`，不因本候选提交而成为正式 F2 产物。

## 待验收限制

- 持久化隔离解决重启丢状态，但无法撤销已发 HTTP；人工证据的充分性须由负责人审查。
- 单 runner 独占 journal 目前靠运维约束；若要多实例或热备写入，先实现并验证进程间互斥/租约。
- 未在真实 HA 上故障注入，未在新模拟窗口完成连续多 tick 只读 M3，也未验收 live 写入。
