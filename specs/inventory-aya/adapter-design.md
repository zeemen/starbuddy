# 阿鸦（aya）适配器设计初稿

状态：草稿，等人确认。**不开始实现。**
依据：本目录 inventory.md；完整方案 v1.0 第 5.4、5.5 节；开发计划冲刺 1-D（SymPy 验证结果带 confidence）；阿鸦-规格书-V1.1；点点-规格书-V1.2。
路径相对 `legacy/aya/`。

> 契约字段、`aya.*` schema、RLS、计量、阿鸦人设 Prompt 都属于必停项，落地时 PR 首行必须写 [NEEDS-HUMAN]。

---

## 1. 六方法映射

所有方法都要返回 `result / evidence_ids / confidence / source_version / model_version / human_review_required`。

| 方法 | 复用的现有代码 | 可包装程度 | 做法与差距 |
|---|---|---|---|
| **diagnose** | lib/adaptive.ts:14、lib/calibration.ts:53、lib/grading.ts:46、lib/scoring.ts:25,118,181、content/chapters/*/blueprint.ts | **高**（纯函数，没有 IO） | 输入从 localStorage 改为平台 evidence_event；输出映射到 `skill_node(aya, local_id=K-XXX-NNNN)` + `mastery_estimate`；BKT 参数登记算法版本；自评为"猜的"时降权 |
| **grade** | 客观题：lib/grading.ts:46-73 + lib/symbolic.ts:223（确定性）；过程题：/api/process-grade + lib/process-grade-prompt.ts + lib/process-score.ts:39 | 客观题**高**，过程题**中** | 客观题判分**移到服务端**（题库和答案不再下发到客户端），登记为验证器；过程题（LLM）结果一律进复核队列；补齐溯源字段（见第 2 节） |
| **report** | app/report/narrative.ts、lib/evaluation.ts、weekly、teacher、consultation、trends、exam.ts:74 | **高**（规则式） | 只输出学科字段，由平台合成三视角；家长侧去掉阿鸦口吻；预测区间只能基于 Q2 以上内容 |
| **plan** | lib/scoring.ts:145（优先级）、lib/review.ts:29（复测）、lib/curriculum.ts:129 | **中** | 阿鸦只输出学科内优先级和复测清单，日程编排归平台和点点；agenda 剥离 |
| **generate_variation** | lib/mistakes.ts:54 pickVariant、lib/methods.ts:54、content/sprint | **中**（只有"选择"语义） | v1 建议语义定为"从已审题库选同节点、同陷阱的变式"；真正的生成器要另立项，加 11.3 十项检查和验证器闸门 |
| **teach** | /api/coach（SSE 网关）、lib/prompts.ts、lib/lesson.ts:49、lib/verify-claims.ts | **低到中** | 提示等级改由服务端引擎决定；人设按 V1.1 重写（AI 身份披露、禁语 A–F、转介、年级语气档、猜对不庆祝、未验证题不讲）；会话落库并抽检；接平台模型路由和计量 |

**建议接入顺序**（对齐冲刺 1–2）：diagnose、grade（客观题）、report 先接；plan、generate_variation（选题语义）随后；teach 放到人设重写之后。

---

## 2. 判分溯源字段差距

| 契约字段 | 客观题 `GradeResult`（grading.ts:40-43） | 过程题（process-grade/route.ts:85-89） | 适配器做法 |
|---|---|---|---|
| result | correct、trapsHit | verdicts[{id, achieved, evidence 片段, note}]、transcription | 保留 |
| evidence_ids | 无（Attempt 本身也没有 id） | 无 | 作答、OCR 转写、采分点证据都落为 evidence_event |
| confidence | 无（`Attempt.confidence` 是学生自评，语义不同） | 无 | 客观题：验证器给出（采样一致率）；过程题：模型判分统一低置信 |
| source_version | 只有 reviewStatus | rubric 没有版本 | 题目、rubric 加 content_version 和六段标识 |
| model_version | 不适用，应填验证器版本 | 知道模型名但没有返回 | 验证器记算法版本；过程题由平台模型路由回传 |
| human_review_required | 无（可以由 reviewStatus < Q2 推出） | 无 | Q1 内容、过程题、低置信时置真 |
| usage_id / idempotency_key | 不适用 | 无 | 过程题和教练走平台计量 |

**验证器路线（待决）**：
- (a) 把 TS 数值采样引擎包装后登记为验证器（成本低，但不支持三角函数，也不能验证证明）；
- (b) 新建 Python SymPy 服务（与 `services/learning` 同栈，对齐冲刺 1-D）；
- (c) 两者并存：a 作为快路径，b 作为权威路径。

---

## 3. 数据归属

现在全部塞在 `StudentProfile` 一个 JSON 里（lib/types.ts:176-200），按三类拆开：

**原始证据 → platform.attempt / evidence_event**：
- attempts[]（questionId、givenAnswer、correct、confidence、hintLevelUsed、elapsedSec、trapsHit、at）
- exams[].items
- OCR verdicts（目前没有持久化）
- 教练会话里实际用到的最高提示等级

**推断 → platform.mastery_estimate / error_attribution 等**（带算法版本、置信度、证据 ID）：
- nodeMastery（五门槛映射到平台状态机）
- rootCauseCounts（12 根因映射到跨科大类，待教研签字）
- abilityScores（10×10）、六维评价、θ、复测队列

**自述 → 单独存放**：intake 里的近期分数、目标分、自评、每周学时。

**档案字段 → platform.student_profile**：年级、省市、学期。

**废弃**：`parentGate`，由 guardian_student_link 和按档案授权的 RLS 取代。

**留在 aya.***（只读内容，带版本）：
- knowledge_nodes（939 个，保留原 ID 作为 local_id）
- questions（721 道，**答案与题干拆表**，不公开可读；补六段标识）
- error_traps（429）、ability_codes（11）、methods（10）、rubrics（6）、sprint 母题
- 诊断蓝图、南京卷面结构与权重、pathways、figures
- 教练会话 transcript：放 aya.* 还是平台对话日志，待定

**数据迁移**：如果线上有真实用户，要写一次性脚本把 profiles.data 拆成事件和推断。整份数据"最后写入者胜出"的历史**无法还原中间状态**，只能迁移快照，并标注为 `legacy_import`。

---

## 4. 包装 vs 重写

**可以直接包装**（TS 纯函数）：adaptive、calibration、grading、symbolic、geometry、scoring（需要参数化和版本化）、review、mistakes.pickVariant、methods、exam（组卷要补年级过滤）、evaluation、weekly、teacher、consultation、trends、narrative。

**需要重写或废弃**：
- store.ts、sync.ts（整份写入 → 事件化写入 + 幂等键）
- prompts.ts（人设 V1.1）
- coach route（服务端控制提示等级、计量、模型路由、落库）
- process-grade（溯源字段、复核队列、计量）
- parent-gate、admin（交给平台 BFF）
- **agents.ts 整体废弃**，由平台显式状态机取代

**语言栈决策点**：平台学科服务规划是 Python FastAPI，阿鸦逻辑全是 TS。三个选项：
- (a) 适配器用 TS 包装现有纯函数（最快）；
- (b) 移植到 Python；
- (c) 只把验证器放到 Python。

建议 **a + c**：适配器用 TS 实现，验证器按第 2 节 (c) 并存。

---

## 5. 与点点、平台的边界

| 现有能力 | 去向 |
|---|---|
| lib/agenda.ts 今日中枢 | 点点和平台今日计划 |
| lib/growth.ts 连续天数和成就 | 点点和平台星图 |
| 家长周报和会诊报告口吻 | 平台报告引擎（成长顾问口吻） |
| 学期同步 | 平台课程日历 |
| 账号、设置、云同步、家长门禁、管理后台 | 平台 BFF |
| agents.ts 管理页 | 废弃 |

**阿鸦保留**：唤醒词、石子比喻（频率按规格 2.2 收敛）、规律碎片和定理图鉴、学科内提示阶梯、苏格拉底教练对话。

---

## 6. 接入前阻断项（建议）

1. 题库和答案不再下发到客户端，判分移到服务端（否则作答证据不可信）。
2. `/api/coach` 和 `/api/process-grade` 必须鉴权、计量；提示等级改由服务端决定。
3. Q1 内容不得进入正式测评、星亮门槛和成绩预测（方案 11.3）。
4. AI 身份披露、学生侧危机转介、家长端去掉阿鸦形象。
5. 题目版权确认。
6. 如果线上有未成年人数据：补监护人同意、隐私条款，并确认数据区域。

---

## 需要人确认的疑点（适配器设计相关）

1. 验证器路线：TS 数值采样、Python SymPy，还是两者并存？
2. generate_variation 的 v1 语义：接受"从已审题库选变式"吗？
3. 适配器语言栈：TS 包装，还是移植到 Python？
4. 12 根因映射到跨科大类，需要教研签字。
5. Q1 升 Q2 的教研排期，这决定 grade 和 diagnose 在试点中能否输出"确定"结果。
6. 线上 profiles 数据是否迁移？是否接受"只迁快照、标注 legacy_import"？
7. 教练会话 transcript 放在 aya.* 还是平台对话日志？
