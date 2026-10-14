# W3：下一模拟窗口 D1 / D3 只读清单

当前模拟窗口已结束，旧 PID/heartbeat/cache 只是历史证据。本清单是准备材料，不是启动授权；不要重启旧窗口或对 HA 写入。

## 窗口前

1. 由比赛负责人确认新的模拟赛确实为 `running/mock`。记录 `/api/contest` 原始响应、当前墙钟、设备时钟和本次窗口标识。
2. 核定 HA `device_time` 的时区坐标。CLI `--device-timezone` 优先于明确配置的 `local.device_timezone`，两者均无时才采用 API 时间戳时区；普通 `local.timezone` 不代表设备时区。未核定时不得声明 D1 有效。
3. 为本次窗口生成唯一 `epoch_id`（例如 `mock3-20261015T120000Z`），创建全新、空的 `data/live_history_<epoch_id>/`、`outputs/collector_health_<epoch_id>/` 与 `outputs/d1_<epoch_id>/`。不可复制旧 heartbeat 或把旧缓存合并到新目录。

## 新窗口只读启动模板

确认第 1–3 项后，在项目根目录执行；将 `<epoch_id>`、`<confirmed_zone>` 替换为核定值。只读采集器仅调用 HA 状态和模拟器天气 GET，不调用 HA 服务写入。

```powershell
python -u collect_online_history.py --config local_online_config.json --cache-dir data/live_history_<epoch_id> --health-dir outputs/collector_health_<epoch_id> --node-id nodeA --epoch-id <epoch_id> --device-timezone <confirmed_zone> --iterations 0 --interval-seconds 5 --max-consecutive-errors 12
```

天气快照与采集器必须使用相同 epoch；在窗口运行时定期保存到不同文件，避免覆盖历史响应：

```powershell
python capture_api_weather_snapshot.py --config local_online_config.json --epoch-id <epoch_id> --output outputs/d1_<epoch_id>/weather_<capture_id>.json
```

## D1 验收

- 当前进程 PID、命令行、`epoch_id`、版本、heartbeat `started_at` 必须一致；旧文件不能证明当前运行。
- 三站各自报告墙钟新鲜度、独立设备 tick 推进、站间最大差、缺口数量和最大缺口。按“基本连续、基本新鲜”评估；孤立缺口不阻断当前 API-first 策略。
- 检查 `writes_performed=0`；天气缺点应保留 HA、天气字段置 `null` 和原因；非法 HA 单站拒绝不影响其余站。
- 窗口结束后保留最后 heartbeat、日志、缓存哈希及 `/api/contest` 终止状态，停止已确认归属的采集进程。停止后 heartbeat 只能称历史证据。

## D3 离线分析

```powershell
python analyze_d3_causal_pairs.py --snapshots outputs/d1_<epoch_id>/weather_<capture_id>.json --cache-dir data/live_history_<epoch_id> --epoch-id <epoch_id> --output outputs/d1_<epoch_id>/d3_pairs.json
```

若要报告 PV 误差，另提供在每次预测响应之前冻结、同 epoch 的逐站转换系数 JSON：`{"1":{"epoch_id":"<epoch_id>","scale_w_per_wm2":0.5,"frozen_at":"<UTC 时间>","last_training_device_time":"<带时区设备时间>"}}`，并加 `--calibration <path>`。没有可信冻结系数时可以审计因果配对，但 `scored_pairs=0`、无 PV 误差结论。只有预测目标严格晚于该次 API 响应后的设备时钟，并有目标时刻以后、获取以后、同站同 epoch 的独立 HA PV 才计一对。缓存天气辐照度绝不充当实测辐照度。

当前旧 v4 快照没有显式 epoch；离线审计报告为 `insufficient_data`，144 个目标点因 epoch 缺失被排除、0 有效配对、0 可评分配对：`outputs/d1_recovery_20261014/d3_pairing_insufficient_20261015.json`。D3 继续未完成，线上继续 persistence-only。
