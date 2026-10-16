# 项目重构与“打通在线流程”里程碑

## R0：盘点与冻结

建立 worktree、代码、文档和输出清单。保留当前主工作区和 HA 事故证据，不操作远端比赛机或正式比赛。

## R1：无语义变化的目录化

建立 docs/architecture、docs/operations、docs/milestones、docs/decisions 和 scripts/online、scripts/analysis、scripts/maintenance。迁移脚本时保留兼容入口或同时更新全部引用。旧文件先归档并生成 SHA 清单，不直接删除。

## R2：分支集成

每个 worktree 先提交 SHA、变更清单、测试结果和证据路径，再逐个合入。每次合入运行核心 pytest、产品测试、配置安全检查和引用扫描。记录冲突决策与回滚点。

## R3：在线流程验收

必须同时证明：本机模拟赛从 running/mock 到 ended/mock 且 runner 自动退出；HA 下发、下一 tick 回读、失败隔离均有审计；未知下发不重试、不跨站扩散；看板新增 run 可由前后原始 JSON 和 SHA 对账；核心与产品测试通过；三站 pending 经人工核验恢复；形成单一证据包、manifest 与验收报告。

当前 run3 已证明赛程结束和看板落库，但三站 journal 仍 pending、设备未回显安全模式；因此里程碑尚未通过。目录化和 worktree 集成也尚未完成。
