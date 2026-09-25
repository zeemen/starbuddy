# 学习者 Schema v0.9 · ERD

状态：草稿（与 `db/migrations/0001`–`0006` 一致）。spec：`specs/learner-schema-v0.9/spec.md`。
依据：完整方案 v1.0 第四章、第 5.5 节、第七章；ADR 02、05、09。

## 三类数据分开存（CLAUDE.md 规则 6）

| 类别 | 表 | 谁写 | 学生可读 | 家长可读 |
|---|---|---|---|---|
| 身份与家庭 | account、account_contact、household、student_profile、guardian_student_link、guardian_invite、consent_record、subject_enrollment | BFF（受 RLS 与授权函数约束） | 自己的 | 已绑定学生的 |
| 内容登记 | curriculum_version、skill_node、item | 学习服务（发布闸门） | 已发布 / 已准入的 | 同左 |
| **原始证据** | evidence_event、attempt（按月分区） | 只有学习服务，只追加 | 自己的 | 已绑定学生的 |
| **模型推断** | skill_observation、error_attribution、mastery_estimate、grade_result、grade_dimension | 只有学习服务，只能更正不能改写 | 自己的 | 已绑定学生的 |
| **家长自述** | guardian_report | 写它的家长 | **不可见** | 只有写它的家长 |
| 复核 | review_task | 学习服务 | 否 | 否 |

## 关系图

```mermaid
erDiagram
  account ||--o| account_contact : "登录用，不参与授权"
  account ||--o{ household : "created_by"
  household ||--o{ student_profile : ""
  account ||--o| student_profile : "学生账号"
  account ||--o{ guardian_student_link : "家长"
  student_profile ||--o{ guardian_student_link : ""
  student_profile ||--o{ guardian_invite : "一次性邀请（只存哈希）"
  student_profile ||--o{ consent_record : "只追加"
  student_profile ||--o{ subject_enrollment : ""

  curriculum_version ||--o{ skill_node : "六段标识"
  curriculum_version ||--o{ item : ""
  skill_node ||--o{ item : "subject + local_id"

  student_profile ||--o{ evidence_event : "按月分区"
  student_profile ||--o{ attempt : "按月分区"
  attempt }o--|| evidence_event : "response_evidence_id"

  student_profile ||--o{ skill_observation : ""
  student_profile ||--o{ error_attribution : ""
  student_profile ||--o{ mastery_estimate : "每个能力点一条 is_current"
  student_profile ||--o{ grade_result : ""
  grade_result ||--|{ grade_dimension : ""
  grade_result ||--o| review_task : "human_review_required"

  student_profile ||--o{ guardian_report : "学生不可见"
```

推断表都用 `evidence_ids uuid[]`（至少一条）引用 evidence_event，没有建外键：证据表是分区表，而且证据与推断可能跨月。引用的完整性由学习服务在写入时保证。

## 授权模型

- **当前账号**：BFF 在每个事务开头 `SET LOCAL app.account_id = '<账号 UUID>'`。没有设置时，按账号的策略一律不放行（spec 待人确认 #1）。
- **授权函数**：`is_self_student`、`is_linked_guardian`、`can_read_student`、`can_read_household`。属主是 `starbuddy_authz`（BYPASSRLS，不授予任何登录角色），固定了 search_path。
- **家长访问**只看 `guardian_student_link` 中 `status = 'active'` 的记录。**手机号不参与任何授权判断**（规则 5）。
- **建立绑定**只有两种方式：
  - 家庭创建者绑定本家庭的学生（RLS 策略 `link_household_creator`）；
  - 兑换学生或已绑定家长签发的一次性邀请（`redeem_guardian_invite`）。
- **角色**：

| 角色 | 用途 |
|---|---|
| `starbuddy_bff` | 代表登录账号访问，受 RLS 约束 |
| `starbuddy_learning` | 唯一能写证据与推断的服务角色；读不到联系方式 |
| `starbuddy_authz` | 只作为授权函数的属主 |

## 分区（ADR 05）

- `evidence_event`、`attempt` 按 `occurred_at` 月分区，边界是 UTC 月初。初始建 2026-09 起 16 个月。
- 没有 DEFAULT 分区：超出范围的写入直接失败，便于发现“分区没建好”。
- 分区上不授予任何权限，只能经父表访问，所以父表的 RLS 一定生效。
- 主键 `(evidence_id, occurred_at)`：契约要求重试时返回同一个 evidence_id，所以用它作为幂等身份（spec 待人确认 #2，需要回写 ADR 05）。

## 与契约 v0.9 的对应

| 契约结构 | 表 |
|---|---|
| `EvidenceDraft` / `EvidenceRecord` | evidence_event（payload 白名单由 `evidence_payload_ok` 检查，与契约一致） |
| `AttemptInput` | attempt |
| `SkillObservation` | skill_observation |
| `ErrorAttribution` | error_attribution |
| `MasterySnapshot` | mastery_estimate（is_current） |
| `GradeResult` + 信封 | grade_result + grade_dimension |
| `review_reasons` | review_task |

库约束还补上了契约里表达不了的规则：
- 总分、维度分不超过满分；
- 有主观维度或 null 维度时，父判分必须复核。这条是跨行约束，用约束触发器在事务提交时检查。
