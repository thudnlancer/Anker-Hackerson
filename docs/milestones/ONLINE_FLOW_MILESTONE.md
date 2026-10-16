# 在线流程里程碑验收矩阵

## 目标

证明本机策略能够在模拟比赛窗口内读取 API/HA、运行策略、执行 HA 写入、在下一 tick 回读、发生异常时安全隔离，并让看板记录新成绩；随后具备可部署准备，不以负收益作为唯一通过条件。

## 验收矩阵

| 门槛 | 必须证明的事实 | 权威证据 | 当前结论 |
|---|---|---|---|
| O1 | `running/mock` 到 `ended/mock`，runner 自动退出 | run3 `console.log` 的 `mock_ended`、进程状态、`ended_snapshot.json` | 已通过 |
| O2 | API 价格与 HA 设备时间完成逐 tick 对齐，冲突有明确回退 | `audit.csv`、console site rows 的 alignment 字段 | 部分通过；仍有档位冲突回退 |
| O3 | HA 命令包含模式/流向/功率事务审计 | `audit.csv`、`console.log`、reconciliation journal | 已通过，但有失败事务 |
| O4 | 下一 tick 回读能够区分已验证、陈旧和未知 | `previous_readback_verdict`、`reconciliation_required` | 已通过；陈旧回读较多 |
| O5 | HA 失败不自动重试、不跨站扩散，事故持久化 | journal pending、失败行、事务单元测试 | 修复已测；下一场需真实复验 |
| O6 | 看板新增本次 mock run，可由前后快照对账 | `prelaunch_snapshot.json`、`ended_snapshot.json`、dashboard SHA | 已通过，runs/mock_count 6→7 |
| O7 | 三站赛后恢复到安全模式，或未恢复时保留阻塞证据 | `post_end_recovery.json`、post-recovery snapshot、journal | 未通过；三站仍 pending/third_party_control |
| O8 | 核心、产品、前端和配置门禁可复现 | pytest、产品 lock、Vitest、config SHA | 部分通过；W2 独立依赖环境仍待建立 |
| O9 | 比赛机可部署且不间断运行 | 固定 commit、部署 dry-run、PID/日志/重启证据 | 未验收；当前明确暂停远端控制 |

## 通过规则

O1、O3、O4、O5、O6 是流程闭环硬门槛；O7、O8、O9 是发布前硬门槛。O2 的回退可以存在，但必须可解释且不伪造优化归因。任何 pending reconciliation 都阻止“完整在线流程”宣称通过。
