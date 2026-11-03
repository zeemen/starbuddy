# 冲刺 3-F 人设治理组件 · lessons

状态：实现完成，待人审（2026-09-30）。spec 待人确认 #1–#14 按默认执行。
提交方式（spec 状态行）：**禁语种子词表与词表收拢单独一个 PR，首行 `[NEEDS-HUMAN]`**（清单见文末“提交分组”）；其余组件另一个 PR。

## 实现要点

1. **登记表与指纹锁**（`packages/personas/registry.yaml`、`prompts.lock`、`personas/registry.py`）
   - 角色：点点、阿鸦、妙笔、方圆、春秋、雅典娜（`status: active`），阿基、X 博士（`status: not_started`）。`prompts` 全部为空——仓库里还没有任何真实人设 Prompt，本冲刺不写。
   - `tasks` 登记三份非人设 Prompt：`zhixing-m3-grade`（`adapters/zhixing/src/core/m3-prompt.ts`）、`wenli-essay-grade`（`adapters/wenli/src/core/essay-prompt.ts`）、
     `kge-compile-kp`（`services/knowledge/knowledge/compiler/full.py`）。它们都是代码常量，登记“文件 + 符号名”，指纹按符号源码（规范化后）计算；
     `version_symbol` / `template_symbol` 要与代码里的 `PROMPT_VERSION` / `PROMPT_TEMPLATE` 一致（改了代码版本没改登记，或反之，都失败）。
   - 检查四项 + 一项：文件与符号存在、版本合法、`prompt_id` 不重复；内容变了版本没升 → “Prompt 变更必须升版本”；与锁文件不一致 → “运行 make persona-lock 并跑回归”；
     未登记 Prompt 的启发式扫描（`*.prompt.*`、`prompts/` 目录、句首“你是 / 你扮演”的 ≥ 40 字字符串、内容不是短字面量的 system 消息），`persona-registry: ignore <原因>` 豁免且必须写原因；
     另核对禁语词表旧位置镜像。禁语词表整体也进锁文件：任何词表文件变了，`manifest.yaml` 的 version 要升。
   - `make persona-lock` 在“内容变了而版本没升”时拒绝写锁，否则“必须升版本”会被锁文件绕过。
2. **guard 分类器**（`packages/personas/guard/`、`lexicon.py`）：规则层（词表 + 正则、句长、疑问句）+ 结构上下文层（`hint_overreach`、`unverified_claim`、
   `off_moment`、`star_outside`、`uncited_quote`、`full_generation`，只有调用方给了 context 才判）；模型层只登记为 `pending_model_rules`。
   按登记表 `forbidden_classes` 判定，平台级条目按角色类型生效；输出 class / rule_id / span / method / confidence / lexicon_version，不回显原文。
   命令行 `python -m personas.guard scan|rules|export-kge` 输出 JSON。词表加载 fail closed（YAML 坏、正则不合法、缺正反例、context 条目的 check 未实现、rule_id 重复都报错）。
3. **词表收拢**：点点 A–E 原样迁入 `packages/personas/forbidden/{A..E}.yaml`（id、origin、styles、regex、source、`reviewed: false` 逐条不改，只把键名改为
   `rule_id` / `pattern`，补 `method` 与合成正反例），规则模式迁入 `packages/personas/patterns/diandian.yaml`。点点评分器改读新位置；
   `evals/persona/tests/test_lexicon_migration.py` 用迁移前的加载方式读旧文件，在 120 条用例、自检夹具与新词表正反例的全部字符串上逐条比对 (rule_id, start, end) 与规则模式命中：一致。
   KGE-03 `knowledge/lint/lexicon.py` 改为复用 `guard.export_kge`（角色名、称呼、口头禅、A–G），不再从 `docs/personas` 抽取；
   `services/knowledge/tests/fixtures/lexicon-before-persona-guard.json` 是迁移前导出的快照，测试逐条比对（差异见偏差 5）。
4. **F:math、F:chinese、G、platform 种子**：只从规格书原文逐条摘录。原文加引号的说法 → 词面规则（`literal: true`，`origin: verbatim`，rule_id 沿用 KGE-03 的
   F-aya-01…11、F-wenli-01…13、G-01…12）；没有具体说法的条目 → `context`（已实现的结构检查）或 `model`（本冲刺不执行）。
5. **七类家族级检查器**（`evals/persona/governance/family.py`）：静态面（合同对照表、一科一位、场景 Prompt 无判分讲解指令、老师 Prompt 不提别科、
   叠字与直呼名、比喻与设定元素两两不相交、考官型 Prompt 声明的出场时刻 ⊆ 角色卡、转介话术结构）+ 转写面（跨科静默、称谓、比喻隔离、三型边界、
   考官型非判分环节发言计数、转介结构与 `referral_triggered`）。合同对照表草稿 `evals/persona/contract-matrix.yaml`（`reviewed: false`，待人确认）。
6. **漂移与 N16**（`drift.py`）：20–40 轮、窗口 5 轮；逐轮 guard、首轮且仅首轮披露、点点风格标签窗口一致与课堂内不讲题、常驻型设定元素年级上限与越界话题、
   考官型句式（guard G-S-01/02）与形容词副词近似、退场后不再开口。N16：标准答案（规范化后）在 `allowed_hint_level < reveal_level` 的讲解里出现即泄露。
   每个已立项角色一条漂移脚本（点点 24 轮、阿鸦 22、妙笔 21、三位考官各 20），外加按子项失败的反例变体（共 45 条漂移用例）。
7. **回放框架**（`replay.py`、`schema/recording.schema.json`）：录音附该角色全部登记 Prompt 的指纹与版本、用例指纹、`model_version`、参数、录制人、日期、`data_level: L0`、
   `synthetic`；状态 valid / outdated（Prompt 指纹、版本、用例指纹或学生输入不一致 → “需要重新录制”）/ missing / invalid（格式不合法，任何模式都失败）。
   合成夹具 `evals/persona/fixtures/governance/`（合成登记表、合成 Prompt、两条合成录音，`model_version: synthetic-replay-0`）证明框架本身。
   `make persona-record` 只是占位（打印说明、退出码 2）。
8. **报告**（`report.py`、`schema/report.schema.json`）：角色 × 16 类（persona-evaluator 七项 + 家族级七类 + 漂移 + 泄露），`total / passed / failed / not_run / pass_rate /
   baseline_pass_rate / delta`；禁语命中只列编号与位置；判定来源占比；录音状态；检查器自检；规格冲突；扫描豁免；结论。md 第一屏是前后对比表。
   基线：`git archive <BASE>` 导出 `packages/personas`、`evals/persona`、`docs/personas` 到临时目录，用基线自己的运行器跑（基线早于本冲刺时报告写明）。
9. **Makefile / CI**：新增 `persona-registry-check`、`persona-guard-test`（都进 `make check`）、`persona-lock`、`persona-record`；`eval-persona` 在原有点点评测之后
   跑 `python -m governance run [--role] [--base] [--strict] --out $(PERSONA_REPORT_DIR)`，变异测试扩到 85 条并改为并行 4 路（`ROLE` 时跳过变异）。
   `check.yml`：checkout `fetch-depth: 0`；新增 `make eval-persona CONTRACT_STRICT=1` 一步；`actions/upload-artifact@v4`（与现有 actions 一样按版本标签固定）
   上传 `persona-report`，`retention-days: 90`，`if: always()`。

## 验收标准 ↔ 测试

| 验收 | 测试 |
|---|---|
| 改合成 Prompt 不升版本 → `make check` 失败 | `evals/persona/tests/test_governance.py::test_acceptance_1_*`（含命令行退出码）、`packages/personas/tests/test_registry.py::test_change_without_bump_*`、`test_cli_exit_codes` |
| 升版本不重录 → 严格 `eval-persona` 失败“需要重新录制”（非严格退出码 3、记“未运行”） | `test_acceptance_2_bump_without_rerecord_fails_strict`、`test_cli_writes_report_and_exit_codes` |
| 补合成录音 → 通过，`changed` 列出 prompt_id | `test_acceptance_3_rerecord_passes_and_changed_lists_prompt`（临时 git 仓库，基线 HEAD~1 真的用基线运行器跑一遍） |
| 未登记的“你是……” system 长字符串 → 失败 | `test_registry.py::test_unregistered_system_string_fails`、`test_ignore_marker_*`、`test_scan_heuristics` |
| 种子正例 100% 命中、反例 0 误报；“差一点”方圆命中、点点不命中 | `test_guard.py::test_seed_examples_positive_hit_negative_clean`（每条非模型规则）、`test_same_sentence_differs_by_persona` |
| 结构层三种情况各一正一反 | `test_guard.py::test_context_layer_positive_and_negative`（越级提示、无验证字段的对错陈述、考官型非判分时刻） |
| 七类家族级、漂移各至少一正一反 | `evals/persona/cases/`（运行器的检查器自检 74/74）、`test_cases_cover_every_check_with_positive_and_negative`、静态面每类的正反例测试；合同符合性是纯静态面，正反例在 `test_static_contract_empty_cell_fails` / `test_static_clean_on_repo` |
| 变异测试 | `evals/persona/scripts/mutations.py`：新增 41 条（guard 10、登记表 6、家族级 13、漂移 6、泄露 1、回放与结论 5），加原有 44 条共 85 条全部被发现；KGE 侧改写 1 条、新增 1 条 |
| 报告通过 schema 校验 | 运行器每次写报告前自校验（不符即失败）；`test_runner_on_repo_passes_and_report_validates` 等 |
| `ROLE=aya BASE=…` 输出前后对比表，列与 persona-evaluator 七项一致 | `test_runner_on_repo_passes_and_report_validates`（表头逐字比对）、`test_role_filter_limits_results`；本地 `make eval-persona ROLE=aya BASE=HEAD` 已跑 |
| 不读密钥、不联网 | `packages/personas/tests/conftest.py` 与 `test_governance.py` 的自动夹具：每个测试都把 socket 的 connect / create_connection / getaddrinfo 换成抛错 |

## 偏差（相对 spec 或任务说明）

1. **平台级类名 `platform`**：spec 的输出类只有 A–E、F:math、F:chinese、G。七科规范合同第 5 条（“粗心”“马虎”“不认真”全平台禁用）、第 3 条（对错陈述要有验证）、
   第 8 条（星只在两处）需要对全部老师生效、又不属于任何一科，登记为 `class: platform`（`applies_to_types: [resident, examiner]`）。要不要另起字母，待人确认。
2. **G 类收了 §5.2 清单以外的三条**：G-S-01（§5.1 每句 ≤ 25 字）、G-S-02（§5.1 只有陈述句，按句末问号近似）、G-CTX-01（§2.1 只在判分时刻出场）。
   spec 三层表把“考官型句长 > 25 字”“考官型在非判分时刻开口”列为 G 类的规则层 / 结构层例子，这里照做；文件头与出处写明它们不在 §5.2 的清单里。
3. **KGE 编纂 Prompt 版本是整数**：代码里 `PROMPT_VERSION = "1"`（KGE-03）不是 semver。没有改代码（改了会动编纂请求与测试），登记为 `version_scheme: integer`，
   只允许 `type: task` 这样写。契约 `persona.prompt_version` 要求 semver 只对人设 Prompt 生效。
4. **旧词表位置不是纯引用文件**：`apps/web/tests/probe.test.ts`、`diagnosis.test.ts` 用行解析读 `evals/persona/diandian/lexicon/forbidden.yaml` 的 `forbidden:` 段，
   而本任务不能改 `apps/web`。旧文件保留原内容作只读镜像，加 `moved_to: packages/personas/forbidden/` 与迁移说明；`persona-registry-check` 与 guard 测试核对它与新位置逐条一致。
   前端改为调用 guard 后删除旧文件与 `manifest.yaml` 的 `legacy_mirror`（见“待前端接入”）。
5. **KGE 页面检查词表的两处有意差异**（其余逐条一致，有快照测试）：
   - 称呼表多了“方圆”“春秋”“雅典娜”（登记表按考官型基线第六章记“点点直呼老师名”）。它们本来就在角色名里，页面命中结果只多一条“称呼”类提示；
   - 口头禅不再含“你为什么不……”：旧抽取按“主句式”所在行抽引号，把阿鸦规格书 §3.2 的反例（“而不是‘你为什么不……’”）误当成了口头禅。
   - `platform` 类、结构层、模型层条目不导出给 KGE（页面不是角色输出，口径与 KGE-03 一致）。
6. **转介“真实资源”用更宽的正则**：点点评分器的 `referral_resources` 只覆盖点点句式，阿鸦的“找你们数学老师或者班主任”匹配不上；家族级检查用
   “动词 + 8 字内 + 资源名词”（`governance/family.py` 的 `RESOURCE_RX`）。点点评分器的模式不变。
7. **八年级设定元素上限**：阿鸦 §5.3、妙笔 §5.3 只写了七年级 3 次、九年级 1 次，八年级按 §2.2 反滥用规则取 3（登记表 `setting_caps` 注明）。
8. **近似规则**（都待金标准校准）：`full_generation` 在引擎没标 `generated_passage` 时按“单段连续正文 ≥ 150 字”近似；考官型“无形容词、副词”按一张程度副词与评价形容词短表近似；
   对错陈述按一张词表（含考官型的“命中 / 达标”）近似。
9. **场景角色**：仓库里还没有登记的场景角色（英语 NPC 在已搁置的 echo，道法情境卡角色由点点临时扮演），`type: scene` 的静态面与转写面用合成登记表测试。
10. **录音记该角色全部 Prompt 的指纹**（`prompt_fingerprints` 字典），而不是单个指纹：一个角色将来可能有多份 Prompt（人设层 + 情境卡），任一份变了都要重录。
11. **点点 120 条用例接入回放**：点点登记 Prompt 后，开发集每条用例都要有录音（留出集不录、不跑，规则 14），用现有评分器判定并归入报告各栏；现在点点没有 Prompt，这条路径只做了实现，
    没有真实数据可跑。
12. **CI 会跑两遍 eval-persona**：新增的 CI 步骤一遍（写报告并上传），`make check` 里再一遍。变异测试改为并行 4 路后单遍约 3–6 分钟；CI 总时长若吃紧，可改为 CI 步骤只跑报告部分，待人决定。
13. **eval-persona 仍用学习服务的虚拟环境**（沿用 1-I；离线全流程要用模型路由的假供应商）；`persona-registry-check` 与 `persona-guard-test` 用 `$(PYTHON)`（CI 是 `.venv-contracts`），
    只需要 PyYAML 与 pytest，没有新依赖。
14. **合同对照表的考官型第 8 条**：考官型基线没有单列“星”的用法，对照表写“第一章：不讲题（规格未单列星的用法，按‘不用星讲题’的前提处理，待人确认）”，不是留空。

## 规格冲突清单（spec 待人确认 #9；报告里标“规格冲突”，不算失败，不修改规格）

- **(a) 转介对象不同源**：七科规范合同第 10 条写“学校心理老师、班主任与家长”；阿鸦 V1.1 §4.10 是“你们数学老师或者班主任”，妙笔 V1.0 §4.9 是“你们语文老师或者班主任”，
  都没有心理老师和家长（静态面自动检出，报告逐角色列出）。
- **(b) F 类重名**：阿鸦、妙笔各自定义了“F 类”，本冲刺按 `F:math` / `F:chinese` 命名空间处理；是否改成 F、H 等不同字母待定。
- **(c) “砺石”**：七科规范第八章“比喻隔离”示例“在数学课诱导‘砺石’”，妙笔 V1.0 的比喻体系是“笔 / 落笔 / 收笔 / 笔锋 / 留白”，没有“砺石”；合成用例改用“落笔”。
- **(d) 阿基、X 博士没有规格书**：合同符合性标“未立项”（not_run），不算通过。
- **(e) 新发现：考官型首次披露句超长**：三张角色卡规定的首次披露句（如“我是 AI 扮演的道法考官方圆，只按南京中考道法评分标准判分。”）按句号切分都超过考官型基线 §5.1 的“每句不超过 25 字”。
  漂移检查对“登记表里的披露原句”不计 G-S-01，记规格冲突。
- 另：雅典娜的设定元素“天平”也是物理学科的常用器材名，阿基立项后比喻隔离会误报，届时需要规格负责人定口径。

## 待人确认

1. 禁语种子词表（F:math、F:chinese、G、platform）与 A–E 的迁移：逐条核对后把各文件与 `manifest.yaml` 的 `reviewed` 改为 true（必停项，`[NEEDS-HUMAN]`）。
2. 登记表里从规格书摘录的判定依据（称谓、比喻与设定元素、设定元素上限、转介话术、出场时刻、固定句、披露句）与合同对照表草稿：人设负责人与三科教研确认。
3. 偏差 1（`platform` 类名）、偏差 2（G 类收 §5.1 / §2.1 三条）、偏差 3（KGE 整数版本）、偏差 12（CI 跑两遍）。
4. 近似规则的阈值（偏差 8）与 N16 各科的 `reveal_level`：金标准到位后按召回率 ≥ 95%、精确率 ≥ 90% 校准（spec 待人确认 #6、#7）。
5. spec 待人确认 #11：`fetch-depth: 0` 与 `actions/upload-artifact@v4` 已按默认做了；#12：报告取 CI artifact，未改 `contents: read`。
6. 规格冲突 (a)–(e) 的裁决（修订规格属于人设变更，必停）。

## 待前端接入

- `apps/web` 的探针与诊断文案单测（`tests/probe.test.ts`、`tests/diagnosis.test.ts`）还在用行解析读旧位置 `evals/persona/diandian/lexicon/forbidden.yaml`。
  主会话接入时改为调用 guard：`PYTHONPATH=packages <python> -m personas.guard scan --persona platform --stdin-json`（stdin 传文案字符串数组，输出 JSON，
  有命中退出码 1；`--persona platform` 只查 A–E，与现有口径一致），或 `--text` 逐条传。接入后删除旧文件与 `packages/personas/forbidden/manifest.yaml` 的 `legacy_mirror`，
  同时删 `evals/persona/tests/test_lexicon_migration.py` 里依赖旧文件的比对（或改为从 git 历史取迁移前的版本）。
- 前端单测要能找到 Python 与 PyYAML（CI 里有 `.venv-contracts`）；是否让 `web-test` 依赖 Python 由主会话定。

## 运行记录（2026-09-30，本地）

- `make eval-persona CONTRACT_STRICT=1`：用例校验、评分器自检、pytest（136 条）、人设治理回归（检查器自检 74/74，录音 0/0/0）、变异 85/85 全部通过；
- `make persona-registry-check`、`make persona-guard-test`（131 条）：通过；
- `make knowledge-test CONTRACT_STRICT=1`：350 条通过；KGE 变异只跑了词表 / 人设相关子集 5 条（全量约 40 分钟，未跑），全部被发现；
- `make contract-test CONTRACT_STRICT=1`：通过（本地用临时虚拟环境装 `packages/contracts/requirements-dev.txt`）；
- `apps/web` 读旧词表的两个单测文件（只读运行）：通过。

## 提交分组

- **禁语词表与收拢（`[NEEDS-HUMAN]`，单独 PR）**：`packages/personas/forbidden/*`、`packages/personas/patterns/diandian.yaml`、
  `evals/persona/diandian/lexicon/forbidden.yaml`（改为迁移镜像）、点点评分器改读新位置（`evals/persona/scorer/{judge,__main__,selftest}.py`、
  `evals/persona/diandian/{run,compare}.py`、`evals/persona/tests/{test_scorer,test_compare,test_lexicon_migration}.py`、`evals/persona/scripts/mutations.py` 里三条词表变异的新路径）、
  KGE-03 复用 guard（`services/knowledge/knowledge/lint/{lexicon,__main__}.py`、`services/knowledge/tests/{test_mathcheck_lexicon.py,mutations.sh,fixtures/lexicon-before-persona-guard.json}`、
  `tools/kge-pages-template/README.md`、`tools/kge-pages-template/repo/lexicon/README.md`），以及它们依赖的 `packages/personas/{__init__,lexicon,registry}.py` 与 `guard/`。
  登记表里的判定依据与合同对照表按 spec 也随这个 PR 请人确认。
- **其余组件**：`packages/personas/registry.yaml`、`prompts.lock`、`packages/personas/tests/`、`evals/persona/{governance,cases,schema,fixtures/governance,recordings}/`、
  `evals/persona/contract-matrix.yaml`、`evals/persona/tests/test_governance.py`、`evals/persona/README.md`、`Makefile`、`.github/workflows/check.yml`、本文件。
  （两组互相依赖：若分开合并，先合词表组；`persona-registry-check` 与 guard 的代码可以随任一组先进。）
