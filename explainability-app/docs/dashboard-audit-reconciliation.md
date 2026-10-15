# 看板成绩与在线审计对账契约 v1

状态：W2 只读调查与契约交付，待集成；暂停离线页面扩展。本轮没有启动或复位比赛、没有下发 HA 指令、没有导入成绩或修改应用数据库。

## 实际字段与来源

2026-10-15 使用本地私有配置登录环境看板后，只读检查页面引用的 `app.js`，确认页面统一从 `GET /api/dashboard` 获取 `contest`、`results`、`sites`、`total`。认证 POST 仅用于换取会话 token。设备遥测仍只能经 HA 获取，不能把 dashboard telemetry 用作策略输入。

| JSON 路径 | 用途与限制 |
|---|---|
| `contest.mode / track / start_at / end_at / now / days / date_range / speed` | 当前场次状态；带 Z 的调度墙钟不能当设备时间 |
| `results.runs[]` | 已结束场次；不能默认最后一条、best 或最高分就是本次策略运行 |
| `runs[i].ended_at / track / days / date_range / data_seed / speed` | 实际存在的历史场次元数据；未观察到稳定 run_id 或历史 start_at |
| `runs[i].sites[j].site_id / metrics` | 同场三站结算，必须显式站号 1、2、3，不按数组位置猜测 |
| `runs[i].total` | 同场总成绩；历史 total 字段比当前顶层 total 少，不能跨场补齐 |
| `sites[j].comparison / total`（顶层） | 当前快照指标，不能与任意历史场次混用 |
| `results.verify` | 保留 verified、key_fingerprint、signing_enabled 原值；不据此宣称独立签名验真 |

每站 metrics 实际包括 `baseline_cost`、`baseline_revenue`、`device_revenue`、`saving`、`saving_percent`、`relative_percent`、`baseline_settled`、`device_settled`、`saving_settled`、`saving_settled_percent`、设备/基线购电成本、售电收益、并网净成本、`valid`、`shortage_count`。

## 成绩算术契约

同一条 `results.runs[i]` 的站号必须恰好为 1、2、3 且无重复。按每站 K=`metrics.baseline_cost`、ΔR=`metrics.saving_settled`：

```text
单站 ΔR = device_settled - baseline_settled
单站分数 = 100 × saving_settled / baseline_cost
总分 = 100 × Σ saving_settled / Σ baseline_cost
```

分母是 `baseline_cost`，不是 `baseline_settled`；百分比字段已乘 100。总分不是三站百分比的算术平均。`saving_percent` 是已实现收益参考项，不替代排名结算指标；负分原样保留。内部寿命代理损耗和离线期末估值不得再扣入看板成绩。

逐项比较单站差额、单站百分比、三站 K 之和与历史 total.baseline_cost、三站差额之和与 total.saving_settled、总分与 total.saving_settled_percent。使用原始 JSON 数值而不是页面四舍五入值。建议金额绝对容差 1e-8 EUR、百分比绝对容差 1e-8 个百分点；放宽需记录理由。缺失、非有限值、ΣK≤0 均返回 unknown/invalid，不补 0；`valid` 和 `shortage_count` 独立检查，算术相符不代表比赛有效。

## 同一窗口与双时钟契约

每轮受控运行应保留独立 sidecar manifest（不要求修改现有在线 CSV）：

| 字段 | 要求 |
|---|---|
| `local_run_id` | 本地唯一身份，不冒充模拟器官方 ID |
| `dashboard_source / captured_at_utc / dashboard_sha256` | 脱敏快照来源、采集墙钟与原始响应哈希；不存 token |
| `selected_result` | ended_at、track、days、date_range、data_seed、speed 及所选整条 JSON 的规范化哈希；不只存数组索引 |
| `start_snapshot_sha256 / end_snapshot_sha256` | 同场开始/结束状态证据；缺失则场次关联为 unknown |
| `device_window_start / device_window_end / boundary_rule` | 已证实设备时钟的半开区间 [start,end)，完整比赛为 168 设备小时；不能仅由 date_range 拼接 |
| `device_time_basis / site_suffix_map` | 实际时区来源和站号到 HA 后缀映射；未知则保留原始坐标，不从本机时区推断 |
| `audit_sources / polling_contract` | CSV、控制台异常日志哈希、代码提交、脱敏配置哈希、策略版本、真实轮询与缺口规则 |

同数据窗口可重复运行，因此 track+date_range+data_seed 不足以把日志归入某一场；还需开始/结束快照、采集区间与本地运行 manifest。若结果 ended_at 与调度 end_at 有延迟，保存两者及匹配依据，不强制字符串相同。本轮当前场次两者相差 3 秒。

## 在线审计契约

当前主仓 `run_live.py --audit-csv` 追加 CSV。实际代码字段包括 site、device_time、received_at、status、solver_status、planned_flow、planned_power_w、command_mode、command_flow、target_w、readback_verdict、realized_soc、optimizer_attributed、writes_performed、reason、observed_soc、observed_pv、observed_load、write_enabled、attribution、previous_readback_verdict、guard_action、guard_reason、soc_divergence_percent；版本可能增加 accepted_incumbent、mip_gap。交付时记录实际 CSV 表头和源码提交，不要求旧日志伪造新增字段。

- `site+device_time` 只用于排序与查重，不自动视作唯一指令 ID。真实时间轮询可能多次读取同一模拟 tick；重复记录不能静默去重，冲突要列出。
- `received_at` 是采集墙钟，`device_time` 是加速时钟。禁止直接相减判新鲜度或用墙钟判断 168 小时覆盖。
- `writes_performed` 是现有执行层的逻辑写入标记，不等于 HA 服务调用次数或成功回读。dry-run、noop、拒绝、回退分别统计，不计成已验证策略执行。
- `previous_readback_verdict` 对应前一条命令，不能挂到当前命令。需按同站顺序及真实提交/回读标识关联，无法消歧为 unknown。
- 缺失 optimizer_attributed、attribution、回读字段时保持未知，不从 solver_status 推断归因、实际执行或成功回读。
- 重启/切换须定位，不能把本地与远程两个控制器重叠写入伪装成一条连续运行。
- stale、candidate_error、station_error 分支可能只进入控制台结果、未写 audit CSV。必须同时保存控制台异常日志，否则 CSV 缺行不能解释为正常。列出每站首尾 tick、最大缺口、逆序、重复与冲突、异常区间及末条未确认命令。

## 输出门槛与本轮结果

正式对账报告独立给出 `settlement_arithmetic`、`window_identity`、`audit_coverage`、`execution_readback` 四项 pass/fail/unknown，不能用一个 verified 掩盖缺失证据。三站算术通过只证明结算内部一致；日志覆盖和回读关联完成后才可描述在线审计链路闭合，仍不能把全部收益因果归于某次 MILP 命令。模拟赛不能称正式赛排名验收。

本轮读取时 contest 为 mock/ended、days=7、speed=180，results.runs 共 4 条，均包含站号 1/2/3。逐场复算 `100×ΣΔR/ΣK` 与看板总分一致：

| ended_at（UTC 墙钟） | 数据日期窗口 | 看板及复算总分（百分点） |
|---|---|---:|
| 2026-10-14T06:29:55Z | 2026-10-11 ~ 2026-10-18 | 0 |
| 2026-10-14T08:41:55Z | 2026-10-13 ~ 2026-10-20 | -0.5057177169262999 |
| 2026-10-14T12:32:50Z | 2026-10-14 ~ 2026-10-21 | 0 |
| 2026-10-14T18:17:05Z | 2026-10-10 ~ 2026-10-17 | 0.03145965024241769 |

这仅是现场读取时的算术调查。本轮未将含遥测的原始响应提交 Git，也未签发带原始快照哈希的正式验收报告。主仓 outputs 中未找到 `run_live_audit.csv`，远程主机日志未读取；尚无同场开始快照、明确设备时钟边界和完整控制台日志，因此 `window_identity`、`audit_coverage`、`execution_readback` 均为 unknown。不能把这四场成绩归因于当前代码或宣称完成在线闭环。下一步由集成负责人收齐同场 manifest、结束成绩原始响应及三站实际审计日志后复验。
