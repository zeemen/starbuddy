# 阿鸦（aya）适配器设计

状态：草稿 v0.2，已按 [decisions.md](./decisions.md)（2026-09-25）修订；其余条目仍等人确认。**不开始实现。**
依据：本目录 inventory.md、decisions.md；完整方案 v1.0 第 5.4、5.5 节；开发计划冲刺 1-D；阿鸦-规格书-V1.1；点点-规格书-V1.2。
路径相对 `legacy/aya/`。

> 契约字段、`aya.*` schema、RLS、计量、阿鸦人设 Prompt 都属于必停项，落地时 PR 首行必须写 [NEEDS-HUMAN]。

### v0.2 修订摘要（对应 decisions.md 编号）

| 决定 | 本文改动 |
|---|---|
| #9 变式仅从已审题库选题 | 第 1 节 generate_variation 定为选题语义；生成器移出契约 v1 |
| #10 TS 验证器 v0，SymPy 放到阶段 1 | 第 2 节验证器路线由"待决"改为已定，补边界兜底规则；第 4 节语言栈建议同步修改 |
| #11 Q1 不进正式测评与星亮门槛 | 第 1 节 diagnose、grade、report 增加内容准入规则；新增第 3.1 节 |
| #3 693 道题来源待核 | 新增第 3.1 节内容准入状态；第 6 节阻断项 |
| #4 答案与评分标准只在服务端 | 第 1 节 grade、第 3 节 aya.* 拆表与 RLS、第 6 节阻断项 |
| #5 管理员权限在服务端校验 | 第 5 节边界、第 6 节阻断项 |
| #2 数据驻留并入 ADR 09 | 第 6 节阻断项 |
| #1 无真实用户 | 第 3 节删除数据迁移方案 |
| #7 禁语 A–F | teach 行保持 A–F 不变 |

---

## 1. 六方法映射

所有方法都要返回 `result / evidence_ids / confidence / source_version / model_version / human_review_required`。

| 方法 | 复用的现有代码 | 可包装程度 | 做法与差距 |
|---|---|---|---|
| **diagnose** | lib/adaptive.ts:14、lib/calibration.ts:53、lib/grading.ts:46、lib/scoring.ts:25,118,181、content/chapters/*/blueprint.ts | **高**（纯函数，没有 IO） | 输入从 localStorage 改为平台 evidence_event；输出映射到 `skill_node(aya, local_id=K-XXX-NNNN)` + `mastery_estimate`；BKT 参数登记算法版本；自评为"猜的"时降权。**只有准入内容（见 3.1）产生的证据能推动星亮门槛**；非准入内容的证据可以记录，但必须标记，不计入门槛 |
| **grade** | 客观题：lib/grading.ts:46-73 + lib/symbolic.ts:223；过程题：/api/process-grade + lib/process-grade-prompt.ts + lib/process-score.ts:39 | 客观题**高**，过程题**中** | **只在服务端执行**；答案和评分标准永不下发到客户端（#4）。客观题由**验证器 v0**判定（见第 2 节）；过程题（LLM）结果一律进复核队列。补齐溯源字段 |
| **report** | app/report/narrative.ts、lib/evaluation.ts、weekly、teacher、consultation、trends、exam.ts:74 | **高**（规则式） | 只输出学科字段，由平台合成三视角；家长侧去掉阿鸦口吻；**成绩预测区间只基于准入内容**，准入内容不够时不输出区间 |
| **plan** | lib/scoring.ts:145（优先级）、lib/review.ts:29（复测）、lib/curriculum.ts:129 | **中** | 阿鸦只输出学科内优先级和复测清单，日程编排归平台和点点；agenda 剥离 |
| **generate_variation** | lib/mistakes.ts:54 pickVariant、lib/methods.ts:54、content/sprint | **中** | **契约 v1：只从已审题库选题**（#9）。候选限定为同节点、同陷阱码，并且准入状态为已准入（见 3.1）；没有合格候选时返回空结果并说明原因，**不生成新题**。生成器（11.3 十项检查 + 验证器闸门）不在契约 v1 范围内，另立项 |
| **teach** | /api/coach（SSE 网关）、lib/prompts.ts、lib/lesson.ts:49、lib/verify-claims.ts | **低到中** | 提示等级改由服务端引擎决定；人设按 V1.1 重写（AI 身份披露、禁语 **A–F**、转介、年级语气档、猜对不庆祝、未验证题不讲）；会话落库并抽检；接平台模型路由和计量 |

**建议接入顺序**（对齐冲刺 1–2）：diagnose、grade（客观题）、report 先接；plan、generate_variation 随后；teach 放到人设重写之后。

注意：现在 721 道题全部是 Q1，其中 693 道章节题来源待核。在教研过审之前，接入后的 diagnose 和 grade 只能在"练习与演示"语义下运行，不产生正式测评结果，也不推动星亮门槛。

---

## 2. 判分溯源字段差距

| 契约字段 | 客观题 `GradeResult`（grading.ts:40-43） | 过程题（process-grade/route.ts:85-89） | 适配器做法 |
|---|---|---|---|
| result | correct、trapsHit | verdicts[{id, achieved, evidence 片段, note}]、transcription | 保留 |
| evidence_ids | 无（Attempt 本身也没有 id） | 无 | 作答、OCR 转写、采分点证据都落为 evidence_event |
| confidence | 无（`Attempt.confidence` 是学生自评，语义不同） | 无 | 客观题：由验证器 v0 给出（采样一致点占比；key 集合比对为 1.0）；过程题：模型判分统一低置信 |
| source_version | 只有 reviewStatus | rubric 没有版本 | 题目、rubric 加 content_version 和六段标识 |
| model_version | 不适用，应填验证器版本 | 知道模型名但没有返回 | 客观题填 `aya-symbolic-ts@v0`（版本号以实现为准）；过程题由平台模型路由回传 |
| human_review_required | 无 | 无 | 以下任一情况置真：验证器 v0 边界情况（见下）、过程题、低置信、题目未准入（Q1 或来源待核） |
| usage_id / idempotency_key | 不适用 | 无 | 过程题和教练走平台计量 |

### 验证器路线（已定，#10）

- **阶段 0：验证器 v0 = 现有 TS 数值采样引擎**（`lib/symbolic.ts`），包装后登记到平台验证器注册表，只在服务端运行。
  - 能处理：单表达式等价（多项式、有理式、根式、幂，单字母变量）；选择题 key 集合比对。
  - **边界情况一律置 `human_review_required = true`，并在结果里注明原因码**：
    - 三角函数、多字母函数名
    - 方程或不等式求解、证明、多答案或区间答案
    - 表达式解析失败
    - 采样点求值异常，或一致点少于 4 个
    - 题目没有登记可用于等价判定的标准答案
  - 边界情况下，v0 不给出"对"或"错"的最终判定，只返回"需复核"，confidence 置低。
- **阶段 1：SymPy 服务**（Python，与 `services/learning` 同栈），覆盖上面的边界情况。上线后 v0 降级为快路径，SymPy 作为权威路径，结果不一致时进复核。
- 冲刺 1-D 要求的"SymPy 验证结果带 confidence"，在阶段 0 由 v0 的 confidence 承担，SymPy 顺延到阶段 1。

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
- 题目拆成两张表（#4）：
  - `aya.question`：题干、选项、图、节点、陷阱码、六段标识、准入状态（见 3.1）。登录用户可以按需读取单题。
  - `aya.question_key`：答案、解析、评分标准。**不开任何客户端读取策略**，只有服务端判分角色可读。RLS 变更要附测试，证明匿名和学生角色都读不到。
- rubrics（6）同样存在服务端专用表，不下发客户端。
- error_traps（429）、ability_codes（11）、methods（10）、sprint 母题
- 诊断蓝图、南京卷面结构与权重、pathways、figures
- 教练会话 transcript：放 aya.* 还是平台对话日志，待定

**数据迁移**：不做（#1：四科都没有真实用户）。legacy 的 `profiles` 数据不导入平台。

### 3.1 内容准入状态（#3、#11）

每道题登记两个互相独立的状态，**两者都满足才算"已准入"**：

| 维度 | 取值 | 当前 |
|---|---|---|
| 审核等级 | Q0–Q5（沿用 legacy） | 721 道全部是 Q1 |
| 来源核实 | `source_pending`（来源待核）/ `source_verified`（已核，附来源和授权记录） | 693 道章节题一律为 `source_pending`；28 道压轴题与 6 份 rubric 的来源同样没有记录，建议一并按待核处理（见疑点 4） |

- 已准入 = 审核等级 ≥ Q2，并且 `source_verified`。
- 只有已准入内容能进入：正式测评、星亮门槛、成绩预测区间、generate_variation 的候选池。
- 未准入内容只能用于练习与演示，相关结果一律 `human_review_required = true`。

---

## 4. 包装 vs 重写

**可以直接包装**（TS 纯函数）：adaptive、calibration、grading、symbolic（作为验证器 v0）、geometry、scoring（需要参数化和版本化）、review、mistakes.pickVariant、methods、exam（组卷要补年级过滤）、evaluation、weekly、teacher、consultation、trends、narrative。

**需要重写或废弃**：
- store.ts、sync.ts（整份写入 → 事件化写入 + 幂等键）
- prompts.ts（人设 V1.1）
- coach route（服务端控制提示等级、计量、模型路由、落库）
- process-grade（溯源字段、复核队列、计量）
- parent-gate、admin（交给平台 BFF，服务端校验）
- **agents.ts 整体废弃**，由平台显式状态机取代

**语言栈**：
- **阶段 0**：适配器用 TS 包装现有纯函数，验证器 v0 也是 TS。
- **阶段 1**：新增 Python SymPy 验证服务。
- 适配器本体是否移植到 Python 仍待定（见疑点 1）。

---

## 5. 与点点、平台的边界

| 现有能力 | 去向 |
|---|---|
| lib/agenda.ts 今日中枢 | 点点和平台今日计划 |
| lib/growth.ts 连续天数和成就 | 点点和平台星图 |
| 家长周报和会诊报告口吻 | 平台报告引擎（成长顾问口吻） |
| 学期同步 | 平台课程日历 |
| 账号、设置、云同步、家长门禁 | 平台 BFF |
| 管理后台、管理员身份 | 平台 BFF，**在服务端校验管理员角色**；不使用 `NEXT_PUBLIC_` 白名单（#5） |
| agents.ts 管理页 | 废弃 |

**阿鸦保留**：唤醒词、石子比喻（频率按规格 2.2 收敛，取舍见 decisions.md #14）、规律碎片和定理图鉴、学科内提示阶梯、苏格拉底教练对话。

---

## 6. 上线前阻断项

与 decisions.md 的"上线前必须解决"保持一致：

1. **答案与评分标准只在服务端**（#4）：适配器判分只在服务端运行；`aya.question_key` 不开客户端读取；RLS 测试覆盖匿名和学生角色。
2. **管理员权限在服务端校验**（#5）：不使用 `NEXT_PUBLIC_` 前缀的任何权限配置。
3. **内容准入**（#3、#11）：Q1 或来源待核的题不进正式测评、星亮门槛、成绩预测和变式候选池。
4. **数据驻留**（#2）：数据库、函数、手写照片与画像的存储和传输区域按 ADR 09"数据驻留"执行（ADR 待新建）。
5. `/api/coach` 和 `/api/process-grade` 的替代实现必须鉴权、计量；提示等级改由服务端决定。
6. AI 身份披露、学生侧危机转介、家长端去掉阿鸦形象。

legacy 仓库里对应的问题登记为已知缺陷 AYA-KD-01~04（见 decisions.md），不在 legacy 里修。

---

## 需要人确认的疑点（适配器设计相关）

1. 适配器本体是否长期保持 TS？还是在阶段 1 随 SymPy 一起移植到 Python？
2. 12 根因映射到跨科大类，需要教研签字。
3. Q1 升 Q2 与来源核实的教研排期。这决定阿鸦何时能产出正式测评结果。
4. 28 道压轴题和 6 份 rubric 的来源同样没有记录，是否一并标为"来源待核"？（decisions.md #3 只点名了 693 道章节题）
5. 教练会话 transcript 放在 aya.* 还是平台对话日志？
6. 验证器 v0 的版本号与原因码枚举，是否需要写进 `packages/contracts`？（如果需要，属于契约变更，[NEEDS-HUMAN]）
