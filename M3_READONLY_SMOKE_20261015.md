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

新窗口启动后，再验证同一窗口身份、三站新鲜设备时间及价格窗口对齐，并保存连续多 tick 审计、零写入计数与恢复记录。本轮不启动新模拟赛、live、正式 100 站 F2，不修改 F1 候选 manifest 或任何签核/授权字段。
