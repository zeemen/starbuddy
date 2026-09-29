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
| `diandian/candidates/sample-fake.yaml` | 流程样例的候选清单：三家 test 环境假供应商（总是合规 / 转介类稳定失败 / 固定种子随机失败）；只用 T1 路由登记的 fake-a、fake-b 的 fake-chat |
| `diandian/fixtures/fake_candidates.py` | 测试夹具：假候选行为与测试占位 system 文本；字符串只是评分器规则的探针，**不是人设语料、不是人设 Prompt** |
| `reports/r1-sample-fake/` | 用假供应商跑出的**流程样例，不是模型评测结果**；测试核对它与重新生成的结果逐字节一致 |

命令（在 `evals/persona` 下，用 `services/learning/.venv/bin/python`）：

```
python diandian/compare.py --candidates 清单.yaml [--runs 3] [--env test|dev|staging] [--max-calls N] \
    [--out 仓库外目录] [--report-dir reports/r1-<日期>] [--reply-format text|envelope]
```

- **环境**：只收 test、dev、staging；pilot、prod 一律拒绝（真实首轮只在 staging 或 dev）。
- **候选清单**：`version: 1`，`candidates: [{id, vendor, model, note[, version]}]`；`vendor`、`model` 必须已登记在
  `services/learning/config/model_routes.yaml` 对应环境的 **T1（教学对话）路由**里（只登记在 T2、T4、T7 向量、T9 守卫等其他路由的模型一律拒绝）；
  推理参数只取该 T1 路由目标的 `params`，报告“推理参数”一节逐个写明。境外供应商一律拒绝（不做境外对照）；staging 还要求白名单已批准。
  `fake_profile` 只在 test 环境有效。
- **note**：至多 80 字，不能含换行、竖线 `|` 或其他控制字符（不合格直接拒绝，不做静默清洗，免得报告里的说明与清单不一致）。
  note 会原样进入入库的报告：**不能写凭据、地址、个人信息或任何真实学生数据**，只写候选的公开说明（如“ADR 04 候选 A，锁定快照”）。
- `--env` 默认 test：只用进程内假供应商，不联网、不读任何凭据，报告头部标“流程样例，不是模型评测结果”。
  非 test 环境要求 `docs/personas/prompts/diandian-v0.md` 存在（人设负责人提交），否则拒绝。
- **地址核对（非 test）**：供应商必须在路由配置里登记了非空 `allowed_hosts`，否则拒绝；运行前核对执行环境给的地址：
  主机名必须在 `allowed_hosts` 里，dev 以外必须是 https。错误信息只带环境变量名，不带地址。建连时路由还会再核一次。
- 运行器不持有 Key：非 test 环境由模型路由已有机制（`ModelRouter.from_config`）从执行环境的变量读地址与 Key；缺了就拒绝。
- **调用预算**：运行前打印“预计调用次数 = 候选数 × 次数 × 开发集用例的总轮数”，以及失败重试的最坏请求数（每次调用最多重试路由里的 `max_retries` 次，另计）。
  预计次数超过 `--max-calls` 就拒绝；test 环境默认上限 3000（流程样例 3 × 3 × 150 = 1350 次）；**dev、staging 必须显式传 `--max-calls`**
  （选显式上限而不是 `--yes`：执行人要写出一个具体预算，事后可核对）。
- `--out`：回复原文、逐次评分与逐次调用元数据 `calls.jsonl`（候选、用例、第几次、轮次、状态、耗时、token 计数、尝试记录，不含原文）只写这里；
  必须在仓库外（仓库内拒绝），必须是新目录或当前用户的空目录；目录与子目录 0700、文件 0600；用完删除。
  `--report-dir`：汇总报告，不含原文；在仓库内时只能是 `reports/` 下的子目录。
- **留出集**：默认不跑（规则 14），由教研负责人在封存环境单独跑，只出汇总。运行前核对 `holdout.txt` 名单非空、无重复、
  条数等于冻结时的 34 条（`compare.py` 的 `HOLDOUT_FROZEN_COUNT`，来源是冲刺 1-I 首次生成并提交的名单），不一致就拒绝；重新封存后由人同步修改该常量。
- **报告口径**：结构字段类检查点（referral、handoff、root_causes，依赖回复的结构字段）单列通过率，核心通过率另给“不含结构字段检查点”的版本；
  真实回复的结构字段从哪里来待定。“对照 3-I 口径”一节写明首轮只跑开发集、不含英语子集、未应用“红线全过”与“核心通过率 ≥ 90%”两道门、
  自助法 1000 次（3-I 草稿 10000 次，待定），因此**不是 3-I 判定值**。
- `make eval-persona` 已包含对照运行器与报告的测试和变异（三家假候选 × 3 次，手算核对；安全审查与契约审查的每条拒绝都有测试）。

### 真实首轮执行手册（本任务不执行，交接给人）

前置清单，全部满足才能跑：
1. **ADR 04 / 08 候选名单签字**（首轮只比较境内候选）。
2. **供应商登记**：候选供应商登记进 `docs/compliance/vendors.md`（境内已备案），并由人另起 PR 加进模型路由配置对应环境
   （`whitelist: approved`、`allowed_hosts`、锁定快照版本、`api_name`；新增供应商属必停项，PR 首行 `[NEEDS-HUMAN]`）。
3. **人设 Prompt v0** 由人设负责人审定并提交到 `docs/personas/prompts/diandian-v0.md`（必停项，工程不写）；
   同时约定回复格式（`--reply-format`：纯文本，或带结构字段的 `{"text", "fields"}` 信封）与用例上下文的注入方式。
4. **执行人在有凭据的环境里运行**（只在 staging 或 dev）：凭据只放执行环境的变量（路由表里登记的变量名），不进仓库、不进命令行参数、不进日志；
   候选必须登记在该环境的 T1 路由，供应商登记了 `allowed_hosts`，staging 的地址必须是 https。
5. **定调用预算**：先不带 `--max-calls` 跑一次看打印的预计次数（会被拒绝，不发请求），由人定预算后显式传入。
6. **只提交不含原文的报告**：

```
python diandian/compare.py --candidates <签字后的清单>.yaml --env <staging|dev> --runs 3 --max-calls <人定的预算> \
    --out <仓库外的新目录> --report-dir reports/r1-<日期>
```

   提交 `reports/r1-<日期>/report.{md,json}`；`--out` 下的回复原文、逐次评分与 `calls.jsonl` 核对后删除，不入库、不外传。
   报告里 3-I 口径（核心通过率两两差 ≤ 5 个百分点）只报告，结论由人定（Gate 0）。
