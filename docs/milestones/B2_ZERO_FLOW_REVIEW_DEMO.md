# B2 评审演示：零流量硬件验证（B1）成功/失败对照

> 用途：线 B 评审演示材料。用两份离线 fixture 演示 B1 验证流程的判定逻辑、失败识别能力与证据留档格式。
> 本文档所有输出均为 dry-run 离线演练实录，**未发生任何真实 HA 写入**。
> 当前 runner 已纳入 `scripts/online/`；live 模式必须显式选择并检查 HA reconciliation journal。当前 run2 三站仍 pending，live 会在联网前拒绝。下文 live 命令只作历史格式示例，禁止在 pending 未人工核验关闭时执行。
> 关联文档：[HOLD_SEMANTICS_DESIGN.md](../decisions/HOLD_SEMANTICS_DESIGN.md) §4 步骤 1、§6 V3（判定标准来源）；[PROJECT_STATUS_AND_WORKSTREAMS.md](../project-management/PROJECT_STATUS_AND_WORKSTREAMS.md) 线 B（任务定义）。
> 演示日期：2026-10-13。

## 1. 演示目的

B1 是线 B 的第一个真实写入动作：单站第三方模式下发 `charge=0 W`，下一设备 tick 回读，判定该设备是否真实静置。评审需要回答两个问题：

1. 判定逻辑能否**区分**"零流量生效"与"设备不遵守"？
2. 证据产物是否满足 V3 的存档要求（快照 + 哈希 + 回读轨迹）？

本演示用两份对照 fixture 回答：场景 A（零流量生效）与场景 B（设备接受设定值但电池不静置，即风险 R1 的具象化）。

## 2. 判定逻辑

回读状态需同时通过五项检查才判 `verified`（实现：[zero_flow.py](../../src/energy_lab/zero_flow.py) `judge_zero_flow_state`）：

| # | 检查 | 判定式 | 设计依据 |
|---|---|---|---|
| 1 | `setpoint_echo` | 模式 = third_party_control 且 grid_flow = charge 且 power = 0 | 确认指令真正生效；防止设备恰好在自用静置时"躺过" |
| 2 | `battery_charge_within_tolerance` | 电池充电功率 ≤ 100 W | 设计文档 §4 步骤 1 容差 |
| 3 | `battery_discharge_within_tolerance` | 电池放电功率 ≤ 100 W | 同上 |
| 4 | `grid_import_passive_settlement` | 电网进口 ≈ max(0, 负载−PV)，容差 100 W | 被动结算：电池不参与时电网承担差额 |
| 5 | `grid_export_passive_settlement` | 电网出口 ≈ max(0, PV−负载)，容差 100 W | 同上 |

关键性质：电池侧（2/3）与电网侧（4/5）是**物理耦合的双重签名**。设备若仍在自用，两侧会同时失败——任何单项失败即整体拒绝，不误判通过。

## 3. 演示方法

```powershell
# 场景 A：零流量生效
python scripts/online/run_zero_flow_verification.py --site 1 --config local_online_config.json `
  --snapshot outputs/zero_flow_verification/mock_snapshot_site1.json `
  --output outputs/zero_flow_verification/mock_rehearsal_site1

# 场景 B：设备不遵守（R1）
python scripts/online/run_zero_flow_verification.py --site 1 --config local_online_config.json `
  --snapshot outputs/zero_flow_verification/mock_snapshot_site1_fail.json `
  --output outputs/zero_flow_verification/mock_rehearsal_site1_fail
```

`--snapshot` 模式无网络、无写入；runner 完整走"基线 → 指令 → 等 tick → 回读 → 判定 → 恢复规划"全流程，产出六件 artifact（基线/指令/回读/判定/恢复/报告，各带 SHA-256）。

## 4. 场景 A：零流量生效

**设定**（[fixture](../../outputs/zero_flow_verification/mock_snapshot_site1.json)）：PV 3200 W、负载 1400 W。基线时设备自用，电池正充 1800 W；回读时零流量生效，余量全额上网。

**判定输出实录**（[judgement.json](../../outputs/zero_flow_verification/mock_rehearsal_site1/judgement.json)）：

```json
{
  "verified": true,
  "reason": "verified",
  "checks": {
    "setpoint_echo":                    { "passed": true,  "observed": {"operating_mode": "third_party_control", "grid_flow": "charge", "power_control": "0"} },
    "battery_charge_within_tolerance":  { "passed": true,  "observed": 0.0,    "expected": "0..100.0 W" },
    "battery_discharge_within_tolerance": { "passed": true, "observed": 0.0,   "expected": "0..100.0 W" },
    "grid_import_passive_settlement":   { "passed": true,  "observed": 0.0,    "expected": 0.0 },
    "grid_export_passive_settlement":   { "passed": true,  "observed": 1800.0, "expected": 1800.0 }
  }
}
```

解读：电池双侧归零；出口 1800 W 恰等于 PV−负载，符合被动结算；设定回显正确。五项全过。

## 5. 场景 B：设备不遵守（R1 具象化）

**设定**（[fixture](../../outputs/zero_flow_verification/mock_snapshot_site1_fail.json)）：同一基线。回读时设备**接受了**第三方 charge=0 设定，但电池仍以 1800 W 充电，出口被吃掉降为 0。

**判定输出实录**（[judgement.json](../../outputs/zero_flow_verification/mock_rehearsal_site1_fail/judgement.json)）：

```json
{
  "verified": false,
  "reason": "failed:battery_charge_within_tolerance,grid_export_passive_settlement",
  "checks": {
    "setpoint_echo":                    { "passed": true,  "observed": {"operating_mode": "third_party_control", "grid_flow": "charge", "power_control": "0"} },
    "battery_charge_within_tolerance":  { "passed": false, "observed": 1800.0, "expected": "0..100.0 W" },
    "battery_discharge_within_tolerance": { "passed": true, "observed": 0.0,   "expected": "0..100.0 W" },
    "grid_import_passive_settlement":   { "passed": true,  "observed": 0.0,    "expected": 0.0 },
    "grid_export_passive_settlement":   { "passed": false, "observed": 0.0,    "expected": 1800.0 }
  }
}
```

解读：**setpoint_echo 通过 + 电池/电网双失败**。这把两类失败明确分开：

- "指令没下发成功"（setpoint_echo 会失败）；
- "指令下发成功但设备不遵守"（本场景，R1）——归因为设备行为问题，而非链路问题。

## 6. 对照结论

| 维度 | 场景 A | 场景 B |
|---|---|---|
| 判定结论 | `verified: true` | `verified: false`（双失败） |
| 失败定位 | — | 电池侧超标 + 电网侧偏离，互相印证 |
| dry-run verdict | `null` | `null` |
| writes_performed | 0 | 0 |
| live 时的后续动作 | `zero_flow_verified: true` 落配置，扩展下一站 | `false` 落配置、该站永久禁用零流量 idle、**停止扩展**（V3） |

判定逻辑对两种世界状态给出相反且可归因的结论，区分能力成立。

## 7. dry-run 诚实性保证（两场共用）

1. **`verdict: null`**：即使场景 A 信息性判定为 true，dry-run 不产生硬件结论，`verdict_basis: "not_verified_dry_run"`；
2. **`writes_performed: 0`**：runner 末尾断言兜底；
3. **配置不更新**：`config_update: {"applied": false, "reason": "skipped_dry_run"}`；
4. **恢复计划先行**：指令发出前已从基线算出恢复计划（本例：恢复 self_consumption）；基线模式不可识别时 runner 在任何写入前中止。

## 8. B2 评审待拍板

1. **授权责任人 + 执行窗口（Q4）**：建议在下一次模拟赛窗口开启前完成，避开正式赛状态污染（R3）；
2. **单站先行协议**：首站完成判定全条目 + 模式恢复确认后，再扩展其余两站；
3. **失败即停**：任一站判定失败即停止扩展，维持只读，结论写配置而非仅日志；
4. live 执行命令（授权后）：

```powershell
python scripts/online/run_zero_flow_verification.py --site 1 --config local_online_config.json --mode live `
  --authorization-note "评审会授权记录引用" --write-config
```

（live 需配置三道安全开关全部置 true：live_enabled / allow_ha_write / allow_formal_contest。）

## 附录 A：产物清单与哈希

两场演练各六件 artifact；基线与指令 fixture 相同，故哈希一致——哈希链可交叉验证证据未被篡改。

| artifact | 场景 A（成功） | 场景 B（失败） |
|---|---|---|
| baseline.json | `d8976c79…2506699` | `d8976c79…2506699`（同） |
| command.json | `1e3be5d8…6625bf` | `1e3be5d8…6625bf`（同） |
| readback.json | `cd9a0d34…e568a6b` | `d4cc6d86…2ff456` |
| judgement.json | `7c42243e…db80b1` | `af2b9001…6e0aa5` |
| restore.json | `598acb26…1f6407d` | `598acb26…1f6407d`（同） |
| report.json | `4bda979b…3bd005ce` | `58a1a351…c488ce4e` |

## 附录 B：文件索引

- 判定逻辑：[src/energy_lab/zero_flow.py](../../src/energy_lab/zero_flow.py)；runner：[run_zero_flow_verification.py](../../scripts/online/run_zero_flow_verification.py)；测试：[tests/test_zero_flow.py](../../tests/test_zero_flow.py)（19 用例）
- fixture：[成功](../../outputs/zero_flow_verification/mock_snapshot_site1.json) / [失败](../../outputs/zero_flow_verification/mock_snapshot_site1_fail.json)
- 产物目录：[场景 A](../../outputs/zero_flow_verification/mock_rehearsal_site1) / [场景 B](../../outputs/zero_flow_verification/mock_rehearsal_site1_fail)
