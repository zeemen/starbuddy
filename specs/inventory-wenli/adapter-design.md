# 文砺（wenli）适配器设计

状态：草稿 v0.2，已按 [decisions.md](./decisions.md)（2026-09-25）修订；标"待输入"的条目等外部材料。**不开始实现。**
依据：本目录 inventory.md、decisions.md；完整方案 v1.0 第 5.1、5.4、5.5 节；妙笔-规格书-V1.0；CLAUDE.md。
W = `legacy/wenli/初中语文_AI_class/wenli/`

> 契约字段（`packages/contracts`）、`wenli.*` schema、RLS、计量逻辑、妙笔人设 Prompt 都属于必停项，落地时 PR 首行必须写 [NEEDS-HUMAN]。

### v0.2 修订摘要（对应 decisions.md 编号）

| 决定 | 本文改动 |
|---|---|
| #6/A5 变式只从已审题库选题 | 第 1 节 generate_variation 改为选题语义；即时生成不在契约 v1 范围内 |
| A2 主观判分低置信加复核 | 第 1 节 grade、第 2 节 confidence 口径已定 |
| #5/A4 位次和提分不进妙笔话语 | 第 1 节 diagnose、report、teach |
| #4/A1 平台 mastery_estimate 是唯一真相源 | 第 3 节 |
| A7 study_plan 由平台编排 | 第 1 节 plan、第 3 节 |
| #3 教辅不入库、教材全文来源待核 | 新增第 3.1 节内容准入；第 5 节阻断项 |
| #7 家长绑定只走服务端 | 第 3 节、第 5 节 |
| #1 无真实用户 | 第 3 节不做数据迁移 |
| #2 daofa 归知行 | 第 3 节 |
| #9 LGPL 登记 | 第 4 节 |

---

## 1. 六方法映射

所有方法都要返回 `result / evidence_ids / confidence / source_version / model_version / human_review_required`。

| 插件方法 | 复用的现有代码 | 做法 | 主要差距 |
|---|---|---|---|
| **diagnose** | `api/diagnose/route.ts`（OCR、归因）、`api/gap-test`、`api/diagnose/intake`、`api/self-assessment`；纯函数 `diagnosis-confidence`、`signal-conflict`、`pattern-detect` | 抽出 prompt、schema、纯函数作为引擎。**不再直接写 `ability_state` 和 `error_record`**，改为输出 evidence_event 和推断，交给平台 `mastery_estimate`（唯一真相源）。**诊断输出里去掉"全市位次区间"和"预计 +X 分"**，移到 report | 缺 evidence_ids、source_version、model_version；OCR 人工确认没有结构化字段 |
| **plan** | `api/plan/route.ts`、`prompts/plan.ts`、`domain/stage-cycle.ts`、`adaptive.ts recommendTargets`、`exam-weights.ts` | 包装。改读平台学习者模型，**只输出学科处方**（优先级、目标能力格、推荐资源），由平台今日计划编排 | 三连升降阶没有实现（`nextLevel` 没有调用点） |
| **teach** | `api/coach/hint` + `prompts/hint.ts`、`api/daily/today`、`api/writing/socratic`、`compose`、`api/reading/deconstruct`、`followup` | 引擎 prompt 可以复用，**妙笔人设层重写**。妙笔话语里不出现位次和提分 | 没有 AI 披露、禁语过滤、代写拦截、学生侧转介、年级语气档；微课内容基本空；没有讲解 DSL |
| **grade** | `api/essays/grade`（五维）、`api/reading/grade`、`classical/grade`、`noncontinuous/grade`、`writing/speech/grade`；`api/quiz/grade`（规则判分，作为验证器）、`api/exam/grade` | 包装。判分引擎只输出结构化结果，评语由妙笔人设层单独生成（引擎先判，人设后说）。**作文、阅读等主观维度：confidence 一律为低值，并置 `human_review_required = true`**；规则判分给出高置信 | 见第 2 节；人工复核接入平台统一队列 |
| **generate_variation** | 复盘"理解/变式/混合"的选题逻辑（`components/mistakes/ReviewRunner.tsx`）、`item_bank` | **契约 v1：只从已审题库选题**。候选限定为同能力格、同错因，并且已准入（见 3.1）；没有合格候选时返回空结果并说明原因。`api/items/generate` 等生成接口不在契约 v1 范围内，其产出只能进入平台 content_review，过审后才进入候选池 | 审核接口没有管理员鉴权（WENLI-KD-01）；没有统一的错因参数入口 |
| **report** | `apps/parent/app/api/report/weekly`、`api/stage-report` + `server/stage-report-gen.ts`、`apps/parent/app/api/growth`、viz 组件 | 作为字段提供者，由平台报告引擎合成。**位次和提分只在这里输出**，标明"预测、非承诺"，并且只基于已准入内容；准入内容不够时不输出 | 现在直接读学科表；"作文二改幅度"没有数据来源 |

建议冲刺顺序：grade（作文五维 + quiz 规则判分）、diagnose、generate_variation 先接；plan、report、teach 后接。具体以开发计划冲刺表为准。

---

## 2. 判分溯源字段差距

| 契约字段 | 作文 `GradeResultSchema`（schemas.ts:238-253） | 阅读 `ReadingGradeSchema`（:386） | quiz/exam | 适配器做法 |
|---|---|---|---|---|
| result | band、est_score、score_range、五维、marks、upgrade | points、score、score_range | 分数、掌握度 | 保留，评语移到人设层 |
| evidence_ids | 无（marks.span 是原文片段） | 无（evidence 是自由文本） | 无（`assessment_event.ref` 是自由字符串） | submission、marks、error_record 落为 evidence_event，返回 ID |
| confidence | 只有 `by: 主判/参考` + `uncertainty_note` | 无 | `assessment_event.confidence` 可为空 | **口径已定（A2）**：作文、阅读、古诗文、非连续、口语等主观维度一律低置信（具体数值随契约定）；quiz 规则判分给出高置信（key 比对 1.0） |
| source_version | 无 | 无 | 无 | Rubric、prompt、题目加版本常量和六段标识 |
| model_version | `"routed"`（WENLI-KD-04） | 无 | 无 | 由平台模型路由回传（`resolveRoute` 已经有这个信息）；规则判分记算法版本 |
| human_review_required | 有，只作标志 | 无 | 无 | 主观维度一律置真；题目未准入时置真；接入平台统一复核队列 |

**金标准（A6，待输入）**：`calibration_sample` 是空的。作文和阅读的合成标注样本由谁提供还没定，定下之前主观维度只能"全量复核"。

---

## 3. 数据归属

**迁到 platform.*（唯一真相源）**：
- `profiles` 里的年级、年级锚点、距考日期、分型 → 平台 student（订阅归权益引擎）
- 掌握度 → **平台 `mastery_estimate`**（#4/A1）。skill_node 粒度由平台定；`ability_state`（28 格）和 `point_mastery`（知识点级 Beta）只作为算法参考，不作为数据源
- `mastery_snapshot` → 平台时间序列
- `assessment_event` → 平台 evidence_event（设计上可以作为模板）
- `error_record` 的"能力格 × 错因"部分 → `error_attribution`（6 类映射到跨科大类，待教研签字，A3）
- 自述类 → 平台自述存储，与推断分开：`self_assessment`、`ability_self_rating`、`semester_score`、`intake_profile`
- `signal_conflict`、`diagnostic_session` → 平台推断和证据
- `guardians`、`bind_code` → `guardian_student_link`。**写入只允许走服务端兑换流程**，RLS 附越权测试（#7）
- `wechat_identity`、`daily_checkin` → 平台身份和打卡
- 家长侧四张表 → 平台家长端

**留在 wenli.***：
- 原始证据：`submission`、`submission_file`（原图需要加密存储并支持删除）
- 学科过程：`essay_review`、`review_log`、`perception_card`、`socratic_session`、`debate_session`、`mock_exam`、`quiz_attempt`、`pattern_flag`
- 内容：`item_bank` / `item_review`、`knowledge_point`、`classical_text`、`textbook_catalog`、`recitation_corpus`、`doc_chunks`。**受 3.1 准入约束**
- `study_plan` 不再作为学科私有表的权威来源；学科处方作为 plan 的输出交给平台（A7）

**不进入 wenli.***：
- `admin_user`、`model_option`、`app_settings` → 平台模型路由和管理
- `daofa_*` 及 shared 里的 daofa 代码 → 归知行（#2）

**数据迁移**：不做（#1：没有真实用户）。

### 3.1 内容准入（#3、#6）

| 内容 | 状态 | 能否入平台 |
|---|---|---|
| 教辅笔记 `notes_chunks.json`（249 段，Z-Library 来源） | **禁用** | **永不入库**，也不进 RAG 底座 |
| 教材全文切块 `chunks.json`（1097 段） | **来源待核** | 法务确认使用口径之前不入库 |
| 古诗文原文（109 篇候选） | 来源待核 | 底本和注释的版权确认后再入 |
| 教材目录 `catalog.json`（237 篇元数据） | 可用 | 可以入库（只有元数据） |
| 知识点（27 条草稿） | 未审 | 过审后入库 |
| 题库 | 没有已审题 | 只有过审题进 generate_variation 候选池和正式测评 |
| 即时生成题 | 非准入 | 只能用于练习；不进正式测评和掌握门槛 |

"已准入" = 教研审核通过，并且来源已核实。只有已准入内容能进入：正式测评、掌握门槛、report 的位次和提分预测、generate_variation 的候选池。

---

## 4. 包装 vs 重写

**可以包装**：
- `packages/shared/lib/domain/*` 的文砺纯逻辑（在平台仓库里重新建立测试基线，#11）
- `packages/shared/lib/prompts/*` 的文砺模板，作为内层引擎（去掉 diagnose 和 plan prompt 里的位次与提分输出）
- 前端页面按"路由模块挂入统一壳"复用（前端版本以平台统一壳为准，#10）
- 多供应商路由思路，由平台模型路由取代

**需要重写或新建**：
- 妙笔人设层：System Prompt、禁语 A–F、代写拦截、AI 披露、学生侧危机转介、年级语气档、`evals/persona` 回归集
- 命题审核门禁（生成只进 content_review）
- 计量与幂等（从零做）
- 判分溯源字段和复核队列
- 所有直接写学习者表的路由（diagnose、quiz/grade、exam/grade、essays/grade 的 reflow、daily/*）改成经契约写入
- 合规：监护人同意、一键删除、时长和夜间限制

**许可证**：sharp/libvips（LGPL-3.0-or-later）登记进许可证清单（#9）。

---

## 5. 上线前阻断项

与 decisions.md 的"上线前必须解决"和已知缺陷保持一致：

1. **版权**（#3）：Z-Library 教辅永不入库；教材全文和古诗文底本来源待核，确认前不入库。
2. **家长绑定**（#7）：`guardian_student_link` 只能在服务端写入，RLS 附越权测试（[NEEDS-HUMAN]）。
3. **审核门禁**（WENLI-KD-01、02）：审核动作只允许平台审核角色在服务端执行；学生只能拿到已准入的题。
4. **学生侧危机筛查与转介**（WENLI-KD-05）：平台层确定性转介，由点点执行。
5. **模型路由**固定为境内供应商，或者先完成跨境评估（并入 ADR 09"数据驻留"）。
6. **计量与幂等**。

legacy 里的对应问题登记为已知缺陷 WENLI-KD-01~06（见 decisions.md），不在 legacy 里修。

---

## 待输入

| 编号 | 事项 | 影响 |
|---|---|---|
| A3 | 6 类错因映射到跨科大类，需要教研签字 | error_attribution 的写入 |
| A6 | 作文和阅读的合成金标准样本由谁标注 | 主观维度能否从"全量复核"降为"抽样复核" |
| #12 | 南京满分设定、M1 诊断引擎修订文档 | 内容元数据、diagnose 口径 |
