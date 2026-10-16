# 项目共享进度与并行工作分工（2026-10-14）

## 用户优先级更新：暂停比赛机，先跑通本机模拟赛（2026-10-16）

**覆盖本页此前所有 W3 远端切换/HA 写入工作令**：用户要求暂停对比赛机的控制，直到本机完整模拟赛闭环完成。不再向比赛机推送、fetch/切换版本、停止/启动进程、修改配置或发控制命令；远端保持现状。此前已发布的 Git 固定候选 `2a49d23` 仅是代码发布，不代表比赛机已部署。本阶段仅在本机与模拟环境工作；模拟赛成绩高低不是验收门槛。

当前 `/api/contest` 返回 `mode=ended, track=mock`，该场 start/end 为 `2026-10-15T16:41:16Z`/`17:37:16Z`，不能再证明从开赛到结算的本机流程，也不能把旧 HA device_time 拼入下一场。此前本机代理链路在 `2.5s` 截止下 37 个完整三站轮次均未形成有效 MILP 候选。为优先验证流程，本机 `local_online_config.json` 已设为 `proxy_mode=environment`、`device_timezone=null`（避免未经证实的 `Asia/Shanghai` 与 API `Z` 冲突）、轮询 `10s`、采集预算 `6s`、整轮截止 `9.5s`、每站求解 `2s`；三个 HA 写入开关继续 false。只读预检 `outputs/local_mock_readiness_20261016/preflight_ended.json` 完成 current/价格/三站 HA 批量读取约 `3.062s`，三站均因旧窗口 stale 安全拒绝、零写入。10 秒是本机**流程验收暂用节奏**，不是策略调优或 SOP 强制参数；超时仍必须拒绝，不能跨档复用旧候选。

本机下一步：先准备本机唯一 runner 的 PID/独立日志/audit/journal 与比赛阶段检查；再开启**新一场**模拟赛。准备期只做只读预检，正式 `running/mock` 后观察设备时间重置与新鲜三站候选，随后用本机模拟赛专用配置开启 HA 写入，验证至少一条完整 HA 下发与下一 tick 回读，持续到 `ended/mock` 并核对看板新增成绩。当前代码 `allow_formal_contest` 字段仍是通用 live 写入闸门，打开它本身不会触发正式赛；不得点击正式比赛。若网络/代理抖动导致拒绝，记录比例并优先保证进程稳定与安全隔离。发生未知下发时不自动补发或解除 journal。只有本机完成窗口全程、下发/回读和成绩入榜，才开始讨论迁移比赛机。

## 当前优先级改为完整模拟赛闭环（2026-10-16；覆盖下文只读 M3 先行指示）

用户已明确：本次模拟比赛没有成绩门槛，先证明完整流程，之后再调优策略。模拟赛可以反复开启；此指示允许在**模拟赛窗口内**进行 HA 控制写入，不等于启动正式比赛。F1 签名、F2 授权和先取得 10 tick 只读 M3 不再作为本次模拟赛写入的管理前置；仍须遵守单实例、HA 数据/控制边界、设备时间、分步下发审计、未知结果隔离和人工恢复。不要为凑 10 tick 只读样本错过比赛窗口。

**代码发布已完成**：集成负责人已把固定候选 `2a49d236e6b2d873eaac6fda969ce7ae9f1f763a` 精确推送到 `origin/feat/explainability-integration`，远端引用已核对。未发布本地后续代理提交 `6b72ff3`、未提交工作区、凭证配置或其他 Worker 文件。下文“候选未推送”是历史状态。比赛机侧尚未由集成负责人控制进程。

**W3 当前完整工作令**：立刻在比赛机 fetch 并核对固定 SHA、当前模拟赛状态、三站设备时间推进、旧 runner 归属、systemd/nohup 单实例及只读配置。受控停旧且确认 PID 退出后，切到固定 SHA；先以 `--strategy milp --mode dry-run` 做一次快速三站预检，确认 current/HA 锚定、新鲜状态与设备后缀。不要求为了统计而持续只读。然后在本次模拟赛专用、权限 600 的未跟踪配置中开启 live 与 HA 写入，明确保留旧配置备份；当前代码的 `allow_formal_contest` 只是总写入闸门，设为 true 不会点击网页“正式比赛”，但日志必须说明这是模拟赛专用配置。用唯一 `run_live.py --mode live --strategy milp` 实例、独立 PID/日志/audit/journal 运行；`remote_dry_run_ctl.sh` 只能 dry-run，不得用于 live。首次每站下发后在下一设备 tick 核查 HA 模式/流向/功率和审计；持续到 `ended/mock`，停止该 live runner 并核对看板“成绩”出现本次新记录及三站/总分标识。验收只看闭环、不中断运行、命令回读与成绩留存，不设置收益阈值。部分写入/超时站点保持 journal 隔离，不自动补发或解隔离；重复进程、错误比赛阶段、无审计写入或 journal 损坏时停止写入并留证。若本次窗口已错过，保存现有证据并开启下一场模拟赛完成全程，不冒充完整闭环。**不得点击正式比赛。**

W3 回传：完整 SHA、配置 SHA（不含明文凭证）、比赛阶段前后原始响应、PID/命令与启动结束时间、console/audit/journal、各站 HA 下发及下一 tick 回读、看板新增成绩证据和任何隔离事件。旧版只读日志不计入完整闭环。

## W1 代理增量最新状态（2026-10-16）

W1 已把代理修复作为独立后续提交 `6b72ff383537484e3088a63ceae83d4b8170a92e`（仅本地，未推送/部署）；原 W3 固定候选 `2a49d23` 的受控切换指令**未由此自动更改**，任何改用新提交须由集成负责人重新指定。HA 登录/读取与模拟器读取共用显式 `proxy_mode`；本地 `environment`、远程模板 `direct`，不改采集 `2.5s` 或整轮 `5s` 截止。全量测试 `354 passed`，未签 F1 manifest SHA `5058b1dd277543462edd376f6e81c77860d73198ed24d3fffc553ea25b43f513`。本地只读预检在代理下成功取 current，但价格读取超预算；随后连续 dry-run 审计至少 37 个完整三站轮次均安全回退、零 HA 写、零有效 MILP 候选，**10 tick M3 未通过**。证据见 `W1_DELIVERY_TRANSACTION_CANDIDATE.md` 和 `outputs/m3_proxy_10tick_20261016/audit.csv`。本地配置 SHA `51096f1a6dfb060de95cd90a3ca2cd1fc780f609d7827f242de7953f34904546`。环境代理承载明文 HTTP 的 HA 凭证/令牌，W1 仅确认应用日志未主动输出它们；代理自身日志策略仍需运维核查。共享文档保持未提交，以保留其他 Worker 的工作区改动。

## W3 当前工作令：比赛机固定候选只读 M3（2026-10-16）

**现在执行准备检查，不等待新的小步指示**：通过 EC2 Instance Connect 在比赛机记录 `git rev-parse HEAD`、`git status --short`、`./remote_dry_run_ctl.sh status`、现有 PID 的 `/proc/<pid>/cmdline`、`systemctl --user is-enabled/is-active solarbank-dry-run.service`、只读配置的 SHA-256 和三个写入开关。只读验证环境机 HA/模拟器连通性及比赛 `/api/contest` 阶段；记录当前 `running/mock` 与设备时间推进证据。当前远端仍无 `2a49d23`，在 fetch 到固定提交之前不得停旧 runner 或把旧日志计为本候选 M3。不要复制旧 epoch 的缓存、日志或 heartbeat 到新目录。

**发布后连续完成，不再逐项等待**：集成负责人发布 `2a49d236e6b2d873eaac6fda969ce7ae9f1f763a` 后，W3 在远端 fetch 并校验 `git cat-file -t <完整SHA>` 为 `commit`，核对工作区无会被切换覆盖的改动、Python/依赖与配置只读开关；若 systemd 已管理旧 runner，先说明服务启动命令、固定 SHA 和日志位置，由集成负责人决定受控切换方式，**禁止同时用 nohup 和 systemd 启动第二实例**。确认可回滚后受控停旧，检验 PID 退出，切到完整固定 SHA，设置唯一 `PINNED_COMMIT`/独立 epoch 的 `LOGFILE`、`META`、`PIDFILE` 和 audit 路径，启动唯一 `--mode dry-run --strategy milp` runner。若控制脚本不能将 audit 写入独立目录，先保留旧 audit 并记录分割点或用显式受控启动命令，不得覆盖旧证据。

**M3 验收与回传**：先跑只读 preflight；仅在模拟赛正式运行且新 epoch 的设备时间确实重置并推进、三站新鲜且价格档位对齐后，统计连续至少 10 tick。每站交付可用候选/拒绝原因、轮次与求解耗时分布、跳 tick、API current 前后档位、设备时间、`writes_performed=0` 和 HA POST 尝试数；运行 `validate_m3_readonly_log.py --min-ticks 10` 并附原始 console/audit、配置摘要、固定 SHA、PID/启动时间和比赛阶段响应。任何不满足条件的结果明确标为“只读安全拒绝/未验收”，不要重试旧候选、放宽 2.5s/5s 截止或自行改时区。完成后继续单实例 dry-run 并汇报；不启用 HA 写入、F2、主机重启或双节点。阻断时回传准确失败步骤及证据，由集成负责人处理发布/切换问题。

## 集成负责人对 W3 更新的决策（2026-10-16）

复核远端 `origin/feat/explainability-integration` 当前仍为 `6c1b3eafcd676b9038e2d5c981a3532d39a03223`；本地 HEAD `2a49d236e6b2d873eaac6fda969ce7ae9f1f763a` ahead 2，尚未发布，W3 无法在比赛机 fetch 到该固定候选。W3 尚未切换原 runner，也没有新窗口固定候选 M3 证据，这一状态判断正确。工作区另有 W1 未提交的代理适配等改动，主控复跑当前策略测试 `354 passed`；它们**不属于**已验收的 `2a49d23`，不得混入当前模拟窗口部署。比赛机若可直达环境机，使用 `2a49d23` 的原始直连 HA 登录路径即可；代理补丁留作本地链路的独立后续候选。

**当前模拟窗口的最短路径**：由集成负责人先单独核对并发布已有固定候选 `2a49d23`（仅将已提交历史推送至受控远端引用；不提交或推送工作区改动）。发布成功、W3 在比赛机 fetch 并校验完整 SHA 后，W3 核查旧 runner 当前 PID/归属和只读配置，受控停旧再启唯一 `--mode dry-run --strategy milp` 新 runner，独立输出目录保留原日志；仅在比赛已经 `running/mock` 且设备时间新鲜推进后统计至少 10 tick。回传实际 SHA、PID、配置摘要、preflight、console/audit、逐站准入和时延/拒绝汇总、零 HA POST 证据。若发布或远端可达性失败，保留旧进程并记录阻断，不拿旧版本日志冒充候选验收。**本条是工作指示，不表示已执行推送、停进程或 M3 验收。**

## W3 比赛机侧固定候选 M3 接续（2026-10-16）

用户已指定在可直达环境机的比赛机侧复测只读 M3；本轮允许的目标是固定候选 `2a49d236e6b2d873eaac6fda969ce7ae9f1f763a` 的只读验证，不包括 HA 写入、systemd 叠加、远程双节点或 F2。该指示调整 M3 执行位置，不代表候选已经部署。

- 本地复核：候选存在；fetch 后 `git branch -r --contains 2a49d236e6b2d873eaac6fda969ce7ae9f1f763a` 无输出，当前已获取的远端引用未包含候选。按既有集成约束，不由 W3 自行推送，先由集成负责人发布固定提交。
- 当前助手没有比赛机终端控制通道，需由用户通过 EC2 Instance Connect 执行并回传证据。旧 PID `758703`、旧配置 SHA 与旧日志仅为历史记录，不能作为当前进程/新窗口证据；尚未停止或切换远端进程，尚未执行比赛机侧 M3。
- 执行顺序：先 fetch 并核实完整候选 SHA 可取得、当前进程归属、工作区差异和只读配置；候选不可取得时不停止旧实例。确认后受控停止旧 runner，固定候选，再以独立 epoch/输出目录运行唯一 `--mode dry-run --strategy milp` runner。不得叠加 systemd。
- 不因缺少 `device_timezone` 而写入猜测时区：采用候选的稳定 API current 前后夹取与新鲜 HA 标签锚定；如配置显式时区，则须交叉校验。保留 2.5s 采集预算与 5s 整轮截止。
- 新窗口正式开始且设备时间重置推进后才统计至少 10 tick；保留 preflight、PID/命令、提交 SHA、配置 SHA、启动时间、独立 console/audit 及验证结果。`validate_m3_readonly_log.py` 必须显式 `--min-ticks 10`；其通过仍不能代替完整的时延分位数、跳 tick/拒绝分类和 HA 请求尝试审计。准备期、旧窗口 stale、离线注入均不算 M3；目前 M3 仍未完成，D3 亦不因此完成。

## 准备期连通性更正：本地代理可用（2026-10-16）

复核发现本地 `requests` 环境代理指向回环代理；经代理访问环境机 HA `:8123/` 与模拟器 `:45678/` 均返回 HTTP 200，绕过代理直连 HA 超时。之前 `Test-NetConnection` 及预检失败只证明**直连不可达**，不能断言环境机整体不可达。根因是 `src/energy_lab/config.py::exchange_ha_token` 创建 `requests.Session()` 后显式设置 `session.trust_env = False`，使 HA 登录流忽略本机代理；因此本地 `check_m3_readonly_preflight.py` 在登录流连接阶段失败，未采到新窗口数据、零 HA 控制写入。W1 应将“是否使用环境代理”做成明确的只读/运行配置或在本地受控修复，默认与其余 `requests` 调用保持一致；补代理启用/禁用回归并核查 HA 凭证不写入代理日志或命令行。修复后重新跑只读预检，再决定本地是否可承担 10 tick M3。不要用直连 TCP 失败替代应用层代理测试，也不要把 HTTP 200 主页视为已通过鉴权、档位对齐或三站策略。

**代理路径临时只读复测**：未改源代码或配置，仅在一次性 Python 进程内使 HA 登录 Session 使用本机环境代理；鉴权和模拟器访问成功。两次预检均在 `2.5s` 采集截止处安全拒绝，三站没有可用状态且 `writes_performed=0`；首轮请求耗时依次为模拟器登录 `0.494s`、current `0.423s`、两日电价 `1.282s`、HA 批量 `0.298s` 后截止，第二轮在价格请求中截止。产物：`outputs/m3_prestart_20261016/preflight_proxy.json` 与 `preflight_proxy_retry.json`。两次 current 档位已改变，不能将它们拼成同一 tick；当前本地代理链路不足以证明 5s/2.5s 采集预算下可稳定形成三站候选。W1/W3 优先在环境机可直达的比赛机侧复测固定候选；本地若要继续承担 M3，需先做有界延迟与预算评估，不得简单放宽截止或关闭安全拒绝。

## 新模拟赛准备期即时指示（2026-10-16）

用户已启动新模拟比赛，现处 SOP 所述约 5 分钟倒计时准备期；倒计时结束才重置并开始约 56 分钟的加速比赛。**准备期数据不计入新窗口连续 10 tick M3。**本地以 `local_online_config.json` 运行只读 `check_m3_readonly_preflight.py`，在 HA 登录连接阶段超时，未读到比赛数据、未写 HA；本机到配置的环境机 `52.41.23.84:8123` 和 `:45678` 两端口 TCP 检查均失败。当前阻断是本地网络可达性，不能据此判断设备时区、价格对齐或策略有效性，也不要因失败而放宽门槛。

**准备期并行行动**：W1 核对候选 `2a49d236e6b2d873eaac6fda969ce7ae9f1f763a`、只读配置及显式 `--strategy milp --mode dry-run` 启动参数，准备独立输出目录和 10 tick 汇总；W3 在能访问环境机的比赛机侧核实单实例原 dry-run 状态、现有固定提交/配置，并执行当前固定版本的只读连通性与阶段预检，记录 API current、三站 HA device_time 与准备期历史数据可用性。**不得直接把本地新候选当作远端已部署版本**；若需切换，须先由集成负责人核对和推送固定提交，并确保不并行启动第二个 runner。倒计时结束、设备时间确实重置并推进、三站状态新鲜且价格档位对齐后，才开始新窗口至少 10 tick 的零写入 M3 统计；如端口仍不可达，先解决环境机连通性或在可达的比赛机执行，不要盲目重试本地登录。任何 HA 写入、正式 F2 和自动切换 live 仍禁止。

## 集成验收：W1 第 1、2 项通过，转入新窗口 M3（2026-10-16）

主控核对候选提交 `2a49d236e6b2d873eaac6fda969ce7ae9f1f763a`：23 个提交文件均为 W1 下发/锚定/调度链路及其测试和交付说明，其他 Worker 的工作区改动未混入；`git diff --check HEAD^ HEAD` 无报错；复跑 `python -m pytest tests -q` 为 **351 passed**；manifest SHA-256 `9f248d47243f60e7af8a1637b1dca8b090a5859d4f8bcac681430c56de8138de`、本地配置 SHA-256 `257d1a076e6930ad1240cbbf28208d6c9e5d6540c74bbb305bf29d9d0bda0f8c` 与交付记录一致。持久化 journal 在每个 POST 前落盘，超时/崩溃后按站隔离，人工解隔离留审计；并发占槽、重启恢复和坏 journal 拒绝有测试覆盖。**判定：第 1、2 项作为模拟赛准备的代码候选通过；不等于真实 HA 写入或 M3 已通过。**候选仍仅本地、未推送/部署，F1 未签、`f2_authorized=false`。

W1 现在连续推进第 3 项，不再等待新的局部指示：准备固定提交、只读配置和独立输出目录，在新窗口开始时先校验 API current、三站 HA `device_time`、价格档位、设备时间推进和零写入开关，然后连续至少 10 tick 跑 `--strategy milp --mode dry-run`；产出逐站/逐轮记录与汇总，包括完整率、新鲜率、对齐率、P50/P95/P99 时延、求解与跳 tick、拒绝原因、未审计异常和零 HA POST。旧窗口 stale 只能记录为等待新窗口，不能补旧 epoch 或用注入数据代替。完成后将证据路径和结论回填，交集成负责人核对；在此之前 W3 不切换远端。

部署前必须显式锁定 `--strategy milp`：CLI 当前默认是旧 `dispatch`，它不使用新增的持久化 journal；W1 应补配置/启动命令检查或将模拟赛运行入口固定为 MILP，避免 W3 误启动旧策略。live 模式的 journal 仅支持单 runner 独占，不能把它当作进程间锁；正式启用写入前还须确认单实例和人工解隔离值守。上述检查可与第 3 项准备并行，但任何真实写入仍受第 5 项签核门槛约束。

## W1 候选提交收口（2026-10-16）

已增加持久化的 HA `reconciliation_required` journal、人工解隔离脚本与审计、重启恢复及真实并发占槽测试。全量 `tests/` 为 351 passed；本地配置仍为三站只读，live/write/formal 开关均为 false。具体范围、操作步骤、限制和 SHA 见 `W1_DELIVERY_TRANSACTION_CANDIDATE.md`。本次只选择性提交 W1 文件；共享文档还有其他 Worker 的未提交内容，不纳入候选提交。W3 远端版本未切换、HA 未写入，F1 未签、F2 未授权；新窗口连续只读 M3 仍待执行。

唯一候选提交：`2a49d236e6b2d873eaac6fda969ce7ae9f1f763a`（本地 `feat/explainability-integration`，未推送）。未签名 F1 manifest SHA-256：`9f248d47243f60e7af8a1637b1dca8b090a5859d4f8bcac681430c56de8138de`；本地配置字节 SHA-256：`257d1a076e6930ad1240cbbf28208d6c9e5d6540c74bbb305bf29d9d0bda0f8c`。本共享文档保留工作区未提交，不包含在候选提交中。

## W1 代理配置修复与预算复核（2026-10-16）

HA 鉴权、HA 读取和模拟器读取统一为显式 `proxy_mode`：本地 `environment`，远程模板 `direct`；相关回归测试覆盖两种模式和非法配置。代理访问成功仅是连通性/鉴权证据，不代表价格完整、三站新鲜、对齐或 10 tick M3。修复后只读预检在未改变的采集预算 `2.5s` 内取得 `/api/price/current`，但随后 `/api/price` 超时；总采集 `2.504s`，三站均 `eligible_for_milp_tick=false`，HA 写入 0。证据 `outputs/m3_prestart_20261016/preflight_proxy_fixed.json`。后续本地连续 dry-run 审计有至少 37 个完整三站轮次，全部因 `alignment_deadline_exceeded` 回退、无求解、零 HA 写入，**不构成 10 tick 有效 M3**；另有 3 行轮次编号缺失，不计入完整轮次。未放宽整轮截止 `5s` 或采集截止。全量测试 `354 passed`；配置 SHA `51096f1a6dfb060de95cd90a3ca2cd1fc780f609d7827f242de7953f34904546`；增量后未签名 manifest SHA `5058b1dd277543462edd376f6e81c77860d73198ed24d3fffc553ea25b43f513`，`f2_authorized=false`。该代理增量尚未提交、推送或部署；原候选提交不包含它。

## W1 增量：HA 下发故障边界与三站多轮统计（2026-10-16，待集成验收）

**集成负责人长周期指示：以模拟比赛准备为终点（2026-10-16）**：W1 不再继续扩展局部测试，按以下连续闭环推进，完成一项即在本节补证据和状态：

1. **收口代码与安全语义**：将当前下发事务、`delivery_unknown`/`reconciliation_required`、轮次代际、单槽求解和批量 HA 快照整理为一个可部署变更集；补齐持久化隔离状态（至少重启后能恢复“禁止自动重试/补发”的站点清单）及人工核对后解隔离命令/流程。明确 `writes_performed=0` 与 `attempted_unknown` 的含义，禁止日志或看板把服务确认等同于设备已执行。补一条真正并发占槽测试，以及 HA 写入中途超时/部分成功后的重启恢复测试。
2. **形成唯一候选版本**：选择性提交 W1 相关源码、测试、配置 schema、运行脚本和审计文档，排除其他 worker 未提交产物；跑完整策略测试并生成带提交号、测试摘要、配置摘要和 SHA-256 的 F1 候选包。提交前检查 `git diff --check`、敏感信息、启动命令和回滚命令。提交后暂停，不要自行推送或切换远端，等待集成负责人核对。
3. **新模拟窗口只读 M3 验收**：窗口开始后先运行预检，不启动 HA 写入；使用 API current 档位锚定和批量 HA 快照，连续至少 10 个 tick 记录三站：完整状态率、`device_time` 新鲜率、价格对齐率、轮次耗时 P50/P95/P99、跳 tick/拒绝原因、求解耗时、跨轮隔离、零写入计数。验收门槛是没有旧 epoch 拼接、没有跨轮旧候选写入、无未审计异常；若真实网络导致跳 tick，必须证明安全拒绝而不是补发旧命令。不得用离线注入结果替代该证据。
4. **远端比赛机 dry-run 准备**：只有新窗口只读 M3 证据合格且候选提交已核对后，才由 W3 拉取该固定提交，在比赛机执行环境自检、依赖检查、配置 SHA 校验和单实例 dry-run；保留原进程可回滚，不启动 live、不写 HA。验证浏览器断线/SSH 断线后进程存活、日志持续、重启恢复（含持久化隔离状态），并回传 PID、提交号、配置摘要和日志路径。
5. **模拟比赛前闸门**：集成负责人复核 F1 签名/授权、比赛窗口和设备时区证据，确认 `f2_authorized` 后才允许单站 HA 写入回读演练；单站通过后再决定三站 live。任何一步失败都保持只读/dry-run，不能用“测试通过”替代真实窗口证据。

当前执行顺序：先完成第 1 项和第 2 项，随后等待/申请新模拟窗口完成第 3 项；第 4、5 项不得提前。W1 每次更新应报告本闭环中已完成的编号、证据路径、候选提交号和阻断项，而不只报告单个测试数量。

- 下发事务采用整轮单调时钟截止和轮次代际校验；每个 HA POST 前重新校验剩余时间，模式切换后的 1 秒间隔也计入预算。超时只阻止后续 POST，不能撤销已经发出的请求。HTTP 超时或 5xx 一律保留 `attempted_unknown`，HTTP 成功仅表示服务确认，不表示设备状态已经回读验证。
- 失败审计记录 `delivery_steps`、`write_requests_attempted`、`write_requests_acknowledged`、`requires_reconciliation`；`writes_performed=0` 不得被解释为“设备肯定未执行”，而是本轮没有可确认完成的整条命令。对可能部分下发的站点，下一轮仍先读取状态，但保持 `reconciliation_required`、零写入；不能自动重试、补发或仅凭传感器值清除隔离。
- 纯 mock 故障注入覆盖首个 POST 超时/5xx、第二或第三个 POST 超时、首个 POST 慢响应跨越整轮截止、模式成功但 1 秒间隔预算不足、轮次失效和截止前拒发，以及下一轮隔离。`python -m pytest tests -q`：345 passed；未访问真实 HA POST。
- 离线注入新鲜状态、Stub HA/价格、真实 MILP 的三站连续 10 轮：整轮约 0.14–0.19 秒，30 个站轮次中跳过 0、HA 写入调用 0。独立的故意过载注入（每次求解休眠 80 ms，整轮截止 20 ms）连续 10 轮则跳过 30/30 个站轮次、HA 写入调用仍为 0。逐轮/逐站统计见 `outputs/m3_schedule_20261016/offline_three_site_multiround.json`。两者都不包括真实网络延迟或新模拟窗口，不是连续在线 M3 验收。
- 剩余限制：三条 HA 服务调用不具原子性；进程重启后内存中的 `delivery_unknown` 隔离不会自动恢复，线上启用写入前必须设计持久化隔离及人工核对/解隔离流程。当前不切换 W3 远端版本、不启用 HA 写入；F1 未由负责人签署，`f2_authorized=false`，不启动正式 F2。待新窗口取得新鲜三站数据后仅做连续多 tick 零写入 M3。
- 本次重建的未签名 F1 manifest SHA-256：`ebf813dc4100b309bde0bd62cf40c7643a2b0ac7a13d2f796bc011391f6b2d81`；此前 SHA 仅是历史版本。该哈希只供集成负责人核对，不代表签核或 F2 授权。

## W1 最新状态：API current 档位锚定（2026-10-15，待集成验收）

**集成复核与 W1 下一指示（2026-10-15；三站分站调度增量）**：主控复跑 `python -m pytest tests -q` 为 `338 passed`；核对离线注入新鲜状态的三站真实 MILP 轮次产物，约 `0.197s`、三站求解约 `0.076/0.048/0.046s`、零 HA 写；真实只读预检采集约 `1.921s`，旧窗口三站仍 stale/不具备 M3 准入。这说明单槽求解、超时丢弃与代际校验的离线方向可用，**不等于真实三站连续运行或 5s 节奏验收**。代码审阅发现一个上线前阻断：`run_once_milp` 仅在 `execute_candidate` 之前检查截止/代际；真实 `HAClient.set_dispatch` 随后有模式切换、固定 `1s` 等待、流向和功率多次 HTTP 写，截止可能发生在下发中途，代际锁虽阻止新轮交错，却不能证明“超期零写入”，部分成功还可能留下中间设备模式。W1 下一步优先补写命令事务边界：明确开始条件与预留时间、每步有界超时和审计、部分失败/超时后的受控恢复与下一轮抑制，不能把多次 HA 调用宣称为原子操作；用慢 HA/中途失败故障注入证明不会悄悄留下危险中间状态。再补三站多轮（包括慢求解、部分站异常和下一轮抢占）耗时分布与跳轮比例；现有 `tests/test_solve_schedule.py` 的“第二求解不重叠”测试实际只测第一求解正常结束，应增加真正并发占槽用例。待新模拟窗口取得新鲜快照后，只读/零写入连续多 tick M3 复测。当前增量仍未提交/推送/部署，F1 未签、`f2_authorized=false`，W3 原单实例 dry-run 不切换。

**W1 三站轮次测时与有界分站调度（2026-10-15；覆盖下文旧 manifest SHA，待集成验收）**：零写入离线完整三站求解（注入新鲜状态、真实 MILP、Stub HA/价格；不是当前模拟窗口 M3）实测整轮 `0.197s`，三站求解各 `0.076/0.048/0.046s`、HA 写调用 `0`。接入进程级单求解槽的有界分站调度：求解线程只返回候选、不持有 HA；单轮截止默认 `5s`，晚到解丢弃；前轮求解线程未结束时后轮/后站不重叠启动；轮次代际号与执行前同锁校验阻止跨轮旧候选写入和覆盖下一轮 readback 状态，超时/旧候选只产生零写入记录。轮询睡眠改按本轮启动时间计算，避免每轮完成后额外固定睡 `5s`。慢求解/跨轮旧候选故障注入与全策略 `338 passed`。同日真实只读预检锚定 `1.921s`、三站仍 stale/eligible=false、零写入，因此**尚无真实新窗口连续多 tick M3，也未验证真实负载下 5s 稳定性**；单站 2s 最坏、三站串行 6s 的时序风险仍需新窗口观测和集成复核。W3 保持远端原单实例 dry-run，本轮未提交/推送/部署、未擅填时区。F1 未签核、`f2_authorized=false`。本轮重建 manifest SHA-256 `336e7a9d1c01f8edd2c2b7a818b9d3b0fdb5871bc18fed3d27e9368669ea902f`；下文旧 SHA 仅历史记录，不得签核。

**集成复核与 W1 下一指示（2026-10-15）**：批量 HA 读取代码子项通过当前离线复核：主控重跑定向测试 `45 passed`、策略 `tests/` 全量 `334 passed`；核对 `outputs/m3_bulk_20261015/ha_bulk_probe.json` 与 `preflight.json`，只读批量请求约 `0.385s`，预检采集约 `1.538s`，三站均因旧窗口 stale 拒绝、零写入。这里仅确认读取优化与安全拒绝，**M3 三站有效求解、5s 运行节奏、HA 写入和看板成绩均未验收**。逐实体 `fresh` 目前用于诊断，实际准入只强制检查 `device_time` 新鲜度和实体存在/唯一；不得把“全部 8 个实体新鲜”写成已验证结论，也不得把恒定零值传感器的久未变更机械判为失效。W1 下一步先在无 HA 写入的可重复环境中测量三站完整 `current→价格→HA→current→逐站 MILP→命令准备→审计` 的端到端耗时、各站求解分位数及超时/跳 tick；再提交有界并行或分站调度设计与实现，明确每轮总截止、过期候选丢弃、同站不重叠、上一命令回读及每站故障隔离，并补慢求解/单站异常/下一轮抢占回归。`5s` 是轮询目标，不能仅凭串行预算推断每站每轮必须成功；但不得让旧候选跨轮写入。完成后在新模拟窗口用新鲜三站快照、连续多 tick、零写入复测 M3。以上增量目前仍未提交；提交时仅选择性纳入 W1 文件和依赖（含 `read_budget.py`），不要混入其他 worker 未提交产物。F1 未签、`f2_authorized=false`，暂不指定 W3 新部署版本或启动 live。

**W1 HA 批量快照与三站求解预算（2026-10-15；覆盖下文截止预算增量的现状，待集成验收）**：只读实测单次 `GET /api/states` 返回 89 实体、47,933 字节，三站各 8 个所需实体全部唯一，HA GET 耗时 `0.385s`、零写入。已接入每轮一次批量快照和按站实体索引，保留每实体 `last_changed`/`last_updated`/新鲜度与缺失/重复诊断；缺失只拒绝受影响站。真实只读预检以新链路耗时 `1.538s`，模拟器登录/current/两日电价加 HA 批量请求完成，三站仍因旧窗口 device_time stale 而 `eligible=false`、零写入，不能宣称 M3 通过。`solver_seconds=2` 是**每站**上限，三站串行最坏求解 `6s`；若采集上限仍为 `2.5s`，理论最坏轮次至少 `8.5s`（不含执行/日志余量），**不能满足 5s 轮询节奏**。日志现在显式记录 `serial_solver_worst_seconds=6`、`serial_tick_budget_floor_seconds=8.5`、`serial_tick_fits_poll=false` 和实际轮次耗时。需由集成负责人决定求解并行/整体截止或调整运行节奏并重新实测，不得默认为当前预算可行。策略测试 `334 passed`；本增量未提交/推送/部署，W3 保持原单实例 dry-run、未擅填时区。F1 未签署、`f2_authorized=false`；本次重建 manifest SHA-256 `6250cbf49a0a2a2d39a8dba5731dc53b9512e78154bf92c35a98d1f917fe7ff2`，下文旧 SHA 均为历史候选。

**W1 截止预算增量（2026-10-15，覆盖下文旧重读/哈希口径；待集成验收）**：已加入整轮单调时钟预算，默认 `alignment_budget_seconds=2.5`，必须小于 `poll_seconds-solver_seconds`；预算同时覆盖所有站、所有重读和各 HTTP 请求，超时数据丢弃并安全拒绝。仅跨档、短暂两源冲突、临时连接/超时等候选瞬态最多重读 3 次；stale、坏配置、结构性缺档/重复档、不连续价格等确定性错误立即停止相应站/整轮，不再盲目重复。日志新增请求级、操作级与整轮耗时、尝试数和停止原因。慢响应、跨档、单站失败与全套策略测试 `332 passed`（`python -m pytest tests -q`）。本轮源码仍仅在工作区，未推送、未部署；F1 未签署且 `f2_authorized=false`。**新 manifest SHA-256 为 `8752e41f21f102eb9dafa1629cd914d737d3adad70b4f0bd5808fb0ae2221759`，下文 `e4b7…` 已过期，不能用于签核。**

**集成负责人复核与下一指示（2026-10-15）**：主控复跑策略测试 332 passed、锚定/在线链路定向 47 passed；旧窗口预检产物确认 deadline 在 2.51 秒安全停止，避免了此前 39.58 秒整批重读，但三站无一取得完整状态，不能称为有效 M3。`HAClient.station_state()` 当前逐站串行读取 8 个实体，三站共 24 次 HA GET；真实请求耗时约 0.38 秒/实体，本地 2.5 秒采集预算必然难以覆盖。W1 下一步先只读验证 HA `GET /api/states` 单次全量快照的实际响应、字段齐全性和耗时，再在不改写控制路径的前提下实现按实体 ID 提取三站所需状态、保留每个实体的 `last_changed` 与缺失/重复/不可用诊断；API 不可用时不得无预算地回退到 24 次串行 GET。完整 current→价格→三站 HA→current 必须同一截止预算且满足跨档拒绝。另须修正整轮预算口径：`solver_seconds=2` 是每站上限，三站串行最坏为 6 秒，`poll_seconds=5` 减去一次 solver 不能证明整轮达标；需测量并明确三站策略求解/命令准备的总耗时，必要时采用逐站截止、优化执行或有界并行，并证明不会超期写入。未取得三站新鲜完整快照与端到端时延证据前不指定新远端部署提交、不启动新模拟赛或 HA 写入。

**真实只读复测与限制**：`outputs/m3_deadline_20261015/preflight.json` 显示 48 个价格点、尝试 1 次、预算 2.5 秒、锚定耗时 2.510 秒、`alignment_stop_reason=deadline_exceeded`、三站 `eligible_for_milp_tick=false`、零写入；预算内依次完成模拟器登录/current/价格和首站两次 HA 读取，第三次 HA 请求超时。HA token 交换属于预检启动阶段，非上述单 tick 锚定预算。当前串行 HA 每站 8 次传感器 HTTP 读取，在预算内无法形成三站完整锚定 tick。旧窗口状态仍陈旧，本地显式 `Asia/Shanghai` 未经当前档位核实；不得通过擅填时区绕过。需由集成负责人评估 HA 批量只读接口或其他满足 SOP 节奏的采集方案，再在新窗口复测三站连续多 tick M3；本轮保持 W3 原单实例 dry-run，不切换其固定提交或进程。该预检证明“能及时安全拒绝”，**不证明线上策略已达标**。测试和预检 SHA 见 §9 新交接行。

**集成复核（2026-10-15，覆盖本节“待验收”表述）**：`86c7a1851d4a40d6f05e21737a1d8f287884fe2b` 的接口契约与安全拒绝方向通过代码审阅；主控复跑 `python -m pytest tests -q` 为 328 passed，定向锚定/在线链路/日志测试 44 passed。真实 `/api/price/current` 样本 `08:00Z–09:00Z` 在两日电价中唯一匹配，旧版将 HA `08:15` 用 `Asia/Shanghai` 对到 `00:00Z` 的判断应撤回。旧窗口三站仍 stale，真实 M3 未通过。

**部署前时延阻断**：主控以原本地配置运行一次只读 `check_m3_readonly_preflight.py`，实际耗时 **39.58 秒**；当轮三站 stale，现有 `read_anchored_tick` 对任何错误均最多重读完整批次 3 次。该数字只代表这次旧窗口/当前网络的预检，不能推定新窗口每轮耗时，但已足以说明不能仅凭单元测试认定满足 SOP 的约 5 秒真实时间决策节奏。W1 应加入整轮单调时钟截止预算：仅在 current 跨档等可能瞬时恢复的竞态下做有预算的重读；确定性缺失/坏配置/旧状态不重复整批读取，超预算按站或全局安全拒绝并记录各 API/HA 请求时延、总耗时、尝试次数及原因。补慢响应/跨档/单站失败测试，再复测实际只读耗时。此门槛未收口前，`86c7a18` 不指定为 W3 的新固定部署提交，W3 保持现有单实例 dry-run，不为规避问题擅填时区。

已只读核实真实 `/api/price/current` 返回明确 `start/end`，并与两日电价 48 点中唯一档位对应。W1 在线 MILP / 旧 dispatch 已接入 current → 价格与 HA → current 的最多 3 次整批重读；不用价格数值、数组下标或猜测 `Asia/Shanghai` 定位。新鲜 HA 的日期/小时须与档位标签相符，显式 IANA 时区若存在还须交叉校验；冲突、缺档、未来不连续则安全拒绝。策略项目全套测试 `328 passed`（`python -m pytest tests -q`），样本、测试、预检和各 SHA 见 [M3_API_ANCHOR_DELIVERY_20261015.md](M3_API_ANCHOR_DELIVERY_20261015.md)。选择性代码提交：`86c7a1851d4a40d6f05e21737a1d8f287884fe2b`（仅本地，尚未推送）；本共享文档含其他负责人的未提交修改，当前进度更新仍在工作区，不属于该提交。

**限制与待办**：旧窗口三站状态仍 stale，最新只读预检均 `eligible_for_milp_tick=false`、零写入，未取得新窗口连续多 tick 的真实 M3 证据；模拟测试通过不等于真实线上求解或收益验收。旧的“必须人工指定 `device_timezone=Asia/Shanghai`”门槛已被本节覆盖，但本地配置仍有旧显式值，可能与 API current 冲突；未擅改含凭证的本地配置或远端配置。根目录直接收集测试仍受看板子项目缺少 `fastapi` / `energy_explain` 依赖限制，`328 passed` 仅指策略项目 `tests/`。F1 manifest 已随源码重建，当前 SHA-256 为 `e4b7d369915b60ef9ec709e273b219b538cc4d9f2c9ca1c517a4fc854640f235`，签核字段空、`f2_authorized=false`，旧候选 SHA 不得继续作为当前签核依据。W3 须等集成负责人验收、推送固定提交并核对远端配置后受控切换；本轮没有推送、停止远端 PID、启动比赛、HA 写入或正式 F2。

## W1 历史交付：显式设备时区契约（2026-10-15；已被上方 API current 契约覆盖）

选择性提交 `2efda5bfe0e1b84b319719eb27d4f3f5db0b081a` 已完成：不再以 `local.timezone` 推断设备时区；显式时区来源可追踪，缺失/无效时区阻断经济动作，MILP 自用回退记 non-optimizer-attributed。配置与策略 `tests/` 全套 `297 passed`；根目录看板子项目因缺依赖无法收集，不声称其测试通过。

交付说明与完整 SHA 见 [M3_TIMEZONE_CONTRACT_20261015.md](M3_TIMEZONE_CONTRACT_20261015.md)。最新预检为 `outputs/m3_timezone_preflight_20261015/preflight.json`：48 点价格、三站对齐，但全部 stale、eligible=false、零写入，非 M3 通过。配置来源为显式 `local.device_timezone=Asia/Shanghai`，非 `local.timezone`；当前对齐只是支持证据，不是设备方独立时区声明，新窗口须再次核实。

最新未签核 manifest SHA-256：`18135692e64fa97f73ebc6d1b774fa0dd99e0c7ccba7a53779425442055ddff7`，替代此前候选 SHA；`f2_authorized=false`，未代签、未启动 F2/live、新模拟赛或远端进程更新。W3 待集成负责人确认上述提交及真实设备时区后再受控部署。

> 用途：此后各 worker 与集成负责人交流进度的唯一共享看板。本文档区分已验证事实、进行中工作和计划，不做时间估算；排序以里程碑门槛表示。
> 关联文档：[预赛方案_更新版.md](../预赛方案_更新版.md)（产品方案）、[SYSTEM_ARCHITECTURE_GUIDELINE.md](SYSTEM_ARCHITECTURE_GUIDELINE.md)（架构基线）、[NEXT_STAGE_RESULTS.md](NEXT_STAGE_RESULTS.md)（实验证据）、[HOLD_SEMANTICS_DESIGN.md](HOLD_SEMANTICS_DESIGN.md)（语义对齐设计，Q1 已决策）。
> 状态核验：2026-10-14；当前集成分支 `feat/explainability-integration`，E4-1、W3 选择性集成和 W2 E4 产品分别落在 `82ba72c`、`a632a56`、`76399f0`。工作区仍有其他实验/设计改动，未纳入上述提交。

## 0. 共享协作规则（所有 worker 必读）

- 本文档是**进度与交接的共同入口**，不是把未提交文件视为已交付的依据。状态只能写成 `待开始`、`进行中`、`待集成`、`已验收`、`阻塞`；`已验收`须附测试/报告路径或提交号。口头“完成”不自动改变状态。
- 多 worker 同时开工前由集成负责人盘点并归属当前未提交改动，确保本文档已纳入 Git 跟踪，再为每个 worker 建独立分支/worktree。**W1 在主工作树推进；W2 的 E4 已集成；W3 的 B4 本地基线已选择性集成并继续做 D1。** 不允许在当前脏工作区上并发编辑，也不允许整枝合并旧 `feat/energy-floor`。
- 每个 worker 只修改自己的文件域；共享的 `src/energy_lab/rolling.py`、`src/energy_lab/config.py`、`src/energy_lab/online_contract.py`、`src/energy_lab/explainability_evidence.py` 和跨线样例配置由集成负责人协调。跨域需求先在本文档 §9“接口/阻塞记录”登记，由所有者或集成负责人处理。
- 更新节奏：开始任务、完成可验收增量、发现阻塞或接口变化时，更新 §8 一行，并在 §9 追加一条记录；集成负责人验收后才修改 §2/§4 的总状态。不要让多 worker 在同一工作树同时编辑本文档；各分支可提出文档补丁，由集成负责人串行合并。
- 每次交接至少包含：任务编号、分支/提交、变更文件、验证命令及结果、证据产物、已知限制、下一步/阻塞。所有对外收益数字只使用 A5/F2 冻结后的零流量口径；自用回退标记 `non-optimizer-attributed`。
- 所有线默认只读或离线。采集恢复和 M3 只读 dry-run 不等于授权 HA 写入；真实控制写入、远程部署/重装、故障切换须单独确认执行范围与窗口。

## 1. 一句话定位

Anker 黑客松充电储能赛道场景三："会解释自己的 AI"——基于 LP/MILP 滚动调度引擎的家庭能源管家，调度与解释共享同一份状态/决策记录。当前处于**离线证据充分、线上闭环未通**的阶段。

## 2. 当前状态

### 2.1 已验证（离线，可对外声称）

| 项 | 证据 |
|---|---|
| 压力闭环回退安全 | 8 场景 408 步，超时/坏预测回退均保持物理约束 |
| 历史探索性优化器对照 | 曾有 100 站 MILP vs 自用基线的探索性结果；不属于当前冻结参数、零流量口径或正式收益，W1 完成前不对外发布 |
| 跨站点泛化 | 80/20 留出，三组参数均 20/20 站点为正（未冻结比赛参数） |
| 比赛口径策略主线 | 20 站 × 2×168h：辐照度 PV + 当前负载最优（+14.612% / +6.441%），第三窗口可加但非门槛 |
| SOP 控制语义 | 充放/购售互斥、SOC 全量程、第三方模式两条指令、约 1 秒间隔、下一 tick 生效 |
| Hold 语义差异量化 | pair 实验：自用 idle vs 零流量 idle，persistence 臂 6.238% vs -2.115%，辐照度臂 11.167% vs 9.204% |
| 回归测试 | 2026-10-14 集成后主仓 `pytest tests -q`：241 passed；Explainability 应用后端：95 passed、49 subtests passed（1 个 Starlette/httpx 弃用警告）；前端 14 passed，生产构建通过 |
| 只读采集链路 | D1 后续运行期报告已有三站多个递增 tick、约 88–89% 覆盖、最大缺口 45 设备分钟、零写入；这些是已结束模拟窗口的历史证据。模拟赛随后 `ended/mock`，采集 PID 67288 已停止；新窗口须重新验收，旧 2028 epoch 不得与新 2026 epoch 拼接 |
| HA 物理校准（B3） | 两轮真实只读审计（夜 12×5s + 日光 18×10s，[报告](outputs/ha_physical_calibration_20261013/report_daylight.json)）：三站 PV/负载符号、充放互斥、电网进出口互斥零违例；三向功率平衡残差 ≤1W；日光轮 PV 激励峰值 1232/1150/720W；30/30 样本可用 |
| 零流量硬件验证（B1/B2） | 三站真实 `charge=0` 受监管写入全部 verified（live_readback，[证据目录](outputs/zero_flow_verification)）：设定回显正确、电池 0/0W、电网被动结算（进口 150≈158W）、写入后下一 tick 回读、模式恢复回读确认无残留；`stations.{1,2,3}.zero_flow_verified=true` 已落配置；测试后三开关已收回 false |

### 2.2 已完成或进行中

- 线 A：A1-A4 已提交；A5-1/A5-2 的离线证据已由主控验收（2026-10-14，范围仅限归因与同输入对比）；A6 定向回归 89 passed，V1/V5 清单验收仍待完成。
- 线 B：B0-B3 已完成；B4 本地分片、merge view、监控、watchdog、离线故障演练已选择性集成（`a632a56`）。远程 Tailscale/双节点部署未完成。
- 线 C：C1-C3 已提交；C4 的 `solver_seconds=2.0`、readiness 校验及 72 个冻结病例复验已由主控验收（2026-10-14，72/72 可输出、0 物理违规），仅表示求解预算/配置子项通过，不表示在线整体 readiness 通过。
- 线 D：D2 批 1-20 分析已完成；batch 20 因 ConnectTimeout 仅 14/24 轮，缺口保留。另有 batch 21 完整 24 轮补采（零写入，继承 batch 20），尚未并入 20 批主分析。D3、D4 未完成。
- 线 E：E1-E3 已完成；E4 已集成（`82ba72c`、`76399f0`）；E5 已选择性集成验收（`dfe0265`、`0861787`）。三站家庭历史 PV 核算与人工决策样例是独立证据，不代表完整历史决策回放或官方结算。旧 `feat/energy-floor` worktree 保留，不整枝合并。
- 采集器：天气降级补丁已在工作区交付，尚未独立提交；受控重启及停止证据已保存。模拟赛已结束，PID 67288 已停止，旧 heartbeat 仅作历史证据。下一窗口需使用独立缓存重新核验实际运行版本、设备时间推进和零写入。

### 2.3 能力缺口（不可声称，按优先级）

1. A5/C4 已通过对应离线验收；terminal price/PV 候选诊断和完整未签核 manifest 已交付，正式参数仍待集成负责人签核，F2 尚未启动。
2. `run_live.py` 的 MILP dry-run 接线已完成，但当前工作区未完成端到端 M3 在线只读验收；真实持续写入控制及恢复流程未验收。
3. 天气预报质量尚未完成 D3；D2 的 PV 误差统计只支持校准方向判断，仍需线 F 用策略收益门槛复核后才能冻结切换规则。
4. self_consumption 回退分支的 MILP 一致性模型未重建；回退收益需标为非优化器归因。
5. E4/E5 产品与离线演示已集成验收；比赛现场决策接入、控制执行与官方结算证据仍缺失。
6. 远程双节点冗余及 Tailscale 部署尚未完成；本地模拟窗口已结束、采集器已停止，不能声称当前在线健康。旧 2028 缓存与新 2026 epoch 保持分段。

### 2.4 决策状态

- 已决策（2026-10-13）：Q1 未验证站点回退 = 显式 `self_use_mode` + 三条件（见 HOLD_SEMANTICS_DESIGN §8.1）。
- 已决策（2026-10-13）：Q4 设备验证执行窗口——B0 已授权并已执行，三站验证完成（见 §2.1）。
- 已决策：Q2 SOC 发散阈值采用 2 个 SOC 百分点（已实现于 A3）。
- 待评审：Q3 自用 idle 建模立项条件。

## 3. 总体路线（里程碑门槛）

```
M0 设计评审收尾 ✅（Q1/Q2/Q4 已决策，Q3 待定）
  → M1 并行实施（A/C/D/E/F 部分完成，仍有未提交改动和未交付项）
  → M2 设备语义验证 ✅ 2026-10-13（三站 charge=0 受控验证完成；比赛写入开关已关闭）
  → M3 线上只读 dry-run 验收 ⏳（A4 代码已接线，当前需复核配置、运行状态和完整链路）
  → M4 比赛参数冻结、零流量口径回测和赛前检查 ⏳
```

原则：任何真实 HA 写入需单独授权；所有线默认只读/dry-run；每条线独立可回退。

## 4. 并行工作线

### 线 A：契约与执行层重构（hold 语义对齐实施）

**目标**：落地 HOLD_SEMANTICS_DESIGN 步骤 0/2/3/4/5，消除计划-执行语义差异。
**来源**：HOLD_SEMANTICS_DESIGN §4、§6。
**需要设备/授权**：否（A4 的 live 分支除外，依赖线 B 结果）。

- [x] A1（步骤 0）契约去歧义：`rolling.py` `hold→idle`、执行映射显式化（`fec9f35`）
- [x] A2（步骤 2）`execute.py` 统一执行决策表和审计字段（`ecac401`）
- [x] A3（步骤 3）SOC 发散守卫，阈值 2pp（`780fe08`）
- [x] A4（步骤 4）`run_live.py` dry-run 链路和第四道安全开关（`d11bc87`）；实时链路验收仍待 M3
- [x] A5-1（步骤 5a）归因报告：逐步归因字段、汇总计数、旧字段未知处理和离线报告由主控验收（2026-10-14；见 `A5_ATTRIBUTION_AND_BASELINE_REPORT.md`）；不包含在线 fallback 收益验证
- [x] A5-2（步骤 5b）同输入基线对比：旧 self-consumption idle vs 新 zero-flow idle 的收益、fallback、归因步数和物理审计由主控验收（2026-10-14；见 `outputs/w1_a5_compare_20261014_1/a5_same_input_comparison.json`）；非官方结算
- [ ] A6 核对 V1 清单剩余测试；hold/zero-flow、执行层、回放、配置、shadow、rolling 与时间链路定向回归 89 passed；本轮 `pytest tests -q` 全量回归 241 passed。A6 仍待集成负责人按 V1/V5 清单验收

**验收**：HOLD_SEMANTICS_DESIGN §6 V5 表步骤 0/2/3/4/5 行。
**依赖**：A3 需 Q2 拍板；A4 需 B2 产物；A2 审计 schema 冻结后线 E 可对接。

### 线 B：设备验证与 HA 写入（授权门槛）

**目标**：三站 `zero_flow_verified` 结论落配置；建立下发→回读闭环能力。
**来源**：HOLD_SEMANTICS_DESIGN §4 步骤 1、§6 V3。
**需要设备/授权**：**是**，本线是唯一真实写入路径，需单独授权与执行窗口（Q4）。

- [x] B0 授权与窗口确认（Q4 评审）+ 单站先行协议。**完成（2026-10-13）**：用户会话授权，按单站先行协议执行
- [x] B1 单站 `charge=0` 受监管写入测试。**完成（2026-10-13）**：站1 verdict=true（live_readback）——设定回显正确、电池 0/0W、电网被动结算（进口 150≈158W）、下一 tick 回读、模式恢复确认无残留（[证据](outputs/zero_flow_verification/site1_live_20261013)，写入 6 次=下发 3+恢复 3）
- [x] B2 三站扩展 + 证据产物存档 + `zero_flow_verified` 落配置。**完成（2026-10-13）**：站2、站3 相继通过（同站1口径），三站 `zero_flow_verified=true` 落配置；六件 artifact/站 + SHA-256 哈希链存档（[站2](outputs/zero_flow_verification/site2_live_20261013)、[站3](outputs/zero_flow_verification/site3_live_20261013)）；测试后三开关收回 false 恢复默认只读
- [x] B3 HA 物理校准：PV 传感器额定、负载符号/方向、电池功率实体三向一致性。**完成（2026-10-13）**：两轮真实只读审计三站全部 calibrated，平衡残差 ≤1W，日光轮 PV 峰值 1232/1150/720W（[报告目录](outputs/ha_physical_calibration_20261013)，[audit_physical_calibration.py](audit_physical_calibration.py)，15 单测）；注：被测为模拟器环境，真机需复跑确认
- [x] B4 本地采集分片/合并/监控改造与 watchdog 离线故障演练已选择性集成；远程电脑 Tailscale 重装及双节点回传未完成（远程动作未授权）

**验收**：三站结论落配置、无模式残留；B3 校准报告。
**阻塞他人**：无（B0~B3 已完成，A4 与线 F 线上口径验证已解锁）。**被阻塞**：B4 需远程机访问。

### 线 C：求解可用性与时限回退

**目标**：把 90.28% 候选可用率提升到满足控制周期，且回退可执行、可验证。
**来源**：NEXT_STAGE_RESULTS §4 第 1 条。
**需要设备/授权**：否。

- [x] C1 `limit_reached` 病例根因分类（`31b85af`）
- [x] C2 超时时提取并复核可行 incumbent，供只读候选和回退链路使用（`82325b3`）；生产时限仍待 C4/F 冻结
- [x] C3 区分真实 SOC 边界与可选软件保底：配置冻结建议（比赛 `soc_min=0`，呼应 Q1 条件 2）。**完成（2026-10-14，commit c223894）**：分析与核查见 NEXT_STAGE_RESULTS "C3" 节——SOP 固定比赛 SOC 空间 0~100% 且备用预留不可改；`soc_min>0` 可致结构性 infeasible 并与自用回退冲突；比赛在线链路核查已全程 `soc_min=0`。冻结建议已递交线 F（见 F1）
- [x] C4 预算扫描结论固化：`solver_seconds=2.0` 映射、readiness 校验、脱敏配置快照和 72 病例复验由主控验收（2026-10-14）；不放宽 `mip_rel_gap`（R8）。整体在线 `ready=false` 仍然成立

**验收**：重放 72 次病例 + 压力场景，可用率与回退越界次数有量化报告；回退路径与 A2 决策表对接。
**依赖**：与 A2 接口对齐（回退candidate 的字段契约）。

### 线 D：预测校准与数据采集

**目标**：PV/负载校准在真实 API 数据上下结论；天气预报质量完成验证。
**来源**：NEXT_STAGE_RESULTS 2026-10-13 辐照度试采、§4 第 4 条。
**需要设备/授权**：否（只读）。

- [ ] D1 采集器监护：已结束窗口的多 tick、基本连续/基本新鲜及零写入证据保留；天气降级补丁待独立提交。当前采集器已停止；下一运行窗口按站重新验收，不要求零缺口，不拼接不同 epoch
- [x] D2 PV 校准扩样主分析已完成：20 批有 19 个有效设备日；1h inconclusive/维持 persistence，2h/4h 校准 MAE 优；batch 20 真实缺口保留。batch 21 已补采完整设备日，未并入主 bootstrap
- [ ] D3 天气预报质量验证：API 预报 vs 后续实测的误差分布（替代历史实测代理的缺口）
- [ ] D4 负载预测在当前负载主线下的误差画像（服务线 C 的病例分析）

**验收**：D2/D3 各自产出可写入比赛配置的结论或"样本不足"的明确声明。
**依赖**：采集持续运行；与线 F 共享数据口径。

### 线 E：场景三解释器与演示产品（比赛交付物）

**目标**：预赛方案五大功能中面向评委的解释体验：决策解释卡片、自然语言反事实追问、用电计划可行性检查、光伏消纳分析。
**来源**：[预赛方案_更新版.md](../预赛方案_更新版.md)。
**需要设备/授权**：否，**可完全基于离线 replay 数据与审计 schema 开发**。

- [x] E1 审计证据消费层与 schema
- [x] E2 决策解释卡片
- [x] E3 反事实追问与异步重算
- [x] E4-1 优化器支持逐步 `energy_floor_kwh` 截止电量约束；已提交 `82ba72c`，模型指纹与解释证据层一致，集成后完整回归见 §2.1
- [x] E4-2/E4-4 可行性检查、光伏日核算 API/导入和页面已集成；真实共享优化器 deadline 测试通过（`76399f0`）
- [x] E5 演示叙事：三站家庭历史 PV 离线核算讲稿与界面已集成验收；人工决策样例保持独立来源（`dfe0265`、`0861787`）

**验收**：在离线数据上跑通四大功能演示；语义诚实（区分优化归因/非优化归因，口径同 A5）。
**依赖**：A2 审计 schema（可先按 HOLD_SEMANTICS_DESIGN §3 字段定义开工）；线 F 的 replay 产物做数据源。
**备注**：这是场景三评分本体；E1-E5 已有可运行演示基座，后续优先补真实策略决策证据，保持离线核算与人工样例的来源边界。

### 线 F：离线验证收尾与比赛参数冻结

**目标**：把"已验证"与"可声称"收口为比赛配置。
**来源**：NEXT_STAGE_RESULTS §4、README 退出条件。
**需要设备/授权**：否。

- [ ] F1 参数建议已逐项明确并生成最新待签核 manifest [`outputs/f1_parameter_manifest_20261014.json`](outputs/f1_parameter_manifest_20261014.json)；当前 SHA-256 `6ab83c5ac66af61b2362007c96ebffcad653b704ab48680776299c0b47cee45a`。在线价格异常回退验收保留；replay/线上均接受独立复验的超时可行 incumbent，并保留 `limit_reached`、gap、accepted 标记。PV 混合候选仅用于离线 F2，线上保持 persistence-only。签核字段留空，`f2_authorized=false`；F2 暂停。readiness 控制配置通过但整体 `ready=false`（历史仅 30 小时）。
- [ ] F2 全量回测重跑：**阻塞于 F1 签核**；签核前不得启动冻结参数下的 100 站正式零流量回测或发布 100 站收益数字
- [ ] F3 第三窗口补充评估（可选，非门槛）
- [ ] F4 赛前检查清单：`check_online_readiness.py` 已覆盖 `solver_seconds`、`soc_min_percent`、`poll_seconds` 并保存脱敏配置快照，待集成负责人验收

**验收**：冻结配置文件 + 可复现的正式收益报告（零流量口径）。
**依赖**：A5 集成验收、C4 集成验收、D2 校准结论；F1 清单签核是 F2 的硬门槛。

## 5. 依赖关系速查

| 线 | 阻塞于 | 阻塞谁 |
|---|---|---|
| A | 无（A1-A4 已完成） | F、E（口径） |
| B | B4 的远程机访问；本地 B4.1-B4.4 不需远程授权 | D1；M3（线上链路） |
| C | A2 接口契约 | F |
| D | 采集器持续运行 | F |
| E | A2 schema（可先 mock）、F 数据源 | 无（纯交付） |
| F | A5/C4 集成验收、D2 | F1 签核后方可开始 F2 |

**关键路径**：A5/C4 完成 → F1 参数冻结 → F2 零流量口径回测 → M3 线上只读验收 → M4 赛前检查。E4/E5 可并行开发，不应阻塞回测口径工作。
**可立即推进（无设备写入）**：A5/C4 集成评审、A6、F1 清单签核准备、E5 演示、D1 多 tick 观察与 D3 数据准备。F2 在 F1 签核前不得启动。

## 6. 跨线风险

- 授权边界：M3 是只读 dry-run，不以真实 HA 写入授权为前提；持续写入控制、远程部署或真实故障切换需要单独确认权限与窗口。
- 口径蔓延：自用 idle 曾抬高表观收益（pair 实验），所有线的对外数字统一走 A5 零流量口径，E 线演示同样遵守。
- 采集单点：D 线全部结论与 B3 校准依赖采集器；建议采集产物每日备份（可并入 B4）。
- 文档-代码漂移：A 线每步单独提交并回归（R7），禁止多步合并提交。

## 7. Worker 并行分工

拟采用“3 个 worker + 1 名集成负责人”。worker 只在独立 worktree 工作；集成负责人负责接口冻结、文档串行更新、合并和最终验收。

### W1：核心策略、口径与正式回测

- **范围**：A5、A6 → C4 → F1 → F2。先收口零流量归因，再固化 `solver_seconds=2.0`、SOC 边界、损耗/吞吐成本、hold 语义，最后运行 100 站正式回测和比赛回放。
- **文件域**：`contest_replay.py`、核心回测脚本、C/F 测试与报告；不得擅自改动共享优化器、线上契约和解释 schema。
- **输入依赖**：A2 审计字段、C3 `soc_min=0` 建议、D2 已有结论。D3 未完成不阻塞 C4，但天气切换规则必须在 F1 标注“已证实/样本不足”。
- **交付门槛**：每个子任务独立提交；有可复现命令、正式零流量数字、失败/回退统计和配置快照。W1 的 F1/F2 完成后才能声称正式收益。

### W2：场景三产品与演示

- **范围**：E4-2 可行性检查、光伏消纳分析 → E5 演示叙事和界面。
- **文件域**：独占 `explainability-app/`；优化器需求只能登记接口契约，不直接修改共享 `src/energy_lab/rolling.py`。
- **输入依赖**：E1-E3/E4-1、A2 schema。F2 正式 replay 未完成前可以使用明确标记的 mock/历史样例，但不能把样例收益作为正式数字。
- **交付门槛**：API、前端页面、错误/空数据状态、测试和三站点演示脚本齐全；解释必须区分优化归因与 `non-optimizer-attributed` 回退。

### W3：数据可靠性、采集与预报证据

- **范围**：B4.1-B4.4 本地分片/合并/监控/watchdog → D1 采集恢复 → D3 天气预报质量；可附带合并 D2 batch 21，但不阻塞 W1。
- **文件域**：采集器、监控器、watchdog、采集/预报分析脚本及对应测试；不得修改优化器、产品前端或 HA 写入开关。
- **授权边界**：本地故障演练和只读采集无需设备写入；远程 Tailscale、远程部署、HA 写入和故障切换须单独授权与窗口确认。
- **交付门槛**：给出心跳/熔断/恢复证据、缓存新鲜度与缺口报告、天气 API 预测误差分布；不能用历史实测代理冒充 API 预测验证。

### 集成负责人（主控）

- 负责当前脏工作区归属、建立 worktree、冻结跨线接口、串行更新本文档、审查合并。
- 负责 M3 线上只读 dry-run、最终全量回归、F4 赛前检查和发布材料。
- 不代替 worker 重写其工作域；只在共享文件或跨线契约上做最小必要改动。

## 8. Worker 状态表（共享更新区）

| Worker | 当前状态 | 当前任务 | 分支/提交 | 下一验收 |
|---|---|---|---|---|
| W1 | 待集成 | HA 批量快照；三站离线零写入完整求解 0.197s；有界分站调度与跨轮旧候选写入门禁已在工作区。真实三站仍 stale，M3 未通过；F1 未签核、`f2_authorized=false` | 基线 `86c7a185`；增量未提交/未推送；策略测试 338 passed；`outputs/m3_schedule_20261015/`；当前 manifest SHA-256 `336e7a9d1c01f8edd2c2b7a818b9d3b0fdb5871bc18fed3d27e9368669ea902f` | 集成负责人复核真实负载下 5s 节奏与执行门禁；等待新模拟窗口做连续多 tick 零写入 M3。W3 保持原单实例 dry-run，不切换远端版本；F1/F2 门槛不变 |
| W2 | 已验收 | W1 单站真实策略 trace 已接入独立离线 API/页面；不写决策、执行或结算表；保留 `dataset_local_hour` 和来源哈希边界 | 选择性合入 `d251ae3`（来源 `1b1c788`）；后端 98 passed + 49 subtests、前端 16 passed、build 通过；独立服务浏览器 smoke `passed=true`、`browser_errors=[]` | 仅代表练习站离线回放，不是在线决策、比赛三站或官方结算；跨仓 shared-provider 测试仍需根项目 `joblib` |
| W3 | 待集成 | 天气降级和离线 D3/epoch 增量已进入主仓且测试通过；下一窗口 D1 与真实 D3 验收未完成，模拟赛已结束、采集停止 | `13c9730`、`96ba692`、`82c2c83`；本轮主仓 279 passed、W3 定向 25 passed；证据在 `outputs/d1_recovery_20261014/` | 下一模拟窗口另建 epoch/cache 重验 D1；D3 保持样本不足，离线代码通过不等于实测完成 |
| 集成负责人 | 进行中 | W1/W3 增量已进入当前提交历史；代码、135 项 manifest 条目和根测试已核验；F1 签核字段仍为空，W3 下一窗口 D1/D3 未完成 | `feat/explainability-integration` @ `23efb11` | 更新 F1 checklist 的人工签核状态；不代填授权、不启动 F2；应用测试需在其独立环境复现 |

## 9. 进度交接记录

| 日期 | 角色 | 事项 | 结果/阻塞 | 证据 |
|---|---|---|---|---|
| 2026-10-15 | W1 | 三站完整轮次测时、有界分站调度和旧候选门禁 | 注入新鲜状态的离线完整三站真实 MILP dry-run `0.197s`、零 HA 调用；单求解槽、5s 轮次截止、跨轮代际与同锁执行检查，慢/跨轮结果零写入；策略 338 passed。真实只读预检锚定 1.921s、三站 stale，M3 未通过。未提交/推送/部署或切换 W3，F1/F2 未授权 | `outputs/m3_schedule_20261015/offline_three_site_round.json` SHA `a9049758d4597bf3873946352f5adeecabd332865192756a654d5f1706c77d90`；`real_readonly_preflight.json` SHA `761bb7c0d81ad3480968867fa7eb8d5d27866acb7e3e86c20d22ea62d3cabdf4`；`tests.xml` SHA `af908867bff0ded132b7f5c67ea4883cec3d88ae997184a27c1878eda987f1da`；manifest SHA `336e7a9d1c01f8edd2c2b7a818b9d3b0fdb5871bc18fed3d27e9368669ea902f` |
| 2026-10-15 | W1 | HA 批量快照只读核验与三站求解预算纠偏 | 一次 GET 返回 89 实体、三站 24 实体唯一，0.385s；批量接入后锚定 1.538s、三站 stale/eligible=false、零写入。每实体缺失/新鲜度可诊断；三站串行求解最坏 6s，加 2.5s 采集预算至少 8.5s，超 5s poll，M3 未通过。策略测试 334 passed，未部署/推送/改 W3/填时区/授权 F2 | `outputs/m3_bulk_20261015/ha_bulk_probe.json` SHA `508eb84a9531d370630861286cb33cf890c5a0dd78225ca8b9167bf3bd983d9e`；`preflight.json` SHA `c66034606c116febe9509dc85dcbabcf7f0b1c88d1ba7754ecaaf7a2ff72f097`；`tests.xml` SHA `bb2a42fda8f18f3ee4e22e6992110e1ee28e0b3c458266fbb81ea880bd2971b3`；manifest SHA `6250cbf49a0a2a2d39a8dba5731dc53b9512e78154bf92c35a98d1f917fe7ff2` |
| 2026-10-15 | W1 | 整轮预算、分类重读、慢响应与真实只读复测 | 默认 2.5 秒预算、请求/整轮耗时记录；慢响应超时安全拒绝，stale/坏配置/结构性价格错误不重读；策略 tests 332 passed。真实锚定耗时 2.510 秒触发截止，三站均不可优化、零写入：当前串行 HA 读取不满足预算，新窗口 M3 未验收。W3 原单实例 dry-run 不变，未擅填时区、未推送/部署/授权 F2 | `outputs/m3_deadline_20261015/preflight.json` SHA-256 `33504564faa15d1b8ff2c198958d1e4033e87ba28b3e21316dc3e8d1183fd167`；`tests.xml` SHA-256 `e9da2dcf667497204301cd82bd8c32c29a9b250ed06cb2638a90a935cb834962`；manifest SHA-256 `8752e41f21f102eb9dafa1629cd914d737d3adad70b4f0bd5808fb0ae2221759` |
| 2026-10-15 | W1 | API current 档位契约与进度限制交接 | 真实 current `start/end` 在 48 点两日电价中唯一；最多 3 次整批重读，跨档、HA 日期小时冲突、显式时区冲突、重复/缺失档位及未来缺价安全拒绝。策略测试 328 passed；旧窗口三站 stale、eligible=false，未获真实多 tick M3；旧 `Asia/Shanghai` 配置待复核。F1 未签署、F2 未授权，未推送或远端切换 | 本地提交 `86c7a185`；`M3_API_ANCHOR_DELIVERY_20261015.md`；`outputs/m3_api_anchor_20261015/price_contract_sample.json`、`preflight.json`、`tests.xml` |
| 2026-10-14 | 集成负责人 | 建立并行分工基线 | W1 在主工作树推进；W2 待启动；W3 已转为待集成；当前工作区仍存在跨线未提交改动，禁止直接并发编辑 | 本节 §0、§7、§8 |
| 2026-10-14 | W1 | A5 初步落地 | trace 新增字符串归因标签；跨站归因计数对 legacy `null` 保持未知，不再求和崩溃；回放摘要显式记录 idle 核算臂，非官方分数仍明确否定 | `tests/test_contest_replay.py`；当时阶段性回归 231 passed（不是当前总数） |
| 2026-10-14 | W1 | C4 预算复验 | 同一冻结快照/因果校准路径、每站 2 秒预算复跑；65 optimal + 7 verified suboptimal incumbent，72/72 输出；最大/平均 gap 0.755%/0.403%，72 计划物理审计 0 违规；不改 `mip_rel_gap=1e-9` | `outputs/w1_c4_verification_20261014_1/verification_report.json` |
| 2026-10-14 | W3 | 完成 B4 本地基线与消费者接线 | 分片/heartbeat、merge 视图、监控状态机、watchdog 过滤与过期状态保护；未启动真实采集、未执行远程部署或 HA 写入 | `2eb92ef`、`905a40f`；W3 定向测试 33 passed |
| 2026-10-14 | 集成负责人/W3 | 选择性集成与 D1 首轮恢复 | 仅集成采集可靠性、watchdog、监控状态机、离线演练和对应测试；明确未合并 W3 的 explainability-app/旧模型哈希修改。主仓回归 241 passed；应用回归 83 passed + 49 subtests。只读采集 PID 与 heartbeat 匹配、三站覆盖、零写入；新设备时间单 tick，旧缓存 2028 与新 2026 epoch 不连续。D3 未启动 | 主仓集成提交 `a632a56`（来源 `905a40f`）；演练证据来源 W3 `5586550`；`outputs/d1_recovery_20261014/d1_live_report.json`；`outputs/w3_integration_20261014/` |
| 2026-10-14 | W3 | 新模拟窗口 D1 只读采集 | `/api/contest` 确认为 `running/mock`；准备期到运行期设备时钟重置，因此运行期单独缓存。运行期三站均多个递增 tick、无回退，heartbeat 对应当前进程，零写入；站点仍有少量 30–45 分钟设备时间缺口，待继续诊断。重复观测去重已从 W3 `7cd64c4` 选择性提取，仅采集域/测试；主仓 244 passed。D3 未开始 | `outputs/d1_recovery_20261014/d1_mock2_running_clean_report_latest.json`；`data/live_history_mock2_running_20261014/` |
| 2026-10-14 | W3 | D1 基本连续/基本新鲜评估与天气快照能力 | 运行期采集墙钟新鲜度约 1 秒、heartbeat 无错误、最新站间差 0–15 设备分钟；三站 tick 覆盖约 88–89%，最大单缺口 45 分钟，无时间回退。共同缺口与单站缺口分类已记录；当前 API/HA 输入可用，历史缺口不阻断策略，受影响站点历史预测降级或转 persistence/当前值。天气 API 已保存 today/tomorrow 快照，包含 `retrieved_at`、`source_device_time_at_capture`、目标设备时刻、预测值和响应哈希；API 未提供 `published_at`，因此不能伪称发布时间。D3 未启动 | `analyze_d1_window.py`；`tests/test_d1_window_analysis.py`；`outputs/d1_recovery_20261014/d1_quality_report_latest.json`；`outputs/d1_recovery_20261014/api_weather_snapshot_20261014_v3.json` |
| 2026-10-14 | W3 | D1 持续运行复查与交接 | 12:18 UTC 只读核对 `/api/contest=running/mock`；PID 72388 的 heartbeat 已推进至第 411 轮，距核对约 2.34 秒、`consecutive_errors=0`、`writes_performed=0`；三站最新设备时间为 12:30/12:45/12:45，站间差 15 设备分钟。D1 当前输入继续可用，不因历史缺口停止策略；D3 未启动，远程双节点及自动故障切换未授权、未执行 | `outputs/collector_health_mock2_running/heartbeat_nodeA.json`；`outputs/d1_recovery_20261014/collector_mock2_running.out.log` |
| 2026-10-14 | W1 | A5 产物 smoke 与 C4 配置门槛 | 1 站 × 168h × 3 臂零流量回放：504 行物理审计通过；optimizer-attributed 计数为辐照度臂 168、持久化臂 168、自用基线 0；`official_score_claim=false`。运行配置新增预算必须为正且短于轮询周期；当时阶段性回归 232 passed | `outputs/w1_a5_zero_flow_smoke_20261014_1/`；`tests/test_config_solver_seconds.py` |
| 2026-10-14 | W1 | A5 拆分验收 | 归因报告与同输入基线对比均生成；5 站 × 168h 两口径输入等价，旧/新各 2520 行物理审计通过；收益、fallback、optimizer/non-optimizer 步数均列明；`official_score_claim=false` | `A5_ATTRIBUTION_AND_BASELINE_REPORT.md`；`outputs/w1_a5_compare_20261014_1/a5_same_input_comparison.json` |
| 2026-10-14 | W1 | readiness 三参数与快照 | `check_online_readiness.py` 检查 `solver_seconds=2.0`、`poll_seconds=5.0`、`soc_min_percent=0.0`；配置检查通过，历史仅 30 小时所以整体 `ready=false`；保存脱敏快照 | `outputs/w1_readiness_actual_20261014/readiness.json`；`config_snapshot.json`；235 全仓测试通过 |
| 2026-10-14 | W1 | 提交并复核 F1 参数冻结清单 | 早期清单迭代记录；A5/C4 后由集成负责人验收，参数建议现已在最终 manifest 逐项明确 | `F1_PARAMETER_FREEZE_CHECKLIST.md`；`outputs/w1_readiness_actual_20261014/readiness.json` |
| 2026-10-14 | W1 | A6 定向回归复核 | hold/zero-flow 契约、执行决策表、A5 回放、solver 配置、shadow、rolling MILP、在线时间链路定向测试共 89 passed；未改变 A5/C4 的待集成验收状态，也未启动 F2 | `pytest -q tests/test_idle_contract.py tests/test_execute.py tests/test_config_solver_seconds.py tests/test_contest_replay.py tests/test_run_live_chain.py tests/test_run_live_time.py tests/test_shadow.py tests/test_rolling_milp.py tests/test_system_model.py` |
| 2026-10-14 | W1 | 集成后全量回归复核 | 在当前共享工作区（含 W3 选择性集成）运行 `pytest tests -q`，241 passed；未改变 A5/C4 待集成验收状态，未启动 F2 | `pytest tests -q` |
| 2026-10-14 | W1 | F1 参数建议与 manifest 定版提交 | 5 练习站 × 168h × 3 terminal 口径 × 3 PV 臂；因果 current-visible 口径混合臂 +8.970% 是诊断性离线估计，非正式收益。补验在线缺价、非有限价格、设备时间错位路径：优化器 0 调用，live 恰一次 self_consumption，审计标记 non-optimizer-attributed；dry-run 不写入。manifest 含 100 份可用数据哈希、5 份诊断输入哈希和 15 个证据哈希，签核留空、`f2_authorized=false`。F2 暂停等待用户完成签核 | `F1_PARAMETER_FREEZE_CHECKLIST.md`；`F1_TERMINAL_PV_EVIDENCE.md`；`F1_ONLINE_PRICE_ANOMALY_EVIDENCE.md`；`outputs/f1_parameter_manifest_20261014.json`；SHA-256 `d65aec1cfbd9098b22ecc6fb6336dc07ddc85216877eb818e2028e9cad5ee674`；W1 提交 `7264265`、`539fd65`、`2ec6288` |
| 2026-10-14 | W1 | F1 在线价格异常验收补充 | 缺失、非有限、设备时间超出窗口及无时区四类输入均跳过优化器；live 显式自用回退且审计归因为 `non-optimizer-attributed`，dry-run 不写入。定向测试 61 passed，全仓 254 passed；manifest 中 26 项源码/证据哈希复算全匹配。此为模拟在线链路测试，非远程比赛机验收；F1 待签核、F2 未授权，线上 PV 仍为 persistence-only | `F1_ONLINE_PRICE_ANOMALY_EVIDENCE.md`；`outputs/f1_parameter_manifest_20261014.json`；SHA-256 `d65aec1cfbd9098b22ecc6fb6336dc07ddc85216877eb818e2028e9cad5ee674` |
| 2026-10-15 | W1 | F1 签核包、输出校验器与单站 smoke | 签核包逐项对照冻结参数、来源、限制和证据哈希；输出校验器检查 100 站输入哈希、每站 168 小时/四臂、672 行 trace、fallback/incumbent/归因统计、汇总金额和独立物理审计。单站显式冻结参数 smoke 通过：1 站 × 168 小时 × 4 臂，672 行，物理审计通过，`official_score_claim=false`、`formal_result_authorized=false`。定向 73 passed，全仓 279 passed；`f2_authorized=false`，未运行正式 F2 | `F1_SIGNOFF_PACKAGE.md`；`F2_RUNBOOK.md`；`validate_f2_output.py`；`outputs/w1_f1_signoff_smoke_20261015_v3/validation.json`；validator SHA-256 `4deebfbabe4edbbc7721e1f3ecead3b4c309fcd62caa34108e91ac6471bf3f68`；manifest SHA-256 `6ab83c5ac66af61b2362007c96ebffcad653b704ab48680776299c0b47cee45a` |
| 2026-10-14 | W1 | 全量回归复核（早期） | `pytest tests -q` 曾为 242 passed、1 failed；失败为采集器重复快照预期未同步。后续 W3 去重实现及预期已更新；集成负责人本轮当前共享工作区全量复跑为 246 passed | `pytest tests -q`；集成复核 2026-10-14 |
| 2026-10-14 | W1 | F1 terminal-price/PV 诊断与候选 manifest | 5 站 × 168h × 3 terminal 口径 × 3 PV 臂；current-visible price 口径因果、5/5 站物理审计通过；混合 PV 候选在 current-visible 口径 +8.970%，5/5 站正收益、0 fallback。结果是练习站离线代理证据，F1 仍待签核、F2 未启动 | `F1_TERMINAL_PV_EVIDENCE.md`；`outputs/f1_parameter_manifest_20261014.json`；`outputs/f1_terminal_*_v2_20261014/` |
| 2026-10-14 | W1 | 在线价格异常验收与 manifest 更新 | 缺失、非有限、设备时间超出窗口、无时区四类负例均跳过优化器，执行一次显式 self-consumption，归因 `non-optimizer-attributed`；dry-run 不写入。定向 61 passed，全仓随后 254 passed；manifest 当前 SHA-256 `d65aec1cfbd9098b22ecc6fb6336dc07ddc85216877eb818e2028e9cad5ee674`，26 项源码/证据哈希全匹配。仍是 mock 在线链路，不是远程比赛机验收，F1 未签核、F2 未授权 | `F1_ONLINE_PRICE_ANOMALY_EVIDENCE.md`；`7264265`、`539fd65`、`2ec6288`；`outputs/f1_parameter_manifest_20261014.json` |
| 2026-10-14 | W3 | 天气降级补丁与受控重启交接 | 天气缺点/坏响应时保留有效 HA、天气字段置空并记录原因；非法 HA 仍拒绝；单站异常不阻断其他站；定向 31 passed。旧 PID 72388 经归属核验后受控重启到 `collector/1.2-weather-degraded`，最终 PID 67288；比赛已 `ended/mock`，按赛程停止，零 HA 写入。D3 尚无后续独立实测配对 | `collect_online_history.py`、`src/energy_lab/online_contract.py`、`tests/test_history_collector.py`；`outputs/d1_recovery_20261014/restart_20261014T124116Z/`、`restart_20261014T124720Z/`；`api_weather_snapshot_20261014_v4.json` |
| 2026-10-14 | W2 | 完成 E4-2/E4-4 第一版 | 持久计划任务队列、基准重放对账、截止 SOC 约束接口、明确状态与调整建议；实现 trace 日核算导入/查询、JSONL CLI 与两页 UI。真实共享优化器调用待 E4-1 进入基线；暂无三站正式 trace 导入 | `feat/w2-e4-product` @ `9bb5d2a`; 94 后端测试、49 subtests、14 前端测试通过，build 通过 |
| 2026-10-14 | 集成负责人 | W1/W3 当时进度验收复核（历史快照） | 当时 W1 旧版 manifest 的 143 项 SHA-256 表列均存在且匹配；此数字不是当前 manifest 的源码/证据项数。顶层与 signature 的 `f2_authorized=false`、签核字段为空，故不授权 F2。全仓当时 `pytest tests -q`: 246 passed。W3 当时 heartbeat cycle 476、3.4 秒、errors=0、writes=0；三站设备时间一致，覆盖 87.5–89.1%、最大缺口 45 分钟、D1 可用。发现一次跨日天气缺点导致该轮整批丢样，需按降级语义修复后继续 D1；D3 仍未开始 | 旧版 manifest 验收记录；当前 W1 manifest 哈希和 26 项源码/证据校验见本节最新 W1 记录；`outputs/d1_recovery_20261014/d1_quality_report_latest.json` |
| 2026-10-14 | 集成负责人 | 集成 E4-1、W3 B4 与 W2 E4 | E4-1 与 W2 页面/服务以独立提交集成；W3 仅选择性集成采集域，排除其应用哈希修改。主仓 241 passed、应用 95 passed + 49 subtests、前端 14 passed/build 通过；真实 shared MILP deadline 约束测试通过 | `82ba72c`、`a632a56`、`76399f0`；`explainability-app/tests/test_e4_shared_integration.py` |
| 2026-10-14 | 集成负责人 | A5/C4 离线证据签核 | A5 5 站同输入两口径各 2520 行物理审计通过，归因和收益差异可对账；C4 72/72 输出、0 物理违规，配置子项通过。随后 W1 已补交 terminal/PV 敏感性证据与完整候选 manifest；F1 仍未签署，F2 仍阻塞 | `A5_ATTRIBUTION_AND_BASELINE_REPORT.md`；`outputs/w1_a5_compare_20261014_1/a5_same_input_comparison.json`；`outputs/w1_c4_verification_20261014_1/verification_report.json`；`outputs/f1_parameter_manifest_20261014.json` |

### W1/W3 本轮集成指示

**W1（原指示已交付，待签核）**：负责人认可 `current_visible_mean` 作为终值候选、效率 `0.95/1.0` 和显式吞吐成本 `0.02` 的离线模型约定，并认可混合 PV 仅用于离线候选、线上不据此启用的边界；这不是完整 F1/F2 签核。可选择性集成的代码/文档提交与 manifest 校验入口已交付（`7264265`、`539fd65`、`2ec6288`）。在线 `run_once_milp` 已补验缺价、非有限价格、设备时间超出价格窗口及无时区四类负例：该 tick 不进优化器，显式自用回退并审计 `non-optimizer-attributed`；离线 replay 的整站拒绝仍是不同路径，不能拿它替代在线验收。当前 manifest SHA-256 为 `d65aec1cfbd9098b22ecc6fb6336dc07ddc85216877eb818e2028e9cad5ee674`，26 项源码/证据哈希复核通过。下一步仅待集成负责人按该哈希签核 F1；签核前 `f2_authorized=false`，不得运行 F2。不要求等待 168 小时完整实时历史。

**W3**：不再等待零缺口历史。先交付天气缺测时保留有效 HA 样本的降级补丁：显式记录天气缺失原因/来源，历史依赖预测可降级，不能伪造辐照度或静默沿用旧值；非法 HA 状态仍应拒绝。补跨日缺点、坏天气响应、恢复后续采的测试，与现有整批失败测试同步契约。质量报告应把当前输入可用与历史预测质量分开，站间错位按站处理，不能用一站异常阻断其他有效站。当前 PID 72388 在去重源码修改前启动，运行中 Python 进程不会自动加载此后的代码；下一次经归属确认的受控重启才可声称线上采集实际运行新去重/降级逻辑，重启前后须留 heartbeat/缓存校验，不得直接覆盖现有证据。随后推进 D3：逐次快照保存可复核响应及获取前后设备时钟，筛选目标时刻严格晚于获取时设备时钟的预测；同一次读取前取得的 `/api/health` 时间只是近似锚点，三站六次 API 调用期间加速时钟可能已推进，不能把它当每个响应的准确发布时间。缓存辐照度来源为 `api_weather_at_HA_device_hour`，不能把“预测 API vs 缓存中的同源 API”称为预报 vs 实测误差；若没有独立实测辐照度，改报告因果 PV 预测 vs 后续 HA PV，并注明校准/组件转换误差也包含其中。

### W2 E5 集成验收记录

2026-10-14 集成负责人签核：E5 已选择性集成（`dfe0265`、`0861787`），以下“待集成”记录为交接时状态，本段为最新结论。主工作区应用 96 passed + 49 subtests、前端 15 passed/build 通过；真实共享 MILP feasible/infeasible/timeout/unsupported/rejected 五类通过。由源 trace 重建 24 site-days（每站 8 天），逐站 API 查询通过，JSONL SHA-256 `3f79b4e8e5c62e930ff063f5b34dd2f5506a485ebfc349cf7d11d3e2c40636fe` 与交接一致；浏览器 7 项通过、errors=[]。报告见 `explainability-app/artifacts/e5-real-replay/import-report.json`、`artifacts/e4-floor-acceptance/acceptance.json`、`artifacts/browser/result.json`；复现入口为 [演示讲稿](explainability-app/docs/e5-demo-runbook.md)。生成数据库/报告均被 Git 忽略，API/worker 已停止；W1/W3 未提交改动保留。验收范围仅为离线核算及人工样例产品流程，不覆盖现场控制、官方结算、负价实证或完整历史决策回放。

W2 最新进展：显式 `dataset_local_hour` 离线模式已实现（默认仍要求带时区时间，不猜 UTC），三站选定 replay 已导入独立 E5 数据库，新建 24 site-days、逐站 API 查询通过。五类真实优化器验收通过；应用回归 95 passed + 49 subtests，floor/桥接定向回归 11 passed + 6 subtests。证据为 `w2-worktree/explainability-app/artifacts/e4-floor-acceptance/acceptance.json`、`artifacts/e5-real-replay/import-report.json`。E5 仅核算数据导入，不代表在线决策重放、正式比赛三站或官方结算；所选窗口无负价样本。

2026-10-14 集成更新：E4-1 已在 `82ba72c`，E4-2/E4-4 已在 `76399f0`；真实共享 MILP 截止约束测试通过。此前预检定位 `outputs/contest_hold_pair_zero_v2_20261013/` 三站练习站点 replay（每站 504 行、选定 `irradiance` 臂 168 行），但原 trace 无时区，被核算接口正确拒绝；不得悄悄补时区并冒充原始数据或官方比赛三站。来源说明见 [W2 E4/E5 预检](../w2-worktree/explainability-app/docs/w2-e4-e5-preflight.md)。

1. 已完成：共享模型版本/哈希及证据导出层对齐；可行、设备不可达、真实 HiGHS solver timeout、缺失约束能力 unsupported、非对齐 deadline 拒绝五类验收通过。验收输入为人工快照，超时与缺失能力采用明确记录的故障注入，不是在线设备验收。
2. 已完成（待集成验收）：三站真实家庭历史 replay 导入独立 E5 核算库；显式使用 CSV 原始本地日期，不附加虚构时区，不与在线时间戳混合。默认带时区接口仍拒绝无时区输入。来源哈希、已有物理审计、逐行电网/PV 核算复核与逐站 API 查询已记录。
3. 已完成（待集成签核）：E5 三站演示叙事与界面证据边界，代码冻结 `257bf13`；主基线 `040257d` 的隔离树选择性接入前置核算代码 `8362854`，实际复验应用 96 passed + 49 subtests、前端 15 passed/build、真实 MILP 五类验收、浏览器 7 组检查（errors=[]）。原始 JSONL 哈希一致；SQLite/报告均未跟踪，命令与哈希见 [W2 冻结交接](../w2-worktree/explainability-app/docs/w2-freeze-handoff.md)，逐步输入/预期/回退见 [演示讲稿](../w2-worktree/explainability-app/docs/e5-demo-runbook.md)。主仓其他 worker 改动保留；E5 不标记已验收。PV 历史核算不包装成 E1–E3 完整决策回放。

### 接口/阻塞记录

### W3 2026-10-14 天气降级与受控重启交接

**2026-10-15 W3 补丁验收**：天气降级补丁独立提交 `13c9730`，仅含 `collect_online_history.py`、`src/energy_lab/online_contract.py`、`report_d1_recovery.py` 和四个对应测试文件；未纳入解释性应用、其他 worker 代码或历史产物。时区解析顺序为 CLI `--device-timezone` > 明确确认的配置 `local.device_timezone`/顶层 `device_timezone` > API 时间戳推断；普通 `local.timezone` 是地理/显示设置，不当作已确认 HA 设备时区，无可信时区时拒绝该站 HA 样本。已有 `local_online_config.json` 只填 `local.timezone=Asia/Shanghai`，下一运行窗口需先核定设备时间坐标，再显式填 `device_timezone` 或 CLI；不得直接沿用上个 epoch 的 UTC 假设。

天气 `null` 读入历史为 `NaN`；缓存 PV 校准门槛排除缺测/非有限/非白天值并按独立设备小时计数，样本不足返回 `current_pv_persistence` 与明确原因。天气缺失时 shadow 策略仍能生成当前 PV/负载 persistence 候选，未伪造天气。定向 44 passed；最终全仓 `pytest tests -q` 为 269 passed。已结束模拟窗口未重启；下一窗口必须另建 epoch/cache 并重新验收 D1。D3 仍未完成，必须有获取后目标时刻的独立 HA 实测 PV 配对才可给天气质量结论，同源缓存辐照度不可代替实测。

**2026-10-15 W3 D3/D1 准备**：新增离线 `analyze_d3_causal_pairs.py`，严格要求天气响应原文哈希有效、显式共同 `epoch_id`、预测目标严格晚于该响应后的设备时钟，以及获取之后同站同 epoch 的独立 HA PV；重复目标只计一次，缓存天气辐照度不作为实测。可选 PV 转换系数必须在同 epoch、预测前冻结，否则配对保留但 `scored_pairs=0`。旧 v4 快照离线审计为 `insufficient_data`：144 预报点因缺少 epoch 被排除，0 有效配对，0 可评分配对；报告见 `outputs/d1_recovery_20261014/d3_pairing_insufficient_20261015.json`。测试新增后全仓 `pytest tests -q` 为 277 passed。代码提交 `96ba692`，D3 仍未完成。

下一模拟窗口清单见 `W3_NEXT_WINDOW_D1_D3_CHECKLIST.md`：新建独立 epoch/cache/heartbeat，采集行和天气快照使用同一 epoch；只读进程须显式记录 PID、版本、设备时区、零写入和三站时间缺口。当前不重启已结束窗口、不启动远程部署、不做 HA 写入。

- 采集器已实现：天气 API 跨日缺点、坏响应或无匹配点时，保留有效 HA 样本，三个天气数值字段为 `null`，记录 `weather_status=missing`、`weather_missing_reason`，不填邻点或旧值。非法 HA 状态仍拒绝；单站 HA 异常记录在 `station_diagnostics.rejected_stations`，不影响其他站有效样本。天气恢复后可继续采，重复观测去重仍生效。当前工作区修改尚未单独提交，集成时仅选择 W3 文件。
- 定向测试 31 passed；随后全仓回归 `pytest tests -q` 为 253 passed。此前一次全仓运行 10 项 live-chain `station_error` 失败，原命令重跑已恢复，且独立复现对应 dry-run 为 `zero_flow_dispatched`；未修改 live-chain 文件，故暂按瞬时共享工作区波动记录，不把首次失败抹去。
- 已核对旧 PID 72388 命令行、路径和 heartbeat 归属后受控重启，保留旧心跳及三站缓存大小、哈希、尾部样本于 `outputs/d1_recovery_20261014/restart_20261014T124116Z/`。新 PID 86752 验证 `collector/1.2-weather-degraded`、cycle 2、0 errors、0 HA writes。为确保 Windows 后台日志持续落盘，又以相同归属检查从 PID 86752 受控重启到 PID 67288，前后证据见 `outputs/d1_recovery_20261014/restart_20261014T124720Z/`；当前心跳须以 PID 67288 的实时文件为准，旧文件不是当前运行证据。
- 模拟器只读 `/api/contest` 于 12:47 UTC 确认 `ended/mock`，设备时间停在 `2026-10-21 08:15`，故即使采集心跳仍推进，缓存尾部墙钟新鲜度也会下降；这不是运行期实时输入已通过验收。已在确认归属后停止 PID 67288，停止前心跳/缓存证据与 `stopped.json` 保存在 `outputs/d1_recovery_20261014/restart_20261014T124720Z/`。赛程已结束，不自行重开、不做 HA 写入。
- API 天气快照 v4 保存每次响应原文、SHA-256、获取时间和每次调用前后的 `/api/health` 设备时钟；目标时刻必须严格晚于该响应后的设备时钟才可形成因果预测样本。API `published_at` 缺失时保持 `null`。缓存辐照度来自同一天气 API，不能当独立实测；若无独立辐照度，则 D3 只比较预报驱动 PV 预测与后续 HA 实测 PV，并注明辐照度到 PV 转换模型误差。当前比赛已结束，v4 尚无后续实测配对，D3 未启动。文件：`outputs/d1_recovery_20261014/api_weather_snapshot_20261014_v4.json`。

| 日期 | 提出者 → 接收者 | 需求或阻塞 | 决策/状态 |
|---|---|---|---|
| 2026-10-14 | W2 → 集成负责人 | E4-2 需要 E4-1 `energy_floor_kwh` 已提交基线及模型哈希 | 已解决：`82ba72c` / SHA-256 `603de45ca089b13665abd91fe2f58aa12e576909dfcba2ffb481cff2aedd13a4`；真实集成测试见 `76399f0` |

### 2026-10-15 集成复核与下一步指示（覆盖上文旧指示）

本轮核对实际 HEAD 为 `2ec6288`，W1 三个交付提交已在当前分支，不再要求重复集成。上一轮主仓全量回归为 254 passed；本轮运行 `python -m pytest tests/test_history_collector.py tests/test_weather_snapshot_provenance.py tests/test_run_live_chain.py tests/test_contest_replay.py -q` 为 47 passed。已有的在线价格负例是 mock 验收，不能替代远程比赛机 M3。F1 当前未签核 manifest 哈希仍为 `d65aec1cfbd9098b22ecc6fb6336dc07ddc85216877eb818e2028e9cad5ee674`；本轮不修改它、不授权 F2。

**W1 下一增量：先闭合正式回测与线上求解接受口径。** `run_contest_strategy_replay.py:115` 目前使用 `status != "optimal"` 判断回退，而 `shadow.make_candidate` 接受共享求解器独立核验后的 `suboptimal` incumbent。C4 的 72/72 输出结论不能直接证明当前 replay 在 2 秒预算下也有同等可用率。让 replay 对 optimal 和经过共享求解器验证的超时可行解采用同一接受条件；保留 `limit_reached` 原始状态，另记 accepted-incumbent 标志、gap 及优化器归因，不能冒充 optimal。无解、未验证解和物理不合格解仍显式自用回退、非优化器归因。补“验证可行解被接受”和“未验证/不可行解被拒绝”的离线测试，并复跑 C4 冻结病例与小规模同输入 smoke；若改变此前诊断结果，更新相应证据。

随后交付 F2 启动入口：明确指定 `--stage 4 --hold-mode zero_flow --terminal-price-mode current_visible_mean --wear 0.02 --solver-time-limit 2`，显式记录效率 `0.95/1.0`、SOC `0/100`、PV 各评估臂、输入哈希、源码版本和失败/回退统计；不能依赖 CLI 当前的旧默认值。先给出小规模产物供负责人复核，100 站 F2 等待最终签核。源码/证据改动后生成新的候选 manifest 版本与哈希，保留旧版，仍保持未授权。负责人已认可上述参数约定及 PV 线上 persistence-only 边界；本次退回的是执行口径闭合，而不是要求继续等实时历史。

**W3 下一增量：独立提交降级补丁，并补足时区和消费边界。** 当前受控启动显式传入 `--device-timezone UTC`，因此既有运行证据不受缺失天气时区影响；但通用采集入口只使用 CLI 参数/天气推断，天气坏响应且 HA 时间无时区时仍会拒绝样本，即使配置中提供了设备时区也不会自动读取。按“CLI 覆盖 > 已确认配置 > API 时间戳推断”解析时区，记录依据；没有可信时区时明确拒绝时间契约，不猜墙钟时区。补完全缺失天气且显式时区可用、配置时区生效、无可信时区拒绝，以及单站异常不阻断其他站的验收。

补一个下游链路验收：天气 `null` 经历史加载成为 NaN 后，HA 的 PV/负载仍可用于 persistence；日照校准排除缺测点，样本不足显式降级，不能整站崩溃或静默填零。此为待补证边界，不表示已发现下游必然崩溃。仅提交采集/契约/报告/快照及对应测试的归属文件；不纳入其他 worker 的模型或应用改动，提交前提供文件清单。

D1 当前窗口已结束且采集停止，交接中 `PID 67288` 的 heartbeat 是停止前证据。不要重启已结束窗口来制造“新鲜”数据。下一可用模拟窗口按新的 epoch/缓存重新只读验收，采用基本连续、基本新鲜及按站降级。W3 可先离线完成 D3 配对分析器与测试：每次预测目标严格晚于该次调用后的设备时钟，使用后续 HA PV 为独立观测，防止不同 epoch 或同源 API 自比较，分别报告有效配对数、缺失和样本不足。当前 v4 无后续配对，D3 不标已完成；有真实后续数据后再形成质量结论。

集成负责人收到这两组独立增量后负责选择性集成、最终全仓回归和 manifest 签核；worker 不自行改签核字段或启动 F2。W1 与 W3 不互相等待 D3/168 小时历史，W3 的共享契约改动须登记；同时编辑时使用独立工作树，文档由负责人串行更新。

### 2026-10-15 W1/W3 更新后的集成结论

W1 的 `23efb11` 已在当前提交历史中，补丁范围包括 replay 的 verified incumbent 接受、执行审计字段、F2 命令门控及对应测试。`run_limit_fallback_verification.py` 在 2 秒预算下完成 72 次只读复验：65 optimal、7 verified `limit_reached` incumbent、0 explicit fallback、0 blocked/error，物理审计 0 violations；1 站 stage-4 显式参数 smoke 完成 168 小时且物理审计通过。新 manifest 为 `f1-final-proposal-20261015-2`，135 项源码/数据/证据条目逐项哈希匹配，当前 `f2_authorized=false`、签核人和日期为空。`prepare_f2_command.py` 对未签核 manifest 正确拒绝，未创建 F2 输出。因此 W1 的技术交付可进入“待签核”，但不能写成 F1 已签核或 F2 已开始。

W3 的 `13c9730` 已独立提交，限定在采集/契约/报告/测试域。天气缺失保留 HA、天气字段使用 `null`、历史加载为 NaN 后 persistence 可用、校准样本不足显式降级；时区优先级和无可信时区拒绝均有测试。当前根仓 `pytest tests -q` 为 269 passed。应用目录在根 Python 环境直接运行会因缺少其独立 `fastapi`/包路径而在收集阶段失败；这属于环境复现限制，不能记为主仓代码回归失败，也不能借此声称应用测试已复现。

本轮集成决策：W1 进入 F1 人工签核门；W3 进入选择性集成验收门；两者均不自动修改 manifest 签核字段。F2 仍保持关闭。下一步是由签核人确认 F1 checklist 中效率、吞吐成本、terminal 缺价回退、PV 线上 persistence-only 和准确 manifest 哈希五项，再明确是否授权 F2；W3 仅在新模拟窗口建立新缓存后重新做 D1，D3 仍需后续 HA 实测配对。

### 2026-10-15 下一轮 W1/W2/W3 工作令（由集成负责人发布）

排序以验收门槛而非日历估算。当前 `23efb11` 是共同代码基线；本节只派发下一增量，不改变 F1 未签核、F2 未授权、M3 未完成和 D3 样本不足的事实。各 worker 在独立 worktree 提交；不得在主工作树同时编辑本文档，进度补丁交由集成负责人串行合并。已有未归属文件及产物保持原样，不作为新任务的默认清理对象。

| 责任方 | 立即可做的独立增量 | 交付/验收门槛 | 不得越过的边界 |
|---|---|---|---|
| W1：策略与正式回测 | 整理 F1 签核包：逐项对照 `F1_PARAMETER_FREEZE_CHECKLIST.md` 与哈希 `fac1fb83c96c1d4e86fabee18db1d74f135a79b73b9bb32ba551efb901730be4`，提供只读签核说明、参数/来源/限制表和新旧 replay 口径差异；冻结 1 站 smoke 与 72 例 C4 证据。为 F2 准备可复现运行单和输出校验器，先用 mock/单站验证统计字段，不重跑正式 100 站。 | 签核包列明 `0.95/1.0` 效率、`0.02` 吞吐成本、当前可见价格均值、缺价自用回退、零流量成功 idle、线上 PV persistence-only、离线 PV 三臂和官方分数否定声明；输出校验器检查 100 站数据哈希、每站 168h、四臂、物理审计、fallback/incumbent/归因计数与 manifest 哈希。集成负责人复核后才生成带签署信息的新 manifest；旧候选保持可追溯。 | W1 不代签 F1、不改 `f2_authorized`、不自行运行 100 站 F2 或公布正式收益；不得改 W2 应用、W3 采集和共享线上契约。若发现冻结参数要变，先登记例外并重做受影响证据。 |
| W2：解释产品与演示 | 在 `explainability-app/` 内做“真实策略回测记录 → 现有解释卡/反事实/计划检查”的只读导入适配，优先使用 W1 已产生的单站 smoke trace；建立来源标签、字段映射与缺字段拒绝规则。另为未来带时区的在线审计事件保留独立接口，不能把离线数据冒充在线。修复本机应用测试的可复现启动说明，使用应用自己的依赖环境复跑，不以根 Python 环境缺 `fastapi` 的收集失败判断产品代码。 | 先交付字段映射表与一站端到端演示：每条回测决策可追溯 `site`、原始数据集本地小时、输入/计划/模拟执行、`solver_status`、`accepted_incumbent`、`mip_gap`、fallback 原因和归因；优化器/自用回退明确区分，无法从旧 trace 证明的字段保持未知。`dataset_local_hour` 仅用于离线，不伪造设备时区。后端/前端测试与构建、一次浏览器流程、导入数据哈希和逐步复现命令齐全。 | 不把练习站单站数据称为比赛三站、官方结算或在线控制；不修改共享优化器/线上契约；不新增与真实决策接入无关的页面。跨线字段缺口先报集成负责人，由 W1/接口所有者处理。 |
| W3：采集可靠性与天气证据 | 在离线状态实现 D3 因果配对分析器及合成/冻结数据测试；输出 `valid_pairs`、未到目标设备时刻、缺少后续 HA、跨 epoch、同源 API 不合格等分项。准备下一窗口 D1 只读启动清单，明确设备时区的确认来源、新 epoch/cache 路径、PID/heartbeat/零写入/站间时刻检查。 | 合成测试证明目标时刻严格晚于该次天气调用后的设备时钟、后续观测来自独立 HA PV、同站同 epoch、设备时间加速下不使用墙钟配对；报告同时给预测误差和样本数，样本不足时输出显式 `insufficient_data`。下一窗口只读实测需另行记录新缓存覆盖/缺口/新鲜度与版本哈希，旧 heartbeat 不算新验收。 | 不拿缓存中的 `api_weather_at_HA_device_hour` 当独立实测辐照度；不因 D3 无样本阻塞当前 persistence 策略；未确认设备时区不启动有效性声明；不得重开已结束窗口、做 HA 写入、远程部署或故障切换。 |

**集成负责人自己的关键路径**：先完成 F1 参数/例外签核判断，形成可核验的签署版本与准确哈希；F2 授权是独立决策，授权后由 W1 执行正式回测，负责人复核每站物理审计、失败率、归因与非官方收益。并行接收 W2 字段映射并冻结审计接口；接收 W3 分析器后在下一可用窗口组织 D1/M3 只读验收。M3 只读不等于 HA 控制写入；真实控制、远程部署及切换均需另行确认。

**汇合顺序**：① W1 签核包 + W2 字段映射 + W3 离线 D3 分析器可并行；② 负责人冻结 F1/审计接口后，W1 才能在单独授权下跑 F2，W2 才导入 F2 正式 trace；③ 下一模拟窗口 W3 重验 D1，负责人做 M3 只读；④ F2、真实决策解释链和 M3 均通过后再整理 F4 赛前证据。D3 若仍无独立配对，保持 `insufficient_data` 和线上 persistence-only，不拖住其他可用链路。

**每次交接模板**：任务编号、独立提交号和文件清单、可复现命令、测试结果、产物路径及哈希、来源/时间/官方口径限制、未解决风险、下一步。W1/W2/W3 只报告自己线的证据，不互相代签；集成负责人在本节及 §8 更新状态。

### 2026-10-15 W2/W3 进度复核（集成负责人）

本轮以当前主仓 `82c2c83`、W2 worktree `1b1c788` 和 W3 worktree `7cd64c4` 为核对对象。主仓全量 `python -m pytest tests -q` 实测 **279 passed**；W3 的 D3/天气 provenance/collector 定向回归实测 **25 passed**。W3 的 `96ba692`（D3 因果配对分析器、天气快照 provenance、下一窗口清单）及 `82c2c83`（显式采集 epoch provenance 测试）已在主仓历史中，代码范围和测试通过；旧 v4 快照仍为 `insufficient_data`（144 个预报点缺少 epoch，0 个有效/可评分配对），所以 D3 不能标记完成，也不阻塞 persistence-only 线上策略。下一窗口仍须新建 epoch/cache，完成只读 D1 多 tick 后再获取独立 HA 后续观测。

W2 的 `1b1c788` 尚未合入主仓，变更限定在 `explainability-app` 的离线回放 API、前端只读页面、测试和运行说明。其 SHA 锁定 W1 单站 trace `c0a7ff9479f2f8faffc6791cd4c0670f38107beba22599fcfd2c4a5479e99fbf`，严格要求 504 行/三臂各 168 小时，保留 `dataset_local_hour`，并明确无设备时区、无现场执行、无官方结算；字段映射没有把计划、指令和离线物理流混称。应用独立环境定向后端测试 **13 passed**（仅既有 Starlette/httpx 弃用警告），前端 Vitest **16 passed**。主仓根 Python 直接收集应用测试失败是环境路径/依赖问题，不作为产品失败证据；W2 交接文档已说明需使用应用 `.venv`，并为跨仓 `test_shared_provider` 显式设置 `PYTHONPATH=src;../src`。

本轮集成决策：W3 离线代码增量已进入主仓且回归通过，实测验收仍待新窗口；W2 保持“增量待集成”，暂不整枝合并。W2 前端生产构建已由负责人在 frontend 目录复跑通过。W2 下一步补两项证据：①按交接命令完成 `scripts/offline-replay-smoke.cjs`，保存结果、截图及命令输出；②将 `test_shared_provider` 的跨仓依赖写成可直接复制的 PowerShell/Unix 命令并复跑，不能依赖集成负责人当前目录。另明确旧 trace 未提供的 `accepted_incumbent`、`mip_gap` 和具体 fallback 原因为未知，不能由状态猜测；与 W1 协调新版 trace 的可选字段契约。浏览器 smoke 通过且文件清单无越界后，集成负责人再选择性合入 `1b1c788`，并复跑主仓与应用定向回归。W2 不得把离线回放称为在线决策、比赛三站或官方结算，也不得修改共享优化器和线上契约。

W3 下一步保持原指令：不得重开已结束模拟窗口、不得 HA 写入或远程部署；在新窗口按清单完成基本连续/基本新鲜 D1，D3 仅接受“天气调用后设备时刻严格晚于目标、同站同 epoch、后续独立 HA PV”的因果配对，样本不足时继续输出 `insufficient_data`。W1 的 F1 人工签核和 F2 门控不因 W2/W3 进度自动改变。

### 2026-10-15 W1/W2/W3 最新进度复核

W1 的 `c3da6fa` 已完成选择性集成。F1 候选 manifest 当前哈希为 `6ab83c5ac66af61b2362007c96ebffcad653b704ab48680776299c0b47cee45a`，签核栏仍为空，`f2_authorized=false`；单站 smoke 校验报告为 `valid=true`、1 站 × 168 小时 × 4 臂、672 行物理审计通过，且 `official_score_claim=false`。W1 定向校验/回放测试 28 passed，主仓全量回归 **279 passed**。该证据足以进入 F1 人工签核，不构成 F1 已签核或 F2 授权。

W2 的 `1b1c788` 已经选择性合入为 `d251ae3`。合并冲突仅涉及 `w2-freeze-handoff.md` 的追加交接说明，已保留原 E5 边界并纳入 W1 离线回放说明。应用自身环境复跑为 **98 passed + 49 subtests**（仅 Starlette/httpx 弃用警告），前端 **16 passed**，生产构建通过；配置真实 trace 的独立服务浏览器 smoke 输出 `passed=true`、`browser_errors=[]`。唯一未闭合项是应用独立环境中的跨仓 `test_shared_provider` 仍因缺少根项目 `joblib` 失败；这不是回放适配失败，但 W2 需提供带根依赖的完整复现命令或明确将该测试列为跨仓环境前置条件。

W3 没有新的实测窗口交付，状态不变：D1 当前窗口已结束，D3 旧快照仍为 `insufficient_data`，不得把离线分析器测试当作天气质量验收。下一任务仍是新窗口独立 epoch/cache 的只读 D1 和后续独立 HA PV 因果配对；不重开旧窗口、不写 HA、不做远程部署。

本轮集成结论：W1 进入 F1 人工签核门；W2 的只读回放增量已集成验收；W3 保持等待新窗口实测。下一顺序为：①负责人/签核人完成 F1 checklist 五项确认并决定是否授权 F2；②若单独授权，W1 运行 100 站 F2，使用 validator 复核后 W2 再导入正式 trace；③下一模拟窗口由 W3 重验 D1/M3 只读。任何一步均不自动触发 HA 写入或正式收益发布。

### 2026-10-15 下一阶段工作令

本节覆盖上一轮 worker 指示，作为当前执行版本。所有 worker 必须在各自 worktree 工作，提交号、变更文件、命令、结果、产物哈希和限制写入交接；不得直接修改主工作树的共享状态文档。未获得负责人明确授权前，不运行正式 F2、不写 HA、不重开已结束模拟窗口。

| Worker | 当前任务 | 完成门槛 | 禁止事项 |
|---|---|---|---|
| W1 | 完成 F1 签核准备包的最后核对：逐项检查 `F1_SIGNOFF_PACKAGE.md` 的效率、吞吐成本、terminal price、缺价回退、zero-flow、PV persistence-only、incumbent 接受和当前 manifest 哈希；补一份只读差异/风险说明。等待负责人签核后，再仅用 `prepare_f2_command.py` 生成命令并做命令参数审计。 | 交付签核清单逐项证据、manifest 当前 SHA、单站 smoke 校验结果；如负责人单独授权 F2，再运行 100 站并提交 validator 输出、每站 168h/672 行、物理审计、fallback/incumbent/归因统计和完整日志。 | 不代填签名、不修改 `f2_authorized`、不在授权前运行 100 站、不发布正式收益；发现参数/源码哈希变化必须暂停并报告。 |
| W2 | 维护已集成的只读回放功能。补齐跨仓测试说明：给出应用 `.venv` + 根项目依赖（含 `joblib`）的可复制命令；确认旧 trace 缺失 `accepted_incumbent`、`mip_gap`、fallback 原因时显示为未知，不从 `solver_status` 推断。准备未来 F2 trace 的可选字段兼容测试，但不导入虚构数据。 | 交付更新后的运行说明、跨仓测试成功或明确环境前置、后端/前端回归和浏览器 smoke；所有页面继续显示 `dataset_local_hour`、源哈希及非在线/非官方结算边界。 | 不修改共享优化器/线上契约，不把练习站回放称作比赛三站、在线控制或官方结算，不用人工样例填补缺失字段。 |
| W3 | 保持离线 D3 分析器和下一窗口 D1 清单。新模拟窗口可用后，创建全新 `epoch_id`、cache、heartbeat 和日志，先只读验证三站多 tick、设备时区依据、基本连续/基本新鲜、零写入；随后采集天气调用后的独立 HA PV，运行 D3 因果配对。 | D1 报告覆盖 PID/版本/设备时区/站间时间差/缺口/新鲜度/写入计数；D3 报告分列有效配对、未到目标时刻、缺后续 HA、跨 epoch、同源 API 排除，并在不足时输出 `insufficient_data`。 | 不重启旧窗口、不复用旧 epoch/cache、不用墙钟时间配对、不把 API 辐照度当实测、不写 HA、不远程部署或故障切换。 |

**集成负责人动作**：在收到 W1 签核材料前不改变 manifest；收到 W2 兼容说明后冻结离线审计边界；收到 W3 新窗口报告后复核 D1/D3 证据。只有 F1 签核和 F2 单独授权均明确完成，才允许 W1 执行正式 F2；F2 校验通过后才安排 W2 导入正式 trace。W3 的 D3 即使继续样本不足，也不阻塞线上 persistence-only 策略。

### 2026-10-15 在线完整模拟赛优先路线

本阶段目标改为单一端到端验收：**比赛机上的策略进程连续运行三站完整模拟窗口，按 SOP 经 HA 写入控制，窗口结束后在模拟器看板产生真实成绩，并保留可审计日志；本地电脑离线时进程仍继续运行。** F1/F2 离线收益优化、D3 天气质量和 E5 展示均降为辅助项，不得阻塞在线闭环。线上成绩只认看板的 `saving_settled_percent` 及三站结算明细，不用本地估算替代。

| Worker | 关键路径工作 | 交付门槛与证据 | 当前边界 |
|---|---|---|---|
| W1：在线策略链 | 收口 `run_live.py --strategy milp` 的比赛运行入口；核对价格/天气 API、HA 设备时间、SOC/功率回读、三站逐站隔离和异常回退。修正远程启动入口显式传 `--strategy milp`，并补在线审计字段（设备时间、候选、实际指令、下一 tick 回读、写入计数、错误/恢复）。先 dry-run，再在负责人授权窗口执行 live。 | ①本地/环境机三站 dry-run 连续多 tick；②单站受控写入后下一 tick 回读通过；③三站 live 写入小窗口无物理违规、无未解释错误；④完整 168 设备小时结束后生成 `run_live_audit.csv`、日志和看板成绩截图/API 快照。 | 未完成 dry-run 和单站回读前不得三站 live；不得把本地 replay 或模型收益当看板成绩；不得修改 HA 安全开关绕过门禁。 |
| W2：成绩与运行证据 | 不再扩展离线解释页面。实现/验证在线运行证据采集：按设备时间记录每站策略输出、HA 回读、API 窗口、进程健康和最终看板成绩；提供比赛结束后的成绩核对脚本/报告，将看板 `saving_settled_percent`、三站分数、期末 SOC 与本地审计按时间对齐。 | 交付一套只读核对命令和报告模板；能证明“同一模拟窗口、同一三站、同一时间基准、看板结算已生成”，并明确本地日志与官方成绩不一致时以看板为准。浏览器/API 证据需保存时间、窗口/epoch、响应哈希。 | 不伪造成绩、不写看板、不把离线回放页面当在线控制台；不阻塞 W1 的 live 控制链。 |
| W3：比赛机常驻与恢复 | 负责远程运行可靠性：在比赛机验证固定代码版本、独立配置、进程守护、日志轮转/落盘、健康检查、断线重连和重启恢复。适配当前浏览器 EC2 Instance Connect 现实条件，先提供无需本地 SSH 私钥的 Git 拉取/启动步骤；若采用 `run_remote.ps1`，补充 `--strategy milp` 和明确的远程启动方式。 | 交付比赛机 dry-run 常驻证据：本地终端断开后进程仍在、HA/API 短暂失败后重试、进程异常后可恢复且不重复写入；提供 `status/logs/stop` 和恢复演练记录。最终 live 前完成一次 168 小时窗口级守护方案评审。 | 未完成 dry-run 常驻与恢复演练前不得宣称“不间断”；不得自动重启已结束模拟赛、不得远程写 HA，除非负责人已给出明确窗口授权。 |

**汇合门槛（按顺序）**：

1. W3 先在比赛机固定版本并完成常驻 dry-run；W1 同时完成三站在线 dry-run 和单站受控回读。
2. 集成负责人核对 W1/W3 的配置哈希、进程命令、设备时间和零写入证据，才批准三站 live 小窗口。
3. W1 执行三站 live；W2 实时保存审计与看板窗口证据；W3 监护进程和恢复，不改变策略决策。
4. 完整 168 设备小时结束后，W2 对照看板真实结算；集成负责人确认成绩、日志、写入计数和异常恢复后，才称为“在线模拟赛闭环完成”。

**当前已知接线风险**：`run_remote.ps1` 当前启动命令未显式传 `--strategy milp`，而 `run_live.py` 默认策略是 `dispatch`；下一轮 W1/W3 必须优先修正并用启动日志证明实际运行策略。远程脚本依赖 SSH 私钥，而当前操作方式是浏览器 EC2 Instance Connect，W3 必须提供与实际访问方式匹配的常驻启动方案，不能把未执行的 SSH 自动部署当作已完成。

### 2026-10-15 在线优先路线首次进度核对

本轮核对主工作树 HEAD 仍为 `d251ae3`。W1 没有新的在线策略提交或三站 dry-run/受控写入证据；W3 worktree 仍为 `7cd64c4`，没有比赛机常驻、恢复或新模拟窗口证据。主仓 `run_remote.ps1` 仍未传 `--strategy milp`，`run_live.py` 默认仍为 `dispatch`，因此远程 MILP 入口风险未解除。不得据旧 B1/B2 零流量写入或旧窗口只读缓存宣称在线闭环已有进展。

W2 worktree 新提交 `cf0adcf` 仅补旧离线 replay 的诊断字段缺失时 `null/未知` 语义，以及跨仓依赖说明；未实现在线成绩采集。定向后端 3 passed，前端 16 passed、生产构建通过。该增量可保留在 W2 分支，**暂不优先合入**，避免偏离在线关键路径；也不把其分支内共享状态文档覆盖主控版本。W2 下一次交付应转向只读看板成绩与在线审计对账工具，而不是继续扩展离线页面。

本轮指示：W1 下一次必须交付 `--strategy milp` 的三站连续多 tick dry-run、实际命令/配置哈希/零写入日志，并列明设备时间和价格窗口对齐结果；W3 下一次必须交付比赛机固定提交的常驻 dry-run、断开本地连接后存活和进程恢复证据；W2 下一次必须交付只读看板 API/页面成绩字段调查、同一模拟窗口身份与三站结算对账契约。三线未达到这些门槛前，不批准三站 live 小窗口，也不启动完整 168 设备小时模拟赛。

### 2026-10-15 W1 签核包检查与 W3 结束窗口回复

W1 回复已完成 F1 签核包检查；当前主仓仍无新的 W1 提交或在线 dry-run 产物。现场复核 `outputs/f1_parameter_manifest_20261014.json` SHA-256 仍为 `6ab83c5ac66af61b2362007c96ebffcad653b704ab48680776299c0b47cee45a`，但 `signature.integration_owner`/`signed_at` 为空、terminal/PV 两项批准为 false、`f2_authorized=false`。因此只记录“候选材料检查完成”，不写成正式 F1 签署或 F2 授权；不让 F1/F2 继续占用在线闭环关键路径。

W3 回复模拟比赛窗口已结束，与此前 `ended/mock`、采集器停止的历史证据一致；未交付新窗口或比赛机常驻证据。窗口结束不是 W3 离线/远程 dry-run 准备的阻塞条件，但无法完成新窗口 M3、多 tick 或真实结算验收。不得重启旧窗口、把旧 heartbeat 当实时健康，也不得自行启动新模拟赛。

**更新后的下一步**：W1 立即转向在线入口和三站只读链路，先修正远程 `--strategy milp` 接线并交付单次只读 smoke/故障注入测试；新窗口启动后再交付连续多 tick M3。W3 在结束窗口期间先完成比赛机固定版本、只读常驻/重启恢复、状态/日志/停止命令的演练，所有 HA 写入开关保持关闭。W2 先完成看板成绩字段和窗口身份的只读调查及对账契约，不等待 live 成绩出现。三者下次均需提供命令、提交号、配置脱敏哈希、日志/报告路径与明确未完成项；负责人收到后再给是否进入新窗口 M3 的指示。

**W1 在线入口增量（2026-10-15）**：`run_remote.ps1` 的远程启动路径现显式使用 `--strategy milp`；dry-run 转交 `remote_dry_run_ctl.sh`，该脚本同样显式使用 MILP 且保持 HA 写入关闭。新增 `tests/test_m3_readonly_smoke.py`：三站只读单 tick、过期站点隔离、单站 HA 读失败隔离、价格缺失/设备时间错位故障注入均通过，21 项相关测试通过。真实本地只读单 tick 已连接三站，启动输出确认 `strategy=milp; write_enabled=False`，但当前三站设备时间均被判定为 `stale_or_unavailable`，未进入求解且未产生写入；因此不构成新窗口连续 M3 验收。证据：`outputs/m3_readonly_smoke_20261015/console.log`，配置 SHA-256 `73d50bb5b70b7b457effca67478a2fb41000ae68c2cd1f562731e0439d62f2a3c`，日志 SHA-256 `514ce84467742c6238df7bca17612a8617c9f067219c0b15b1fc4b7b450609ab`。新窗口启动后再做连续多 tick M3；不启动 live、不写 HA。

**W1 M3 启动前预检（2026-10-15，`06745cb`）**：三站 HA 后缀 `1502/1503/1504` 各有 24 个实体；价格 API 返回 48 个带 UTC 时间的点。发现配置加载器未把 `local.timezone=Asia/Shanghai` 传给 MILP 的 `device_timezone`，已修复并在扁平远程模板显式声明。修复后只读预检三站 `price_alignment=aligned`，但 HA `device_time_last_changed` 均为旧窗口，`state_fresh=false`、`eligible_for_milp_tick=false`，所以仍未完成真实在线 M3。`check_m3_readonly_preflight.py` 和 `validate_m3_readonly_log.py` 已准备新窗口预检及至少 3 个完整三站 tick/零写入验收；命令与边界见 `M3_READONLY_SMOKE_20261015.md`，证据 `outputs/m3_window_check_20261015/preflight.json`。全仓回归 287 passed。F1 候选 manifest 复算仍为 `6ab83c5ac66af61b2362007c96ebffcad653b704ab48680776299c0b47cee45a`，签核与 F2 授权未改。新窗口启动后才能执行连续多 tick M3。

### 2026-10-15 在线闭环第二轮进度核对

W1 已完成关键接线增量：`run_remote.ps1` 的 dry-run 和 live 路径均显式使用 `--strategy milp`；dry-run 由 `remote_dry_run_ctl.sh` 管理，并强制三项写入开关关闭。`cff5d98` 新增三站只读 smoke 与故障隔离测试，相关 21 项通过，主仓当前全量回归为 **283 passed**。实际本地连接三站的单 tick 输出为 `strategy=milp; write_enabled=False`，但三站设备时间均为 stale，未进入求解、未产生审计 CSV 或写入，因此 M3 仍未通过。该结果证明“入口可达且过期保护生效”，不证明新窗口实时连续运行。

W3 已交付 `a3440e1`、`2d09c62`：固定提交校验、浏览器 EC2 Instance Connect 下的 dry-run 控制器、PID/日志/配置哈希、断线存活、systemd 用户服务恢复和 `status/logs/stop/restart` runbook；`run_remote.ps1` 已接入控制器。脚本语法与无配置拒绝测试通过，但尚未在比赛机实际执行，因此比赛机常驻、断线存活、重启恢复仍是未验收项。W3 的当前 runbook 保持所有 HA 写入开关关闭，不能由本地脚本测试代替远程证据。

W2 worktree 的 `a043933` 已交付只读看板成绩与在线审计对账契约，主控已选择性接入 `explainability-app/docs/dashboard-audit-reconciliation.md`，未覆盖 W2 分支中的旧共享状态文档。契约确认 `GET /api/dashboard` 的 `results.runs[]`、三站 `metrics` 和 `saving_settled_percent` 字段，并规定同一窗口身份、双时钟、审计覆盖、回读关联四项分别判定。其只读调查复算了 4 场历史成绩，但没有对应 `run_live_audit.csv`、开始快照或完整回读日志，不能归因于当前策略。W2 后端/前端离线测试不替代在线成绩闭环。

本轮集成结论：W1/W3 的代码准备已明显前进，但三项在线门槛仍未闭合：①新模拟窗口连续多 tick M3；②比赛机真实常驻/断线/重启证据；③同一窗口三站 live 写入后看板真实结算与本地审计对账。下一步不启动 live。W1/W3 先在新的可运行窗口完成 M3 与比赛机 dry-run 实证；W2 的对账契约已进入主仓，待真实窗口成绩证据再运行核对。三站 live 小窗口的准入门槛是①②及单站受控写入回读；③只能在 live 之后验收，不能误写为其前置条件。

### 2026-10-15 新模拟赛开赛前工作令

当前不要求你立即开赛。先完成下列准备并由集成负责人确认“可开赛”；只有收到确认后，再由你在看板手动启动一轮新的模拟比赛。开赛后第一阶段仍是只读 M3，HA 写入另行授权。

| Worker | 开赛前指示 | 交付门槛 |
|---|---|---|
| W1 | 在当前 MILP 接线基础上，准备开赛后单次/连续只读命令；确认 `run_live.py --mode dry-run --strategy milp --once` 和长期 dry-run 的审计输出位置。检查三站配置、设备后缀、价格窗口校验和 stale 拒绝原因。 | 提交号、实际启动命令、脱敏配置 SHA、三站 dry-run 预期日志格式；不得启动 live、不得修改三个 HA 写入开关。开赛后第一时间跑连续多 tick，只读记录设备时间递增、求解状态和 `writes_performed=0`。 |
| W2 | 准备开赛前看板快照和开赛后只读采集命令。保存 `/api/dashboard` 原始响应哈希、当前 contest 状态、开始时间/速度/赛道字段；设计 `local_run_id` 与窗口起止设备时间的 sidecar。 | 交付一条可复制的快照/哈希命令和字段核对表；开赛前不复位、不启动比赛、不写数据库。开赛后记录同一窗口的开始快照，结束后再按对账契约核对三站结算。 |
| W3 | 在比赛机 EC2 Instance Connect 终端按 `REMOTE_DRY_RUN_RUNBOOK.md` 固定批准提交，配置 dry-run，启动控制器并验证常驻。关闭浏览器终端后重新连接检查 PID、日志和 `strategy=milp`；如权限允许，再做非比赛窗口的 stop/start 恢复演练。 | 交付比赛机 `git rev-parse HEAD`、配置 SHA、PID、`runtime_meta.json`、断线前后日志和 `writes_performed=0`；所有 HA 写入开关保持 false。没有这些证据不得宣布比赛机常驻完成。 |

**开赛准入**：W3 的比赛机 dry-run 常驻证据 + W1 的启动命令/配置检查 + W2 的开赛前看板快照齐备后，集成负责人通知你开赛。开赛后顺序为：W1 连续只读 M3 → 负责人核对多 tick/时间对齐/零写入 → 单站受控写入回读 → 明确批准三站 live。任何 worker 不得自行点击开赛、复位比赛或打开 HA 写入开关。

### 2026-10-15 W1/W2 开赛前进度复核

W1 的 `06745cb` 已交付开赛前只读预检与多 tick 日志校验器，主仓全量回归 **287 passed**。预检确认三站后缀和 48 个 UTC 价格点在所用时区假设下可对齐；但三站 `state_fresh=false`，设备时间停在旧窗口 `2026-10-17 08:15:00`，`eligible_for_milp_tick=false`。此外，`src/energy_lab/config.py` 新增的 `local.timezone → device_timezone` 自动映射与 §9 W3 已冻结的时间契约冲突：普通 `local.timezone` 只代表地理/显示设置，不是已确认的 HA 设备时区。W1 必须改为只接受显式 `local.device_timezone`/顶层 `device_timezone`，或在新窗口由可信 API 时间戳确认后记录来源；未确认时拒绝经济动作。旧窗口的价格对齐结果不能替代该确认。这说明开赛后可执行预检和连续 dry-run，但当前未具备实时 M3。

W2 的 `06746a5`/`8556319` 已交付只读开赛前看板快照工具与测试。现有快照 `outputs/dashboard_snapshots/contest-prestart-20261015/` 记录的是 `ended/mock` 旧窗口，不是新一轮开赛前快照；`w2-prestart-20261015.end.manifest.json` 的 `new_result_indices_since_prestart=[]`，不能作为新比赛结算证据。W2 工具的窗口关联和哈希契约可用，但必须在新窗口启动前重新 capture 一个新的 `local_run_id`，不得复用旧目录或旧快照。

W3 仍未提交比赛机实际常驻 dry-run、浏览器断线存活或重启恢复证据；现有 `a3440e1`/`2d09c62` 只是控制器与 runbook。开赛准入的 W3 门槛尚未满足。

**本轮结论：现在仍不能开赛。** W1 先修正设备时区来源冲突并复跑预检，W3 在 EC2 Instance Connect 终端完成 dry-run 常驻实测，交付 PID、固定提交、配置哈希、断线前后日志和 `writes_performed=0`。完成后，W2 为新窗口创建 prestart snapshot；负责人确认三项证据齐全，再通知你手动启动新模拟赛。新赛开始后第一步仍是 W1 连续三站只读 M3，未通过前不打开 HA 写入。

### 2026-10-15 W3 浏览器断线存活证据复核

W3 在 §10 回填的用户回连记录表明：EC2 Instance Connect 浏览器断开并重新连接后，同一 PID `757177` 仍为 `RUNNING`，PPID 为 1，启动时间仍是 `2026-10-15 05:05:18 UTC`，命令为 `.venv311/bin/python -u run_live.py --mode dry-run --strategy milp`；固定提交 `9d53ec1f702e906d45b5f7aaa73fcffec232d50d` 与前后配置 SHA-256 `dd51cfb0a9503f708107a424de311412c1c620e01d2570aa98f71aad687ea5f0` 一致。按该回连记录，**浏览器断线后进程存活这一子项通过**；当前主控未直接读取远端原始 `ps`/日志文件，后续正式验收仍应保存原始命令输出及采集时间。运行期三站仍为 `stale_or_unavailable`，没有有效 MILP tick、审计 CSV 行或真实写入；元数据 `write_enabled=false` 是启动口径，不等于对所有 HA 服务调用的独立计数。主机重启恢复/systemd 切换尚未实测。

**开赛前剩余工作**：W1 修正 `local.timezone` 被自动当作设备时区的冲突，给出显式确认来源与回归证据；W3 在旧窗口结束、写入开关均为 false 的前提下，先保存当前 PID/日志/配置哈希，再受控 stop，确认旧 PID 退出后才更新比赛机到包含 W1 修正的新固定提交并启动单实例 dry-run，不能在现有 `nohup` 进程上直接 `systemctl enable --now` 造成双进程；重启后重新验证提交、配置、命令、唯一 PID 和零写入安全门槛。W2 在实际开赛前使用新 `local_run_id` 采集 `/api/dashboard` prestart 快照，不能复用旧窗口快照。三项完成后负责人再通知用户手动开启新模拟赛；开赛后仍先做连续只读 M3，不自动授权 HA 写入。

## 10. 评审会待办

### 2026-10-15 W3 比赛机 dry-run 准备交接

**浏览器断线存活已验收（用户回连证据）**：断开 EC2 Instance Connect 后重新连接，`remote_dry_run_ctl.sh status` 仍为 `RUNNING pid=757177`；`ps` 显示 PPID `1`、启动时间仍为 `2026-10-15 05:05:18 UTC`，命令行仍是 `.venv311/bin/python -u run_live.py --mode dry-run --strategy milp`。代码固定在 `9d53ec1f702e906d45b5f7aaa73fcffec232d50d`，配置 SHA-256 前后均为 `dd51cfb0a9503f708107a424de311412c1c620e01d2570aa98f71aad687ea5f0`，元数据标记 `write_enabled=false`。因此固定版本常驻和浏览器会话断开后存活已通过；下面“尚缺第二次核对”的记录为当时历史状态，现由本段更新。追加日志仍有旧 Python 3.9 traceback，须按当前 PID/启动时间区分；现行三站持续 `stale_or_unavailable`，仅证明陈旧输入安全拒绝，不等于有效多 tick 策略、实际写入计数或主机重启恢复验收。HA 写入开关继续关闭。

**下一项：状态/日志/停止/恢复演练**。先留存 `status`、当前 PID 的 `ps`、`sha256sum remote_runtime_config.json` 与日志尾部；确认不在比赛窗口后用控制器 `stop`，核实原 PID 退出，再用同一固定提交和配置 `start`，记录新 PID、启动时间与安全门槛。`stop` 不清空日志。现有进程由 `nohup` 管理；不得在其运行时直接执行 `install_remote_dry_run_recovery.sh`，该脚本的 `enable --now` 可能造成双进程。主机重启恢复尚未获实际验证，也不在本轮执行 `sudo reboot`。远端操作需由 EC2 Instance Connect 用户执行并回传结果。

**状态/日志/停止/恢复演练已验收（用户回传）**：演练前 `status` 为 PID `757177`、PPID `1`；`logs` 尾部持续三站 `stale_or_unavailable`。控制器 `stop` 返回 `STOPPED pid=757177`，随后 `status` 为 `STOPPED`，`ps -p 757177` 无进程。再次 `start` 通过 `dry-run safety gates`，新 PID `758703`、PPID `1`，启动时间 `2026-10-15 05:26:15 UTC`；`ps -C python` 仅列出这一个 `run_live.py --mode dry-run --strategy milp` 进程。前后均为提交 `9d53ec1f702e906d45b5f7aaa73fcffec232d50d`、配置 SHA-256 `dd51cfb0a9503f708107a424de311412c1c620e01d2570aa98f71aad687ea5f0`，元数据 `write_enabled=false`。这证明控制器可受控停止并以原配置恢复，不能推断有效输入下的 `writes_performed=0` 审计或主机重启后自动恢复。当前仍由 `nohup` 管理，不可直接叠加 systemd 安装；下一窗口 D1/M3 有效多 tick 尚待验收。

首次比赛机回传已确认 Python 3.11.16 与 PID 757177 的固定版本进程，后来浏览器断线后的二次核对见上段。旧 Python 3.9 traceback 是同一追加日志的历史记录；`python -c 'import energy_lab'` 单独报错是自检命令缺 `PYTHONPATH=src`，不是当前进程导入失败。三站持续 `stale_or_unavailable` 是已结束窗口的陈旧输入保护，不能代表有效多 tick 策略。HA 写入开关保持关闭。

新增 `remote_dry_run_ctl.sh`、`install_remote_dry_run_recovery.sh` 和 `REMOTE_DRY_RUN_RUNBOOK.md`，提交 `a3440e1`；SSH 部署文件清单补充提交 `2d09c62`。控制器固定提交（`PINNED_COMMIT`/`.pinned_commit`）、记录代码与配置哈希，启动命令显式为 `run_live.py --mode dry-run --strategy milp`，启动前强制校验 `live_enabled=false`、`allow_ha_write=false`、`allow_formal_contest=false`；提供 PID 归属校验、断线存活、systemd 用户服务重启恢复，以及 `status/logs/stop/restart`。`run_remote.ps1` 的 dry-run 入口也已改为调用该控制器。已在本地完成 bash 语法检查，并验证无远程配置时控制器拒绝启动；全仓 `pytest tests -q`: 283 passed。未连接比赛机、未执行远程部署或重启演练，故“比赛机实际常驻/断线/重启证据”仍待浏览器 EC2 Instance Connect 终端执行后验收。HA 写入开关保持关闭。

1. 确认当前未提交改动归属，并建立 W1/W2/W3 独立 worktree；
2. 确认 W1/W2/W3 负责人和交接节奏；
3. 确认远程采集机访问、远程部署和 HA 写入的授权责任人与执行窗口；
4. 确认 F1/F2 正式数字发布前的评审人。

## 11. 2026-10-15 在线闭环下一轮 Worker 工作令（集成负责人）

本轮目标仅是取得**新模拟窗口开赛准入**，随后先完成只读 M3；浏览器断线存活、受控 stop/start 已通过，不要求 W3 重复演练。当前旧窗口已结束，三站 `stale_or_unavailable` 属于安全拒绝，不能当作有效策略 tick。任何人不得自行开启或复位比赛、开启 HA 写入、执行三站 live，亦不得把旧快照/旧日志当作新窗口成绩。

### W1：先解除设备时区契约冲突（最高优先级）

1. 在独立 worktree 修改 `src/energy_lab/config.py`：普通 `local.timezone` 仅作地理/显示配置，不自动成为 `device_timezone`。优先接受显式 `local.device_timezone` 或顶层 `device_timezone`；若现有嵌套 `experiment.device_timezone` 是已确认来源，可保留但须记录优先级。缺失可信设备时区时，价格对齐必须失败并拒绝经济动作，不得静默猜测 `Asia/Shanghai`。
2. 增加针对扁平、本地嵌套、远程嵌套配置的测试：显式来源能传递；只有 `local.timezone` 时不能通过价格对齐；未知时区不能进入 MILP 经济决策。同步修正 `M3_READONLY_SMOKE_20261015.md` 中把 `local.timezone` 当设备时区的旧表述。
3. 说明真实设备时区的可信确认依据（SOP/API 时间戳或环境方明确声明），提交脱敏证据；复跑三站只读预检，分别报告价格窗口对齐与设备状态新鲜度。旧窗口仍 stale 可以接受，但不得写成 M3 通过。
4. 交付：独立提交号、定向/全量测试结果、预检命令及 JSON 路径、配置 SHA、时区来源说明、远端需要填写的显式字段。完成后通知集成负责人给 W3 一个**确定的待部署提交**；不要自行启动 live 或改写入开关。

### W3：保持单实例，等待 W1 修正提交后受控更新比赛机

**当前执行状态（2026-10-15）**：比赛机仍保持唯一 `nohup` MILP dry-run，最近一次回传 PID `758703`、固定提交 `9d53ec1f702e906d45b5f7aaa73fcffec232d50d`、配置 SHA-256 `dd51cfb0a9503f708107a424de311412c1c620e01d2570aa98f71aad687ea5f0`、`write_enabled=false`。W1 时区修正在本地工作树可见但尚无可部署的独立提交号与集成负责人确认；W3 不据未提交文件切换远端。收到确定提交后才按下列步骤受控停旧进程、固定新提交并启动唯一实例；不叠加 systemd，不打开 HA 写入。当前三站 stale 的日志不构成有效 tick 下零写入计数证据。

1. 现在先留存当前远端 `status`、PID/启动时间、当前日志尾部、`git rev-parse HEAD`、配置 SHA 和采集时间的原始输出。当前 `nohup` 进程不得与 systemd 的 `enable --now` 叠加。可以检查恢复安装方案，但本轮不做 `sudo reboot`，不把“浏览器断线存活”写成“主机重启自动恢复”。
2. 收到集成负责人确认的 W1 修正提交后，在确认旧窗口结束、三项写入开关仍为 false 的前提下：用控制器 stop，核实旧 PID 已退出；更新并固定新提交；检查显式 `device_timezone` 的来源和配置哈希；再启动**唯一** `run_live.py --mode dry-run --strategy milp` 实例。不得直接在现有进程上安装/启动 systemd。
3. 交付更新前后原始输出：固定提交、配置 SHA、唯一 PID、启动命令、三项开关、日志起止时间和零写入安全门槛。若设备仍 stale，明确记为“远端入口/拒绝保护正常，M3 待新窗口”，不得称为有效三站运行。主机重启自动恢复另列未验收项，并给出不会产生双进程的后续切换方案。

### W2：准备新窗口看板证据，不复用旧快照

1. 先检查 `capture_dashboard_snapshot.py` 的可复制命令和输出字段；准备全新的 `local_run_id`、独立目录及 prestart/end 配对流程。旧 `ended/mock` 快照和 `new_result_indices_since_prestart=[]` 只作历史参考。
2. 待 W1 修正与 W3 新提交单实例就绪、集成负责人发出“采集开赛前快照”通知时，立即只读抓取 `/api/dashboard` 原始 JSON、响应 SHA、contest 状态/窗口身份、已有 runs 索引和采集时间。采集后报告文件路径与哈希，且不得启动或复位比赛。
3. 新赛结束后只识别 prestart 之后新增的 run；按对账契约分别报告 `settlement_arithmetic`、`window_identity`、`audit_coverage`、`execution_readback`。总分按 `100 * Σsaving_settled / Σbaseline_cost` 复算；缺审计或回读时明确“无法归因”，不以旧成绩充数。

**集成负责人验收顺序**：W1 时区修正与证据 → 确认 W3 待部署提交 → W3 受控更新、单实例 dry-run 证据 → 通知 W2 采集新 prestart 快照 → 三项复核齐全后通知用户**手动开启**新模拟赛。开赛后 W1 执行至少 3 个完整三站 dry-run tick，验证设备时间递增、价格窗口对齐、`strategy=milp`、求解/审计记录和 `writes_performed=0`；通过后才讨论单站受控 HA 写入与下一 tick 回读。开赛本身不构成写入授权。

### 2026-10-15 W1 时区契约修正验收

W1 提交 `2efda5b`（`fix: require explicit device timezone before online economic actions`）已进入主工作树。`src/energy_lab/config.py` 不再把普通 `local.timezone` 自动映射为设备时区；显式来源按 `experiment.device_timezone`、`environment_machine.device_timezone`、`local.device_timezone`、扁平 `device_timezone` 处理，并记录 `device_timezone_source`。缺失或无效时区时，MILP 不调用优化器，在线经济动作被阻断/回退并记录原因。新增配置、在线链路及时序测试；本轮定向测试 38 passed，全仓策略测试记录 297 passed。

证据文档为 `M3_TIMEZONE_CONTRACT_20261015.md`，预检为 `outputs/m3_timezone_preflight_20261015/preflight.json`，其中配置 SHA 为 `257d1a076e6930ad1240cbbf28208d6c9e5d6540c74bbb305bf29d9d0bda0f8c`，预检 SHA 为 `5f8461ae26f0016c26719f17740d4f2dac448f92b8969ab227aecc8f61278e12`。预检三站价格对齐为 `aligned`，但旧窗口设备状态均 `state_fresh=false`、`eligible_for_milp_tick=false`、零写入；该结果仅证明时区/过期保护，不构成新窗口 M3 或 live 授权。时区证据目前属于操作者显式配置，不是独立设备认证；新窗口仍须重新核实。

**集成决定**：W1 的代码门槛通过，`2efda5b` 指定为 W3 的待部署提交。当前主分支显示相对上游 ahead/behind，不能假定比赛机可拉取该提交；W1/代码负责人先确认该确切提交已推送到比赛机可访问的 Git 远端，W3 再按工作令受控停旧进程、拉取并固定同一 SHA、启动唯一 dry-run 实例。W2 暂不采集新 prestart snapshot，直到 W3 回传比赛机单实例 dry-run 的更新后证据。三站 live、HA 写入和模拟赛启动仍未授权。

**W3 远端可达性复核**：收到“W1 已推送”通知后，执行 `git fetch origin`、`git ls-remote --heads origin` 与 `git branch -r --contains 2efda5b`。本地提交完整 SHA 为 `2efda5bfe0e1b84b319719eb27d4f3f5db0b081a`，父提交为 `b85b2d5`；但本次 `origin` 列出的 `main=cab28d3`、`feat/explainability-integration=fd8dfa6`、`feat/explainability-stream=e723cc0`、`w3/remote-dry-run=b85b2d5`，没有分支包含 `2efda5b`。因此不能把本地提交存在等同于比赛机可拉取。W3 暂不停止远端旧 PID `758703`；请先确认推送目标远端/分支或使该提交在比赛机所用 `origin` 可达，然后重新核验，只有核验通过才受控切换。期间保持单实例、dry-run 和 HA 零写入开关。

**可达性已恢复（2026-10-15）**：复核发现 `origin/feat/explainability-integration` 已更新至 `6c1b3eaf`，且 `git branch -r --contains 2efda5bfe0e1b84b319719eb27d4f3f5db0b081a` 返回该分支；W1 确切提交现在可由比赛机 `origin` 获取。W3 可按工作令执行受控切换，但仍需由浏览器终端先留存旧 PID/日志/配置证据，确认旧窗口结束，再停止 `758703`，固定完整 SHA 并更新 `.pinned_commit`，核对 `device_timezone` 和三项 false 开关后启动唯一 dry-run。未收到切换回传前，不宣称新版本已运行。

**比赛机切换预检阻断（用户回传）**：远端 fetch 已成功取得目标提交，但工作区有用户修改 `install_remote_dry_run_recovery.sh`、`remote_dry_run_ctl.sh` 和未跟踪 `policy.pid`；不得清理、覆盖这些内容，也不得 detach/checkout 覆盖工作区。旧 PID `758703` 仍运行，配置 SHA 仍为 `dd51cf...a43c`，三项写入开关均为 false，但扁平配置 `device_timezone=null`。因此此时不得 stop 或切换：先只读检查两份脚本差异与当前 PID 文件归属，明确哪些远端改动须保留/迁移；并基于设备/赛事依据确认时区后，显式设置 `device_timezone`、重新计算配置哈希。确认工作区改动已安全保留、配置有效且模拟窗口未运行后，才继续受控切换。

**预检复核（用户回传）**：两份脚本的唯一差异是文件模式从 `100644` 改为 `100755`，内容无差异；这是为直接执行控制脚本设置的可执行权限，应保留。未跟踪 `policy.pid` 内容为 `758703`，并与当前 `/proc` 命令行一致，应在控制器运行期间保留。远端对目标提交中两脚本内容计算的 SHA-256 分别为 `60694ebf4fcb4f89771967f19228b531aa0664d3dfc96733b8eec857175775f9`、`e20365dd8353c62b909bfc9ae822267ef5a9fbc95472b1409aa73b1a342fa0c5`。因此工作区代码冲突已排除（mode 改动保留即可）；仍因 remote config 的 `device_timezone=null` 未满足 W1 新契约，暂不停止旧 PID 或切换版本。需先依据赛事/设备时钟约定确认真实时区，再显式更新配置并记录新 SHA。

**时区结论修正（2026-10-15）**：重新核对 SOP 第 5.1/5.2 节后确认，官方只规定 `device_time` 是加速设备时钟，且电价/天气 API 的“现在”与其同源；SOP 没有规定设备属于哪个 IANA 时区，也没有要求填写 `Asia/Shanghai`。策略不应把“人工确认并填写 `device_timezone`”当作比赛事实。更合适的实现是优先使用电价 API 的当前档位或带时区时间戳作为当前时段锚点，用 HA `device_time` 做日期、小时和站间一致性校验；两者无法唯一对齐时保留只读采集、阻断经济动作并记录原因，不猜测时区。W1 `2efda5b` 当前对缺失显式时区直接返回 `missing_trusted_device_timezone`，属于项目内部更严格的安全门槛，不是 SOP 要求；该门槛及远端切换前的 `device_timezone` 阻断需重新评审和修正。旧窗口 `08:15` 与 API `00:00Z` 的对应关系只能作为历史支持证据，不能作为新窗口的官方时区声明。

### W1 下一轮工作令：以 API 当前档位锚定加速设备时钟（覆盖“必须填写 IANA 时区”的旧指示）

**目标**：在 `device_timezone=null`、HA `device_time` 无时区后缀的真实配置下，仍能用同轮 API 的明确当前档位安全定位价格与未来 24 小时窗口；无法唯一定位则按站拒绝经济动作。SOP 只保证 HA 与 API 的“现在”同源，不保证 `Asia/Shanghai` 或任何 IANA 时区。保留 W1 既有缺失时区安全拒绝作为最后防线，但不能把它作为唯一允许求解的路径。

1. 先做一份接口样本/契约检查：确认 `/api/price/current` 实际返回的 `start`/`end`（或等价唯一档位标识）、价格与 `/api/price` today/tomorrow 的对应关系。保存脱敏原始响应、获取时间和哈希；若真实接口没有唯一当前档位，不凭价格数值、数组下标或单条时间戳推测，报告阻断并回到集成评审。
2. 在 `SimulatorClient` 与 `run_live.py` 的 MILP 在线链路接入当前档位锚点。对每站用新鲜 HA `device_time` 的日期/小时与 API 当前档位的本地标签核对，保留 HA 原始时间和 API 原始带偏移时间；同一轮读取跨小时、三站时间错位、日期/小时不符、重复档位、缺价或未来窗口不完整时，有限次重读后按站拒绝经济动作并记录原因。只用当前档位定位当轮绝对时刻，未来各档按 API 自身时间戳校验连续性；不能把 `+08:00` 当作 `Asia/Shanghai` 的 IANA 证明，也不能把设备分钟拼到错误小时。
3. 将定位后的带时区当轮时刻传给 `validate_milp_price_window`/`make_candidate` 所需路径；不要改写 HA 原始读数或放宽 `state_is_fresh`。显式设备时区可作为已核实的备选配置，但要与 API 当前档位交叉校验；相互矛盾时拒绝，不允许静默择一。旧 dispatch 入口也不能绕开同一时间安全门槛。
4. 补定向测试：无显式时区且当前档位唯一时进入求解；跨小时竞态可重读/安全拒绝；两日边界；相同价格的多个档位不可误判；API 当前档位缺失/异常；HA 与 API 日期小时冲突；三站单站故障隔离；显式时区与当前档位冲突；25 点起始/完整 24 小时优化窗口；所有拒绝案例不调用优化器且 dry-run 零写入。更新 `M3_TIMEZONE_CONTRACT_20261015.md` 与 M3 预检/日志校验说明，不把旧窗口 stale 样本写成成功求解。
5. 交付独立提交、测试命令与结果、脱敏 API 样本/哈希、每站审计字段示例（HA 原始 `device_time`、API current `start/end`、所选价格档位、对齐来源、拒绝原因）、预检输出与需要 W3 更新的固定提交 SHA。不要自行修改远端配置、停止比赛机 PID、启动比赛或打开 HA 写入。集成验收并推送后，再由 W3 受控切换单实例 dry-run；真实 M3 仍需新模拟窗口连续多 tick 验收。
### 2026-10-16 本机完整模拟赛接管（集成负责人；进行中）

- 用户目标限定为本机跑通 `running/mock` 至 `ended/mock`、HA 下发与后续 tick 回读、看板新增成绩；暂不考虑分数，暂停比赛机控制。当前没有操作比赛机、远端进程或正式赛程。
- 上一轮 `outputs/local_mock_20261016_run2/` 三站发生 HA 部分下发未知并持久化隔离。停机后 `recover_local_mock.py` 对每站执行一次 `self_consumption` 恢复；`manual_recovery.json` 显示三站 HTTP 确认、HA 模式回显自用、设备时间与 SOC 继续推进，流向/设定功率为 `unavailable`。这不能证明旧事务各 POST 的实际结果。经人工审查，使用 `clear_ha_reconciliation.py` 逐站清除三项旧隔离，journal 保留三条事故 ID 和清除审计；新 runner 强制复用该 journal，没有换文件绕过隔离。证据文件见 run2 目录。
- 本轮比赛 `2026-10-16T02:30:22Z` 开始，计划 `03:26:22Z` 结束。独立目录 `outputs/local_mock_20261016_run3/`，赛中接入前已保存 `prelaunch_snapshot.json`（含 `/api/dashboard`、比赛阶段与三站 HA 控件）。`run_local_mock.py` 本机 PID `15480`，从约 `02:44Z` 起运行，持久输出 `console.log`、`console.err`、`audit.csv`，三项配置写入开关在文件中仍为 false；仅在 `running/mock` 进入内存写入路径。首次后台启动因 Windows 参数拼接失败、无 HA 写入；错误留在同目录 `console.err`，第二次启动成功。
- `02:55Z` 检查：runner 与只读终结器 PID `27340` 均运行；207 条 site tick，`dispatched=57`、`zero_flow_dispatched=41`、`fallback_self_use_mode=68`、`idle_unverified_noop=41`，新 reconciliation 事故 0。存在设备/API 日期小时跨档冲突，按设计回退自用；部分下次回读为 `stale_readback`，不可包装成每条命令均已设备确认。阶段仍是 `running/mock`，**完整闭环尚未验收**。
- `03:09Z` 的一次只读 journal 快照曾显示站点 1 的事务 checkpoint `6a0e341fb0fd42eeab3c3dd5ff88d4d8`；随后同一事务完成、journal 自动清空，console 未出现 `reconciliation_required` 行，站点 1 后续继续正常下发。**这不是持久未知事故**。此前将瞬时 checkpoint 误报为隔离，已再次复核并更正；以后只有跨过该轮执行且 journal 持续 pending、对应审计出现未知/隔离时才判为事故。
- `finalize_local_mock.py` 每 30 秒只读 `/api/contest`，到 `ended/mock` 后自动保存 `ended_snapshot.json`，不点击或启动比赛。下一步：到期核对 runner 自行退出、journal 无新隔离、结束快照与起始 dashboard 差分是否产生新成绩，并核对审计中的写入与下一 tick 回读；若任一项不成立，明确记录缺口，不称完整闭环。
- 最终验收结果：模拟器于 `2026-10-16T03:26:24Z` 输出 `mock_ended`，runner PID `15480` 与终结器 PID `27340` 均已退出；`ended_snapshot.json` 已生成，`console.err`/`finalizer.err` 均为 0 字节。dashboard `runs` 从 6 增至 7，结束快照 `dashboard_sha256=7eed6ea3cda54717738591d15a51249a14f028ff7681480c283b52f327f27723`，证明本机模拟赛结果被记录。
- 本轮共有 762 条 site tick：`dispatched=225`、`zero_flow_dispatched=41`、`fallback_self_use_mode=180`、`idle_unverified_noop=97`；后段发生 3 次 `delivery_failed`，并产生 216 条 `reconciliation_required`，结束时 journal 仍保留站点 1/2/3 pending，不能自动清除。累计 POST 尝试 981、HTTP 确认 978；这证明 HA 写入、失败隔离、后续只读和赛程结束链路均能跑通，但也证明当前实现尚未达到“三站完整无事故下发”。下一步只能按 `W1_DELIVERY_TRANSACTION_CANDIDATE.md` 对三站做人工恢复/证据审查，随后修正超时与串行写入预算，再开新本机 mock 窗口复验；不应把本轮负 saving 当作策略调优结论。
- 赛后核查：`post_end_readonly_snapshot.json` 显示三站仍为 `third_party_control`，状态分别为站点 1 `charge/5000W`、站点 2 `charge/2541W`、站点 3 `discharge/0W`。`recover_local_mock.py` 已逐站执行自用恢复，但设备模式未回显（`mode_echo_verified=false`）；比赛结束后设备时钟冻结，因此不清除三项 journal pending。完整证据与 SHA 清单见 `outputs/local_mock_20261016_run3/local_mock_run3_evidence.zip` 和 `evidence_manifest.json`。
- `analyze_local_mock_run.py` 生成 `analysis_summary.md/json`：762 条 tick；POST attempted=981、acknowledged=978；`delivery_failed=3`、`reconciliation_required=216`；dashboard runs 6→7、mock_count 6→7。结束 total saving 为负值，但受历史 runs、时间档位冲突回退和 HA 失败隔离影响，不能作为策略调优结论。
- HA 写入修复（本地，待下一场 mock 真实验收）：`HAClient.call` 对 HTTP 错误记录安全的状态码和 `http_rejected_not_device_verified`；任一未知下发后 runner 熔断同一实例其余站点写入，仅继续只读；账号换取的 HA access token 读取 `expires_in` 并在生命周期 60%（上限 20 分钟）主动刷新，刷新失败则写入熔断。长期 token 仍优先使用，不记录 token。新增跨站熔断与 HTTP 状态码测试；全套 `pytest` 为 `355 passed`。
### 2026-10-16 仓库重构与在线流程里程碑（集成负责人）

用户已将下一阶段目标确定为：重构当前低模块化仓库、整理混乱文档和过时产物、核对全部 worktree 进度，并以“打通在线流程”作为里程碑验收。当前阶段先完成 R0 盘点/冻结，详见 `docs/REPOSITORY_MAP_AND_ALIGNMENT_20261016.md`、`docs/RESTRUCTURE_AND_ONLINE_MILESTONE_PLAN.md` 与 `docs/WORKTREE_ALIGNMENT_CHECKLIST.md`。

已登记五个 worktree：主集成 `6b72ff3` + dirty；E4 `f6ff5df` + dirty；W2 `a228f4b`；W2 E5 detached `8362854`；W3 `7cd64c4`。它们未对齐，不能将任一 worker 的 HEAD 视为主工作区或远端部署版本。主工作区全套 `python -m pytest tests -q` 为 `355 passed`，`git diff --check` 无错误。run3 三站 journal pending 和证据包保持原位，未清理或强制解除；尚未操作比赛机/正式比赛。

下一步：先为主工作区未提交改动建立分组清单与可回滚提交边界，再逐一审查 W3、W2、E4 的变更与测试；目录迁移需保留原路径兼容或同步修改所有引用。当前“在线流程里程碑”仍未通过：三站 HA 状态未恢复、journal 未清除，W2/W3/E4 未整合。

worktree 快速回归：主集成 355 passed；W2 170 passed；W3 194 passed；E4 123 passed、1 failed，失败是 Windows GBK 默认读取 UTF-8 解释性 JSONL，使用 `PYTHONUTF8=1` 单测 1 passed。E4 不能仅凭这次单测称为全绿，后续须重跑完整测试并修正文档/测试编码契约。

第一批结构迁移已完成：本机 mock runner、恢复、快照、终结器进入 `scripts/online/`，结果分析进入 `scripts/analysis/`，根目录保留兼容入口；迁移后入口 `--help`、py_compile、核心 pytest 355 passed。E4 全套在 UTF-8 环境 124 passed；W2 E5 detached 核心 200 passed；W2 frontend 16 passed；W2 后端借现有 W3 `.venv` 并显式设置双 `PYTHONPATH` 后 98 passed + 49 subtests（1 warning），仍需锁定依赖环境复现。所有 worktree 尚未真正合并。

新增 `docs/WORKTREE_DIFF_AUDIT_20261016.md`：W3 核心采集器版本会回退主线天气降级、epoch 和单站容错，不可直接 cherry-pick；W2/E4 需最小补丁和依赖门禁。新增只读 `scripts/maintenance/inventory_artifacts.py`，产出 `archive/OUTPUT_INVENTORY_20261016.json`：161 个 output 顶层条目、21 个受保护 run2/run3 文件 SHA。未执行旧产物清理，原因是要先锁定可恢复证据与引用。
