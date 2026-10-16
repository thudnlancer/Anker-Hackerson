# MILP 负荷预测模型导出包（V16 · V12_bolt25，18 点原版）

源模型：`foundation-model-benchmark/runs/v16/models/final/bundle.joblib` + `models/bolt_small`。
本文件夹为自包含副本，供 MILP 决策算法以「先预测、后优化」方式使用。源实验文件未做任何改动。

## ⚠️ 状态（来自 runs/v16/model_freeze.json，必须先读）

- **development_pass = false**：未通过全部开发门槛（UCI 单步 MAE 426.72W，较 V12 的 422.10W 退步 1.09%；3h MAE 739.86W，较 V12 改善 0.58%）。
- **active_default = "V12 unchanged"**：项目默认投用模型仍是 V12（`deployment/milp_forecast_v12/`）。
- **formal_acceptance = false**：未经正式独立验收。
- 本导出仅按用户要求提供 V16 冻结候选的可用副本，不等于投用批准。

## 模型结构（18 点原版契约）

- 输入：18 个历史 15 分钟负荷点（W，最旧→最新）；无 scaler、无其他字段。
- 单步输出 = `0.75 · V12树(18滞后→1) + 0.25 · chronos-bolt-small 零样本中位数`，截负后经低负荷跟随融合（lo=0.204、hi=0.244 kW），再截负。
- 递推 12 次得未来 3h，每步只回填自身预测。
- 不依赖 head1/head3 微调权重与 21 维门控（那些属于其他未入选候选）。

## 文件

| 文件 | 用途 |
| --- | --- |
| `bundle.joblib` | 冻结包（内嵌 V12 树、gates、阈值），sha256 `a4e52717…` 与冻结记录一致 |
| `bolt_small/` | chronos-bolt-small 本地权重（amazon/chronos-bolt-small，离线加载） |
| `model_freeze.json` | V16 冻结元数据与选择状态 |
| `inference.py` | 推理库：`Engine()` → `engine.forecast(history_w)` |
| `predict.py` | 命令行入口，18 个 W 值 → 下一点 + 未来 12 点 |
| `verify_export.py` | 一致性校验（哈希、行为探针、与原管道逐点对照） |
| `requirements.txt` | 依赖版本锁定（torch + chronos，重依赖） |

## 运行环境

根 `.venv` 不含 torch/chronos。直接用 benchmark 环境：

```bash
../../foundation-model-benchmark/.venv/bin/python verify_export.py   # 全部 PASS 方可使用
../../foundation-model-benchmark/.venv/bin/python predict.py --history-w 50 50 50 50 50 50 50 50 50 50 50 50 50 50 50 50 50 50
```

或按 `requirements.txt` 新建独立环境。

## MILP 集成方式

模型不进 MILP 约束。每 15 分钟滚动：取最近 18 个实测点 → `forecast()` → 未来 12 点作为固定参数 `P_load[t]`（t=1..12）。

```python
from inference import Engine

engine = Engine()                     # 进程启动时加载一次（含 bolt 权重，数秒）
next_w, future_w = engine.forecast(history_w)   # 18 个 W，最旧→最新
# MILP: P_load[t] = future_w[t-1], t = 1..12
```

完整 12 步递推常驻 p95 ≈0.73 秒/起点（不含首次加载），15 分钟级滚动足够。

## 使用注意

- 低负荷段（最近值 ≤0.244 kW）输出本质是「跟随最近值」，不会预知突发设备启动。
- 若 MILP 决策会反过来改变实际负荷（需求响应回路），本预测假设负荷自然演化，需另行讨论。
- bolt_small/model.safetensors sha256：`06a6a19bbe74bc10a9cd193bd4bf2bf638ae07f7e0d51653ae7ab8ea968a21dd`（冻结记录未含此哈希，此处为导出时登记）。
