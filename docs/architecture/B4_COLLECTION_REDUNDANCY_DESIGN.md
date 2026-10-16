# B4 采集链路冗余设计（冗余采集进程 / 心跳检测 / 故障切换）

日期：2026-10-14
状态：设计定稿，待实施（需远程机访问授权）
关联：PROJECT_STATUS_AND_WORKSTREAMS 线 B4；collect_online_history.py；deploy_remote.ps1；audit_physical_calibration.py

## 0. 设计基点：一个让方案简单的既成事实

现有采集器是**只读且幂等**的：`fetch_snapshot` 只做 GET，`capture` 写前校验整批、`append_observation` 对 `device_time` 重复记录天然去重（重启后首条重复曾被去重，见 §2.1）。

推论：**双活采集是安全的**。两个节点同时采同一 HA 不会产生任何设备侧副作用；唯一需要管理的共享状态是缓存文件，用"按节点分片 + 读取时合并去重"解决，**不需要分布式锁、不需要 leader 选举**。故障切换因此简化为"消费者始终读合并视图"——数据面无切换动作，进程面只需本地守护。

## 1. 现状与单点清单

当前链路：本机单进程 `collect_online_history.py`（5 秒轮询）→ `data/online_history/site_{1,2,3}.jsonl`。

| # | 单点 | 故障后果 | 现有缓解 |
|---|---|---|---|
| S1 | 采集进程崩溃/挂起 | 数据断流，无自动拉起 | 无（D1 看护未落地） |
| S2 | 本机重启/断电/系统更新 | 长时间断流 | 无（重启后靠人工恢复，已发生过） |
| S3 | 本机→HA 网络路径 | 断流 | 无第二路径 |
| S4 | 缓存磁盘写满/损坏 | 断流或污染 | 无水位监控 |
| S5 | 远程机管理通道（tailscale 失效） | 无法运维远程节点 | tailscale 重装是 B4 另一半 |

目标：**任一单点故障，缓存数据缺口 ≤ 5 分钟；故障可观测（心跳）且进程面自动恢复；全程只读，不触碰写入路径**。

非目标：HA/模拟器本身的冗余（不可控）；写入路径双活（写入唯一且受监管，见线 B）；秒级实时性。

## 2. 总体架构

```
                 ┌──────────── HA (52.41.23.84:8123) ────────────┐
                 │ GET /api/states（只读）                          │
        ┌────────┴─────────┐                            ┌────────┴─────────┐
        │ Node A（本机）     │                            │ Node B（远程机）   │
        │ collector --node- │                            │ collector --node- │
        │   id nodeA        │                            │   id nodeB        │
        │ heartbeat_nodeA   │                            │ heartbeat_nodeB   │
        └────────┬─────────┘                            └────────┬─────────┘
                 │ 写分片 site_N.nodeA.jsonl                      │ 写分片 site_N.nodeB.jsonl
                 ▼                                               ▼
        data/online_history/                        data/online_history/（远程本地）
                 │                                               │ 定期回传（tailscale/SSH）
                 ▼                                               ▼
        ┌────────────────── merge 视图（按 site+device_time 去重，新鲜优先） ──────────────────┐
        │  下游消费者：check_online_readiness / replay / 优化器标定 / F2                        │
        └──────────────────────────────────────────────────────────────────────────────────────┘
        监控器 collector_monitor.py（本机，30s）：双节点心跳 + 缓存增长 → 状态机 → incidents.jsonl
```

三原则：**数据面双活无切换；进程面本地自愈；节点面独立成败、恢复即回归**。没有任何"主备倒换"动作，也就没有倒换脑裂。

## 3. 冗余采集进程

### 3.1 采集器改造（小步、向后兼容）

`collect_online_history.py` 增加：

- `--node-id`（默认 `nodeA`）：决定分片文件名与心跳文件名；
- 每周期结束原子写心跳（`tmp` 文件 + `os.replace`，读端永远看到完整 JSON）；
- 退出码语义：正常退出 0；连续 N 次周期失败（默认 12 次 ≈ 1 分钟）非零退出，交给守护层处置；
- 现有 stdout 周期 JSON 增加 `node_id` 字段。

分片写盘：`append_observation` 的目标文件由 `site_{site}.jsonl` 变为 `site_{site}.{node_id}.jsonl`（`--cache-dir` 不变）。**旧无分片文件保留为 nodeA 历史**，合并视图一并读取，不迁移。

### 3.2 进程守护

| 节点 | 机制 | 策略 |
|---|---|---|
| Node A（Windows 本机） | 新增 `collector_watchdog.ps1`（计划任务，开机自启 + 每 2 分钟触发）：进程不在则拉起，在则校验心跳新鲜度（见 §4），挂死则杀后拉起 | crash-loop 退避见 §5.2 |
| Node B（Linux 远程机） | systemd unit `solix-collector.service`：`Restart=always`、`RestartSec=10`、`StartLimitIntervalSec=300 / StartLimitBurst=3`（systemd 原生实现 crash-loop 熔断） | 部署复用 `deploy_remote.ps1`，unit 文件随部署上传 |

## 4. 心跳检测

### 4.1 心跳文件（采集器写，监控器读）

`outputs/collector_health/heartbeat_{node_id}.json`，每周期原子覆盖：

```json
{
  "node_id": "nodeA",
  "pid": 66988,
  "version": "collector/1.1",
  "started_at": "2026-10-14T08:00:00+00:00",
  "last_cycle_at": "2026-10-14T09:12:35+00:00",
  "cycle": 4021,
  "last_success_at": "2026-10-14T09:12:35+00:00",
  "consecutive_errors": 0,
  "last_error": null,
  "device_times": {"1": "2028-04-02 03:00", "2": "...", "3": "..."},
  "cache_bytes": {"1": 30152, "2": 30152, "3": 30152},
  "writes_performed": 0
}
```

`device_times` 兼作 HA 侧活性证据；`cache_bytes` 兼作磁盘水位信号（S4）。

### 4.2 阈值与状态机（监控器 `collector_monitor.py`，30 秒一轮）

per 节点状态机（以 `last_cycle_at` 相对本地时钟计龄；`interval=5s`）：

```
HEALTHY ──龄>15s(3×)──▶ STALE ──龄>60s(12×)──▶ DEAD
   ▲                      │ 龄回落 ≤15s          │ 心跳复现
   └──────────────────────┴──────────────────────┘（→ RECOVERING：连续 3 轮新鲜后回 HEALTHY）
```

- **STALE**：记录，不动作（容忍 HA 偶发慢响应）；
- **DEAD**：本机节点 → 触发守护拉起（§5.2）；远程节点 → 仅标记与记录（无带外控制时不臆造重启）；
- **RECOVERING**：防抖动门槛，避免拉起-崩溃循环刷屏；
- 每次状态迁移追加 `outputs/collector_health/incidents.jsonl`（含迁移前后状态、计龄、证据哈希），供 D 线恢复审计复用。

另有两个全局信号：**HA 不可达**（双节点 consecutive_errors 同步增长 → 判定为 HA/上游故障而非节点故障，不重启进程，只记录）；**磁盘水位**（`cache_bytes` 停止增长但心跳正常 → 数据面异常，立即记录 incident）。

## 5. 故障切换

### 5.1 数据面：无切换

消费者永远读**合并视图**（`merge_observation_history(cache_dir)`：读 `site_N*.jsonl` 全部分片 + 旧无分片文件，按 `(site, device_time)` 去重，同刻多值取 `received_at` 最新者）。任一节点断流，另一节点分片仍在增长，下游零感知。远程分片经回传滞后到达时，合并天然补洞（回填缺口，不覆盖已有值）。

### 5.2 进程面：本地自愈 + 熔断

本机 watchdog 拉起策略：
1. 进程不存在 → 立即拉起；
2. 进程在但心跳 DEAD → 终止后拉起；
3. **crash-loop 熔断**：5 分钟内拉起 3 次仍 DEAD → 进入退避序列 5min → 15min → 60min，并在 `incidents.jsonl` 标记 `node_disabled_manual_check_required`，停止重启（防 crash-loop，对齐 D1 要求）；
4. 人工处置后删除禁用标记即恢复。

### 5.3 节点面：独立成败，恢复即回归

- 远程节点失联（心跳 DEAD > 10 分钟或回传中断）：本机节点独立承担全部采集（双活本就如此），incident 记录，等待远程恢复；
- 远程恢复后无需"切回"：其分片恢复增长、回传补洞，监控器观察到心跳新鲜即经 RECOVERING 回 HEALTHY；
- **明确不做**：不做任何 HA 写入侧的故障切换（写入路径唯一、受监管，见线 B）；不自动修改运行配置；不臆造对远程机的带外重启。

## 6. 故障矩阵

| 故障 | 检测 | 自动行为 | 数据缺口 |
|---|---|---|---|
| 采集进程崩溃 | 心跳 DEAD（≤60s） | watchdog 拉起 | ≤ 拉起耗时（~1 分钟） |
| 进程挂死（无输出） | 心跳 STALE→DEAD | 杀后拉起 | 同上 |
| crash-loop | 5min 内 3 次 DEAD | 熔断退避 + 禁用标记 | 转人工，远程节点兜底 |
| 本机重启 | 开机自启任务 | 自动恢复采集 | 停机时长（远程节点兜底 → 0） |
| 本机→HA 断网 | 本机 consecutive_errors 增长 | 无（等恢复） | 0（远程节点兜底） |
| HA 整体不可达 | 双节点同步报错 | 无（不重启进程，记录 incident） | 双节点同断（不可控，记录证据） |
| 磁盘写满 | cache_bytes 停滞 + 心跳正常 | incident + 人工清理 | 清理前缺口（远程兜底） |
| 远程机失联 | 心跳 DEAD >10min / 回传中断 | 记录，本机独立承担 | 0（回传恢复后补洞） |

## 7. Tailscale 重装与远程节点（B4 运维半区）

前置：远程机访问授权。步骤：
1. 远程机重装 tailscale（全新安装而非修复，清理旧 state），使用**一次性 auth key** 入网，禁用 key 复用；
2. ACL 最小化：仅放行本机↔远程机的 22（SSH）与心跳/分片回传所用端口，拒绝其它；
3. 运维通道固定走 `ssh over tailscale`（`deploy_remote.ps1` 的 `connection.host` 改为 tailscale IP）；
4. 上传并启用 `solix-collector.service`（§3.2），远程 `remote_runtime_config.json` 保持只读凭据、三开关 false（远程节点**永不写入**）；
5. 分片回传：每 15 分钟 `rsync --append` 增量回传 `site_*.nodeB.jsonl` 至本机 `data/online_history/remote_ingest/`，合并视图同时挂接该目录；回传失败只影响补洞时效，不影响本机采集；
6. 验收演练：依次制造 §6 矩阵中前四行故障，观察心跳/熔断/兜底行为与记录完整性。

## 8. 实施任务分解与验收

| # | 任务 | 产物 | 依赖 |
|---|---|---|---|
| B4.1 | 采集器 `--node-id` + 心跳 + 退出码 + 分片写盘 | collect_online_history.py 改造 + 单测 | 无 |
| B4.2 | 合并视图 `merge_observation_history` + 消费者接线（check_online_readiness 等） | online_contract 扩展 + 单测 | B4.1 |
| B4.3 | 监控器 `collector_monitor.py`（状态机 + incidents） | 脚本 + 单测 | B4.1 |
| B4.4 | 本机 watchdog + 计划任务（crash-loop 熔断） | collector_watchdog.ps1 | B4.3 |
| B4.5 | tailscale 重装 + 远程节点部署（systemd + 回传） | 运维记录 | **远程机授权** |
| B4.6 | 故障演练（§6 前四行）+ 证据存档 | 演练报告 | B4.4/B4.5 |

**验收**：
1. 杀进程 → 90 秒内自动拉起，incident 记录完整，数据缺口 ≤ 5 分钟；
2. crash-loop 注入 → 熔断退避生效，禁用标记落盘；
3. 远程节点断链 → 本机连续采集无缺口，恢复后补洞正确（merge 无重复记录）；
4. 全程 `writes_performed == 0`，配置三开关保持 false；
5. 48 小时双节点稳定运行，incidents 仅含演练记录。

## 9. 风险

- **HA 单点不可控**：双节点同断时只能记录证据，无法在采集侧冗余——接受，比赛窗口内以 F4 赛前检查复核；
- **远程机时钟漂移**影响心跳计龄与 `received_at`：心跳以采集器本地时钟写、监控器以到达时刻复核，>60s 漂移即 incident；merge 去重以 `device_time` 为准，天然免疫墙钟漂移；
- **分片回传滞后**：只影响补洞时效；F2 赛前以本机分片为准，远程分片为增强而非门槛。
