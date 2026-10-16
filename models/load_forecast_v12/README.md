# MILP 负荷预测模型导出包（V12 · L12_low_follow）

源模型：`experiments/tree_low_follow_v12/run/model.joblib`（开发复验通过，**尚未正式独立验收**）。
本文件夹为自包含副本，供 MILP 决策算法以「先预测、后优化」方式使用。源实验文件未做任何改动。

## 文件

| 文件 | 用途 |
| --- | --- |
| `model.joblib` | 冻结模型包（P08 梯度提升树 + 低负荷跟随参数），sha256 见 `model_freeze.json` |
| `model_freeze.json` | 冻结元数据：特征顺序、输入输出维度、低负荷阈值、哈希 |
| `inference.py` | 推理库：`load_bundle()` + `forecast(bundle, history_w)` |
| `predict.py` | 命令行入口，18 个 W 值 → 下一点 + 未来 12 点 |
| `verify_export.py` | 导出一致性校验（哈希、行为探针、与原管道逐点对照） |
| `requirements.txt` | 依赖版本锁定 |

## MILP 集成方式

模型不进 MILP 约束。滚动时序用法：

1. 每 15 分钟收集最近 18 个实测功率点（W，最旧→最新）；
2. 调 `forecast()` 得到未来 12 点（3 h，每 15 分钟一点，单位 W）；
3. 把这 12 个数作为固定参数 `P_load[t]`（t=1..12）传入 MILP。

```python
from inference import forecast, load_bundle

bundle = load_bundle()  # 进程启动时加载一次即可
next_w, future_w = forecast(bundle, history_w)  # history_w: 18 个 W，最旧→最新
# MILP: P_load[t] = future_w[t-1], t = 1..12
```

## 推理契约（不可改动，改动即新模型）

- 输入 18 点，最旧→最新，W；内部除以 1000 转 kW、float32；特征顺序 `power_lag_18 … power_lag_1`。
- 树输出后：`max(raw,0)` → 低负荷跟随融合（lo=0.204、hi=0.244 kW，
  `w=clip((hi−最近值)/(hi−lo),0,1)`，`out=(1−w)·raw+w·最近值`）→ 再截负。
- 递推 12 步，每步只回填自身预测；禁止回填未来实测。
- 无 scaler、无日历/天气等其他输入。

## 使用注意

- 精度参考（冻结后站点回放，两站合计）：单步 MAE ≈49 W，3h MAE ≈163 W。
  对误差敏感的决策请考虑区间/场景化处理（属新增内容，需另行确认）。
- 低负荷段（最近值 ≤0.244 kW）输出本质是「跟随最近值」，不会预知突发设备启动。
- 若 MILP 决策会反过来改变实际负荷（需求响应回路），本预测假设负荷自然演化，需另行讨论。

## 校验

```bash
python verify_export.py   # 全部 PASS 方可投用
python predict.py --history-w 50 50 50 50 50 50 50 50 50 50 50 50 50 50 50 50 50 50
```
