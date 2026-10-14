# 储能决策可解释性框架

W2 本轮冻结交接见 [交接清单](docs/w2-freeze-handoff.md)，三站演示步骤与失败回退见 [E5 演示讲稿](docs/e5-demo-runbook.md)。E5 为历史练习 PV 核算，待集成验收；不代表完整决策回放或官方结算。

独立运行的 FastAPI / SQLite 服务与中文 React 前端，把策略的输入、动作、理由、替代方案、执行回读及结算串成可查证记录。

首版附带三个**人工演示**站点和四步 MILP 算例。示例费用属于模型估计，不代表真实收益或官方比赛结算。框架消费外部决策和执行记录；接入真实策略时需要注册对应模型，人工模型不能替真实决策做反事实。

## 本地启动

要求 Python 3.12 和 Node.js 22.12 或以上。在仓库根目录执行：

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.lock
python -m pip install --no-deps -e .
```

服务、工作进程和前端分别运行。完整命令见 [运行说明](docs/operations.md)。前端生产构建由后端同源提供；开发时使用 Vite 代理访问 API。

## 查看和接入

- 三站总览：查看建议、求解状态、预测状态、执行状态和结算状态。
- 决策详情：查看价格、SOC、数据来源、降级原因、成本和证据引用。
- 替代方案：修改首步充电功率，重新优化后续步骤，并保留正负费用差。
- 追问：支持“为什么这样做”“首步不充会怎样”“为什么不能增加功率”。
- 计划检查：从保存的决策快照检查目标 SOC 与设备时间截止点，不读新状态、不下发控制。
- 光伏分析：导入回放 trace 核算 JSONL，查看估算去向与 trace 记录的负价外送损失；没有潜在出力时不估算弃光。

真实策略的接入方法和边界见 [接入契约](docs/integration.md)，数据流和六个模块见 [架构说明](docs/architecture.md)，验收方法见 [验证说明](docs/validation.md)。服务启动后可通过 `/docs` 查看生成的 OpenAPI。

离线光伏核算示例：`python scripts/build_pv_accounting.py trace.csv report.jsonl --arm zero_flow --step-hours 1`。trace 至少需要 `site,time,arm,pv_w,load_w,flow,power_w,soc_after,sell,grid_export_kwh,negative_export_loss_eur`；若要统计充电功率瓶颈，再提供 `--max-charge-kw`。

训练集离线 replay 的无时区 `local_hour_time` 需显式传 `--time-basis dataset_local_hour`，只按原始日期分组；不会补猜 UTC，也不用于在线截止时间。默认仍要求带时区时间。E4 真实共享优化器五类验收：`python scripts/verify_plan_floor.py --output artifacts/e4-floor-acceptance/acceptance.json`（人工输入，不连接设备）。

已从提供的历史日志恢复并接入实际 MILP 源码。运行 `python -m energy_explain verify-model` 可验证 4、24、96 步模型记录；这些记录使用人工输入并明确标记来源。源码版本、启动方式与真实决策重放所需字段见 [恢复模型接入说明](docs/recovered-model-integration.md)。

本目录现在位于共同策略仓库。使用 `shared-milp-603de45c-cf1` 的记录直接调用上层 `energy_lab.rolling`，从同一次 shadow 求解导出输入与费用。共同源码、Python 路径和团队交接步骤见 [共同仓库协作接入说明](../EXPLAINABILITY_COLLABORATION.md)。历史提供器仅服务其明确匹配的旧模型版本，不作为共享模型缺失时的替代。

## 仓库边界

SQLite 运行数据、环境配置、凭证、原始会话日志和大型素材不进入版本控制。已完成共同 MILP 源码适配和人工输入接入验证；真实遥测、HA 控制闭环和官方结算验收依赖实际运行记录。
