# M3 设备时区契约与只读预检（2026-10-15）

## 契约

- `local.timezone` 仅为本机时区元数据，不能证明加速模拟中的 HA `device_time` 时区。
- 可信设备时区须由操作者根据现场说明或设备时钟与带时区价格时间戳的核对，显式写入 `experiment.device_timezone`、`environment_machine.device_timezone`、`local.device_timezone` 或扁平配置的 `device_timezone`。解析优先级依次为 experiment、environment_machine、local；扁平配置只取顶层字段。配置字段与核对结果均应由集成负责人复核。
- 缺失或无效时区：MILP 不调用优化器，按既有自用模式回退并记 `non-optimizer-attributed`；旧 dispatch 入口阻断经济动作、零写入。价格时间戳不可反向推断设备时区。

## 预检与证据

只读命令：

```powershell
python check_m3_readonly_preflight.py --config local_online_config.json --output outputs/m3_timezone_preflight_20261015/preflight.json
python -m pytest tests -q
```

本地配置显式 `local.device_timezone=Asia/Shanghai`，来源记录为 `local.device_timezone`；`local.timezone=Asia/Shanghai` 不参与设备时区推断。预检样本中 HA 无时区设备时刻为 `2026-10-17 08:15:00`，价格 API 样本为 `2026-10-17T00:00:00Z`，按该显式时区换算至同一小时。这个对齐支持当前配置，但不是独立的设备时区权威声明；开赛新窗口仍须重新核实。

- 配置 SHA-256：`257d1a076e6930ad1240cbbf28208d6c9e5d6540c74bbb305bf29d9d0bda0f8c`（配置含凭证，文件不入库）。
- 预检 JSON：`outputs/m3_timezone_preflight_20261015/preflight.json`；SHA-256：`5f8461ae26f0016c26719f17740d4f2dac448f92b8969ab227aecc8f61278e12`。
- 测试：`python -m pytest tests -q`，`297 passed`。仓库根目录直接执行 `pytest -q` 时，看板子项目因缺少 `fastapi` / `energy_explain` 依赖在收集阶段失败；这不是本轮策略测试失败。
- 重建的未签核 F1 manifest：`outputs/f1_parameter_manifest_20261014.json`；SHA-256：`18135692e64fa97f73ebc6d1b774fa0dd99e0c7ccba7a53779425442055ddff7`；`f2_authorized=false`。

预检读到 48 个价格点，三站 `1502/1503/1504` 的价格对齐均为 `aligned`，但三站 `state_fresh=false`，因此 `eligible_for_milp_tick=false`、零写入。这只说明旧窗口安全拒绝正常，**不是**新窗口 M3 多 tick 通过，也没有批准 live 或 F2。
