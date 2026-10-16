# O7 reconciliation 结案门禁

本文档只描述事故结案前的证据顺序。它不授权启动模拟赛、正式比赛或远程控制；当前 run2 三站 pending 必须保留。

## 结案前置条件

对每个 pending incident（站点 1、2、3）分别取得：

1. runner 已停止的进程证据；
2. 与当前 `incident_id` 对应的 HA 只读快照；
3. 安全模式 `self_consumption` 的实体回显，且 `last_updated` 晚于 incident 创建时间；
4. 同一站点的 `device_time` 在前后两次快照之间推进；
5. 快照中的实体后缀与 journal 的 `suffix` 完全匹配。

HTTP POST 返回成功不能替代第 3、4 项。任一条件缺失时保持 pending，不得清除。

## 只读机械审查

先将两次脱敏 HA 快照保存为 `before.json` 和 `after.json`，再运行：

```powershell
python scripts/online/review_ha_reconciliation.py `
  --journal outputs/local_mock_20261016_run2/ha_reconciliation_state.json `
  --before before.json `
  --after after.json `
  --output outputs/o7_review_<timestamp>/review.json
```

只有 `all_readback_candidates=true` 且人工确认设备安全、runner 已停止，才允许进入逐站清除。该脚本是只读的，永远不会修改 journal。

## 逐站清除门禁

使用 review 输出中的当前 incident ID，逐站调用根目录兼容入口 `clear_ha_reconciliation.py`，并提供真实证据文件和明确 operator/reason：

```powershell
python clear_ha_reconciliation.py `
  --state outputs/local_mock_20261016_run2/ha_reconciliation_state.json `
  --site <site> --incident-id <current-incident-id> `
  --operator <operator> --evidence <readback-evidence.json> `
  --reason "same incident safe-mode echo and advancing device_time verified" `
  --confirm-runner-stopped
```

每次清除后立即重新读取 journal，确认只减少对应站点且其他 pending 未被改写。不得批量清空、复用旧 incident 的 clearance 或删除原始快照。

## 当前状态

`outputs/local_mock_20261016_o7_readonly_20261016/review.json` 的三个站点均为 `readback_candidate=false`，模式仍为 `third_party_control` 且设备时间未推进。因此当前不能执行清除命令；run2 journal 的三个 pending incident 是受保护证据。
