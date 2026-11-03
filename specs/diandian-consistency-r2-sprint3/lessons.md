# 冲刺 3-I 点点跨模型一致性二轮 · lessons

状态：工具与流程实现完成，待人审（2026-09-30）。spec 待人确认 #1–#7 按默认执行。PR 首行 `[NEEDS-HUMAN]`（后处理引用规格原句、验收口径、决策记录草稿）。
真实首轮与二轮没有跑（候选签字、供应商登记、人设 Prompt v0 都不在）；本任务只用 test 环境假供应商证明流程。

## 实现要点

1. **后处理 v0**（`evals/persona/diandian/postproc/`，`diandian-post@0.1.0`，确定性）
   - 顺序：学生这一轮命中情感信号 → 整段换成转介句、置 `fields.referral = true`；否则回复命中点点适用的禁语 A–E（3-F `personas.guard` 规则层，`classes=A–E`）→ 整段换成 `safe_reply`；
     最后，首轮对话（`context.first_contact` 且第 0 轮）回复里没有身份披露（与评分器同一条 `identity_disclosure` 模式）→ 在最前面加披露句。
   - 替代句只在 `replacements.yaml`：`disclosure` = 备考搭子 V1.0 §4.1 原句，`referral` = §4.9 原句（点点 V1.2 第一章、第四章“原样继承”）；加载时逐字核对出处文件
     （只允许 `docs/personas/`、`docs/subjects/` 下的规格文档），不在原文里就拒绝加载。`safe_reply` 规格没有原句 → 占位 `{{SAFE_REPLY}}`，报告头部与“后处理”一节标“替代句待人设负责人提供”。
   - 动作记录只有动作名、禁语类、规则编号、信号编号，不含原文。
2. **两组数字**：`compare.py` 在同一次调用里同时留原始回复（只 Prompt）与后处理后回复（Prompt + 后处理），分别评分（`run-k/` 与 `run-k-post/`），不混算、不重复调用。
3. **修订流程**：`diandian/revisions/<n>.yaml`（`diandian/schema/revision.schema.json`）+ `compare.py --revision <n>`：修订前后两个版本各跑 N 次，输出 `revision-<n>.{json,md}`；
   列 = persona-evaluator 七项 + 其余类别 + 禁语触发率；任一候选任一项下降或修订后禁语触发率 > 0 → 不采纳。编号连续、至多 3 次、一次只改一个对象；
   二轮时核对修订链（每次修订前 = 当时基线）与修订记录 sha256（生成结果后被改过就拒绝）。
4. **二轮报告**（`diandian/r2report.py`，`compare.py --r2`）：两组数字、七项表、两两差与区间（复用首轮 `scorer.compare_report`）、红线两组分别单列、修订记录、留出集汇总位、
   后处理说明、失败样例（学生侧合成输入）、验收判定（`r2_criteria.yaml`）。确定性输出。
5. **流程样例** `evals/persona/reports/r2-sample-fake/`：三家假候选（总合规 / 转介类稳定失败 / 随机失败）；修订 1（后处理 none → v0）采纳，修订 2（Prompt v0 → v1，
   用 test 专用 `fake_profiles` 让总合规那家在星图类变差）因星图话术下降不采纳；二轮判定“需选替代方案”（后处理后核心两两差最大 8.2 个百分点）；
   `scenario-pass/`（两家：总合规 + 转介失败）判定“达到验收口径”，“只 Prompt”参考判定为“需选替代方案”，报告标“达标与否依赖后处理”。头部都标“流程样例，不是评测结论”。
6. **测试** `evals/persona/tests/test_r2.py`（69 条）：后处理正反例、替代句逐条核对出处与章节、没有新造话术（开发集全部用例 × 合规 / 失败假回复逐条核对输出组成）、
   两组数字与手算一致（含“只 Prompt”与首轮样例逐项相等）、红线清零而“只 Prompt”照报、修订采纳 / 不采纳与手算一致、修订记录格式与链、验收判定边界（恰好 5 个百分点、恰好 90%）、
   两种场景、留出集汇总位、确定性与入库样例逐字节一致、不含回复原文与模型标识名、模块级夹具禁止任何网络调用。
7. **变异**：`evals/persona/scripts/mutations.py` 新增 24 条（`R2` 组，只跑 `test_r2.py`）：后处理漏拦、拦截后造新句、替代句不核对原文、占位不查格式、缺披露不注入、
   非首轮也注入、情感信号不转介、转介不置字段、两组数字混算（两个方向）、修订下降仍采纳、禁语触发率 > 0 仍采纳、修订次数上限不查、前后两版同一行为、修订链不核对、
   记录被改不核对、Prompt 指纹不核对、差值阈值错、核心下限不查、红线不进判定、判定按只 Prompt 一组、红线未单列、占位未标出、留出集汇总收用例级字段。全部被发现。

## 偏差（相对 spec 或任务说明）

1. **“只 Prompt”一组在多轮用例第 2 轮起不是纯 Prompt**：两组来自同一次调用，对话历史里写的是后处理后的回复（与上线一致），所以第 2 轮起的原始回复是在“后处理过的历史”上生成的。
   单轮用例不受影响。另一个做法是两组各跑一次（调用翻倍），待人确认（待人确认 #3）。
2. **修订判定看“实际下发的输出”**：有后处理的版本用 Prompt + 后处理的数字判“下降”和“禁语触发率 > 0”；只 Prompt 的禁语触发率另列参考、不参与判定。
   否则只要模型本身出过一次禁语，任何修订都不能采纳，后处理就无从引入。待人确认 #4。
3. **验收判定默认按“Prompt + 后处理”**（`r2_criteria.yaml` 的 `verdict_basis`，可改为 `prompt_only`）；另一组判定作参考并在报告标“达标与否依赖后处理”。待人确认 #5。
4. **“其余类别”列也参与“下降”判定**（记忆、世界观、漂移、根因）：七项之外的退步同样拦下。点点没有跨科静默、比喻隔离用例，这两列记“未运行”、不参与判定
   （与 3-F 的 `DIANDIAN_TO_CATEGORY` 同口径，世界观类不映射到比喻隔离）。
5. **拦截是整段替换**，不是逐句删改（删改会拼出新句子，等于造话术）。
6. **修订记录是输入，判定结果写在报告目录**：`revisions/<n>.yaml` 只含人写的假设、版本、签字栏；前后对比与是否采纳由工具写进 `revision-<n>.{json,md}`（带修订记录 sha256）。
   签字人在审阅结果后填 `signoff`；签字会改变文件 sha256，**签字后要重新运行 `--revision <n>`**，否则二轮会以“修订记录被改过”拒绝。这一步略繁琐，
   另一种做法是 sha256 计算时排除 `signoff` 字段，待人确认 #6。
7. **留出集汇总只能在跑 `--r2` 时带入**：没有“只重排报告不重跑模型”的开关；教研负责人交回汇总晚于二轮时要补跑一次（真实模型结果会有波动）。需要的话后续加离线重排。
8. **ADR 08 决策记录的替代方案**：旧稿 R-1…R-4 中 R-3、R-4 是英语链路方案，随英语搁置移到第二部分；点点部分的 R-3（加强后处理后重跑）、R-4（推迟该项、扩充用例或换候选）
   是本任务按点点一致性场景重列的，待决策人确认（待人确认 #7）。
9. `evals/persona/diandian/r2report.py` 放在 `diandian/` 下（不放 `scorer/`），首轮 `scorer/compare_report.py` 没有改动，只被复用。
10. **Makefile 没有改**：`make eval-persona` 本来就跑 `evals/persona/tests` 全部测试与 `scripts/mutations.py`，新测试与新变异自动纳入。

## 待人确认

1. **后处理替代句**：`safe_reply` 需人设负责人提供并签字（必停项）；`disclosure` 用的是备考搭子 V1.0 §4.1 原句，里面“陪你备考的搭子”“这门课”是道法时期的措辞，
   点点 V1.2 声明原样继承，是否要为跨科场景换句由人设负责人定（换句属人设变更）。登记表 `packages/personas/registry.yaml` 里点点的 `disclosure.text` 是 §2.1 的要求短语
   “这是 AI 扮演的陪练角色”，与后处理用的 §4.1 整句不同源，是否统一待定。
2. **情感信号规则**（`postproc/signals.yaml`，`reviewed: false`）：规格没有信号清单，这里按 §4.9“超出学习范畴的情绪困扰”与点点 §5.3（情绪内容、家庭事件、人际矛盾）分五类做规则近似，
   正反例是自写合成短句。开发集上：转介负向用例全部命中，其余用例零误报。**说明**：用例按类别一个文件，`referral.yaml` 里开发集与留出集混在一起，实现时读过这个文件；
   规则按规格条文分类编写、没有按留出集调参，测试只用开发集与自写短句，但严格意义上构建方已接触过留出集原文，建议教研负责人正式评测前重新封存（1-I 已约定，规则 14）。
   金标准到位后按召回 ≥ 95%、精确 ≥ 90% 校准。
3. 偏差 1（两组同一次调用 vs 各跑一次）。
4. 偏差 2（修订判定看实际下发的输出）。
5. 偏差 3（验收判定默认按 Prompt + 后处理）与 `r2_criteria.yaml` 的下限 90%、差值 5 个百分点、红线 0（spec 待人确认 #4）。
6. 偏差 6（签字后需重跑修订，或 sha256 排除签字栏）。
7. 偏差 8（决策记录点点部分的 R-3、R-4）。
8. **后处理与首轮待人确认 #1（回复格式）**：后处理会补上 `referral` 结构字段，但 `handoff`、`root_causes` 仍依赖回复格式；真实输出格式随人设 Prompt v0 一起定。
9. **拦截后的 `{{SAFE_REPLY}}` 在评分里按“无禁语”计**：评测里拦截等于合规；上线前必须换成人设负责人提供的句子（否则学生看到占位）。
10. 自助法次数仍是首轮的 1000 次（旧稿 10000 次），沿用 2-I 待人确认。

## 运行记录（2026-09-30，本地）

- `make eval-persona CONTRACT_STRICT=1 PYTHON=services/learning/.venv/bin/python`：通过（用例校验、评分器自检、pytest 206 条、人设治理回归 74/74、
  变异 109/109 全部被发现：点点评分器 44、人设治理 41、一致性二轮 24）；
- 新增测试 `tests/test_r2.py` 69 条；`make persona-registry-check`：通过；
- 严格 `make check`、安全审查、契约审查**没有跑**（spec 验收最后两条留给主会话）。

## 改动文件

- 新增：`evals/persona/diandian/postproc/{__init__.py,replacements.yaml,signals.yaml}`、`evals/persona/diandian/r2report.py`、`evals/persona/diandian/r2_criteria.yaml`、
  `evals/persona/diandian/schema/revision.schema.json`、`evals/persona/diandian/revisions/sample-fake/{1,2}.yaml`、`evals/persona/diandian/candidates/sample-fake-pass.yaml`、
  `evals/persona/reports/r2-sample-fake/`（`report.*`、`revision-{1,2}.*`、`scenario-pass/report.*`）、`evals/persona/tests/test_r2.py`、
  `specs/diandian-consistency-r2-sprint3/{adr-08-decision-record.md,lessons.md}`；
- 修改：`evals/persona/diandian/compare.py`（抽出公共步骤；`run_candidate` 支持后处理；`--revision`、`--r2`；首轮输出逐字节不变）、`evals/persona/scripts/mutations.py`（R2 组）、
  `evals/persona/README.md`（二轮一节与执行手册）。
