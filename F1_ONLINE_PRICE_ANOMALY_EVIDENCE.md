# F1 在线价格异常路径验收

日期：2026-10-14。验收入口为 `run_live.run_once_milp`，不是离线 replay 的整份数据集校验。

对单站模拟在线 tick 注入三类价格输入异常，并对 `energy_lab.shadow.optimize_window` 设置为不可调用的 mock：

| 输入异常 | tick 结果 | fallback 原因 | 归因 | live 写入 |
|---|---|---|---|---|
| 当前优化窗口缺少小时 | `fallback_self_use_mode` | `missing_price_data:incomplete_optimizer_horizon` | `non-optimizer-attributed` | 一次 `set_self_consumption` |
| 窗口含 `NaN` import price | `fallback_self_use_mode` | `non_finite_price:import_price` | `non-optimizer-attributed` | 一次 `set_self_consumption` |
| 设备时间超出 API 价格窗口 | `fallback_self_use_mode` | `device_time_misaligned:outside_price_window` | `non-optimizer-attributed` | 一次 `set_self_consumption` |
| 设备时间无时区，无法与 API 时间对齐 | `fallback_self_use_mode` | `device_time_misaligned:price_timestamp_alignment_failed` | `non-optimizer-attributed` | 一次 `set_self_consumption` |

四类场景均断言优化器调用次数为 0；缺价、NaN 和超出窗口的设备时间还检查审计 CSV 同步记录 `reason` 与字符串 `attribution`。dry-run 使用相同候选路径但 `writes_performed=0`，不进行 HA 写入。

复现命令：

```text
pytest -q tests/test_run_live_chain.py tests/test_run_live_time.py tests/test_execute.py tests/test_contest_replay.py
```

结果：在线链路及 replay/MILP/F2 门槛定向组合 `71 passed`，全仓 `269 passed`。该证据不表示远程比赛机在线验收，也不授权 F2。

## 超时 incumbent 与 F2 门槛

- 独立约束复验通过的超时解以 `solver_status=limit_reached`、`accepted_incumbent=true` 和 `mip_gap` 保留，执行但不标为 `optimal`。
- 缺少独立验证的 `limit_reached` 解、`infeasible` 解均拒绝执行，使用 `self_consumption` 回退。
- `prepare_f2_command.py` 只在签核 manifest 的顶层和 `signature` 同时授权、且传入准确 SHA-256 时打印显式 F2 命令；它本身不启动 replay。当前 manifest 仍未授权。
