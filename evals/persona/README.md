# evals/persona · 人设一致性评测

## 点点一致性用例 v0（冲刺 1-I，`specs/diandian-cases-v0-sprint1/`）

| 位置 | 内容 | 谁写 |
|---|---|---|
| `diandian/cases/*.yaml` | 120 条用例：学生侧合成输入 + 可判定的检查点，九类一类一个文件 | 工程起草，人设负责人 / 教研抽查 |
| `diandian/schema/case.schema.json` | 用例格式（合并文件级 defaults 之后） | 工程 |
| `diandian/lexicon/forbidden.yaml` | 禁语 A–E 与规则模式的机器可读转写，逐条注明出处；`reviewed: false` 直到人设负责人核对 | 工程转写，**人核对**（必停项） |
| `diandian/holdout.txt` | 留出集冻结名单（首次按类别分层、固定种子抽 30%）；修订 Prompt 时不看；正式评测前由教研负责人重新封存（规则 14，lessons 待人确认 #6） | `python -m scorer holdout --write` 首次生成 |
| `diandian/fixtures/selftest.yaml` | 评分器自检用的合成回复：中性“【测试】”口吻，带 `not_persona_corpus: true`，**不是人设语料、不是金标准**，只有评分器读 | 工程 |
| `diandian/gold/` | 期望答案与判定要点（本任务不建；hook 拦截写入） | **只能教研写** |
| `diandian/run.py` | 离线全流程：用例 → 模型路由 test 环境（假供应商）→ 评分；只验证流程，分数无意义 | 工程 |
| `scorer/` | 离线评分器：规则、结构字段判定；review 类只记待评审；报告不含回复原文 | 工程 |

命令（在 `evals/persona` 下，用 `services/learning/.venv/bin/python`）：
- `python -m scorer validate`：格式、条数、正负样例、编号、核心标记、留出集、只含合成数据、核心可自动判定比例 ≥ 80%；
- `python -m scorer selftest`：每类“应通过 / 应失败”的合成回复判得对，红线样例进红线清单；
- `python -m scorer score --responses X.jsonl --out DIR`：对一批回复评分（回复格式见 spec 设计要点 1）；
- `python diandian/run.py [--runs N] [--out DIR]`：离线全流程；回复只能写到仓库外（`--out` 指向仓库内会被拒绝），用完请删除。

`make eval-persona` 跑上面的校验、自检、测试与变异（不联网、不需要密钥），已并入 `make check`。
真实模型评测（2-I、3-I）等 ADR 04 / 08 签字后另做，不进 CI；真实回复不入库，只把不含原文的报告入库。

## 点点跨模型一致性对照 · 首轮（冲刺 2-I，`specs/diandian-consistency-r1-sprint2/`）

| 位置 | 内容 |
|---|---|
| `diandian/compare.py` | 对照运行器：每个候选只经模型路由调用它自己（为它临时拼一张只含该候选的路由表，T1 × L0），同一套**非留出集**用例、同一版人设 Prompt 各跑 N 次，每次单独评分 |
| `scorer/compare_report.py` | 首轮差异报告（Markdown + JSON，确定性）：每候选总 / 核心 / 按类通过率、红线单列、N 次间不稳定用例比例；两两通过率差与 95% 置信区间（按用例配对的自助法，1000 次，固定种子）、按类差值表、只在某一家失败的用例（只列用例 ID 与检查点编号）；对照 3-I 口径只报告、不下结论 |
| `diandian/candidates/sample-fake.yaml` | 流程样例的候选清单：三家 test 环境假供应商（总是合规 / 转介类稳定失败 / 固定种子随机失败） |
| `diandian/fixtures/fake_candidates.py` | 测试夹具：假候选行为与测试占位 system 文本；字符串只是评分器规则的探针，**不是人设语料、不是人设 Prompt** |
| `reports/r1-sample-fake/` | 用假供应商跑出的**流程样例，不是模型评测结果**；测试核对它与重新生成的结果逐字节一致 |

命令（在 `evals/persona` 下，用 `services/learning/.venv/bin/python`）：

```
python diandian/compare.py --candidates 清单.yaml [--runs 3] [--env test] \
    [--out 仓库外目录] [--report-dir reports/r1-<日期>] [--reply-format text|envelope]
```

- 候选清单：`version: 1`，`candidates: [{id, vendor, model, note[, version]}]`；`vendor`、`model` 必须已在
  `services/learning/config/model_routes.yaml` 的对应环境登记；境外供应商一律拒绝（不做境外对照）；staging、pilot、prod 还要求已批准。
  `fake_profile` 只在 test 环境有效。
- `--env` 默认 test：只用进程内假供应商，不联网、不读任何凭据，报告头部标“流程样例，不是模型评测结果”。
  非 test 环境要求 `docs/personas/prompts/diandian-v0.md` 存在（人设负责人提交），否则拒绝。
- 运行器不持有 Key：非 test 环境由模型路由已有机制（`ModelRouter.from_config`）从执行环境的变量读地址与 Key；缺了就拒绝。
- `--out`：回复原文、逐次评分只写这里，必须在仓库外（仓库内拒绝），用完删除。`--report-dir`：汇总报告，不含原文；在仓库内时只能是 `reports/` 下的子目录。
- 默认不跑留出集（规则 14）：留出集由教研负责人在封存环境单独跑，只出汇总。
- `make eval-persona` 已包含对照运行器与报告的测试和变异（三家假候选 × 3 次，手算核对）。

### 真实首轮执行手册（本任务不执行，交接给人）

前置清单，全部满足才能跑：
1. **ADR 04 / 08 候选名单签字**（首轮只比较境内候选）。
2. **供应商登记**：候选供应商登记进 `docs/compliance/vendors.md`（境内已备案），并由人另起 PR 加进模型路由配置对应环境
   （`whitelist: approved`、`allowed_hosts`、锁定快照版本、`api_name`；新增供应商属必停项，PR 首行 `[NEEDS-HUMAN]`）。
3. **人设 Prompt v0** 由人设负责人审定并提交到 `docs/personas/prompts/diandian-v0.md`（必停项，工程不写）；
   同时约定回复格式（`--reply-format`：纯文本，或带结构字段的 `{"text", "fields"}` 信封）与用例上下文的注入方式。
4. **执行人在有凭据的环境里运行**：凭据只放执行环境的变量（路由表里登记的变量名），不进仓库、不进命令行参数、不进日志。
5. **只提交不含原文的报告**：

```
python diandian/compare.py --candidates <签字后的清单>.yaml --env <登记的环境> --runs 3 \
    --out <仓库外目录> --report-dir reports/r1-<日期>
```

   提交 `reports/r1-<日期>/report.{md,json}`；`--out` 下的回复原文与逐次评分核对后删除，不入库、不外传。
   报告里 3-I 口径（核心通过率两两差 ≤ 5 个百分点）只报告，结论由人定（Gate 0）。
