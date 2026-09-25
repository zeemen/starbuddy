# 文砺（wenli）适配器设计初稿

状态：草稿，等人确认。**不开始实现。**
依据：本目录 inventory.md；完整方案 v1.0 第 5.1、5.4、5.5 节；妙笔-规格书-V1.0；CLAUDE.md。
W = `legacy/wenli/初中语文_AI_class/wenli/`

> 契约字段（`packages/contracts`）、`wenli.*` schema、RLS、计量逻辑、妙笔人设 Prompt 都属于必停项，落地时 PR 首行必须写 [NEEDS-HUMAN]。

---

## 1. 六方法映射

所有方法都要返回 `result / evidence_ids / confidence / source_version / model_version / human_review_required`。

| 插件方法 | 复用的现有代码 | 做法 | 主要差距 |
|---|---|---|---|
| **diagnose** | `api/diagnose/route.ts`（OCR、归因）、`api/gap-test`、`api/diagnose/intake`、`api/self-assessment`；纯函数 `diagnosis-confidence`、`signal-conflict`、`pattern-detect` | 抽出 prompt、schema、纯函数作为引擎。**不再直接写 `ability_state` 和 `error_record`**，改为输出 evidence_event 和推断，经契约交给 platform.* | 缺 evidence_ids、source_version、model_version；OCR 人工确认只是 `assessment_event.source` 的语义，没有结构化字段；位次预测与禁语 F 类冲突（待确认） |
| **plan** | `api/plan/route.ts`、`prompts/plan.ts`、`domain/stage-cycle.ts`、`adaptive.ts recommendTargets`、`exam-weights.ts` | 包装。改读平台学习者模型，输出学科处方，交给平台今日计划编排 | 三连升降阶没有实现（`nextLevel` 没有调用点）；study_plan 的归属待定 |
| **teach** | `api/coach/hint` + `prompts/hint.ts`、`api/daily/today`、`api/writing/socratic`、`compose`、`api/reading/deconstruct`、`followup` | 引擎 prompt 可以复用，**妙笔人设层重写** | 没有 AI 披露、禁语过滤、代写拦截、学生侧转介、年级语气档；微课内容基本空；没有讲解 DSL |
| **grade** | `api/essays/grade`（五维）、`api/reading/grade`、`classical/grade`、`noncontinuous/grade`、`writing/speech/grade`；`api/quiz/grade`（规则判分，可以作为验证器）、`api/exam/grade` | 包装。判分引擎只输出结构化结果，评语由妙笔人设层单独生成（引擎先判，人设后说） | 见第 2 节；主观判分没有验证器兜底，只有 zod 校验；人工复核没有队列 |
| **generate_variation** | `api/items/generate`、`quiz/generate`、`reading/*/generate`、`exam/generate`；复盘"理解/变式/混合"（`components/mistakes/ReviewRunner.tsx`） | 包装生成逻辑，**审核门禁重写**：产出物一律进平台 content_review，学生只消费已审题（或者由教研确认"即时练习题"的口径） | 审核接口没有管理员鉴权；没有统一的错因参数入口 |
| **report** | `apps/parent/app/api/report/weekly`、`api/stage-report` + `server/stage-report-gen.ts`、`apps/parent/app/api/growth`、viz 组件 | 知行一样做字段提供者，由平台报告引擎合成 | 现在直接读学科表；"作文二改幅度"没有数据来源 |

建议冲刺顺序：grade（作文五维 + quiz 规则判分）、diagnose、generate_variation 先接；plan、report、teach 后接。具体以开发计划冲刺表为准。

---

## 2. 判分溯源字段差距

| 契约字段 | 作文 `GradeResultSchema`（schemas.ts:238-253） | 阅读 `ReadingGradeSchema`（:386） | quiz/exam | 适配器做法 |
|---|---|---|---|---|
| result | band、est_score、score_range、五维、marks、upgrade | points、score、score_range | 分数、掌握度 | 保留，评语移到人设层 |
| evidence_ids | 无（marks.span 是原文片段） | 无（evidence 是自由文本） | 无（`assessment_event.ref` 是自由字符串） | submission、marks、error_record 落为 evidence_event，返回 ID |
| confidence | 只有 `by: 主判/参考` + `uncertainty_note` | 无 | `assessment_event.confidence` 可为空 | 主观维不确定度映射成数值（口径待定）；规则判分用 1.0 或算法给出 |
| source_version | 无 | 无 | 无 | Rubric、prompt、题目加版本常量 |
| model_version | `"routed"` | 无 | 无 | 由平台模型路由回传（`resolveRoute` 已经有这个信息）；规则判分记算法版本 |
| human_review_required | 有，只作标志 | 无 | 无 | 接入平台统一复核队列 |

---

## 3. 数据归属

**迁到 platform.*（唯一真相源）**：
- `profiles` 里的年级、年级锚点、距考日期、分型 → 平台 student（订阅归权益引擎）
- `ability_state`（28 格）、`point_mastery`（Beta）→ `skill_node`（subject=wenli）+ `mastery_estimate`（两套取哪个待定）
- `mastery_snapshot` → 平台时间序列
- `assessment_event` → 平台 evidence_event。它最接近"原始证据与推断分开"的设计，**可以作为迁移模板**
- `error_record` 的"能力格 × 错因"部分 → `error_attribution`（6 类映射到跨科大类，待教研签字）
- 自述类 → 平台自述存储，与推断分开：`self_assessment`、`ability_self_rating`、`semester_score`、`intake_profile`
- `signal_conflict`、`diagnostic_session` → 平台推断和证据
- `guardians`、`bind_code` → `guardian_student_link`（邀请码模型可以直接映射）
- `wechat_identity`、`daily_checkin` → 平台身份和打卡
- 家长侧四张表（`parent_consult_session`、`parent_action_plan`、`parent_report`、`stage_report`）→ 平台家长端

**留在 wenli.***：
- 原始证据：`submission`、`submission_file`（原图需要加密存储并支持删除）
- 学科过程：`essay_review`、`review_log`、`perception_card`、`socratic_session`、`debate_session`、`mock_exam`、`quiz_attempt`、`study_plan`、`pattern_flag`
- 内容：`item_bank` / `item_review`、`knowledge_point`、`classical_text`、`textbook_catalog`、`recitation_corpus`、`doc_chunks`（需要先完成版权处置）
- `calibration_sample`（如果复核队列统一到平台，可以废弃）

**不进入 wenli.***：
- `admin_user`、`model_option`、`app_settings` → 平台模型路由和管理
- `daofa_*` → 划归 zhixing

---

## 4. 包装 vs 重写

**可以包装**：
- `packages/shared/lib/domain/*` 的文砺纯逻辑，测试充分
- `packages/shared/lib/prompts/*` 的文砺模板，作为内层引擎
- 前端页面按"路由模块挂入统一壳"复用
- 多供应商路由思路，由平台模型路由取代

**需要重写或新建**：
- 妙笔人设层：System Prompt、禁语 A–F、代写拦截、AI 披露、学生侧危机转介、年级语气档、`evals/persona` 回归集
- 命题审核门禁
- 计量与幂等（从零做）
- 判分溯源字段和复核队列
- 所有直接写学习者表的路由（diagnose、quiz/grade、exam/grade、essays/grade 的 reflow、daily/*）改成经契约写入
- 合规：监护人同意、一键删除、时长和夜间限制

---

## 5. 接入前阻断项（建议）

1. 修复 `review/item` 的管理员鉴权；验证并修复 `guardians_guardian_rw` 的越权风险（RLS，[NEEDS-HUMAN]）。
2. 版权处置：Z-Library 教辅 OCR 不得进入平台内容底座；教材全文的使用口径待法务确认。
3. 学生侧危机筛查与转介。
4. 模型路由固定为境内供应商，或者先完成跨境评估。
5. 计量与幂等。

---

## 需要人确认的疑点（适配器设计相关）

1. 掌握度真相源：用 28 格 `ability_state`、知识点级 `point_mastery`，还是由平台重新定义 skill_node 粒度？
2. 主观判分的 `confidence` 数值口径。
3. 语文 6 类错因映射到跨科大类，需要教研签字。
4. 位次和提分预测：是否保留在学生端？如果保留，是否移出妙笔话语、只放在报告引擎里？
5. 即时生成练习题是否一律改为"已审题库 + 受控变式"？
6. grade 的金标准来源：`calibration_sample` 是空的，冲刺内由谁标注作文和阅读的样本？
7. `study_plan` 是学科私有，还是作为平台今日计划的输入交给平台？
