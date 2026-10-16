# W1：API 当前档位锚定交付

## 结论

真实 `/api/price/current` 可唯一标识当前一小时档位。保存的两次响应均为 `2026-10-17T08:00:00Z`–`09:00:00Z`，与两日电价 48 点中的唯一同起止项一致。购售电价仅用作同档一致性校验，不用作定位。

`device_timezone=null`、HA 时间无时区时的主路径已实现：稳定 current 档位 + 新鲜 HA 日期/小时核对 → 带偏移当轮时刻 → API 连续未来 24 小时窗口 → MILP。保留 HA 原始时间；不猜测 IANA 时区。显式时区若存在但矛盾，拒绝执行。读取跨档、两源冲突、重复/缺失档位、current 异常、未来缺价，最多 3 次整批读取后按站安全拒绝。MILP 自用回退为 non-optimizer-attributed，dispatch 同样受门控，dry-run 不写 HA。

## 证据与 SHA-256

- 原始 JSON 样本与请求时间：`outputs/m3_api_anchor_20261015/price_contract_sample.json`。
  SHA：`650447dcac245cd892621da1a694daa683a5119858a8375da6f418a81050523e`。
- 三站只读预检：`outputs/m3_api_anchor_20261015/preflight.json`。
  SHA：`4e357aa6e2f535136804dce96077487169c1215cd85f0bc338cbe5fbd5b57862`。
- 配置文件字节 SHA：`257d1a076e6930ad1240cbbf28208d6c9e5d6540c74bbb305bf29d9d0bda0f8c`。配置含凭证，不入库，也未改动；其旧显式 `local.device_timezone=Asia/Shanghai` 不是官方时区事实，负责人须复核，不能带着冲突配置执行新鲜 tick。
- 策略项目测试：`python -m pytest tests -q --junitxml=outputs/m3_api_anchor_20261015/tests.xml`，**328 passed**。JUnit SHA：`b1e05f4c1bd5e6d82db84bdebe6a45a523e70a6198c0a05dc5c20539c2184dfe`。不包含看板子项目；此前根目录收集因其依赖缺失而失败。
- 新未签核 F1 manifest：`outputs/f1_parameter_manifest_20261014.json`。
  SHA：`e4b7d369915b60ef9ec709e273b219b538cc4d9f2c9ca1c517a4fc854640f235`。已纳入新在线链路源码与新增测试哈希；替代此前候选。签核留空，`f2_authorized=false`，不得据此启动正式 F2。

测试涵盖无时区成功求解、同价多档、current 缺失/异常、跨档重试恢复/耗尽、午夜及 API 偏移保留、显式配置冲突、HA 日期小时冲突、重复/缺失档位、25 点非整点完整窗口、三站故障隔离、dispatch 同门控及新日志证据要求。健康求解为模拟输入，不冒充真实新窗口验收。

## 每站审计例与真实预检限制

模拟输入示例：`ha_device_time_raw=2031-01-01 00:15:00`，`api_current_start/end=00:00Z/01:00Z`，`selected_price_slot=2031-01-01T00:00:00Z`，`resolved_device_time=2031-01-01T00:15:00+00:00`，`alignment_source=api_current_slot`，`alignment_attempts=1`，`alignment_rejection_reason=null`，dry-run `writes_performed=0`。

真实预检：三站后缀 `1502/1503/1504`，原始 HA 均为 `2026-10-17 08:15:00`，但最后更新仍为旧窗口，三站 `state_fresh=false`、`eligible_for_milp_tick=false`、`resolved_device_time=null`；3 次读取后拒绝，零写入。不能将 stale 时间解释为有效锚定或称为多 tick M3 通过。新日志校验要求时间递增、current 对齐证据且无拒绝；等待新模拟窗口。

W3 待集成负责人验收、推送并告知固定提交后再受控切换；本轮没有推送、修改远端配置、停止远端 PID、开启比赛、HA 写入或正式 100 站 F2。详细算法与命令见 `M3_TIMEZONE_CONTRACT_20261015.md`。共享文档仅更新本轮进度，不将其他负责人未提交的内容带入选择性代码提交。
