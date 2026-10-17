# 交付说明 · Anker SOLIX 智能测控黑客松

赛道：充电储能（三站光储协同调度，7 天 168 设备小时窗口）。
本仓库为完整交付：**策略引擎 + 负载预测 + 离线试验场 + 可解释性前后端**。

## 评分口径（与官方 SOP 第 9 节一致）

```
总分 = ΣΔR_settled ÷ ΣK × 100%   （三站合并，字段 saving_settled_percent）
```

- `ΔR_settled` = 已实现收益 + **期末电池库存按整场平均买电价的结算价值**
  （期末库存市值 − 期末库存成本）。窗口结束时的 SOC 直接影响得分：
  低价充入、期末仍持有的电计正值；高价充入未放出的电计亏损。
- 已用在线结果实证：2026-10-17 场次 site_3 期末 SOC≈1%，
  `device_settled == device_revenue` 分毫不差（库存为零，realized==settled）。

## 系统架构

```
AWS 采集层 (7×24)          离线试验场                 在线控制
sim API + HA ──► tick 流 ──► epoch 数据集 ──► 策略×预测矩阵回放 ──► run_live.py (MILP)
                              (数据卫生门禁)        │
                                                  ▼
前端 (React/Vite) ◄── 解释后端 (energy_explain) ◄── 证据导出 (DecisionIn)
 :5173                   :8080                       build_explainability_record
```

### 可选策略（`src/energy_lab/strategies/`）

| 策略 | 说明 | 定位 |
|---|---|---|
| `milp` | 96 步全视野混合整数规划（CBC，5s 时限） | **主力** |
| `gradient` | 梯度下降启发式 | 无求解器环境兜底（实测显著弱于 MILP，见下） |

### 可选负载预测

| 模式 | 说明 |
|---|---|
| `persistence` | 持久假设（下界基线） |
| `v16` | v16 神经网络负载预测（15-min 粒度，`.venv-forecast` 隔离环境） |
| `perfect` | 完美预测（上界，仅离线分析用） |

## 前端交付（重点）

分支：`codex/solix-v13-frontend`（本仓库 branch，基于 `explainability-app/`）。

- **数据场次选择器** = 预测模式对比：`epoch-replay-v1`（MILP×persistence）vs `epoch-replay-v16`（MILP×v16）
- **站点选择器** = `epoch_XX:site_N:策略:预测`，同一物理站点在不同配置下的决策可逐一对比
- 每个决策附完整解释：证据事实、费用分解（购电/外送/退化/期末库存估值）、provenance（rolling.py 哈希钉死）
- 页面：HOME / 调度时间轴 / 决策追问 / 替代方案（反事实） / 储能计划 / 光伏日报 / 能源费用

### 本地启动（评审复现）

```powershell
# 后端（explainability-app 目录，Python 环境见 docs/solix-v13-integration.md）
$env:PYTHONPATH = 'src;..\src'
python -m energy_explain import-records --file <主仓>\outputs\epoch_evidence_v1\evidence-decisions.jsonl --db .cache/demo.sqlite
python -m energy_explain serve --db .cache/demo.sqlite --port 8080
python -m energy_explain worker --db .cache/demo.sqlite

# 前端（explainability-app/frontend）
npm ci
npm run dev   # http://localhost:5173 ，/api 代理到 :8080
```

前端 84/84 测试通过，生产构建通过；测试需 `$env:NODE_OPTIONS='--no-experimental-webstorage'`。

## 离线试验场结论（60 组完整回放，EUR/epoch）

| 策略 | persistence | v16 | perfect |
|---|---|---|---|
| **MILP** | 3.58 | **4.17** | 5.87 |
| 梯度 | 1.01 | 0.02 | 1.58 |

1. 预测质量排序成立（persistence < v16 < perfect），v16 较朴素预测 +17%
2. 梯度策略全面弱于 MILP，且不能利用更准的预测
3. site_3（负载重站）是预测价值试金石：persistence 下 MILP 反而亏钱
4. 站点基线差异巨大（site_1 净收益 / site_2 净平衡 / site_3 净付费 20.5 EUR/epoch），
   跨站比较一律用 EUR 绝对值

详见 `docs/delivery/OFFLINE_ANALYSIS.md`（明细 `docs/delivery/comparison_runs.csv`，在线成绩 `docs/delivery/online_results_20261017.json`）。

## 比赛成绩

**正式赛（2026-10-17，窗口 10-14~10-21，MILP live 全程控制）：
saving_settled_percent = +0.90%（settled +2.06 EUR，三站基准电费 228.7 EUR）**

| 站点 | settled | 备注 |
|---|---|---|
| site_1 | -0.85 EUR (-2.3%) | 已实现 +0.53，期末库存估值倒扣 → 末态管理教训实证 |
| site_2 | -0.57 EUR (-0.4%) | 本场为负载重站：多买 7.7 EUR 电未收回，与离线「负载重站是预测价值试金石」结论互证 |
| site_3 | +3.49 EUR (+10.3%) | 本场为 PV 富余站，表现最好 |

（注：不同场次站点物理画像随 data_seed 变化，负载重角色本场落在 site_2）

模拟赛道 13 场：最佳 **+4.45%**（2026-10-16），见 `docs/delivery/online_results_final.json`。

## 已知边界（不粉饰）

- 梯度策略无 MILP 求解证据，不进决策级解释管线，仅以矩阵对比表交付
- epoch 数据集为滑窗（重叠 ~78%），非独立样本
- v16 冻结元数据 development_pass=false / formal_acceptance=false，
  上述数字不构成模型正式验收
- 离线回放的 settled cost 未含末态库存折算（在线评分含），修正口径见 `docs/delivery/OFFLINE_ANALYSIS.md`
