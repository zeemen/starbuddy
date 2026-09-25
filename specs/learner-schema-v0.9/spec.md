# 学习者 Schema v0.9 起草

状态：草稿（2026-09-25）。按负责人“起草学习者 Schema v0.9”的指示，spec 与迁移草稿放在同一个 PR 里评审，没有先单独确认 spec。确认人、日期：＿＿＿
关联：完整方案 v1.0 第四章（学习者模型）、第 5.5 节（数据迁移与能力坐标映射）、第七章（关键对象） · 开发计划 冲刺 0 W2、冲刺 1-B / 1-C · ClaudeCode 执行方案 第八章“W2 周一”第 2 条 · ADR 01、02、05、09 · 契约 `packages/contracts/openapi/plugin-api.v0.9.yaml`

## 目标
在 `db/migrations/` 起草 `platform` schema 中家庭关系与学习者模型两部分的迁移（**只写文件，不在任何共享环境执行**），并附上 ERD 与 RLS 测试。
这样契约里 `evidence`、`ErrorAttribution`、`SkillObservation`、`MasterySnapshot`、`GradeResult` 等结构都有了对应的落库位置。W2 周五评审进入 v0.9。

## 范围
- 做：
  - **家庭与身份**：account、account_contact、household、student_profile、guardian_student_link、guardian_invite、consent_record、subject_enrollment。
  - **内容登记**：curriculum_version（六段标识）、skill_node（`subject + local_id` 命名空间）、item（准入状态）。
  - **原始证据**：evidence_event、attempt，按月分区（ADR 05）。
  - **模型推断**：skill_observation、error_attribution、mastery_estimate、grade_result、grade_dimension。每条推断都记录算法版本、置信度、证据 ID 和更正历史（CLAUDE.md 规则 6）。
  - **家长自述**：guardian_report，与证据、推断分开存（规则 6）；学生端不可见（完整方案第四章）。
  - **复核队列**：review_task（`human_review_required` 的去向）。
  - **角色与 RLS**：数据库角色、授权函数、全部表开 RLS；RLS 测试。
  - 每个迁移都有 `.down.sql`，测试里验证“迁移 → 回滚 → 再迁移”可以走通（开发计划 冲刺 1-B：迁移可回滚）。
  - ERD：`docs/adr/erd.md`。
- 不做：
  - 计量与权益（usage_ledger、entitlement），ADR 06 另立 spec，属于必停项。
  - 学习计划、课堂、模考、申诉（study_plan、lesson_session、exam_run、appeal），阶段 0 后续冲刺再做。
  - 各科私有 schema（`wenli.*` 等），按适配器设计另起。
  - 星亮门槛配置表与复测调度器（冲刺 4-C），本 spec 只保证 mastery_estimate 能承载门槛状态。
  - 不执行任何共享环境的迁移；不连接 Supabase 或任何远程库。

## 验收标准（可测试）
- [x] 迁移只用标准 Postgres 16 能力，不引用 `auth.*`、`storage.*` 等托管专有 schema（ADR 02 第 1 条）。
- [x] 每个迁移都有对应的 down 脚本；在全新的本地库上执行“全部 up → 全部 down → 全部 up”能成功。
- [x] 证据、推断、家长自述三类数据分表存放；推断表都有 `algorithm_version`、`confidence`、非空的 `evidence_ids`，并支持更正（`superseded_by`）。
- [x] `evidence_event`、`attempt` 按月分区，唯一约束包含分区键（ADR 05）；必备索引存在。
- [x] RLS 测试覆盖（CLAUDE.md 规则 5）：
  - 家长只能通过有效的 `guardian_student_link` 读取学生数据；**同一手机号不能跨档案访问**（开发计划 冲刺 1-B 验收）；
  - 家长不能自己插入 link 绕过邀请（修正文砺 `guardians` 的漏洞）；
  - 学生只读自己的数据，不能写判分、掌握度、错因（ADR 02 第 4 条；修正知行漏洞）；
  - 学生读不到家长自述；
  - 没有设置当前账号时，什么都读不到；
  - 学科服务角色能写证据和推断，但读不到联系方式。
- [x] 判分落库的约束与契约一致：`human_review_required` 为真时必须有原因；`model_version` 不能写 `routed`。
- [x] 测试数据全部为合成数据（规则 8）。
- [x] `make db-test` 在本地临时库上运行上述测试；缺 Postgres 时打印“跳过”。

验证方式：`make db-test`（131 项断言）与 `make db-mutation`（56 个变异全部被发现），本地 Postgres 16。

### security-reviewer 审查（2026-09-25）
首轮结论：可以作为草稿提交，但**安全审查不通过**，有 7 个阻断。首版提交 `738658c` 说明里的“推断只能更正不能改写”“库约束与契约 v0.9 一致”两句当时并不成立。原因是首版夹具用超级用户插入，恰好绕开了注册、建档、绑定这几条路径。修复如下：
1. **账号接管**：BFF 不再能直接读写账号与联系方式，只能用 `signup_guardian`、`lookup_accounts_by_phone`。
2. **学生伪装成家长**：建家庭、建档、创建者绑定都收进 `create_student`，要求当前账号是有效家长；同意记录也要求是有效家长。
3. **把别人的账号建进自己的家庭**：`create_student` 在同一事务里新建学生账号，不接受外部传入的 account_id。
4. **解除绑定后预签邀请恢复访问**：解除绑定时作废该家长签发的未用邀请；兑换时重新校验签发人仍有权限；签发人不能兑换自己的邀请；邀请码由库内生成。
5. **回滚删角色、预置 BYPASSRLS 角色**：角色移到 `db/bootstrap/roles.sql`（人执行），迁移只断言角色属性，down 不删角色。
6. **更正可以被滥用**：更正只能单向、一次，指向同一学生、同一对象、更晚的记录；复核未结案的判分不能被“不复核”的判分取代。
7. **漏测**：每条策略都补了正反向断言，审查者的 28 个变异与新增规则的变异都并入 `db/tests/mutations.sh`。

同时采纳的建议：禁用账号不能再读；`revoked_at` 由库写入、解除的绑定不能恢复；needs_review 与验证器原因码必须带 `verifier_boundary_case`；原因码限定取值；主观维度时父判分置信度 ≤ 0.5；判分外键关联作答，证据外键关联题目；证据文本 ≤ 20000 字；分区权限断言；search_path 末尾加 pg_temp；测试目录权限 700、固定 PG16；夹具手机号改用未分配的国家码 +999。
另外补了契约里表达不了的库约束：总分和维度分不超过满分；有主观维度或 null 维度时父判分必须复核（约束触发器）。

## 涉及的必停项（见 CLAUDE.md）
- `db/migrations` 与 RLS 策略：**是**。PR 首行写 `[NEEDS-HUMAN]`，迁移由人执行。

## 依赖与输入
- ADR 02（共库分 schema、RLS 归平台）、ADR 05（分区）、ADR 09（数据分级）、ADR 10（认证由 BFF 自建）。这些都还是草稿，签字结果可能要求改 schema。
- 契约 v0.9（已合并）。

## 待人确认（安全审查新增的在 #7 之后）
1. **当前用户身份怎么传给数据库**：草案用会话变量 `app.account_id`，由 BFF 在每个事务开头 `SET LOCAL`。这取决于 ADR 10 的认证方案。
2. **证据的幂等**：ADR 05 写的是 `UNIQUE (idempotency_key, occurred_at)`。但一次请求会产生多条证据，所以草案改为以契约里适配器生成的 `evidence_id` 作为幂等身份：主键 `(evidence_id, occurred_at)`，同时保留 `request_idempotency_key` 列和索引。需要确认后回写 ADR 05。
3. **手机号**：只存在 `account_contact`，BFF 登录用；任何授权判断都不读它。手机号允许重复（学生可能用家长的手机号），加密方式待 ADR 10 决定。
4. **学生档案的最小字段**：只存年级、地区、教材版本、出生年月（判断是否满 14 周岁），不存姓名和学校。昵称是否需要？
5. **家长自述的可见性**：家长可见、可删；学生不可见；学科服务只读用于报告。是否需要“家长可以选择对学生可见”？
6. **掌握度状态**沿用完整方案 5.5 的五道门槛，再加 `unseen`：`unseen / understand / execute / stable / transfer / retain`。星亮等于 `transfer`，外圈等于 `retain`。
7. **学生能否解除家庭创建者的绑定**：草案允许学生本人解除任何家长的绑定。
8. **学生账号怎么登录**：v0.9 只有家长账号有联系方式，学生账号由家长通过 `create_student` 建立，学生的登录凭证（例如家长签发的登录码）还没设计。
9. **家长能看学生的原始作答与转写**（`evidence_read` 策略）：完整方案说“学生与家长分别看到星图和证据”，但家长版周报不含逐句对话。是否要把家长对原始证据的读权限收窄？
10. **学习服务可以读全部家长自述**（用于报告的外层字段）：是否需要按科或按用途进一步限制？
11. **选科（subject_enrollment）会不会影响权益与计量**：目前学生和家长都能直接写。如果会影响，应改为经 BFF 权益校验的函数。
12. **证据 ID 不是全局唯一**：分区表上无法对 evidence_id 单独建唯一约束，同一 ID 换个 occurred_at 会被当作另一条证据；推断表的 `evidence_ids` 也不校验是否存在。目前由学习服务保证。
13. **契约里表达、库里还没约束的规则**：未准入证据 → practice + 复核；没有维度的整体模型判分必须复核；`transfer` 及以上只能由已准入证据推动。这些是跨表规则，目前由学习服务保证。
14. **app.account_id 的信任边界**：完全信任 BFF。生产必须用 `SET LOCAL`（否则经连接池会串号），部署时登录角色不能同时继承 bff 和 learning（0001 有断言）。
