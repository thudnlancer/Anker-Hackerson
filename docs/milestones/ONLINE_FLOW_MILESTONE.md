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
| O8 | 核心、产品、前端和配置门禁可复现 | pytest、产品 lock、Vitest、config SHA | 部分通过；集成树核心 365、独立锁定环境产品后端 99 + 49 subtests、前端 18 及 build 已过；配置与完整发布门禁仍待复验 |
| O9 | 比赛机可部署且不间断运行 | 固定 commit、部署 dry-run、PID/日志/重启证据 | 未验收；当前明确暂停远端控制 |

## 通过规则

 O1、O3、O4、O5、O6 是流程闭环硬门槛；O7、O8、O9 是发布前硬门槛。O2 的回退可以存在，但必须可解释且不伪造优化归因。任何 pending reconciliation 都阻止“完整在线流程”宣称通过。

2026-10-16 集成状态：W2 增量已在独立 `integration/online-milestone` 分支的 `bc94545` 合入并通过上述本地回归；主工作区、W3 和 E4 尚未与其完全对齐。run3 的 O7 三站恢复失败仍是阻塞项，本轮只做代码与只读证据集成，未再次执行 HA 写入或开启模拟赛。因此当前结论仍为**未通过完整在线流程里程碑**，不能把测试全绿等同于 O7/O9 通过。

后续对齐：主工作区已快进到 `564cf75`，与当时的集成候选代码一致；W3/E4 尚未收敛，三站 pending 未解除。这里的历史状态段落保留作为审计时间线，不应把“主工作区尚未对齐”理解为当前状态。

2026-10-16 05:06:53 UTC 只读复核：`outputs/local_mock_20261016_postmerge_readonly/snapshot.json` 的文件 SHA-256 为 `EA7D287857E2BD9A563113C4F62CF7480B8E14F45C30BD0D7BC5367A6ECD12CA`；比赛仍为 `ended/mock`，看板 `results.count=7`。三站 HA mode 均为 `third_party_control`，设备时间均停在 `2026-10-17 08:15:00`；站 1/2 为 charge 且设定功率 5000/2541 W，站 3 为 discharge 且设定功率 0 W。该复核只有 GET，没有 HA 写入；**O7 继续失败**，run2 journal 三站 pending 不得自动清除。下一个模拟窗口前须由操作者核验安全模式真实回显和事故处理，不能把赛后恢复 POST 的 HTTP 确认当作设备确认。

`energy_lab.zero_flow` 现提供离线判定与恢复计划，测试覆盖设定回显、电池双侧功率和被动电网结算；这不是硬件验收。没有真实下一设备 tick 的五项回读证据前，O7 和零流量执行资格均保持未通过。

B1 runner 已集成到 `scripts/online/run_zero_flow_verification.py`：默认 dry-run；live 要求显式参数、授权说明、安全配置和现存 reconciliation journal，并只读扫描 `outputs/` 下全部 journal，任一 pending 时在执行 HA/API 请求前 fail-closed。当前三站 pending 会阻止 live。离线演练及 CLI 门禁测试通过，但没有真实写入或硬件回读，O7 状态不变。

最新代码基线 `b706fd2`：W3 只读 collector launcher 已改为显式路径与次数参数，避免复用旧 mock 缓存或默认无限运行；主工作区与集成 worktree 对齐。该代码门禁不改变 run3 的事故结论：看板 6→7、三站 HA 事务与失败隔离有证据，但赛后模式仍未回显，O7/O9 未通过。下一轮本机模拟赛必须在三站 pending 经人工核验后才可启动，并用新窗口重新验证 O2/O5/O7。

当前基线更新为 `2e2f0a2`：D2 和 collector restart 工具已加入结构化目录，但默认分别为 plan-only 和只读 preflight；核心全套 370 项通过。三站 pending 仍未清除，O7 继续失败；这次代码重构不构成新的在线比赛或 HA 恢复证据。

2026-10-16 部署准备补充：新增 `deployment_preflight.py`（实现位于 `scripts/maintenance/`），仅检查本地固定提交、干净工作区、必需文件受 Git 跟踪、模板为 dry-run、私有配置未受 Git 跟踪，并记录受控文件散列；不连接 EC2/HA，也不读取本地凭证内容。实际浏览器终端 + Git 拉取流程仍需在恢复远端控制授权后单独验证。此项即使本地通过，也仅证明 O9 的部署前置准备，不代表比赛机已部署或可不间断运行；O7 三站 pending 和 `third_party_control` 状态不变。加入该工具后的核心测试为 373 passed。

最新复核（2026-10-16，提交 `1b1c9f7`）：主工作区与 `integration/online-milestone` 已对齐且干净；两边核心测试均 373 passed。固定提交的本地 browser-terminal dry-run preflight 通过，报告位于 `outputs/deployment_preflight_20261016/report.json`，未进行远端动作或 HA 写入。只读打开当前 `outputs/local_mock_20261016_run2/ha_reconciliation_state.json`，`pending` 仍含站点 `1/2/3`，状态为 `attempted_unknown`；更早的 `clearances` 不对应这些 incident，故不构成清除依据。O7 仍未通过，且本机下一轮模拟赛仍须先由操作者核验和处理当前 incident。
