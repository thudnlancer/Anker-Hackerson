# 储能决策可解释性框架

独立运行的 FastAPI / SQLite 服务、中英家庭能源 React 前端与中文证据工作台，把策略的输入、动作、理由、替代方案、执行回读及结算串成可查证记录。

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

## 家庭使用与证据接入

首页按单个家庭展示状态、当前建议和下一步。新增生活计划、光伏与储能、费用、家庭偏好页面；计划先做时间与设备功率检查，经确认保存在当前浏览器，尚未接入家庭调度或设备控制。默认英语、欧洲本地时区，支持中文。完整设计、新功能优先级和边界见 [UIUX 重构说明](docs/2026-10-14_家庭能源UIUX重构与功能规划.md)，本轮实际验证见 [UI 验收](docs/ux-redesign-acceptance.md)。

原三站解释工作台通过 `/#/evidence` 打开，决策与反事实原地址继续有效。

- 三站总览：查看建议、求解状态、预测状态、执行状态和结算状态。
- 决策详情：查看价格、SOC、数据来源、降级原因、成本和证据引用。
- 替代方案：修改首步充电功率，重新优化后续步骤，并保留正负费用差。
- 追问：支持“为什么这样做”“首步不充会怎样”“为什么不能增加功率”。

真实策略的接入方法和边界见 [接入契约](docs/integration.md)，数据流和六个模块见 [架构说明](docs/architecture.md)，验收方法见 [验证说明](docs/validation.md)。服务启动后可通过 `/docs` 查看生成的 OpenAPI。

已从提供的历史日志恢复并接入实际 MILP 源码。运行 `python -m energy_explain verify-model` 可验证 4、24、96 步模型记录；这些记录使用人工输入并明确标记来源。源码版本、启动方式与真实决策重放所需字段见 [恢复模型接入说明](docs/recovered-model-integration.md)。

本目录现在位于共同策略仓库。使用 `shared-milp-603de45c-cf1` 的记录直接调用上层 `energy_lab.rolling`，从同一次 shadow 求解导出输入与费用。共同源码、Python 路径和团队交接步骤见 [共同仓库协作接入说明](../docs/integration/EXPLAINABILITY_COLLABORATION.md)。历史提供器仅服务其明确匹配的旧模型版本，不作为共享模型缺失时的替代。

已完成真实 HA 三站读取、现场输入的本地 shadow、证据导入及反事实验收；未来功率仍明确使用持久假设。实际结果与复现方式见 [真实环境只读接入验收](docs/live-readonly-acceptance.md)。

现已支持三站连续只读采集、持久化待投递队列及重启恢复。前端展示采集心跳和记录新鲜度，并每 10 秒刷新总览。外部提交凭证和遥测由服务端校验后关联到指令；启动、恢复及真实读取验收见 [连续采集与执行证据接入](docs/continuous-readonly-integration.md)。

## 仓库边界

SQLite 运行数据、环境配置、凭证、原始会话日志和大型素材不进入版本控制。共同 MILP 源码适配和真实遥测读取已验证；实际 HA 下发回执、控制闭环和官方结算仍待对应运行记录验收。
