# W2 E4/E5 接续核验（2026-10-14）

状态：E4 真实优化器五类截止约束验收完成，三站历史 replay 已导入独立 E5 核算库；待集成负责人验收。停止扩展页面。

## E4-1 基线门槛

核查期间集成负责人提交了 `82ba72c`，将 E4-1 合入共享基线；W2 仅接入此集成提交，不整枝合并 `feat/energy-floor`。

已提交共享优化器 SHA-256 为 `603de45ca089b13665abd91fe2f58aa12e576909dfcba2ffb481cff2aedd13a4`；W2 接入后锁定 `shared-milp-603de45c-cf1` 与同一哈希。

已同步提供器、证据版本、README、integration、smoke。`scripts/verify_plan_floor.py` 调用锁哈希的真实共享优化器，五类验收依次得到 `feasible`、`infeasible`、`timeout`、`unsupported`、`rejected`。超时注入仅将实际 HiGHS 约束求解预算设为 1e-9 秒，原求解器返回 status 1；缺失能力案例检查不含 floor 参数的真实旧优化器签名。输入为人工快照，非 HA 实测。完整记录：`artifacts/e4-floor-acceptance/acceptance.json`。

## E5 数据预检

已找到真实家庭历史数据驱动的离线 replay，而非 mock。候选目录为主仓 `outputs/contest_hold_pair_zero_v2_20261013/`，选择站点 1、2、3 的 `irradiance` 臂。它们是练习站点，不是正式比赛三台设备；演示只能标为历史数据离线回放。

| 文件 | 总行数 / 选定臂行数 | SHA-256 |
|---|---:|---|
| site_1_trace.csv | 504 / 168 | 36e145d08a1d634952e815719d293333acdd2d30b84dcd7a993d1d46d805b1b3 |
| site_2_trace.csv | 504 / 168 | 356aee1b77837a0e3e4acd5e5dfd43e3d4035a3b7e12e6ff0391a5a3ac814f21 |
| site_3_trace.csv | 504 / 168 | 41016c78f5ab7adb1bed1983ed570c81b53a086312f4b598e25d292875a22f3b |

三站时间窗均为原始设备小时 `2026-09-14 14:00:00` 至 `2026-09-21 13:00:00`；每小时一步，选定臂 fallback 均为 0。来源 README 明确家庭历史数据，manifest 明确天气使用未来观测短波代理，不是归档天气 API 预报。目录已有 audit.json 记录五站 2520 行审计通过、最大能量残差约 4.97e-15 kWh；本轮未重新运行该全量物理审计。

实际调用 W2 `build_pv_accounting.py` 对站点 1 预检，返回 `ValueError: trace time requires an explicit timezone`，在创建输出文件之前拒绝。CSV 与来源 README 没有明确时区，因此没有补猜 UTC 或主机时区。下一步应由产品文件域定义离线设备本地小时的显式时间约定，或取得可信时区映射；不得将在线 API 的 UTC 约定直接套用到训练集。

三个候选的选定臂均无负售电价行，适合核算链路演示，不能证明负价避免外送能力；后续演示需另选真实负价时间窗。也不能用这些 trace 宣称新 E4-1 模型、现场回读或官方收益已验收。

后续实现显式 `--time-basis dataset_local_hour` 离线选项，只按原始日期分组；默认带时区校验保持不变，不猜 UTC、不用于在线 deadline/跨时区对齐。`scripts/import_replay_demo.py` 已将三站选定臂导入独立数据库 `artifacts/e5-real-replay/demo-verified.sqlite`，新建 24 条（3 站 × 8 个本地日期），逐站 API 查询复核通过。对应 JSONL、逐站核算和来源哈希：`artifacts/e5-real-replay/`。

本轮应用回归为 95 passed、49 subtests passed；共享优化器 floor 与桥接定向回归 11 passed、6 subtests passed。E5 的 PV 数据导入完成，不代表在线决策重放、弃光量、负价应对或官方结算验收。
