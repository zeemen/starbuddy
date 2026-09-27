# 学生洞察引擎（SIE）画像字段字典 v0.9

状态：**草稿，W2 评审冻结**。任务包 SIE-00（`specs/sie-00-field-dictionary/spec.md`）。
机器可读版：`field-dictionary.v0.9.yaml`；检查：`make sie-dictionary-check`。下方“生成”部分由脚本从 YAML 生成，**不要手改**，改 YAML 后运行 `python docs/design/sie/check_field_dictionary.py --write`。

## 字段键说明

| 键 | 含义 |
|---|---|
| `id` | `圈.组.名`；圈 = outer / inner / middle / through，组 = g1–g9（对应 SIE 原九层 ①–⑨） |
| `layer` | 三层数据：fact 事实 / evidence 证据 / inference 推断，分开存（CLAUDE.md 规则 6） |
| `source_type` | 客观记录 / 学生自述 / 家长观察 / 模型推断；多来源时各自单独存，说法不同时两边都存 |
| `collect` | 采集级别（必填 / 建议 / 可选 / 自动）与时机（建档 / 周期 / 持续） |
| `visibility` | 默认可见范围（Agent / 学生 / 家长）。属于“画像可见范围”，改动是必停项 |
| `storage` | 落库位置，也是这个字段的唯一真相源；不能指向判断账本。学生与家长都可能提供的字段按来源分开写 `{self: …, guardian: …}`（规则 6：说法不同两边都存）；还没建的表写 `built_by`（由哪个任务包建） |
| `source_column` | 同一张事实表里记录“由谁录入”的列（考试记录、异议），用它代替按来源分表 |
| `exposed_via` | 推断类字段对外的唯一通道：判断账本 `insight.claim`（CLAUDE.md 规则 11）。判断只是陈述层，经 `basis_kind / basis_id` 引用 `storage` 里的行，不作为计算输入 |
| `teaching_decision` | 它改变哪个教学决策。写不出来的字段不准入（SIE 风险表） |
| `guardian_deletable` | 家长能否单独删除；不能单独删的要写明删除途径 |
| `status` | live 现在就在采 / silent 静默采集（效度检验前只给 Agent）/ planned 后续任务包建 |
| `dimension`、`launch_subjects` | 六维呈现视图中的归属；首发只对外展示数学的知识掌握、知识迁移 |
| `metric` | 中圈指标的可复算定义、最低样本、效度检验方法与是否已通过 |
| `legacy_sources` | 旧产品里的对应结论（来自 KGE-00 结论字段清单），只追溯，不迁移数值 |

## 字典

<!-- BEGIN GENERATED: dictionary -->
字典版本 0.9.0（draft）；字段 47 个。

### g1 基础身份与学习环境（outer）

| id | 名称 | 层 | 来源 | 默认可见 | 落库 | 状态 | 改变的教学决策 |
|---|---|---|---|---|---|---|---|
| `outer.g1.grade` | 年级 | fact | 学生自述、家长观察 | Agent、家长 | `platform.student_profile.grade` | live | 选择教材进度与年级内的知识节点，决定冷启动先验 |
| `outer.g1.region` | 地区 | fact | 学生自述、家长观察 | Agent、家长 | `platform.student_profile.region` | live | 选择地区考情投影（题位与权重） |
| `outer.g1.textbook_version` | 各科教材版本 | fact | 学生自述、家长观察 | Agent、家长 | `platform.student_profile.textbook_version` | live | 按教材投影渲染页码与原句，决定节点的教材进度 |
| `outer.g1.birth_year_month` | 出生年月 | fact | 家长观察 | Agent、家长 | `platform.student_profile.birth_year_month` | live | （合规用途）判断是否未满 14 周岁、需要监护人同意；不参与任何推断与教学决策 |
| `outer.g1.weekly_available_time` | 每周可用时间 | fact | 学生自述、家长观察 | Agent、家长 | `学生自述→insight.self_report.value；家长观察→insight.guardian_report.value（SIE-01 建）` | planned | 决定每日计划时长与跨科时间预算 |
| `outer.g1.devices` | 学习设备 | fact | 学生自述、家长观察 | Agent、家长 | `学生自述→insight.self_report.value；家长观察→insight.guardian_report.value（SIE-01 建）` | planned | 决定是否安排手写拍照、语音讲述等需要设备支持的任务 |
| `outer.g1.tutoring` | 校外补习情况（自愿） | fact | 学生自述、家长观察 | Agent、家长 | `学生自述→insight.self_report.value；家长观察→insight.guardian_report.value（SIE-01 建）` | planned | 避免与校外进度重复布置同一节点的练习 |

### g2 学业水平与历史成绩（inner）

| id | 名称 | 层 | 来源 | 默认可见 | 落库 | 状态 | 改变的教学决策 |
|---|---|---|---|---|---|---|---|
| `inner.g2.exam_score` | 学校考试成绩（原始分与满分） | fact | 学生自述、家长观察 | Agent、学生、家长 | `insight.exam_record.raw_score（记录录入人：insight.exam_record.confirmed_by）（SIE-01 建）` | planned | 外部锚点：校准掌握判断，生成对账卡；七八年级 100 分制与九年级 120 分制分别归一 |
| `inner.g2.exam_scope` | 考试范围与日期 | fact | 学生自述、家长观察 | Agent、学生、家长 | `insight.exam_record.scope_node_ids（记录录入人：insight.exam_record.confirmed_by）（SIE-01 建）` | planned | 考前 14 天按“考情权重 × 当前缺口 × 可提升”重排计划，并在考试当天冻结预测 |
| `inner.g2.exam_rank_band` | 考试排名区间（学生或家长录入） | fact | 学生自述、家长观察 | Agent、家长 | `insight.exam_record.rank_band（记录录入人：insight.exam_record.confirmed_by）（SIE-01 建）` | planned | 只作考试难度与区分度的参考，校准预测区间；不对学生展示，不做同伴比较 |
| `inner.g2.exam_difficulty_note` | 试卷难度说明 | fact | 学生自述、家长观察 | Agent、家长 | `insight.exam_record.difficulty_note（记录录入人：insight.exam_record.confirmed_by）（SIE-01 建）` | planned | 对账时区分“试卷偏难”与“此前判断偏乐观” |
| `inner.g2.graded_paper_upload` | 带批改痕迹的整卷试卷 | fact | 客观记录 | Agent | `kge.attempt.answer_raw（KGE-05 建）` | planned | 作为先验与外部锚点；C 级识别内容先请学生核对，确认前不生成薄弱结论 |
| `inner.g2.exam_prediction` | 考试分数预测区间 | inference | 模型推断 | Agent | `insight.exam_prediction.low（SIE-03 建）` | planned | 考前冻结、考后对账，偏差分析修正个体参数与判断 |

### g3 学科知识掌握（inner）

| id | 名称 | 层 | 来源 | 默认可见 | 落库 | 状态 | 改变的教学决策 |
|---|---|---|---|---|---|---|---|
| `inner.g3.node_mastery` | 节点掌握状态（五道门槛） | inference | 模型推断 | Agent、学生、家长 | `platform.mastery_estimate.state` | live | 选下一步教学节点、决定是否点亮星与安排 0 / 3 / 14 天复测 |
| `inner.g3.next_retest_at` | 下一次复测时间 | inference | 模型推断 | Agent、学生、家长 | `platform.mastery_estimate.next_retest_at` | live | 把到期复测放进今日计划 |
| `inner.g3.error_attribution` | 错因（学科码与跨科大类） | inference | 模型推断 | Agent | `platform.error_attribution.cross_subject_category` | live | 选择处方（限时专项、同题多解、回退补前置、圈画条件训练） |
| `inner.g3.evidence_events` | 作答证据事件 | evidence | 客观记录 | Agent | `platform.evidence_event.payload` | live | 所有掌握判断的证据来源；“为什么”展开到具体题与提示等级 |
| `inner.g3.answer_confidence` | 作答时的把握度（自报） | fact | 学生自述 | Agent | `platform.attempt.self_report` | live | 区分猜对与会做，猜对不加掌握 |

### g4 核心学习能力（学科内）（inner）

| id | 名称 | 层 | 来源 | 默认可见 | 落库 | 状态 | 改变的教学决策 |
|---|---|---|---|---|---|---|---|
| `inner.g4.skill_view` | 各科能力视图（阿鸦 100 点、文砺 28 点、知行五维） | inference | 模型推断 | Agent | `platform.skill_observation.outcome` | live | 星图旁的能力视图侧栏与跨章节的弱项定位；不单独点亮 |
| `inner.g4.transfer_gap_math` | 数学迁移差（知识迁移） | inference | 模型推断 | Agent、学生、家长 | `insight.learner_metric.value（SIE-02 建）` | planned | 迁移差大时增加变式与跨章题，而不是重复母题 |

### g5 学习行为与习惯（middle）

| id | 名称 | 层 | 来源 | 默认可见 | 落库 | 状态 | 改变的教学决策 |
|---|---|---|---|---|---|---|---|
| `middle.g5.behavior_events` | 学习行为事件（会话、计划完成与跳过、连错后退出、异议） | fact | 客观记录 | Agent | `insight.behavior_event.payload（SIE-01 建）` | planned | 降负触发（连续中断、连错后退出）与计划调整 |
| `middle.g5.plan_completion_rate` | 计划完成率 | inference | 模型推断 | Agent | `insight.learner_metric.value（SIE-02 建）` | silent | 计划偏重时缩短任务，并先询问原因 |
| `middle.g5.persistence` | 学习持续性 | inference | 模型推断 | Agent | `insight.learner_metric.value（SIE-02 建）` | silent | 中断后缩短任务、插入熟悉题；只与自己过去比较 |

### g6 学习动力与兴趣（middle）

| id | 名称 | 层 | 来源 | 默认可见 | 落库 | 状态 | 改变的教学决策 |
|---|---|---|---|---|---|---|---|
| `middle.g6.stated_interests` | 学生本人说出的兴趣 | fact | 学生自述 | Agent、学生 | `insight.self_report.value（SIE-01 建）` | planned | 用于题目的情境包装，可随时删除 |
| `middle.g6.self_initiated_sessions` | 主动发起学习 | inference | 模型推断 | Agent | `insight.learner_metric.value（SIE-02 建）` | silent | 主动学习少时把复习放进今日计划而不是单独推送 |
| `middle.g6.learning_mode_preference` | 学习方式偏好（学生自述） | fact | 学生自述 | Agent、学生 | `insight.self_report.value（SIE-01 建）` | planned | 首次教学的节奏（一步步引导 / 先讲再练 / 多独立尝试 / 闯关），之后以讲法响应为准 |
| `middle.g6.self_pain_points` | 学生自评痛点 | fact | 学生自述 | Agent、学生 | `insight.self_report.value（SIE-01 建）` | planned | 冷启动时优先诊断学生自己说的卡点；与客观证据不同时两边都存 |

### g7 学习策略与认知特点（middle）

| id | 名称 | 层 | 来源 | 默认可见 | 落库 | 状态 | 改变的教学决策 |
|---|---|---|---|---|---|---|---|
| `middle.g7.confidence_probe` | 做题前“能做对吗” | fact | 学生自述 | Agent | `insight.confidence_probe.predicted_correct（SIE-01 建）` | planned | 自评校准的输入；高估的学生多安排自检 |
| `middle.g7.independent_solve_rate` | 独立解题率 | inference | 模型推断 | Agent | `insight.learner_metric.value（SIE-02 建）` | silent | 提示后做对率明显高于独立做对率时增加无提示练习 |
| `middle.g7.hint_dependency` | 提示依赖度 | inference | 模型推断 | Agent | `insight.learner_metric.value（SIE-02 建）` | silent | 上调提示阶梯起点，安排无提示变式 |
| `middle.g7.transfer_gap` | 迁移差（跨科） | inference | 模型推断 | Agent | `insight.learner_metric.value（SIE-02 建）` | silent | 跨科都偏大时由点点安排迁移专项 |
| `middle.g7.self_assessment_calibration` | 自评校准 | inference | 模型推断 | Agent | `insight.learner_metric.value（SIE-02 建）` | silent | 高估者多安排自检，低估者多呈现“你比自己以为的更好”的对比证据 |
| `middle.g7.retention_rate` | 保持率 | inference | 模型推断 | Agent | `insight.learner_metric.value（SIE-02 建）` | silent | 调整复习间隔与个体遗忘参数 |
| `middle.g7.reading_question_error_share` | 审题类错因占比 | inference | 模型推断 | Agent | `insight.learner_metric.value（SIE-02 建）` | silent | 安排圈画条件训练；跨科指标须至少两科各有足够证据 |
| `middle.g7.teaching_response` | 讲法响应 | inference | 模型推断 | Agent | `insight.teaching_response.successes（SIE-02 建）` | planned | 同类节点优先选用之后首次独立做对的讲法，并保留轮换 |

### g8 家庭与外部支持（outer）

| id | 名称 | 层 | 来源 | 默认可见 | 落库 | 状态 | 改变的教学决策 |
|---|---|---|---|---|---|---|---|
| `outer.g8.study_routine` | 作息与学习安排（家长观察原文） | fact | 家长观察 | Agent、家长 | `platform.guardian_report.content` | live | 把任务排进学生实际可学习的时段，并给出休息提醒 |
| `outer.g8.sleep_schedule` | 作息时间（家长观察原文） | fact | 家长观察 | Agent、家长 | `platform.guardian_report.content` | live | 夜间不安排任务，按作息给出休息提醒 |
| `outer.g8.accompany_style` | 陪读方式 | fact | 家长观察 | Agent、家长 | `platform.guardian_report.content` | live | 决定周报里“本周一件事”建议家长怎样配合 |
| `outer.g8.expectation` | 家长期望 | fact | 家长观察 | Agent、家长 | `platform.guardian_report.content` | live | 周报与对账卡的解释口径；与学生自述目标不同时两边都存，落差作为信号 |
| `outer.g8.cooperation_capacity` | 家长可配合程度 | fact | 家长观察 | Agent、家长 | `insight.guardian_report.value（SIE-01 建）` | planned | 决定是否给家长布置配合任务以及任务的分量 |
| `outer.g8.school_communication` | 家校沟通情况 | fact | 家长观察 | Agent、家长 | `platform.guardian_report.content` | live | 考前提醒家长录入考试范围与日期，决定对账卡的录入时机 |

### g9 成长轨迹与目标（through）

| id | 名称 | 层 | 来源 | 默认可见 | 落库 | 状态 | 改变的教学决策 |
|---|---|---|---|---|---|---|---|
| `through.g9.focus_subject` | 最想改善的学科 | fact | 学生自述 | Agent、学生、家长 | `insight.self_report.value（SIE-01 建）` | planned | 跨科时间预算的优先级 |
| `through.g9.near_term_goal` | 近期目标（学生自述） | fact | 学生自述 | Agent、学生、家长 | `insight.self_report.value（SIE-01 建）` | planned | 两周学习路径的终点与复盘口径；与家长期望不同时两边都存 |
| `through.g9.disputes` | 对判断的异议 | fact | 学生自述、家长观察 | Agent、学生、家长 | `insight.dispute.kind（记录录入人：insight.dispute.raised_by）（SIE-03 建）` | planned | “我其实会”24 小时内推送验证题；“识别错了”打开原始事实供更正 |
| `through.g9.interventions` | 触发的提醒、处方、鼓励与降负 | fact | 客观记录 | Agent | `insight.intervention_log.action（SIE-03 建）` | planned | 评估触发规则是否奏效，避免同一提醒重复 |
| `through.g9.weekly_snapshot` | 每周画像快照 | inference | 模型推断 | Agent | `insight.profile_snapshot.payload（SIE-03 建）` | planned | 成长轨迹与“与一个月前的自己相比”的依据 |
| `through.g9.star_lit_history` | 星的点亮记录 | inference | 模型推断 | Agent、学生、家长 | `platform.mastery_estimate.state` | live | 证据化鼓励（具体到哪颗星何时点亮、依据是什么） |

### 中圈指标（效度检验通过前只给 Agent）

| id | 定义 | 最低样本 | 效度检验 |
|---|---|---|---|
| `middle.g5.plan_completion_rate` | 完成任务数 ÷ 计划任务数（以学生调整后的计划为准） | 至少 2 周 | 与后续进步相关（观察用） |
| `middle.g5.persistence` | 周活跃天数；中断 3 天以上后 7 天内恢复的比例 | 至少 4 周 | 观察用，不参与评价 |
| `middle.g6.self_initiated_sessions` | 学生主动发起、非提醒触发的学习会话数 ÷ 周 | 至少 4 周 | 与计划完成率、延迟复测结果的相关（观察用） |
| `middle.g7.independent_solve_rate` | 无提示首次作答正确数 ÷ 无提示首次作答数，按学科与认知层次滚动 14 天 | 每层至少 10 题 | 与延迟复测结果相关 |
| `middle.g7.hint_dependency` | 完成一题所用最高提示等级，按各科阶梯长度归一后取均值，看趋势 | 至少 15 题 | 下降是否伴随独立解题率上升 |
| `middle.g7.transfer_gap` | 变式与跨章题独立正确率减同节点母题独立正确率 | 两类各至少 10 题 | 预测综合题得分 |
| `middle.g7.self_assessment_calibration` | 做题前预判与实际结果的 Brier 分数及偏差方向 | 至少 20 次 | 与考试预测偏差方向的一致性 |
| `middle.g7.retention_rate` | 间隔 7 天以上的复测做对数 ÷ 应复测数 | 至少 8 次 | 预测学校考试同节点得分 |
| `middle.g7.reading_question_error_share` | 审题大类错因数 ÷ 全部错因数；跨科时至少两科各 5 条 | 见定义 | 针对训练后是否下降 |

### 六维画像（呈现视图，不存独立分数）

| 维度 | 首发 | 取自字段 |
|---|---|---|
| 知识掌握 | 对外展示（仅数学） | `inner.g3.node_mastery`、`inner.g3.next_retest_at`、`through.g9.star_lit_history` |
| 知识迁移 | 对外展示（仅数学） | `inner.g4.transfer_gap_math` |
| 学习策略 | 静默采集 | `middle.g7.independent_solve_rate`、`middle.g7.hint_dependency`、`middle.g7.transfer_gap`、`middle.g7.self_assessment_calibration`、`middle.g7.reading_question_error_share` |
| 自主学习 | 静默采集 | `middle.g5.plan_completion_rate`、`middle.g6.self_initiated_sessions` |
| 表达应用 | 静默采集 | — |
| 学习持续性 | 静默采集 | `middle.g5.persistence`、`middle.g7.retention_rate` |

### 不再采集的旧字段（retired）

| 旧字段 | 来源 | 别名 | 原因 |
|---|---|---|---|
| student_seg_t | wenli 迁移枚举 | segment、student_segment、学生分型 | 层级标签（规则 12） |
| 基础薄弱 | wenli student_seg_t 取值 | — | 层级标签（规则 12） |
| 中等提升 | wenli student_seg_t 取值 | — | 层级标签（规则 12） |
| 高分冲刺 | wenli student_seg_t 取值 | — | 层级标签（规则 12） |
| 目标校冲刺 | wenli student_seg_t 取值 | — | 层级标签（规则 12） |
| profiles.segment | wenli 表 profiles | segment | 层级标签（规则 12） |
| student_label | wenli DiagnoseResultSchema | student_type、学生标签 | 层级标签（规则 12） |
| rank_range | wenli DiagnoseResultSchema（全市位次区间） | city_rank、rank_estimate、位次区间 | 同伴比较；外部考试只记录学生或家长录入的排名信息（inner.g2.exam_rank_band） |
| abilityBand | aya lib/calibration.ts CalibrationState | ability_band、能力分档 | 层级标签（规则 12） |

### 旧产品对应字段（只追溯，不迁移数值）

| 字段 | 旧产品对应 |
|---|---|
| `inner.g2.exam_score` | aya lib/exam.ts ExamAnalysis、wenli 表 semester_score |
| `inner.g2.exam_prediction` | aya lib/exam.ts ExamAnalysis.scoreLow/scoreHigh、aya lib/consultation.ts Consultation.estLow/estHigh、wenli GradeResultSchema.score_range、契约 v0.9 ReportFields.forecast |
| `inner.g3.node_mastery` | aya lib/types.ts NodeMastery、wenli 表 point_mastery |
| `inner.g3.next_retest_at` | wenli 表 point_mastery.next_review_at、wenli 表 point_mastery.review_stage |
| `inner.g3.error_attribution` | aya lib/types.ts StudentProfile.rootCauseCounts、wenli 表 error_record、daofa 失分归因 |
| `inner.g4.skill_view` | aya lib/types.ts StudentProfile.abilityScores、aya lib/evaluation.ts EvalDimension、wenli 表 ability_state、daofa 表 daofa_ability |
| `through.g9.weekly_snapshot` | aya lib/weekly.ts WeeklyReport、aya lib/trends.ts TrendPoint、wenli 表 mastery_snapshot、wenli 表 parent_report、wenli 表 stage_report |

### 与 SIE V1.1 的覆盖对照

| SIE 条目 | 字典字段 |
|---|---|
| 第四章 建档必填：年级 | `outer.g1.grade` |
| 第四章 建档必填：地区 | `outer.g1.region` |
| 第四章 建档必填：各科教材版本 | `outer.g1.textbook_version` |
| 第四章 建档必填：最想改善的学科 | `through.g9.focus_subject` |
| 第四章 建档必填：近期目标 | `through.g9.near_term_goal` |
| 第四章 建档必填：每周可用时间 | `outer.g1.weekly_available_time` |
| 第四章 建档必填：学习方式偏好 | `middle.g6.learning_mode_preference` |
| 第四章 建议：最近一次大考成绩与排名区间 | `inner.g2.exam_score`、`inner.g2.exam_rank_band` |
| 第四章 建议：带批改的试卷 | `inner.g2.graded_paper_upload` |
| 第四章 建议：学生自评痛点 | `middle.g6.self_pain_points` |
| 第四章 可选：家长问卷（作息、陪读方式、期望与可配合程度） | `outer.g8.study_routine`、`outer.g8.sleep_schedule`、`outer.g8.accompany_style`、`outer.g8.expectation`、`outer.g8.cooperation_capacity` |
| 第四章 可选：错题本、作业、教师评语 | `inner.g2.graded_paper_upload` |
| 第四章 可选：校外补习情况 | `outer.g1.tutoring` |
| 第四章 周期：考试成绩与范围、考前冻结预测 | `inner.g2.exam_score`、`inner.g2.exam_scope`、`inner.g2.exam_prediction` |
| 第四章 持续：平台内作答与学习行为 | `inner.g3.evidence_events`、`middle.g5.behavior_events` |
| 第四章 持续：做题前“能做对吗” | `middle.g7.confidence_probe` |
| 第三章 ① 基础身份与学习环境（设备） | `outer.g1.devices` |
| 第三章 ③ 掌握状态、五道门槛、错因、复习时间 | `inner.g3.node_mastery`、`inner.g3.error_attribution`、`inner.g3.next_retest_at` |
| 第三章 ④ 各科能力视图；迁移差 | `inner.g4.skill_view`、`inner.g4.transfer_gap_math` |
| 第三章 ⑥ 本人说出的兴趣、主动发起学习 | `middle.g6.stated_interests`、`middle.g6.self_initiated_sessions` |
| 第三章 ⑧ 家校沟通 | `outer.g8.school_communication` |
| 第三章 ⑨ 目标、阶段干预、复测结果、每周快照、点亮记录 | `through.g9.near_term_goal`、`through.g9.interventions`、`through.g9.weekly_snapshot`、`through.g9.star_lit_history` |
| 第五章 指标：独立解题率 | `middle.g7.independent_solve_rate` |
| 第五章 指标：提示依赖度 | `middle.g7.hint_dependency` |
| 第五章 指标：迁移差 | `middle.g7.transfer_gap` |
| 第五章 指标：自评校准 | `middle.g7.self_assessment_calibration` |
| 第五章 指标：保持率 | `middle.g7.retention_rate` |
| 第五章 指标：计划完成率 | `middle.g5.plan_completion_rate` |
| 第五章 指标：审题类错因占比 | `middle.g7.reading_question_error_share` |
| 第五章 指标：持续性 | `middle.g5.persistence` |
| 第五章 讲法响应 | `middle.g7.teaching_response` |
| 第九章 exam_record | `inner.g2.exam_score`、`inner.g2.exam_scope`、`inner.g2.exam_rank_band`、`inner.g2.exam_difficulty_note` |
| 第九章 behavior_event | `middle.g5.behavior_events` |
| 第九章 self_report | `outer.g1.weekly_available_time`、`middle.g6.stated_interests`、`middle.g6.learning_mode_preference`、`middle.g6.self_pain_points`、`through.g9.focus_subject`、`through.g9.near_term_goal` |
| 第九章 guardian_report | `outer.g1.tutoring`、`outer.g8.cooperation_capacity` |
| 第九章 confidence_probe | `middle.g7.confidence_probe` |
| 第九章 claim / claim_history | `inner.g3.node_mastery`、`through.g9.star_lit_history` |
| 第九章 learner_metric | `middle.g7.independent_solve_rate`、`middle.g5.plan_completion_rate` |
| 第九章 exam_prediction | `inner.g2.exam_prediction` |
| 第九章 teaching_response | `middle.g7.teaching_response` |
| 第九章 dispute | `through.g9.disputes` |
| 第九章 intervention_log | `through.g9.interventions` |
| 第九章 profile_snapshot | `through.g9.weekly_snapshot` |
| 第九章 purpose_grant | 不属画像字段：用途分项授权是治理记录，等合规方案后建表（对照文档 #6） |
| 第九章 consent_record（已有 platform.consent_record） | 不属画像字段：监护人同意记录，由 0002 管理 |
<!-- END GENERATED: dictionary -->

## 与已有表的对照

- `platform.guardian_report`：现表按 5 个主题存家长自由文本（家长观察原文）。SIE 的结构化 `guardian_report(field, value jsonb, superseded_at)` 在 SIE-01 另建，两者都标“家长观察”（待人确认 #6，已按默认确认）。
- `platform.attempt.self_report`：作答时的把握度（confident / unsure / guessed），与做题**前**的“能做对吗”（`insight.confidence_probe`）时机不同，字典里是两个字段。
- `platform.mastery_estimate` / `skill_observation` / `error_attribution`：KGE 计算的推断。对学生或家长的陈述不能直接读这些表，只能读引用了它们的判断（规则 11；过渡期已合并的星图按原样运行）。
- `platform.consent_record`：监护人同意记录；用途分项授权 `purpose_grant` 等合规方案（对照文档 #6）。二者都不是画像字段。
- 各科旧结论（KGE-00 的 27 个结构）：数值不迁移，只存档；触及不推断清单或同伴比较的 8 个字段或取值列在“不再采集的旧字段”。
