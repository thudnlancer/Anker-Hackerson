# M3 当前价格档位锚定契约（2026-10-15 修订）

## 契约

- `local.timezone` 仅为本机时区元数据，不能证明加速模拟中的 HA `device_time` 时区。
- 主路径不要求配置 IANA 时区。先读 `/api/price/current`，再读两日价格和三站 HA，最后再读 current；前后 `start/end` 必须保持同一档位。真实接口已确认返回带时区的 `start/end`，在两日价格中唯一对应；价格数值仅校验一致性，绝不用于定位。
- 档位必须是完整一小时；价格档位不可重复，API 时间戳必须连续。HA 原始 `device_time` 保留不改；仅当其新鲜且日期/小时等于 current 的原始时间标签，才将档内分钟/秒加到该已确定的档位起点。若 HA 自带偏移，它也必须与锚点绝对时刻一致。
- `experiment.device_timezone`、`environment_machine.device_timezone`、`local.device_timezone`、扁平 `device_timezone` 可作为已核实的交叉检查配置；不是必填。若与 current 锚点矛盾则拒绝，不能静默择一。API 的 `+08:00` 不是 `Asia/Shanghai` 的 IANA 证明，也不能把 `Z` 自动解释为设备属于 UTC 时区。
- 跨档读取、缺失 current、日期/小时冲突、重复/缺失档位及未来窗口异常，最多整批读取 3 次。最终按站安全拒绝，故障站不影响其他最终一致的健康站；全局价格异常拒绝所有站。MILP 不调用对应优化器，自用回退记 `non-optimizer-attributed`；旧 dispatch 路径阻断经济动作。dry-run 始终零写入。
- 未来窗口只按 API 时间戳选取和校验：整点起始需 24 个小时价格点，非整点需 25 个，才能覆盖完整 24 小时 / 96 个 15 分钟步骤。没有明日数据时不得循环今日价格。

## 可复跑命令

只读命令：

```powershell
python capture_price_anchor_contract.py --config local_online_config.json --output outputs/m3_api_anchor_20261015/price_contract_sample.json
python check_m3_readonly_preflight.py --config local_online_config.json --output outputs/m3_api_anchor_20261015/preflight.json
python -m pytest tests -q --junitxml=outputs/m3_api_anchor_20261015/tests.xml
```

## 真实接口结论与旧证据更正

真实 current 返回 `start=2026-10-17T08:00:00Z`、`end=2026-10-17T09:00:00Z`、`import_price=0.27463`、`export_price=0.15813`，在 48 个 dated points 中唯一匹配。两次 current 与完整两日电价原始 JSON（不含登录/凭证）、各请求获取时间已存入 `price_contract_sample.json`。这是明确档位标识，不是靠数值匹配得到的推断。

**撤回旧结论**：旧预检把 HA `08:15` 按 `Asia/Shanghai` 映至 `00:15Z`，但 `00:00Z` 只是日价格数组首项，不是 current；“aligned”不能证明当轮价格对齐。旧产物保留为历史，不作为新契约验收。当前本地配置还保留旧的显式 `Asia/Shanghai`，本轮不擅改本地或远端配置；若新鲜设备仍是 `08:15` 且 current 仍是 `08:00Z`，该显式配置将触发 `explicit_timezone_api_conflict`。集成负责人应依据新契约审核，远端可以保留 `device_timezone=null`。

## 审计与验收

每站求解/回退行包含 `ha_device_time_raw`、`api_current_start/end`、`selected_price_slot`、`resolved_device_time`、`alignment_source`、`alignment_attempts`、`alignment_rejection_reason`、`attribution` 和 `writes_performed`。例如模拟健康输入 HA `2031-01-01 00:15:00`、current `00:00Z–01:00Z`，无显式时区时解析为 `2031-01-01T00:15:00+00:00`，来源 `api_current_slot`；显式配置 `Asia/Shanghai` 则拒绝，绝不求解。

真实旧窗口三站仍 stale，不能生成有效锚定时刻；预检明确返回 `eligible_for_milp_tick=false`。不得称为 M3 成功。开赛后仍需新窗口连续至少 3 个完整三站 tick，通过 `validate_m3_readonly_log.py` 检查零写入、current 档位证据、原始 HA 与锚点一致及设备时刻递增。

## 历史记录（以下哈希仅对应上轮旧契约，不代表本轮交付）

- 配置 SHA-256：`257d1a076e6930ad1240cbbf28208d6c9e5d6540c74bbb305bf29d9d0bda0f8c`（配置含凭证，文件不入库）。
- 预检 JSON：`outputs/m3_timezone_preflight_20261015/preflight.json`；SHA-256：`5f8461ae26f0016c26719f17740d4f2dac448f92b8969ab227aecc8f61278e12`。
- 测试：`python -m pytest tests -q`，`297 passed`。仓库根目录直接执行 `pytest -q` 时，看板子项目因缺少 `fastapi` / `energy_explain` 依赖在收集阶段失败；这不是本轮策略测试失败。
- 重建的未签核 F1 manifest：`outputs/f1_parameter_manifest_20261014.json`；SHA-256：`18135692e64fa97f73ebc6d1b774fa0dd99e0c7ccba7a53779425442055ddff7`；`f2_authorized=false`。

本轮新哈希、测试结果及提交信息见 `M3_API_ANCHOR_DELIVERY_20261015.md`。F2 授权和签核仍保持未批准，未执行 live、远端切换或新模拟赛。
