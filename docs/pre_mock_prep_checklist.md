# 模拟比赛（mock）赛前准备清单

本清单用于「本机模拟赛」启动前的逐项准备与 O7 结案。默认不向现场设备下发控制指令；唯一一次 HA 写入发生在 Phase 2 的逐站恢复，需操作者明确确认。正式比赛不使用本清单。

> 工作目录：`D:\Anker黑客松\实验环境`；解释器：系统 `python`（3.13.5，依赖已装）。
> 当前 journal：`outputs/local_mock_20261016_run2/ha_reconciliation_state.json`。

## 关键事实

- 模拟比赛 = 模拟器上的 `track=mock` 窗口，生命周期 `waiting/mock → running/mock → ended/mock`。
- 运行器 `scripts/online/run_local_mock.py` 是**被动**的：只在 `running/mock` 时下发 HA 命令，`ended/mock` 时自动退出。
- 当前 3 个 pending 事故会隔离三站写入，必须清空后才能跑写入模拟赛。三个 incident id 已固定：

| 站点 | incident_id |
|---|---|
| 1 | `666bd87987c04b37b0eeac65aadf9605` |
| 2 | `79fbd2235bb642a9a49c095c9ea75adc` |
| 3 | `d46070fe697749759eb3d8a6813d364e` |

- 配置 `config/local/local_online_config.json` 三写开关（`live_enabled`/`allow_ha_write`/`allow_formal_contest`）均为 `false`，符合 local mock 要求；HA 凭据走 `admin` 账号换临时令牌。

---

## Phase 0 — 确认无 runner 在跑

`run_local_mock.py` 用 `runner.lock` 文件锁，进程退出即释放；但 O7 结案要求「runner 已停止」。

```powershell
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
  Select-Object ProcessId, CommandLine
```

若看到 `run_local_mock.py` 或 `run_live.py`，先 `Stop-Process -Id <PID>`。

---

## Phase 1 — 只读预检（环境可达 + before 快照）

```powershell
# 1a. 一次性只读检查：sim/HA 可达、三站实体、device_time/soc/功率（writes_performed 应为 0）
python scripts/online/run_online_dry_run.py `
  --config config/local/local_online_config.json `
  --output outputs/local_artifacts/online_dry_run_pre_mock.json

# 1b. O7 结案用的 before 快照（只读）
python scripts/online/capture_local_mock_safety.py `
  --config config/local/local_online_config.json `
  --output outputs/o7_clear_pre_mock/before.json
```

---

## Phase 2 — O7 结案（清除 3 个 pending）

```powershell
# 2a. 逐站下发 self_consumption 恢复（唯一一次 HA 写入）
python scripts/online/recover_local_mock.py `
  --config config/local/local_online_config.json `
  --journal outputs/local_mock_20261016_run2/ha_reconciliation_state.json `
  --output outputs/o7_clear_pre_mock/manual_recovery.json `
  --confirm-runner-stopped

# 2b. 恢复后再抓 after 快照（只读）
python scripts/online/capture_local_mock_safety.py `
  --config config/local/local_online_config.json `
  --output outputs/o7_clear_pre_mock/after.json

# 2c. 机械审查（只读，永不改 journal）
python scripts/online/review_ha_reconciliation.py `
  --journal outputs/local_mock_20261016_run2/ha_reconciliation_state.json `
  --before outputs/o7_clear_pre_mock/before.json `
  --after outputs/o7_clear_pre_mock/after.json `
  --output outputs/o7_clear_pre_mock/review.json
```

**判断门禁**：看 `review.json` 的 `all_readback_candidates` 与每站 `checks`。

- 需要 `safe_mode_echo=true`（mode 回显 `self_consumption`）且 `mode_updated_after_incident=true`。
- `device_time_advanced` 依赖模拟器 tick 设备；若设备时间仍冻结（此前只读复核停在 `2026-10-17 08:15:00`），该项会是 `false`。最终是否清除是操作者决定，`clear` 命令本身只校验 incident_id/operator/evidence。

```powershell
# 2d. 逐站清除（用上表固定 incident id，不能批量、不能换 journal）
python scripts/maintenance/clear_ha_reconciliation.py `
  --state outputs/local_mock_20261016_run2/ha_reconciliation_state.json `
  --site 1 --incident-id 666bd87987c04b37b0eeac65aadf9605 `
  --operator <你的名字> `
  --evidence outputs/o7_clear_pre_mock/review.json `
  --reason "same incident safe-mode echo verified; O7 treated as incidental accident, cleared to permit next mock run" `
  --confirm-runner-stopped

# 站 2（--incident-id 79fbd2235bb642a9a49c095c9ea75adc）
# 站 3（--incident-id d46070fe697749759eb3d8a6813d364e）
```

```powershell
# 2e. 复读 journal：pending 应为 {}，clearances 新增 3 条
python -c "import json;print(json.load(open('outputs/local_mock_20261016_run2/ha_reconciliation_state.json',encoding='utf-8'))['pending'])"
```

---

## Phase 3 — 配置/就绪复核（信息性，非硬门槛）

```powershell
python scripts/online/check_online_readiness.py `
  --config config/local/local_online_config.json `
  --model-dir models/online_api --cache-dir data/online_history --sites 1,2,3
```

> mock 的 MILP 链（`run_once_milp`→`make_candidate`）只用 API 价格 + 当前 SOC，不依赖预测模型/168h 历史；上一步仅供参考，`unready` 不阻止启动。真正硬门槛：三写开关 `false` + 三站后缀正确 + journal 已清空。

---

## Phase 4 — 启动模拟赛

运行器被动等待 `running/mock`；建议**提前启动**，等窗口信号一到自动开始写：

```powershell
python scripts/online/run_local_mock.py `
  --config config/local/local_online_config.json `
  --output-dir outputs/local_mock_20261016_run4 `
  --reconciliation-state outputs/local_mock_20261016_run2/ha_reconciliation_state.json `
  --confirm-mock-ha-write
```

- `--reconciliation-state` 复用已清空的 run2 journal（保持「不换 journal」原则；pending 为空时不隔离）。
- 窗口 `ended/mock` 时 runner 自动退出。
- 日志：`contest_stage` 从 `waiting/mock` → `running/mock` 后出现 `site_result` 行。

---

## Phase 5 — 赛后收尾（抓 ended 证据）

```powershell
python scripts/online/finalize_local_mock.py `
  --config config/local/local_online_config.json `
  --output-dir outputs/local_mock_20261016_run4
```

只读等到 `ended/mock`，再写 `ended_snapshot.json`（dashboard SHA + 三站 HA 状态），用于赛后看板对账与下一次 O7 复核。
