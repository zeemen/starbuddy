# ADR 02 · Postgres 共库分 schema

- 状态：**已采纳（2026-10-09，用户在对话中确认按评审包默认建议；仍待讨论项见 `docs/reviews/decisions-2026-10-09.md`）**。按评审包默认采纳的是正文选项 A 与 insight 增补；kge 增补、policy 增补“无单列默认”，0047 / 0048 / 0050 / 0052 跨 schema 例外“无默认，需讨论”，三者本次未采纳，仍随各自迁移 PR 待人定。签字不等于执行迁移。
- 签字人：用户（对话确认）
- 日期：2026-09-25
- 决策人：待定（建议：技术负责人 + 资深工程师）
- 关联：完整方案 v1.0 第 5.5 节末行、第七章；开发计划 W2（原标题“Supabase 共库分 schema”）；ADR 01、05、06、09、10
- 来源：四份盘点 §3（数据库）；ADR 09 §4“需要一起决定的事项”

> [NEEDS-HUMAN] 本 ADR 决定 `db/migrations` 的组织方式和 RLS 的归属，属于 CLAUDE.md 规定的必停项。

---

## 1. 背景

### 1.1 总纲的设定
完整方案 5.5：“先共库分 schema（`platform.*` 与 `wenli.* / aya.* / zhixing.* / echo.*`），家庭关系与 RLS 由平台 schema 统一；后期按瓶颈拆分。”首期写的是“仍在 Supabase”。

### 1.2 ADR 09 带来的变化
ADR 09 建议 pilot 和生产全链路境内。Supabase 托管版没有中国大陆区域（待核实），所以“Supabase”不能再作为 pilot 和生产的前提。本 ADR 的标题因此从“Supabase 共库分 schema”改为“Postgres 共库分 schema”。托管方式由 ADR 10 决定。

### 1.3 盘点发现

| 学科 | 数据库现状 |
|---|---|
| 文砺 | Supabase，35 个迁移，48 张表，全部开 RLS；**和知行共用同一个 Supabase 项目**；有 `guardians` 邀请码绑定，结构接近 `guardian_student_link`，但 `guardians_guardian_rw` 的 with check 没约束 student_id，可能绕过绑定 |
| 知行 | 寄宿在文砺库里（迁移 0033–0035）；判分和能力行学生可写（违反 CLAUDE.md 第 3 条）；学生可以把自己出的题改成“已通过” |
| 阿鸦 | Supabase，2 个手工 SQL，**没有迁移工具**；代码只用 `profiles` 一张表，存整份 JSON；没有家长实体 |
| 开口成章 | **没有数据库**，全部在浏览器 localStorage |

- 四科都用 `public` schema，没有一个用了学科命名空间。
- 四科都**没有真实用户**（四份 decisions），所以没有需要迁移的生产数据。

---

## 2. 选项

### 选项 A：单个 Postgres，平台和四科各一个 schema
- `platform.*`：家庭关系、学习者模型、内容版本、权益与计量。
- `wenli.* / aya.* / zhixing.* / echo.*`：各科私有表（游戏化数值、学科专用缓存等），**不能存学生画像的第二份真相**。
- RLS 由平台统一定义；各科 schema 的表如果含学生数据，也必须引用 `platform` 的授权函数。

**优点**：跨科查询和事务简单；一套备份和权限体系；与总纲一致。
**缺点**：一个库的故障影响所有学科；各科迁移要在同一条迁移序列里协调。

### 选项 B：平台一个库，每科一个库
**优点**：隔离好。
**缺点**：跨科报表和星图需要跨库；家庭授权要在多个库里重复实现，RLS 容易不一致。阶段 0 的规模（试点约 100 户，内测约 1,000 户）完全不需要这种隔离。**不推荐。**

### 选项 C：沿用四科现有的 Supabase 项目，平台通过 API 汇总
违反“学生画像唯一真相源”（CLAUDE.md 第 2 条），并且阿鸦、开口成章根本没有可用的库。**不推荐。**

---

## 3. 决定

**待签字。**

**建议采纳选项 A**，并加上以下约束：

1. **环境与托管按 ADR 09 分开**：dev 和 staging 可以用 Supabase 托管版，只放合成数据；pilot 和生产用境内 Postgres（具体选型见 ADR 10）。迁移脚本必须**与托管无关**：只用标准 Postgres 能力，不依赖 Supabase 专有的 schema（如 `auth.*`、`storage.*`）。如果要用，先在 ADR 10 里确认境内托管能提供对应能力。
2. **迁移只有一条序列**：全部放在 `db/migrations/`，按编号顺序执行，平台和各科共用。每个迁移只能改一个 schema，文件名带 schema 前缀。
3. **RLS 归平台**：家长访问只能通过 `platform.guardian_student_link`（CLAUDE.md 第 5 条）；各科 schema 里含学生数据的表，读权限都必须调用平台的授权函数。任何 RLS 变更都要附测试。
4. **写权限收紧**：判分、能力、掌握度类的表，学生角色只读，由服务端角色写入（修正知行现有的 owner `for all`）。
5. **四科既有的表不整体迁移**：没有真实用户，所以不做数据迁移。各科私有 schema 按适配器设计文档**重新建**，只保留确实需要的表。
6. **事件表的分区策略**见 ADR 05；**计量账本表**见 ADR 06。

---

## 4. 后果

### 正面
- 家庭授权和 RLS 只有一处实现，渗透测试（开发计划冲刺 3）只需要覆盖一套。
- 迁移与托管解耦，ADR 10 的选型结果不会返工 schema。

### 代价
- 不能直接用 Supabase Auth。认证方案要单独决定：自建、境内托管的 Auth，或 BFF 自己管。这件事并入 ADR 10。
- 文砺、知行现有的 48 张表不能照搬，需要按平台 schema 重新设计。
- 各科的迁移都要过资深工程师审查（必停项）。

---

## 5. 待人确认

1. 认证方案在 ADR 10 里决定，还是另起一份 ADR？
2. 各科私有 schema 的表，由各科负责人提、技术负责人批，还是统一由平台组设计？
3. 文砺 `guardians` 表的 RLS 漏洞（with check 没约束 student_id）只存在于 legacy 里，平台不会沿用。是否还需要在 legacy 里修？（legacy 只读，修复要去原仓库）

---

## 增补：kge schema（草稿，2026-09-27，specs/kge-schema-v0.9）

- **新增 `kge` schema**：放知识与成长引擎的公共知识层（地图版本、骨架、投影、原子、成长治理）。它与 `platform`、`insight` 同库，理由与本 ADR 选项 A 相同：发布流程要在同一事务里核对题目登记（`platform.item`）与能力视图（`platform.skill_node`）。
- **不含学生数据**：公共层没有学生键，外键只指向 `kge` 自身与 platform 的内容登记，`db/tests/80_attack_surface.sql` 断言（CLAUDE.md 规则 13）。个人层表（证据、作答、掌握）仍在 `platform.*`（对照文档 #3）。
- **一个迁移只改一个 schema（第 2 条）**：`0011_kge__knowledge_map` 只建 kge；`platform.curriculum_version.map_version_id` 单独放在 `0012_platform__curriculum_map_version`。
- **写入方**：新增数据库角色 `starbuddy_kge`，是公共层的唯一写入方，与其他应用角色互斥（0011 断言）；没有任何角色能删除公共层的行。
- 待签字：随 0011 / 0012 的 PR 一起评审（必停项）。
- **0018 / 0019（审核台签字，specs/release-gate-sprint4）**：签字人是账号，属于平台层，放 `platform.content_signoff` / `platform.map_change_signoff`（0018 只改 platform），kge 不出现账号列（攻击面测试）；签字、发布、撤下函数在 0019（只改 kge），经新角色 `starbuddy_review` 调用，知识服务 `starbuddy_kge` 只能提交待审。

## 增补：policy schema（草稿，2026-09-28，specs/policy-schema-v0.9）

- **新增 `policy` schema**：放考情政策库与招录库。它们是基础数据，不是学科内容（平台规划 v1.3 §6），与 kge 分开；版本节奏也不同：kge 按地图版本冻结，policy 按城市届别版本化。
- **写入方复用 `starbuddy_kge`**：公共层只有一个写入方，审核台后端统一；发布经 SECURITY DEFINER 函数。
- **0026（知识服务只读角色，specs/kge-03-compile-layer）**：读路径与写路径分开，新角色 `starbuddy_kge_read` 只读已发布的公共知识，与所有其他应用角色互斥（0026 断言）；只改 kge 的授权与策略。
- **一个迁移只改一个 schema**：0013 只建 policy；学生档案的中考届别在 0014（platform）。kge 地图校验对政策库的读取写在 0011 的校验函数里，用动态查询，政策库不存在时跳过。
- **不含学生数据**：攻击面测试的公共层断言扩到 policy。
- **0045–0048（待定事项落地，草稿，2026-10-07，specs/policy-decisions-v0.9.1 PR1，待签字）**：
  - **0045（只改 platform）届别真相源**：学生档案以 `platform.student_profile.exam_cohort` 为准，`grade` 改为“建档时年级”，当前年级由
    `platform.current_grade(届别, 时刻)` 推算；届别来源列 `exam_cohort_source`（规则 6；建档只收 derived / guardian_report，之后改届别必须同时改来源）；
    更正只经 `platform.correct_exam_cohort`（家长经 BFF、active staff 经签字连接，按调用角色分渠道，每次 ±1），最小记录 `platform.exam_cohort_change`
    （随删除请求删除；审计总表随 PLT-01）。
  - **0046（只改 policy）往年方案**：BFF 读方案、方案内容与来源文件放宽到已发布与已撤下；草稿与复核人列仍不可见；方案与来源文件的草稿不能直接撤下
    （否则没签字的草稿会以“往年政策”露出）。
  - **0047 控制线独立成表** `policy.control_line`（城市 × 年份 × 批次 × 指标生），签字与发布照 0041，没有自报复核人列；签字人表 `platform.policy_signoff`
    加对象类型与列；`policy.admission_line.control_line` 原样归档到 `policy.admission_line_control_line_archive` 后，已发布与草稿的值回填为控制线草稿，删除旧列。
  - **0048 官方来源清单由管理员维护**：staff 权限 `platform.staff_permission`（`policy_source_admin`，只由迁移或 DBA 授予）；新增、下线经 `policy.add_source_domain` /
    `retire_source_domain`（只授签字身份），操作者记在 `platform.policy_source_domain_change`，policy 不存账号引用（规则 13）。
  - **跨 schema 的例外（控制线 0047；来源清单 0048，及其后续 0050 的操作记录说明 CHECK、0052 的锁等待上限）**：
    - 0047：签字人表 `platform.policy_signoff` 的新列 `control_line_id` 有外键指向 `policy.control_line`，签字与导出函数（policy.sign_record、object_digest、
      platform.policy_signoff_record）的函数体按对象类型分支；拆开会有一个迁移里签字函数认得控制线、签字人表却存不下（或反过来）的中间状态。
    - 0048：两个 SECURITY DEFINER 函数同时读 `platform.staff_permission`、写 `platform.policy_source_domain_change` 与 `policy.source_domain`；
      账号引用只能在 platform（规则 13），清单只能在 policy，拆开会有一个迁移里函数引用尚不存在的表。
    - 0050（来源清单 0048 例外的延续）：`platform.policy_source_domain_change.note` 的 CHECK 引用 `policy.has_hidden_chars`——操作记录只能在
      platform（账号引用），说明的核对规则在 policy，两边必须是同一条规则（同一张码位表）。除这一条 CHECK 外 0050 只改 policy。
    - 是否接受这些例外随签字决定。
- **0049–0050（待人确认事项按默认处理，草稿，2026-10-07，specs/policy-decisions-v0.9.1 PR3，待签字；审查意见 4 拆成两个迁移）**：
  - **0049（只改 platform）**：客服更正届别要 staff 权限 `cohort_correction`（`platform.staff_permission`，只由 DBA 授予；先查权限、后查学生，
    无权限与学生不存在同一个错误）；家长导出清单加届别、届别来源与更正记录主题（不含操作者）；诊断会话加 `start_grade`（建立时的选题年级，
    会话参数）与 `abandon_reason`；因届别变化放弃的诊断退回额度 `platform.meter_refund_grade_changed`（adjust 流水，actor system）。
  - **0050（只改 policy，除上面登记的一条 CHECK）**：`normalize_source_host` 拒绝不能往返解码的 xn-- 标签（`policy.idn_label_ok`，plpgsql 实现的
    RFC 3492，标签不超过 63 个字符）；来源说明拒绝 Unicode Cc、Cf、Zl、Zp（码位表 `policy.is_hidden_codepoint`，函数与 CHECK 兜底）；
    `url_host` 不收显式端口；来源文件守卫经 `policy.lock_active_source_host`（属主 authz，只授 kge）对清单行 FOR SHARE；
    数据前提核对已有主机名都是规范形式。
- **0051–0052（PR3 遗留的两条已知风险，草稿，2026-10-08，specs/policy-decisions-v0.9.1 PR4，待签字）**：
  - **0051（只改 platform）**：`platform.enroll_subject` 授权通过后按 `platform.current_grade(exam_cohort, meter_now())` 核对当前年级在 7–9
    （对档案行 FOR SHARE，与届别更正串行），否则 55000 GRADE_OUT_OF_RANGE（BFF 映射为与前置检查相同的 409）；未授权先 42501；暂停不受影响。
  - **0052（只改 policy 的三个来源清单函数，属 0048 已登记的来源清单例外：函数读写 platform 的权限表与操作记录）**：新增、下线函数级
    `lock_timeout = 5s`，等锁超时以 55P03“稍后重试”报出（审核台 409 BUSY）；`policy.lock_active_source_host` 取到锁后把本事务的
    `idle_in_transaction_session_timeout` 设为 60 秒（不放宽更短的设置）。spec 写的是对 kge 写入角色 `ALTER ROLE … SET`，但 `starbuddy_kge`
    是 NOLOGIN 组角色，角色级参数只在以该角色登录时生效（SET ROLE 与继承都不读），对任何 kge 连接都不起作用，故改在取锁处按事务设置；
    部署时建议 DBA 另对 kge 的登录角色 `ALTER ROLE <登录角色> SET idle_in_transaction_session_timeout = '60s'`（同 0020 对签字登录角色的做法）。


## 增补：insight schema 的建档事实层（草稿，2026-09-29，specs/sie-01-onboarding，待签字）

- **新增个人层事实表放 `insight.*`**：学生自述 `insight.self_report`、结构化家长观察 `insight.guardian_report`、考试记录 `insight.exam_record`、建档进度 `insight.onboarding_progress`（0022）。
  理由：这些是 SIE 字段字典里新增的事实，没有既有副本；与判断账本同一 schema，洞察服务按同一套授权读取。
  既有个人层事实仍留在 `platform.*`（对照文档 #3），按科的教材版本是既有事实的扩展，放 `platform.student_subject_textbook`（0021）。
- **与 CLAUDE.md 规则 2 的关系**：在本增补签字前，按“ADR 之前 platform.* 是唯一真相源”，这四张表只是新事实的唯一存放处，不复制任何 platform 已有事实：
  - `insight.guardian_report` 只收字段字典里的结构化字段；0006 的 `platform.guardian_report` 仍存五个自由文本主题，两边字段不重叠；
  - `platform.student_profile.textbook_version` 只作建档前的默认值，某科在 `platform.student_subject_textbook` 有行时以后者为准。
  若本增补不被接受，替代方案是把四张表移到 `platform.*`（表结构与函数不变，只改 schema 名），迁移仍是草稿、未执行，改动成本低。
- **一个迁移只改一个 schema**：0021 只改 platform，0022 只改 insight。
- **写入方**：BFF 以当前账号调用 SECURITY DEFINER 函数写事实（SIE-01 待人确认 #3），BFF 没有表授权；判断仍只由洞察服务写。新增只读角色 `starbuddy_report`，只能执行聚合统计函数。
- 待签字：随 SIE-01 的 PR 一起评审（必停项：迁移、RLS 与权限、画像可见范围）。
- **0027（数学自适应诊断，specs/sie-02a-math-diagnosis）**：`platform.diagnosis_session` / `diagnosis_step` 只由学习服务写；学生经 BFF 只读自己的进度列，家长读不到；步骤只存选题决策与引用，对错以 `grade_result` 为准；过程观察以新原因 `process_observation` 挂诊断步骤进统一复核队列。只改 platform。
- **0028（洞察服务只读授权，specs/sie-02b-claims-first-report #4）**：`starbuddy_insight` 列级只读 platform 的掌握估计、观测、错因、判分结论、作答与证据的属性列，不给任何原文、维度、账号与家庭表，不给写权限（79j）。只改 platform。
