# M3 只读入口与三站 smoke

本轮仅单 tick 连接检查及模拟故障注入，不是连续在线 M3，不涉及 HA 控制写入或正式收益。

## 入口

W3 同步修正的 `run_remote.ps1` dry-run 分支转交 `remote_dry_run_ctl.sh start`；控制脚本显式运行 `run_live.py --mode dry-run --strategy milp`。live 分支也显式传入 `--strategy milp`，但本轮未执行。远程部署、守护与恢复证据由 W3 单独交付，不能由本地测试代替。

## 实际环境连接

```powershell
python run_live.py --config local_online_config.json --mode dry-run --strategy milp --once --audit-csv outputs/m3_readonly_smoke_20261015/audit.csv
```

启动输出确认 `strategy=milp`、`write_enabled=False`。三站均返回 `stale_or_unavailable`，设备时间为 `2026-10-17 08:15:00`。因此本轮证明三站读取入口可达及过期状态拒绝；未进入求解、未生成 audit.csv，不能声称实时策略/价格时间对齐通过，也不能声称下一 tick 回读或持续 M3 通过。未放宽新鲜度检查、未重开旧窗口。

- 配置字节 SHA-256（不交付凭证）：`73d50bb5b70b7b457effca67478a2fb41000ae68c2cd1f562731e0439d62f2a3c`
- 控制台日志：`outputs/m3_readonly_smoke_20261015/console.log`
- 日志 SHA-256：`514ce84467742c6238df7bca17612a8617c9f067219c0b15b1fc4b7b450609ab`

## 模拟故障注入

```powershell
python -m pytest tests/test_m3_readonly_smoke.py tests/test_run_live_chain.py tests/test_run_live_time.py -q
```

结果：21 passed。其中新增 4 项测试覆盖三站单 tick 零写入及审计 CSV、单站过期隔离、单站 HA 读失败隔离、全局缺价及单站时间错位回退。价格异常不调用对应优化器，回退标为 non-optimizer-attributed，所有新增测试均断言 HA 写入调用为空。测试的健康状态和求解计划为模拟输入，不是远程比赛机实测。

## 后续门槛

新窗口启动后，在环境机只读执行（不使用 `live`）：

```bash
./remote_dry_run_ctl.sh start
```

该守护进程会持续运行 `run_live.py --mode dry-run --strategy milp`，写入 `logs/live.log` 和 `logs/run_live_audit.csv`。停止/查看使用 `./remote_dry_run_ctl.sh stop|logs|status`。收集至少 3 个完整三站 tick 后，在本地用：

```powershell
python validate_m3_readonly_log.py --console logs/live.log --audit logs/run_live_audit.csv --min-ticks 3 --output outputs/m3_window_<window>/validation.json
```

校验器要求启动头明确为 `dry-run`/`write_enabled=False`/`strategy=milp`，每个 tick 恰有 1/2/3 三站、零写入、审计行数一致且三站均非过期。当前窗口的单 tick 日志会被校验器拒绝（不足 3 tick 且三站过期），这是预期结果。`local.timezone` 仅为本机时区元数据，不再映射到 `device_timezone`。比赛机的扁平 `remote_runtime_config.json` 必须显式填写并经负责人核实 `device_timezone`；模板中的 `Asia/Shanghai` 只是待核实示例，不是环境证明。缺少或无效时区时，MILP 跳过优化器并记录 non-optimizer-attributed 自用回退；旧 dispatch 路径阻断经济动作且零写入。新窗口先执行 `python check_m3_readonly_preflight.py --config remote_runtime_config.json --output logs/m3_preflight.json`，核对三站后缀、`device_timezone_source`、`state_fresh=true`、`price_alignment=aligned`，再开始持续 dry-run。旧窗口三站 `state_fresh=false`，不能进入优化器，也不能视为 M3 通过。

本轮不启动新模拟赛、live、正式 100 站 F2，不修改任何签核/授权字段。源码变更时重建未签核 F1 manifest，旧 SHA 随之失效。

## 最新覆盖说明：API current 档位契约

本节覆盖上文“必须显式填写设备时区”的旧门槛：实际 `/api/price/current` 已确认提供唯一 `start/end` 档位，主链路不再要求 IANA 时区。每轮 current → 两日价格 + HA → current，最多 3 次；稳定且 HA 新鲜、日期/小时相符后才可求解。显式时区若存在则必须交叉校验，冲突拒绝。未来窗口按 API 自身时间戳检查连续性，不按数值、数组下标或 `Asia/Shanghai` 猜测。

开赛前先运行 `check_m3_readonly_preflight.py`；新窗口多 tick 日志须包含原始 HA 时间、API current start/end、所选档位、解析时刻、对齐来源和拒绝原因。日志校验器现额外要求 `alignment_source=api_current_slot`、无对齐拒绝、每站设备时刻递增。旧缺字段日志不作为本轮 M3 证据。

真实接口样本、只读预检、测试和最新 manifest SHA 见 [M3_API_ANCHOR_DELIVERY_20261015.md](M3_API_ANCHOR_DELIVERY_20261015.md)；详细契约见 [M3_TIMEZONE_CONTRACT_20261015.md](M3_TIMEZONE_CONTRACT_20261015.md)。真实旧窗口仍 stale，未完成连续 M3。
