# 未跟踪 Worker 文件归属清单

本清单用于重构期间保护尚未完成归属的 worker 产物。它们不是“垃圾文件”，也不是当前发布包的一部分；在逐项审查前禁止批量删除、移动或纳入部署。

| 文件 | 来源/用途 | 当前处置 | 纳入条件 |
|---|---|---|---|
| `audit_physical_calibration.py` | B3 只读 HA 物理校准 runner | 保留根目录，尚未发布 | 改为 `scripts/online/` 后补齐路径、配置与只读证据测试 |
| `run_zero_flow_verification.py` | B1 单站零流量验证，带 live 写入选项 | 保留根目录，禁止 live | 完成安全开关审查、离线 runner 测试和人工授权流程 |
| `launch_w3_collector.py` | W3 采集器启动器 | 保留根目录，尚未发布 | 与主线天气降级、epoch、单站隔离契约对齐 |
| `restart_w3_collector.ps1` | W3 watchdog 重启演练 | 保留根目录，尚未发布 | 修正固定路径并完成离线进程归属测试 |
| `run_d2_expansion.ps1` | D2 只读跨日采集链 | 保留根目录，尚未发布 | 固定工作目录、输入输出 manifest 和零写入门禁 |
| `archive/.gitkeep` | 归档目录占位 | 可纳入版本控制 | 仅用于保持空目录，不包含运行数据 |

已完成归属的 worker 增量：

- `src/energy_lab/calibration.py`、`src/energy_lab/zero_flow.py` 及 34 项测试，提交 `751f8c6`。
- D1/D2 只读分析实现及测试，提交 `cd1d13d`；根目录入口仍保留兼容性。
- B2/HOLD 评审文档已迁入 `docs/milestones/` 和 `docs/decisions/`，根目录仅保留短兼容链接。

该清单不代表 B1 硬件验证、D1/D3 现场采集或 O7/O9 里程碑已通过。run2/run3 事故证据继续按里程碑文档保留。
