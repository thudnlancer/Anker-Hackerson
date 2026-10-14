# F2 正式回测运行单（未授权，不执行）

当前 F1 候选 manifest SHA-256：`6ab83c5ac66af61b2362007c96ebffcad653b704ab48680776299c0b47cee45a`。其 `f2_authorized=false`、签核字段为空；本运行单不能视为启动许可。

## 签核/授权后的顺序

1. 集成负责人逐项签署 [F1 签核包](F1_SIGNOFF_PACKAGE.md)，形成单独的**已签署** manifest，并计算该文件的新 SHA-256。不要覆盖未签候选以免失去追溯。
2. 单独确认 F2 授权：已签署 manifest 顶层 `f2_authorized=true`，且 `signature.f2_authorization=true`，签核人/时间均非空。仅参数签核不自动授权 F2。
3. 在已签署 manifest 的确切字节哈希下生成命令：

```powershell
python prepare_f2_command.py --manifest <signed-manifest.json> --expected-sha256 <signed-sha256> --data forecast_data --output outputs/<new-f2-directory> --workers 4
```

该脚本**只打印**命令参数，不运行 replay。打印的参数必须包含 `--stage 4`、`--max-sites 100`、`--hold-mode zero_flow`、`--terminal-price-mode current_visible_mean`、`--wear 0.02`、`--solver-time-limit 2.0`、容量 `14.0`、功率 `5000.0`、SOC `0/100`、效率 `0.95/1.0` 和 `--f1-manifest-sha256`。人工核对后才另行运行打印的命令。

4. 运行完成后校验（不修改回测产物）：

```powershell
python validate_f2_output.py --output outputs/<new-f2-directory> --manifest <signed-manifest.json> --expected-manifest-sha256 <signed-sha256> --data forecast_data
```

校验器默认拒绝未签核/未授权 manifest；核对 100 份输入 SHA、每站 168 小时与四臂、每站 672 行 trace、回退原因、incumbent/gap、优化归因计数、汇总统计、独立物理审计和 `official_score_claim=false`。任何失败先保留产物与日志，不发布收益。

## 签核前允许的验证

只允许单站 smoke 使用 `--allow-smoke`。该模式输出 `mode=single_site_smoke`、`formal_result_authorized=false`，不得据此声称 F2 完成或正式收益。

当前单站记录：`outputs/w1_f1_signoff_smoke_20261015_v3/validation.json`。其输入是练习站点，离线未来辐照度仍是代理，零流量执行仍是模型核算而非硬件验证。
