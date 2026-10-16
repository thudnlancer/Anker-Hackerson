# 未跟踪 Worker 文件归属清单

本清单用于重构期间保护尚未完成归属的 worker 产物。它们不是“垃圾文件”，也不是当前发布包的一部分；在逐项审查前禁止批量删除、移动或纳入部署。

| 文件 | 来源/用途 | 当前处置 | 纳入条件 |
|---|---|---|---|
| `audit_physical_calibration.py` | B3 只读 HA 物理校准 runner | 已迁入 `scripts/online/`，根目录保留兼容入口；只 GET，不下发 HA 服务 | 需在受控环境重新采集只读校准证据；代码通过 16 项相关测试 |
| `run_zero_flow_verification.py` | B1 单站零流量验证，带 live 写入选项 | 已迁入 `scripts/online/`，根目录保留兼容入口；默认 dry-run | live 需显式 `--mode live`、人工授权、三项配置开关和现存 journal；程序还扫描 `outputs/` 下所有 reconciliation journal，任一 pending 会在联网前阻断。当前 run2 有三站 pending，因此 live 必然拒绝 |
| `launch_w3_collector.py` | W3 采集器启动器 | 保留根目录，未发布 | 固定 cache/health 路径且启动无限循环；需改为显式参数并完成主线采集契约测试 |
| `restart_w3_collector.ps1` | W3 watchdog 重启演练 | 保留根目录，未发布 | 含 `Stop-Process` 和固定 PID/路径；必须先完成进程归属保护与离线演练，当前禁止运行 |
| `run_d2_expansion.ps1` | D2 只读跨日采集链 | 保留根目录，未发布 | 依赖未在主线登记的 `run_irradiance_shadow.py`，需补 manifest、路径和零写入门禁 |
| `archive/.gitkeep` | 归档目录占位 | 可纳入版本控制 | 仅用于保持空目录，不包含运行数据 |

已完成归属的 worker 增量：

- `src/energy_lab/calibration.py`、`src/energy_lab/zero_flow.py` 及 34 项测试，提交 `751f8c6`。
- D1/D2 只读分析实现及测试，提交 `cd1d13d`；根目录入口仍保留兼容性。
- B2/HOLD 评审文档已迁入 `docs/milestones/` 和 `docs/decisions/`，根目录仅保留短兼容链接。
- B1/B3 runner 已纳入 `scripts/online/`，CLI 安全门禁测试通过；B1 的离线 fixture 演练不是硬件验收，也没有产生 live 写入。

该清单不代表 B1 硬件验证、D1/D3 现场采集或 O7/O9 里程碑已通过。run2/run3 事故证据继续按里程碑文档保留。
