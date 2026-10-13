# 接入日志中已有的 MILP

本轮已把用户提供日志中的实际 `rolling.py` MILP 接入解释服务。保留未修改的历史源文件，另一个副本仅增加首步干预入口；候选生成仍调用未修改的原求解器。验证使用完整人工输入，说明真实模型代码可以接入 API、SQLite、独立工作进程和前端比较，尚未证明当前 Windows / AWS 策略版本或真实遥测闭环已接入。

## 来源与版本

恢复的原文件为 `src/energy_explain/strategy_vendor/rolling_original.py`。其锁定 SHA-256 为：

```text
4197a84d871f635236b320f63e378bae6914e4fa9728d048a5ba08f8af9ba2d1
```

源证据来自所提供 JSONL 的 4705、5357 行，保留原代码中的功率与 SOC 守恒、充放电/购售电互斥、SOP 约束和目标函数。每次重放同时检查原文件和首步适配副本的哈希。适配副本锁定为 `6e2b1ca5e372f9d6a70bc5322ac3f699b417e4ab336df2a25c8a3add84b62d11`。接入后的模型版本为 `historical-milp-4197a84d-cf1`，与首版 `demo-milp-v1` 分别注册。

接入副本仅新增两个互斥的干预参数：`first_step` 固定首步四项功率，`first_charge_kw` 仅固定首步电池充电功率。干预不放宽原变量上下界；超限或相互冲突的功率返回不可行。

## 在本机验证

安装最新 `requirements.lock`（本轮增加 pandas），保留已完成的前端生产构建。在仓库根目录执行：

```bash
source .venv/bin/activate
python -m pip install -r requirements.lock
python -m energy_explain verify-model --db data/recovered.sqlite
python -m energy_explain serve --db data/recovered.sqlite --host 127.0.0.1 --port 8090
```

另开终端启动同一个数据库的独立工作进程：

```bash
source .venv/bin/activate
python -m energy_explain.worker --db data/recovered.sqlite --poll 0.5 --timeout 30
```

访问 `http://127.0.0.1:8090/`，选择场次 `recovered-model-validation-v1`。三站输入明确标为人工构造：

| 站点 | 输入与步长 | 验证用途 |
| --- | --- | --- |
| `model-4-step` | 4 步 × 1 小时、零光伏 | 核对原模型自由候选与首步干预 |
| `model-24-step` | 24 步 × 1 小时、非零光伏和负购电价 | 核对 SOP 约束和全天输入 |
| `model-96-step` | 96 步 × 15 分钟、简化恒定输入 | 核对 15 分钟步长、完整时域及接入链路 |

`verify-model` 导入的候选和费用来自原求解器自由求解；不会生成 HA 提交、回读或结算事件。完整输入示例见 [恢复模型验证记录](examples/recovered-model-validation.json)。

96 步样例使用简化输入；更复杂的曲线、负价 96 步样例曾在设置 10 秒上限时达到求解时限，须归类为超时。成功样例不能证明 96 步在任意预测下都能满足时延。当前适配器每次重放设置 10 秒求解上限（原函数默认值为 5 秒），独立工作进程还有默认 30 秒任务上限。

## 原候选与前版人工规则的区别

相同四步输入下，原 MILP 自由求解首步充电约 **4.421053 kW**，净成本约 **€0.564947**。首版演示把首步规则动作固定为 **5 kW**，净成本为 **€0.569000**。二者的输入相同，但首步选择方式和基准不同。

| 基准 | 基准净成本 | 首步不充后净成本 | 替代减基准 |
| --- | --- | --- | --- |
| 固定 5 kW 的旧人工规则候选 | €0.569000 | €1.380105 | +€0.811105 |
| 原 MILP 自由候选、重放时固定保存的四项首步功率 | €0.564947 | €1.380105 | +€0.815158 |

费用都是四步人工输入下的模型值。不能把旧规则的 €0.811105 当成原 MILP 候选的反事实差额。替代“首步不充”仅表示首步电池充电为 0，模型仍可选择其他允许的首步功率，后续重新优化。

## 决策接入契约

`DecisionIn` 保持 `schema_version=1`，增加可选 `replay_context` 与 `provenance`。字段不存在或为 `None` 时从序列化结果省略，使旧演示记录及已存 v1 记录保持幂等。

| 字段 | 用途 |
| --- | --- |
| `model_version` | 必须匹配 `historical-milp-4197a84d-cf1` |
| `replay_context.upstream_source_sha256` | 锁定历史原文件版本 |
| `replay_context.command_contract` | `battery` 或 `sop`，保留原控制模型语义 |
| `replay_context.first_step` | 原首步 `charge_kw`、`discharge_kw`、`grid_import_kw`、`grid_export_kw` |
| `provenance.origin` | 区分外部适配、历史日志与模型验证 |
| `provenance.input_kind` | 区分观测、混合、人工与不完整输入 |
| `provenance.references` / `note` | 记录来源证据和局限 |

重放还需要完整且等长的 PV、负荷、购电价和售电价序列，设备时间、初始 SOC、容量、步长、上下界、效率、损耗与期末估值，以及保存的各费用分项。功率用 kW；适配器调用原模型时转成 W，返回时再转回 kW。

带 `replay_context` 的记录必须显式保存步长、SOC 上下限、两项效率、期末估值、损耗和四组时序数组，不能依赖接入 schema 的默认值。原模型若使用 `terminal_price=None` 的窗口均价，应把实际窗口均价解析后保存；不能用默认 0 替代。旧的无重放记录仍兼容原有默认字段契约。

导入幂等以解析后的 JSON 值判断，因此浏览器把 `-0.0` 重新序列化成 `0` 不会触发冲突。服务保留首次保存的原字节与快照哈希；重复决策、执行事件和结算记录均不覆盖或新增，真实数值变化仍返回 409。

此历史模型仅支持充放电功率上限相同、放电效率为 1 的记录。`grid_charge` 和 `grid_export_target` 控制语义必须使用 `sop` 契约；来源、版本、哈希、输入或首步语义不匹配时反事实能力不可用。

反事实基准固定保存的四项首步功率并重新优化后续，逐项核对保存的购电、售电、损耗、期末信用和净成本；替代只固定新的首步充电功率。输出独立校验守恒、SOC、功率、互斥、SOP 和费用，不满足校验时报告失败，不替换原证据。

## 真实历史候选仍有哪些缺口

日志 6453 行保留了同一批三站真实只读候选，设备时间均为 `2027-01-24 03:00 UTC`，电池放电约 2280 / 1137 / 516 W，并网目标均约 0 W，记录声明求解最优且没有设备写入。安全提取保存在本机忽略目录 `artifacts/recovered/shadow-candidates.json` 和 `shadow-provenance.json`。

这些输出没有同时保存初始 SOC、当前 PV/负荷、完整预测与电价序列、锁定的模型版本/参数及费用分项。预计 15 分钟后的 SOC 不能冒充初始 SOC；其他时刻的 HA 状态和 6405 行配置默认值不能拼成这一批决策。因此这些候选仅作为历史证据归档，不补零、不导入完整 `DecisionIn`、不提供反事实。

接入 Windows 的实际运行，需要取得该版本的源码或提交标识，以及**同一次决策**的原始 HA 状态、设备/观测时间、实际使用的四组输入序列、预测来源与回退记录、求解参数、首步四项功率、控制指令映射及各费用分项。拿到这些后可以通过已有导入 API 和提供器重放验证；真实控制模块再提供提交/回读，结算模块再提供官方结果。

## 回归验证

```bash
source .venv/bin/activate
python -m pytest tests/test_recovered_integration.py -q
python -m pytest -q
```

测试覆盖双源码哈希、无干预时与未修改原模型对账、固定四项首步功率、两类基准成本、非零 PV、负价、96 步、超限/满电不可行、不匹配记录拒绝、API 到独立工作进程、显式人工来源、无自动控制/结算事件、重放参数不得靠默认值、三类记录的正负零幂等，以及旧 v1 演示的兼容。
