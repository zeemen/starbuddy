# evals/persona · 人设一致性评测

## 点点一致性用例 v0（冲刺 1-I，`specs/diandian-cases-v0-sprint1/`）

| 位置 | 内容 | 谁写 |
|---|---|---|
| `diandian/cases/*.yaml` | 120 条用例：学生侧合成输入 + 可判定的检查点，九类一类一个文件 | 工程起草，人设负责人 / 教研抽查 |
| `diandian/schema/case.schema.json` | 用例格式（合并文件级 defaults 之后） | 工程 |
| `diandian/lexicon/forbidden.yaml` | **已迁移**（冲刺 3-F 词表收拢）：禁语 A–E 与规则模式的唯一真相源移到 `packages/personas/forbidden/` 与 `packages/personas/patterns/diandian.yaml`；本文件只是给前端文案单测留的只读镜像，保留一个版本周期，内容必须与新位置逐条一致（`persona-registry-check` 核对） | 不要在这里改 |
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

`make eval-persona` 跑上面的校验、自检、测试与变异，再跑人设治理回归并写报告（见下节；不联网、不需要密钥），已并入 `make check`。
真实模型评测（2-I、3-I）等 ADR 04 / 08 签字后另做，不进 CI（3-I 二轮工具见下文“二轮”一节）；真实回复不入库，只把不含原文的报告入库。

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

### staging 执行步骤（LT-02，`specs/launch-lt02-model-provider`；本任务不执行，交接给人）

LT-02 已在路由表 staging 节登记百炼（`bailian`，主）与方舟（`ark`，备）的变量名与占位：T1 主 `bailian/qwen3.8-flash`、备 `ark/doubao-2.1-turbo`，
两者都是可选的点点候选。仓库里的 staging 节**带着占位就整节拒绝**（`compare.py` 打印“模型路由配置在 staging 环境不可用：…占位…”），所以：
1. 先按 `deploy/config/README.md` 第 7 节由人完成开通、登记、填写快照名与主机名、批准（`whitelist: approved`），并跑过 `make model-smoke SMOKE_ENV=staging MAX_CALLS=5`。
2. 在 staging 执行环境（有 `MODEL_BAILIAN_URL`、`MODEL_BAILIAN_API_KEY`、`MODEL_ARK_URL`、`MODEL_ARK_API_KEY`，`LEARNING_ENV` 无关：运行器按 `--env` 取路由表）里，
   在 `evals/persona` 下用 `services/learning/.venv/bin/python`：

```
# 候选清单（签字后）只写 T1 路由里的目标，例如
#   version: 1
#   candidates:
#     - {id: bailian-flash, vendor: bailian, model: qwen3.8-flash, note: "T1 主，锁定快照"}
#     - {id: ark-turbo, vendor: ark, model: doubao-2.1-turbo, note: "T1 备，锁定快照"}
python diandian/compare.py --candidates <签字后的清单>.yaml --env staging --runs 3     # 不带 --max-calls：只打印预计次数并拒绝
python diandian/compare.py --candidates <签字后的清单>.yaml --env staging --runs 3 --max-calls <人定的预算> \
    --out <仓库外的新目录> --report-dir reports/r1-<日期>
```

3. 注意：
   - 对照运行器为每个候选临时拼一张只含该候选的路由表（T1 × L0），并带上路由表 staging 节 T1 的成本护栏（staging 要求每个路由都有护栏）；
     护栏只在运行器自己的进程里计数，与学习服务进程的当日计数各算各的。调用量先由 `--max-calls` 管：预算按“候选数 × 次数 × 开发集总轮数”
     （失败重试另计）定；T1 护栏额度小于预算时，超出的调用会以 `ROUTE_BUDGET_EXCEEDED` 失败（记为调用失败），需要时由人临时调大额度。
   - staging 只收 https 地址，主机名必须在路由表 `allowed_hosts` 里（运行前与建连时各核一次）；Key 只放执行环境变量，不进命令行与日志。
   - 回复原文只在 `--out`（仓库外，0700 / 0600），核对后删除；只提交 `reports/` 下不含原文的报告。人设 Prompt、替代句、留出集的前提同上。

## 点点跨模型一致性 · 二轮（冲刺 3-I，`specs/diandian-consistency-r2-sprint3/`）

二轮 = 按首轮差异至多三次修订（人设 Prompt 或后处理，每次只改一个对象、每次签字）后再跑一次，报两组数字并给验收判定。本仓只交付工具与流程，
用假供应商证明；真实二轮由人在前提满足后执行（同 2-I）。

| 位置 | 内容 | 谁能改 |
|---|---|---|
| `diandian/postproc/` | 后处理 v0（`diandian-post@0.1.0`，确定性）：学生这一轮有情感信号 → 整段换成转介句并置 `referral = true`；否则回复命中点点适用的禁语 A–E（3-F guard 规则层）→ 整段换成 `safe_reply`；首轮对话缺 AI 身份披露 → 在最前面加披露句。动作记录只含动作名与规则 / 信号编号 | 工程（规则）；替代句与信号规则**人设负责人确认** |
| `diandian/postproc/replacements.yaml` | 替代句：`disclosure`、`referral` 逐字取自备考搭子 V1.0 §4.1、§4.9（点点 V1.2 原样继承），加载时逐字核对原文；`safe_reply` 规格没有原句，是占位 `{{SAFE_REPLY}}`，报告标“替代句待人设负责人提供” | **只能由人设负责人提供新句子**（必停项） |
| `diandian/postproc/signals.yaml` | 情感信号的规则近似（按 §4.9 与点点 §2.3、§5.3 分类，`reviewed: false`，正反例为自写合成短句） | 工程起草，人设负责人与教研确认 |
| `diandian/revisions/<n>.yaml` | 修订记录（格式 `diandian/schema/revision.schema.json`）：假设、改动对象、修订前后版本（Prompt 版本 + 文件 sha256、后处理版本）、签字栏（留空）。`sample-fake/` 下是流程样例 | 人设负责人写假设、提交 Prompt 并签字 |
| `diandian/r2_criteria.yaml` | 验收口径：核心下限（默认 90%）、两两差上限（5 个百分点）、红线 0、判定按哪组数字（默认 Prompt + 后处理）、最多三次修订 | 人设负责人确认 |
| `diandian/r2report.py` | 修订前后对比与采纳判定、修订链核对、两组数字、验收判定、留出集汇总位、二轮报告（Markdown + JSON，确定性） | 工程 |
| `reports/r2-sample-fake/` | 假供应商跑出的**流程样例，不是评测结论**：修订 1（后处理，采纳）、修订 2（Prompt，星图话术下降，不采纳）、二轮报告（需选替代方案）；`scenario-pass/` 是两家候选的“达到验收口径”场景 | 命令生成，测试核对逐字节一致 |

命令（在 `evals/persona` 下，用 `services/learning/.venv/bin/python`；其余参数与首轮相同：`--env`、`--runs`、`--max-calls`、`--out`、`--report-dir`、`--reply-format`）：

```
# 修订 n 的前后对比：在开发集上按修订前后两个版本各跑 N 次（调用次数是首轮的两倍），写 revision-<n>.{json,md}
python diandian/compare.py --candidates 清单.yaml --revision <n> [--revisions-dir diandian/revisions] --out <仓库外新目录> --report-dir reports/r2-<日期>
# 二轮报告：用修订链最终采纳的版本跑开发集，写 report.{json,md}；读同一报告目录里的 revision-*.json
python diandian/compare.py --candidates 清单.yaml --r2 [--revisions-dir diandian/revisions] [--holdout-summary 汇总.json] --out <仓库外新目录> --report-dir reports/r2-<日期>
```

- **修订判定**：前后对比的列与 persona-evaluator 七项一致（身份披露、禁语 A–G、转介话术、出场协议、星图话术、跨科静默、比喻隔离；点点没有跨科静默、比喻隔离用例，记“未运行”），
  另加“其余类别”与“禁语触发率”。任一候选任一项下降，或修订后禁语触发率 > 0 → **不采纳**。判定看每个版本实际下发的输出（有后处理即 Prompt + 后处理），只 Prompt 的禁语触发率另列参考。
  同一版 Prompt 用于所有候选（按模型分叉是替代方案 R-1）。修订编号必须从 1 连续、至多 3；一次修订只能改 Prompt 或后处理之一；
  test 以外的环境要求修订记录写明 Prompt 文件路径（`docs/personas/prompts/` 下）与 sha256，文件签字后被改过就拒绝。
- **修订链**：第 1 次的修订前是起点；之后每次的修订前必须等于当时的基线（最近一次采纳的修订后）；二轮用最终基线。报告目录里的 `revision-<n>.json` 记着修订记录的 sha256，
  修订记录在生成结果之后被改过，二轮拒绝运行。二轮的最终版本必须带后处理（两组数字）。
- **两组数字**：同一次调用的原始回复（只 Prompt）与后处理后的回复（Prompt + 后处理）分别评分，不混算、不重复调用；多轮用例第 2 轮起，模型看到的历史是后处理后的回复（与上线一致）。
  后处理把红线清零时，“只 Prompt”一组的红线照样逐条列出。
- **验收判定**：红线全过 且 每家核心通过率 ≥ 下限 且 核心通过率两两差最大 ≤ 上限 → “达到验收口径”，否则 → “需选替代方案”；默认按“Prompt + 后处理”一组，另一组的判定作参考，
  两组不同时报告写“达标与否依赖后处理”。只有两家候选时报告注明依据 ADR 08 第 3 节第 8 条。工具判定只按开发集；**结论由人定**（Gate 0），签字栏留空。
- **留出集**：默认不跑（规则 14）。教研负责人在封存环境跑完后，只交回汇总 JSON（`{"version": 1, "sealed_by", "date", "candidates": {候选: {"prompt_only": {"core_rate", "redline"}, "prompt_plus_post": {...}}}}`，
  只收比率与计数，不收用例编号），用 `--holdout-summary` 放进报告的汇总位。
- 报告只含汇总、用例编号、检查点编号与失败样例的学生侧合成输入（用例原文），不含回复原文与命中位置。
- `make eval-persona` 已包含后处理、修订、两组数字、验收判定与二轮报告的测试（`tests/test_r2.py`）和 24 条变异；不联网、不需要密钥。

### 真实首轮 + 二轮执行手册（本任务不执行，交接给人）

前提清单，全部满足才能跑（前 4 条同首轮）：
1. **ADR 04 / 08 候选名单签字**（只比较境内候选；只有两家时比两家）。
2. **供应商登记**：进 `docs/compliance/vendors.md`（境内已备案）并由人另起 PR 加进模型路由配置对应环境的 T1 路由（必停项，`[NEEDS-HUMAN]`）。
3. **人设 Prompt v0 签字**：人设负责人提交 `docs/personas/prompts/diandian-v0.md`，并约定回复格式（`--reply-format`）与用例上下文的注入方式。
4. **执行人在有凭据的环境**（dev 或 staging）运行，凭据只放执行环境变量；先不带 `--max-calls` 看预计次数，再由人定预算。
5. **后处理的替代句**：`safe_reply` 由人设负责人提供并签字（没提供之前，“Prompt + 后处理”一组里拦截后的回复是占位，只能用于评测、不能上线）；信号规则与验收口径经人设负责人确认。
6. **留出集**由教研负责人重新封存（规则 14），并约定汇总交回方式。

步骤：
1. **首轮**：`python diandian/compare.py --candidates <签字清单> --env <dev|staging> --runs 3 --max-calls <预算> --out <仓库外新目录> --report-dir reports/r1-<日期>`，提交 `reports/r1-<日期>/`。
2. **修订（至多三次，每次签字）**：人设负责人读首轮差异报告，写 `diandian/revisions/<n>.yaml`（假设、对象、前后版本；Prompt 修订另提交 `docs/personas/prompts/diandian-v<k>.md` 并在记录里写 sha256）；
   执行人运行 `--revision <n> --report-dir reports/r2-<日期>`（预算按两倍）；人设负责人审 `revision-<n>.md`，在记录的 `signoff` 签字，决定是否采纳后再做下一次。
3. **二轮**：`--r2 --report-dir reports/r2-<日期>`（同一目录，读修订结果）；教研负责人交回留出集汇总后，再带 `--holdout-summary` 重跑报告生成（或补跑一次）。
4. **提交**：`reports/r2-<日期>/`（`report.{md,json}`、`revision-<n>.{md,json}`）与签字后的修订记录；`--out` 下的回复原文、逐次评分与 `calls.jsonl` 核对后删除，不入库、不外传。
5. **决策记录签字**：按 `specs/diandian-consistency-r2-sprint3/adr-08-decision-record.md` 第一部分填写依据、勾选结论、签字；英语部分搁置，待英语方案重定。ADR 08 状态由人另开 PR 修改。

## 人设治理组件（冲刺 3-F，`specs/persona-governance-sprint3/`）

目标：仓库里每一份老师或点点的 Prompt 都登记在册、带版本与指纹；改了 Prompt、禁语表或人设规格，CI 必然跑人设回归，拿不到“跳过”的绿灯；
禁语分类器按角色判定；七类家族级回归与长对话漂移都能在**不持有任何模型密钥**的 CI 上运行，并产出 Gate 0 证据用的运行报告。

| 位置 | 内容 | 谁能改 |
|---|---|---|
| `packages/personas/registry.yaml` | 登记表：角色（点点、阿鸦、妙笔、方圆、春秋、雅典娜；阿基、X 博士未立项）的类型、学科、规格书与版本、适用禁语类、Prompt、称谓、比喻与设定元素、转介话术、出场时刻；`tasks` 登记非人设的模型 Prompt（知行、文砺判分，KGE-03 编纂；代码常量按“文件 + 符号名”登记，只做版本与指纹锁） | 工程登记；判定依据（从规格书摘录的字段）**人设负责人与教研确认**（必停项） |
| `packages/personas/prompts.lock` | 指纹锁（规范化后的 SHA-256）；只能由 `make persona-lock` 改写，PR 里由人审 diff | 命令生成 |
| `packages/personas/forbidden/` | 禁语词表唯一真相源：A–E（点点词表原样迁入）、`F-math`（阿鸦 V1.1 §5.2）、`F-chinese`（妙笔 V1.0 §5.2）、`G`（考官型基线 §5.2，另含 §5.1 句式与 §2.1 出场）、`platform`（七科规范合同第 3、5、8 条）；每条注明出处与 verbatim / inferred，不能落成词面的条目标 context 或 model；`manifest.yaml` 的 version 是 lexicon_version | **禁语表是必停项**：首个 PR 首行 `[NEEDS-HUMAN]`，教研签字前 `reviewed: false` |
| `packages/personas/guard/` | 禁语分类器：规则层 + 结构上下文层（模型判定层只登记、不执行）；`python -m personas.guard scan --persona platform --text … / --stdin-json / 文件` 输出 JSON（给前端文案单测等非 Python 调用方） | 工程 |
| `evals/persona/governance/` | 七类家族级检查器（静态面 + 转写面）、漂移检查器（窗口 5 轮）、N16 泄露探针、录制回放、报告与运行器 | 工程 |
| `evals/persona/cases/{family,drift,leak}/` | 种子用例：手写合成转写，每类检查至少一正一反；每个已立项角色一条 20–40 轮漂移脚本 + 反例变体。**不是金标准**（`source: spec-derived / synthetic`，待教研确认） | 工程起草，QA 按各规格书第八章扩写 |
| `evals/persona/contract-matrix.yaml` | 合同对照表草稿：老师 × 十条合同 → 规格书章节 + 用例；空格即失败，未立项记“未运行” | 工程起草，**人确认** |
| `evals/persona/recordings/<persona_id>/<case_id>.json` | 录制回放（格式 `schema/recording.schema.json`）；本冲刺没有真实录音 | **持有 dev 密钥的人**录制并提交 |
| `evals/persona/schema/` | `report.schema.json`（报告）、`recording.schema.json`（录音）、`gold.schema.json`（金标准格式说明） | 工程 |
| `evals/persona/gold/` | 金标准（本冲刺不写任何 gold 数据；hook 拦截写入） | **只能教研写** |
| `evals/persona/fixtures/governance/` | 合成登记表、合成 Prompt 与合成录音：只用来证明“改 Prompt 不升版本 → 失败；升版本不重录 → 严格失败‘需要重新录制’；补录音 → 通过” | 工程 |

命令（仓库根目录）：

- `make persona-registry-check`（进 `make check`）：登记的文件与符号存在、版本合法、`prompt_id` 不重复、代码里的版本常量与登记一致；
  Prompt 或禁语词表内容变了而版本没升 → 失败“Prompt 变更必须升版本”；指纹与锁文件不一致 → 失败“运行 make persona-lock 并跑回归”；
  扫描 `adapters/ services/ packages/ apps/` 里未登记的 Prompt（`*.prompt.*`、`prompts/` 目录、以“你是 / 你扮演”开头的长字符串、
  内容不是短字面量的 system 消息），可在同一行或上一行写 `persona-registry: ignore <原因>` 豁免（豁免列进输出与报告）；
  另核对禁语词表旧位置镜像与新位置一致。
- `make persona-lock`：重算锁文件（内容变了而版本没升时拒绝）。改 Prompt 的流程：改内容 → 升 `prompt_version` → `make persona-lock` → 重新录制 → `make eval-persona` → PR 附前后对比。
- `make persona-guard-test`（进 `make check`）：分类器与登记表单测。
- `make eval-persona [ROLE=角色] [BASE=提交]`：在点点评测之后跑 `python -m governance run`，写 `$(PERSONA_REPORT_DIR)/persona-report.{json,md}`
  （默认 `$TMPDIR/persona-report`）。每次都跑：检查器自检（合成正反例判错即失败）→ 静态面 → 录制回放 → 基线（`BASE`，默认 PR 目标分支或上一个提交；
  用基线提交自己的运行器在临时目录再跑一遍）→ 影响集（`git diff <merge-base>`，命中 Prompt、登记表、锁文件、禁语表、`docs/personas/`、`evals/persona/` 标“人设变更”）。
  录音缺失或过期：严格模式（`CONTRACT_STRICT=1`，CI）失败并提示“需要重新录制”；非严格打印“跳过（缺录音）”、报告记“未运行”（退出码 3）。
  `ROLE` 只跑一个角色并跳过变异测试（persona-evaluator 子代理用）。
- 报告：md 第一屏是前后对比表，列与 persona-evaluator 的七项一致（身份披露、禁语 A–G、转介话术、出场协议、星图话术、跨科静默、比喻隔离），
  可以直接贴进 PR 描述；“未运行”不记为通过；另列家族级七类、漂移、泄露、禁语命中（只列编号与位置）、判定来源占比、录音状态、规格冲突与扫描豁免。
  CI 把报告作为 artifact `persona-report` 上传，保留 90 天（Gate 0 取最近一次 `main` 运行）。

### 录制回放（第 2 层；本冲刺不执行录制）

录制由持有 dev 密钥的人在本地执行（Claude Code 不持有密钥、不执行录制；CI 永不持有密钥，只回放）：

1. 前置：ADR 04 签字后的境内供应商登记进模型路由；人设负责人提交并登记 Prompt（`registry.yaml` 的 `prompts`，升版本并 `make persona-lock`）；
   在 `evals/persona/cases/` 写 `kind: recorded` 用例（只写学生轮与要跑的检查，输入全是合成数据）。
2. 录制：`make persona-record ROLE=<角色>`（目前是占位，录制脚本随第一份真实 Prompt 一起写）——对每条 recorded 用例调用一次模型，
   用 `governance.replay.make_recording` 组装录音：该角色全部登记 Prompt 的指纹与版本、用例指纹、`model_version`、生成参数、录制人、日期、`data_level: L0`、`synthetic: false`。
3. 提交 `evals/persona/recordings/<角色>/<用例>.json`，由人审 diff；录音里只有合成输入与模型对合成输入的回复，不含任何真实学生数据。
4. 之后 Prompt 或用例一改，指纹就不匹配，严格模式失败“需要重新录制”——Prompt 变更因此一定会触发一次真实回归，只是由持有密钥的人来执行。

带密钥的手动工作流（第 3 层，`persona-record.yml`）本冲刺不做，要等 ADR 04 / 09 签字与人确认（spec 待人确认 #2、#3）。
