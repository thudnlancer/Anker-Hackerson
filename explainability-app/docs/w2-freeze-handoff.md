# W2 本轮冻结交接（2026-10-14）

状态：E4 已集成，E5 实现与 W2 预验完成，**E5 待集成验收**。本轮只修正 PV 查询口径、证据边界和演示复现，不扩展其他页面功能。共享模型保持 `shared-milp-603de45c-cf1`；源码哈希保持 `603de45ca089b13665abd91fe2f58aa12e576909dfcba2ffb481cff2aedd13a4`。

## 交付范围

- `frontend/src/api.ts`、`frontend/src/App.tsx`：PV 可选择 `irradiance`，展示历史练习回放的证据边界。
- `frontend/src/App.test.tsx`：覆盖口径切换与边界声明。
- `scripts/prepare_e5_demo.py`：从人工决策样例和历史 PV JSONL 重建独立演示库，输入身份保持分离。
- `scripts/browser-smoke.cjs`：实际走查 E1–E3 和三站 E5 PV 页面。
- `docs/e5-demo-runbook.md`：逐步输入、页面/API、预期与失败回退；E4 真实共享优化器单独验收。
- README、本文档及共享进度文档：交接入口、复验结果与待集成状态。

前置 W2 提交 `5b5922d` 提供 `import_replay_demo.py`、`verify_plan_floor.py` 和离线本地日期核算契约。集成侧已有 `82ba72c`、`76399f0`，不要整枝合并 W2 的旧状态文档；本轮代码提交独立提交，后续共享状态单独提交。

## W2 预验结果

在 W2 分支实际执行：应用 `95 passed, 49 subtests passed`；前端 `15 passed`；生产构建通过；真实共享 MILP 五类验收 `feasible / infeasible / timeout / unsupported / rejected`；准备演示库为三站各 8 个本地日期；浏览器 7 组检查通过、页面 errors 为空。浏览器使用 API 与独立 worker（本机 8088），完成后已停止。五类 MILP 验收输入为人工快照；timeout/能力缺失是明确故障注入。

所有可复现命令见 [三站演示讲稿](e5-demo-runbook.md)。应用测试和前端构建从 `explainability-app` 运行；浏览器需要同源构建、准备好的库及 API/worker。集成侧须从主基线接入必要代码后再复跑，W2 预验不等于负责人签核。

## Git 与产物

`git ls-files explainability-app/artifacts` 为空，`git check-ignore explainability-app/artifacts/e5-real-replay/demo-verified.sqlite` 命中 `artifacts/`。下面产物均未跟踪；**不提交 SQLite 二进制**。报告与 JSONL 也由命令重新生成，不强行解除忽略。

| 既有本地产物 | SHA-256 |
|---|---|
| `artifacts/e4-floor-acceptance/acceptance.json` | `130ed65dc9d5336969dd62425bf02776b69f7b293470fd3fee15ee133fee4fc1` |
| `artifacts/e5-real-replay/import-report.json` | `1e6d759a836d59c1e84b6d9caca7743fed00218c2ccd8fe14cb24ee45d1e6d6e` |
| `artifacts/e5-real-replay/pv-accounting.jsonl` | `3f79b4e8e5c62e930ff063f5b34dd2f5506a485ebfc349cf7d11d3e2c40636fe` |
| `artifacts/e5-real-replay/demo-verified.sqlite` | `09c2d601e54957b094efb1e835ca608f656f682acdc34dde0bd636c01a0f5931` |

源 CSV 哈希见 [预检记录](w2-e4-e5-preflight.md)。复现必须验证源 trace、manifest 与 audit，保存来源证据；不能用 mock 顶替。原始源目录不在此代码提交内。

重建命令见讲稿的 `import_replay_demo.py`，指定一个新的目标库路径会创建 24 site-days；重复导入同一库为 `created=0`，这是幂等成功。JSONL 可用于稳定内容校验；数据库的物理哈希可能因创建顺序/SQLite 版本而不同，报告哈希也会因输出绝对路径、created 和求解耗时而不同，应同时检查逐站行数、来源哈希及 API 查询结果，不能要求这些运行报告逐字节一致。

哈希复核命令：

```powershell
git ls-files explainability-app/artifacts
git check-ignore explainability-app/artifacts/e5-real-replay/demo-verified.sqlite
Get-FileHash explainability-app/artifacts/e5-real-replay/*,explainability-app/artifacts/e4-floor-acceptance/acceptance.json -Algorithm SHA256
```

## 尚未签核的范围

## 冻结提交与隔离集成复验

代码冻结提交：`257bf132cfaab3a3cafd0f12081c58eb98e5af24`（8 个文件）。前置离线核算代码选择性集成提交：`8362854`（5 个实现/测试文件，来源 `5b5922d`，排除旧 README/共享状态）。隔离 worktree `D:\Anker黑客松\w2-e5-integration-check` 从主基线 `040257d` 开始，先接入本轮代码为 `64b9641`，再接入前置代码为 `8362854`。工作树干净；没有修改主仓其他 worker 的源码。

集成侧可安全在新的独立路径复现，不要在脏主工作树直接 cherry-pick：

```powershell
git worktree add --detach 'D:\Anker黑客松\w2-e5-review' 040257d
git -C 'D:\Anker黑客松\w2-e5-review' cherry-pick 8362854 257bf13
Set-Location 'D:\Anker黑客松\w2-e5-review\explainability-app\frontend'
npm ci
```

然后按讲稿依次运行应用测试、前端测试/构建、导入、准备、API/worker、浏览器和五类验收。真实复跑结果：应用 **96 passed + 49 subtests**（含主分支额外集成测试），前端 **15 passed**，build 通过；三站各 8 个本地日期；五类验收通过；浏览器 **7 passed / errors=[]**，API/worker 已停止。测试唯一 warning 为 Starlette TestClient/httpx 弃用提示，不影响结果。

复验产物位于隔离树 `explainability-app/artifacts/`，均忽略且未跟踪：

| 复验产物 | SHA-256 |
|---|---|
| `e5-real-replay/demo-verified.sqlite` | `76bb18f1637373935cdfb546d0b5414cfc7fc3aa85f831d0d948347486216c90` |
| `e5-real-replay/import-report.json` | `f0e98cd7ad5abb1e90a16d7213588571a5f9004fa5e40cfda3d7044aeb70c401` |
| `e5-real-replay/pv-accounting.jsonl` | `3f79b4e8e5c62e930ff063f5b34dd2f5506a485ebfc349cf7d11d3e2c40636fe` |
| `e5-floor-recheck/acceptance.json` | `b8d528c94a6e2ee8e1d185ebec21892200bf752261138d3236f33f4262eac096` |
| `browser/result.json` | `47a6c3a26df97a82e0d54ed8d623dfcbba252b87dba57a5a7a14d8321e9d0184` |

## 负责人签核

E5 待集成。集成负责人实际复跑应用测试、前端测试/构建、MILP 五类验收和演示脚本后，再给出验收结论。练习站离线核算不能证明现场控制、官方结算、弃光测量、负电价应对或完整决策回放；原始本地小时不能转换成在线 deadline。
