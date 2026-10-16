# 单场模拟的采集与赛末接入

该入口只观察已经由用户启动的 mock。它不启动、复位或停止模拟器，也不调用 HA 控制服务。三站有限控制仍由 `run_milp_live.py` 单独执行，逐站恢复自发自用；不将本场成绩当作全场持续 MILP 的性能结果。

## 场次与启动

将本场等待期 `/api/dashboard` 的 `contest` 保存为 manifest 的 `initial.contest`，保存开赛前结果数 `initial.results_count`，并使用 `energy_explain.mock_acceptance.result_fingerprint` 对每一条开赛前 `results.runs` 生成 `initial.result_fingerprints`。不要保存认证响应或请求头。`run_id` 必须新建，且在本场恢复时保持不变。

从共同仓库执行：

```bash
PYTHONPATH=src:explainability-app/src python -m energy_explain.mock_acceptance \
  --config local_online_config.json \
  --manifest outputs/your-mock/session.json \
  --db explainability-app/data/your-mock.sqlite \
  --checkpoint outputs/your-mock/checkpoint.sqlite \
  --output outputs/your-mock
```

`ANKER_HA_TOKEN` 或 `ANKER_HA_USERNAME`/`ANKER_HA_PASSWORD`，以及 `ANKER_SIM_USERNAME`/`ANKER_SIM_PASSWORD` 可以补入配置的占位值。认证信息只留在进程内存；不要将环境变量值写到报告、命令日志或 Git。观察器强制关闭所有控制开关。

同源 API 和独立反事实工作进程必须使用同一个 `--db`。前端构建后由解释 API 提供；页面刷新只读取证据，不启动采集或比赛。

## 输入、恢复与缺口

- 在观察器写入单调设备时钟前检查 HA 时钟落在选定模拟数据日期窗口内，防止开赛异步切换时把上一场缓存日期写进新场。
- 场次的 track、start/end、数据窗口、天数、加速倍率变化会停止采集。run/window/database/checkpoint 绑定也在本地持久保存，重启不得改变。
- 只读认证在整个进程内具有有限刷新预算。天气批次收齐后统一处理 401，整批最多重读一次并重新读取 HA；403 或预算耗尽停止，不绕过权限。
- 周期日志保留当轮 `state/error_type` 与历史 `last_error`。历史错误不是每轮都失败；统计需读 `stream_cycles`。
- 重启沿用原 run、checkpoint、业务证据和待投递队列。不得将数据缺口补成已采集。

`StreamCheckpoint.recover_opening_alignment` 是显式、一次性的开赛恢复工具。它仅允许首次不超过两轮的窗口外 clock 污染；坏站不能有任何保存的决策，有效时钟不能回退。调用方须先独立只读证明仍为同一场及三站当前时钟已进入窗口。恢复保留全部记录、哈希、周期和错误，将删除的坏 clock 与证据引用写入 `stream_recoveries`。普通的中途回退或已存在决策不得使用此工具。

## 证据与结算

观察器使用 `shadow-` 决策 ID 前缀，有限下发保留自己的决策 ID，避免同一模拟 tick 的影子候选覆盖真实提交证据。总览的 `last_submission` 提供同场同站历史下发链接，明确不是当前控制模式。

异步 HA 回读保留每项真实来源时间与 `pending` 验证结果。属性匹配、提交后目标读数匹配仅是部分证据；完整确认需要同帧来源。模拟器完整功率对象只作为补充响应观测，不用 `contest.now` 或客户端时间伪造来源更新时间。

赛末仅选择未出现在开赛前指纹集合中的唯一完整 mock 结果，并核对数据窗口、结束时间、三站身份及费用对账。实时 KPI、第一行参考值或其他场次不会成为结算。缺少匹配结果时保留 `pending`。

导入来源为 `simulator_mock`，与正式赛来源区分。新费用页按当前站 `site_id` 显示 `saving_settled`（EUR）和 `saving_settled_percent`（百分数），负值如实显示；模型预计费用保持独立。服务器 `verify` 是服务器声明，未独立验签。

原始运行数据、SQLite、业务快照、图片和本场报告位于忽略的 `outputs/`/`data/`，不随协作代码发布。
