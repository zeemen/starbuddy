# 结论字段清单（SIE 迁移输入）

> 由 `tools/kge-inventory` 生成，请勿手改。依据：开发计划 v2.0 盘点项 7；完整方案 v1.1 5.5。
> **SIE 迁移处理：以下全部不迁移，只存档。** 新判断由新证据重算生成，旧报告只作存档。
> 算法一栏是按代码结构的判断：所在文件或使用方有模型调用记“含模型调用”，否则记“规则（纯函数）”；库表与枚举不判断。
> 证据引用、置信度按字段名识别（如 evidence、*_ids、confidence），只说明字段是否存在，不说明内容是否可靠。
> 触及不推断清单的字段也按字段名与取值识别，可能漏报或误报，需人工核对。

## 汇总

| 学科 | 结论结构数 | 字段数 | 带证据引用的结构 | 带置信度的结构 | 触及不推断清单或同伴比较的字段 |
|---|---|---|---|---|---|
| 数学 · 阿鸦 | 10 | 85 | 2 | 1 | 1 |
| 语文 · 文砺 | 11 | 79 | 0 | 3 | 7 |
| 道法 · 知行 | 6 | 35 | 1 | 0 | 0 |

## 触及不推断清单或同伴比较的字段

| 学科 | 来源 | 结构 | 字段 / 取值 | 原因 |
|---|---|---|---|---|
| 语文 · 文砺 | `packages/shared/lib/domain/schemas.ts` | DiagnoseResultSchema | student_label | 层级标签（规则 12：不贴层级标签） |
| 语文 · 文砺 | `packages/shared/lib/domain/schemas.ts` | DiagnoseResultSchema | rank_range | 排名 / 位次（SIE：不展示同伴比较，外部考试只记排名信息） |
| 语文 · 文砺 | `supabase/migrations` | profiles | segment | 层级标签（规则 12：不贴层级标签） |
| 语文 · 文砺 | `supabase/migrations` | student_seg_t | 基础薄弱 | 层级标签（规则 12：不贴层级标签） |
| 语文 · 文砺 | `supabase/migrations` | student_seg_t | 中等提升 | 层级标签（规则 12：不贴层级标签） |
| 语文 · 文砺 | `supabase/migrations` | student_seg_t | 高分冲刺 | 层级标签（规则 12：不贴层级标签） |
| 语文 · 文砺 | `supabase/migrations` | student_seg_t | 目标校冲刺 | 层级标签（规则 12：不贴层级标签） |
| 数学 · 阿鸦 | `lib/calibration.ts` | CalibrationState | abilityBand | 层级标签（规则 12：不贴层级标签） |

## 明细

### 数学 · 阿鸦

| 来源 | 结构 | 类型 | 算法 | 证据引用 | 置信度 | 字段 |
|---|---|---|---|---|---|---|
| `lib/calibration.ts` | CalibrationState | interface | 规则（纯函数）（所在文件没有模型调用） | 有 | 有 | evidence、theta、consistency、confidence、abilityBand、difficultyBand、settled、note |
| `lib/consultation.ts` | Consultation | interface | 规则（纯函数）（所在文件没有模型调用） | 无 | 无 | hasData、estScore、estLow、estHigh、basis、band、round、is120、progression、weakMethods、gateSummary、pathwayAdvice、findings |
| `lib/evaluation.ts` | EvalDimension | interface | 规则（纯函数）（所在文件没有模型调用） | 有 | 无 | key、name、score、level、evidence、summary |
| `lib/exam.ts` | ExamAnalysis | interface | 规则（纯函数）（所在文件没有模型调用） | 无 | 无 | total、correct、rate、scoreLow、scoreHigh、byBoard、overtime、skippedCount、knowledgeIssues、strategyIssues、stabilityIssues、strengths |
| `lib/teacher.ts` | ModuleImbalance | interface | 规则（纯函数）（所在文件没有模型调用） | 无 | 无 | board、strongDim、strongScore、weakDim、weakScore、gap、intervention |
| `lib/teacher.ts` | TeacherReport | interface | 规则（纯函数）（所在文件没有模型调用） | 无 | 无 | hasData、totalAttempts、gateDistribution、imbalances、confidentErrors、rootCauses、weakHotPoints、diagnosis |
| `lib/trends.ts` | TrendPoint | interface | 规则（纯函数）（所在文件没有模型调用） | 无 | 无 | label、at、stableCount、correctRate、abilityAvg、attempts |
| `lib/types.ts` | NodeMastery | interface | 规则（纯函数）（所在文件没有模型调用） | 无 | 无 | nodeId、p、attempts、correct、gate |
| `lib/types.ts` | StudentProfile | interface | 规则（纯函数）（所在文件没有模型调用） | 无 | 无 | createdAt、updatedAt、intake、attempts、exams、nodeMastery、abilityScores、rootCauseCounts、parentGate |
| `lib/weekly.ts` | WeeklyReport | interface | 规则（纯函数）（所在文件没有模型调用） | 无 | 无 | weekStart、weekEnd、hasActivity、retained、newlyMastered、activity、activeDays、difficulties、comparison、nextGoals、parentSupport |

### 语文 · 文砺

| 来源 | 结构 | 类型 | 算法 | 证据引用 | 置信度 | 字段 |
|---|---|---|---|---|---|---|
| `packages/shared/lib/domain/schemas.ts` | DiagnoseResultSchema | zod | 含模型调用（被含模型调用的文件使用：apps/student/app/api/diagnose/route.ts） | 无 | 有 | student_label、board_focus、total_lost、quick_recover、question_count、rank_range、ability、errors、priorities、teacher_note、uncertainty_note、data_confidence、process_profile |
| `packages/shared/lib/domain/schemas.ts` | GradeResultSchema | zod | 含模型调用（被含模型调用的文件使用：apps/student/app/api/essays/grade/route.ts） | 无 | 有 | band、est_score、score_range、dims、marks、upgrade、needs_human_review、uncertainty_note |
| `packages/shared/lib/domain/schemas.ts` | ParentReportSchema | zod | 含模型调用（被含模型调用的文件使用：apps/parent/app/api/report/weekly/route.ts） | 无 | 无 | overview、abilityChange、weakPoints、familyAdvice |
| `packages/shared/lib/domain/schemas.ts` | PlanResultSchema | zod | 含模型调用（被含模型调用的文件使用：apps/student/app/api/plan/route.ts） | 无 | 无 | stages、priorities、est_gain |
| `supabase/migrations` | ability_state | table | 未判断（库表）（写入方要看应用代码） | 无 | 有 | id、student_id、domain、level、mastery、updated_at、data_confidence |
| `supabase/migrations` | mastery_snapshot | table | 未判断（库表）（写入方要看应用代码） | 无 | 无 | id、student_id、snap_date、avg_mastery、by_domain、created_at |
| `supabase/migrations` | parent_report | table | 未判断（库表）（写入方要看应用代码） | 无 | 无 | id、student_id、report_date、week_start、week_end、metrics、overview、ability_change、weak_points、family_advice、created_at |
| `supabase/migrations` | point_mastery | table | 未判断（库表）（写入方要看应用代码） | 无 | 无 | student_id、point_id、mastery、attempts、last_score_rate、mastered、mastered_at、updated_at、alpha、beta、review_stage、next_review_at、last_attempt_at |
| `supabase/migrations` | profiles | table | 未判断（库表）（写入方要看应用代码） | 无 | 无 | segment |
| `supabase/migrations` | stage_report | table | 未判断（库表）（写入方要看应用代码） | 无 | 无 | id、student_id、stage_start、stage_end、metrics、headline、parent_narrative、student_narrative、created_at |
| `supabase/migrations` | student_seg_t | enum | 未判断（枚举）（学生分型的取值） | 无 | 无 | 基础薄弱、中等提升、高分冲刺、目标校冲刺 |

### 道法 · 知行

| 来源 | 结构 | 类型 | 算法 | 证据引用 | 置信度 | 字段 |
|---|---|---|---|---|---|---|
| `packages/shared/lib/domain/daofa-diagnosis.ts` | AbilityRow | interface | 规则（纯函数）（所在文件没有模型调用） | 无 | 无 | dimension、avg_rate、sample_count |
| `packages/shared/lib/domain/daofa-diagnosis.ts` | DiagnosisDimension | interface | 规则（纯函数）（所在文件没有模型调用） | 无 | 无 | key、short、desc、rate、mastery、sampleCount |
| `packages/shared/lib/domain/daofa-diagnosis.ts` | M5Diagnosis | interface | 规则（纯函数）（所在文件没有模型调用） | 无 | 无 | dimensions、overallRate、weakest、recommendations、parentSummary、hasData |
| `packages/shared/lib/domain/daofa-diagnosis.ts` | Recommendation | interface | 规则（纯函数）（所在文件没有模型调用） | 无 | 无 | dimension、why、action |
| `supabase/migrations` | daofa_ability | table | 未判断（库表）（写入方要看应用代码） | 无 | 无 | id、student_id、dimension、avg_rate、sample_count、updated_at |
| `supabase/migrations` | daofa_submission | table | 未判断（库表）（写入方要看应用代码） | 有 | 无 | id、student_id、question_id、persona、answer_text、image_path、graded_json、total_score、needs_human_review、model、created_at |
