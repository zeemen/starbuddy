# 考情库 v0.9 待定事项落地

2026-10-07 用户确认“按默认建议，开始实现”：文末待人确认 #1–#4 全部按默认执行
（第 3 项维持两人签字；家长与 staff 均可更正届别 ±1；新建 staff 权限 `policy_source_admin` 由 DBA 授予；控制线旧列回填后删除）。

来源：`specs/policy-schema-v0.9/lessons.md`“审查提出、需要人决定的”#1、#2、#4、#5、#6。
用户结论（2026-10-07）：
1. 届别以存下的届别为准；
2. 控制线单独建表；
3. 方案、学校数据直接改成已发布；
4. 官方来源清单日常由管理员维护；
5. 已撤下的往年方案可以开放。

涉及 db/migrations 与权限策略，属于必须停下确认的变更；提交首行写 `[NEEDS-HUMAN]`。
迁移仍是草稿，不在任何共享环境执行。

## 范围

### 1. 届别以 `exam_cohort` 为准（迁移 0045）
- **年级改为推算值。** `platform.student_profile.grade` 不再是事实，新增 `platform.current_grade(exam_cohort, at)`。
  - 公式是 `expected_cohort` 的逆，9 月 1 日切学年；结果超出 7–9 返回 NULL。
  - 读年级的地方改读这个函数，时间取调用方给的时钟，不用 `now()`。已知读点是 BFF `shell-routes.ts`，其余由实现时全仓检索。
- **`grade` 列保留，含义改为“建档时年级”**，只在建档时用来推算默认届别。
  - 0014 的 ±1 保护只在 INSERT 时检查；之后改届别不再拿 `grade` 比对。
- **新增 `exam_cohort_source` 列**，满足规则 6：
  - 取值 `derived`（按建档年级推算）、`guardian_report`（家长自述）、`staff_correction`（客服更正）；
  - 回填为 `derived`。
- **更正渠道（默认，待确认）：** 只开一个 SECURITY DEFINER 函数 `platform.correct_exam_cohort`。
  - 调用方：家长（经 BFF，需有该学生授权，按 guardian_student_link）或 active staff。
  - 每次更正幅度 ±1；来源列随调用方写入。
  - 审计表随 PLT-01 修订，现在在函数里写一张最小的 `platform.exam_cohort_change` 记录（旧值、新值、来源、操作者、时间）。
  - 本任务不做前端入口。

### 2. 控制线单独建表（迁移 0046）
- **新表 `policy.control_line`：**
  - 列：city、year、batch_code、track、score、source_document_id、status、reviewers、reviewed_at；
  - 唯一约束：（city，year，batch_code，track）只能有一条已发布；
  - track 限 `quota_allocation`；
  - 保护触发器、两人复核与发布路径照搬 `admission_line` 的现行做法（含 0041 的签字）；
  - 必须对应该届已发布方案里 `control_line_basis <> 'none'` 的招录规则。
- **迁移旧列：** 回填 `admission_line.control_line` 的非空值后删掉这一列，同步更新：
  - 0013/0041 的触发器、BFF 列授权；
  - 快照、攻击面公共层断言、`check_attack_surface.py`。
- **BFF 读取：** 只读已发布行，不给复核人列。

### 3. 方案、学校数据的发布路径（不改）
- **默认：维持 0041 现状，本条按“已决”关闭，不放开。**
  - 现状：来源文件、方案、招录行、学校都只能以草稿新建，经两名不同 staff 签字才变为已发布。
  - 理由：您的结论“直接改成已发布”理解为不另建一层发布函数；0041 去掉了自报工号，如果放开会退回到不签字也能发布。
- **待确认：** 如果您的意思是去掉两人签字、允许直接改状态，请明说，那会是一个单独的迁移。

### 4. 官方来源清单由管理员维护（迁移 0047 + 审核台）
- **新增 staff 权限表 `platform.staff_permission`：**
  - 列：staff_id、permission、granted_by、granted_at；
  - 首个权限 `policy_source_admin`；
  - 只由迁移或 DBA 授予，应用角色只读。
- **新增两个 SECURITY DEFINER 函数：** `policy.add_source_domain(host, note)`、`policy.retire_source_domain(host)`。
  - 要求当前 staff 有 `policy_source_admin`；
  - 主机名规范化（小写、去尾点、拒绝 IP 和通配符）；
  - 已有来源文件引用的主机只能下线，不能删；
  - 操作记录写 `platform.policy_source_domain_change`。账号引用不进 policy schema（规则 13 与公共层断言）。
- **审核台：** 新增 `/review/policy/sources` 页面与学习服务审核台接口，提供列表、新增、下线三项操作。无权限的 staff 看到 403。

### 5. 开放已撤下的往年方案（迁移 0045 内）
- **BFF 的读取范围放宽：**
  - `exam_scheme` 改为可读已发布与已撤下；
  - `source_document` 同样可读已发布与已撤下，“往年政策”要能显示依据。
- **不放宽的部分：**
  - 草稿仍不可见；
  - 复核人列仍不给；
  - 学校、招录行保持只读已发布（未在结论内）。
- **前端不改：** 中考百科的“往年政策”展示等去向层 spec。

## 不做
- 中考百科、去向引擎的读取接口与前端展示（待人确认 #3，仍待去向层 spec）。
- 审计总表（PLT-01 修订）。
- 学科目录外键。

## 拆分与验收
- **PR1（迁移 0045–0047 + 库测试）：**
  - 第 1、2、4、5 项的 schema、函数、RLS 与授权；
  - `db/tests` 正反例（含两会话并发：更正与改年级、控制线发布）；
  - 变异全部被发现；
  - 每个新迁移单独 down → up；快照与攻击面同步。
- **PR2（年级读点 + BFF + 审核台来源清单与控制线签字）：**
  - 年级读点全部改读 `current_grade`（契约审查 B1 补全的清单），测试不依赖墙钟：
    - BFF：`shell-routes.ts` 的 `profile()` 与内容版本选择（经插件契约 `ContentVersion.grade` 发出）、`routes.ts` 的 `GET /me` `children[].grade`（`shell.v0.yaml`）；
    - 学习服务：`diagnosis/store.py` 的 `student_grade()`（诊断选题）；
    - 前端（数据来自 `/me`）：`shell/guard.ts`、`auth/copy.ts` 的 `childLabel` 与家长端各页标签、`trust/ExamPages.tsx` 的满分默认值；
    - `POST /students` 写入建档年级保持不变；建档表单写明“9 月起的年级”。
  - 审核台支持控制线签字（契约审查 B2）：`review_console/app.py` 对象类型、`store.py` 待签列表、前端 `review/actions.ts`、`lib/review.ts`、记录页标签；
  - BFF 届别更正入口加频控；
  - 审核台接口与页面，含 403 用例；
  - e2e 加 DOM 泄漏检查；路由矩阵与 OpenAPI 同步。
- **两个 PR 都要：**
  - 安全审查、契约审查；
  - 严格 `make check`（`CONTRACT_STRICT=1`）通过。

## 待人确认
1. **第 3 项的理解：** 维持两人签字（默认），还是允许直接改成已发布。
2. **更正渠道：** 默认家长与 staff 都能更正，每次 ±1；家长端入口另做。
3. **管理员是谁：** 默认新建 staff 权限 `policy_source_admin`，由 DBA 授予；是否改为复用现有某类 staff。
4. **控制线旧列：** 默认回填后删掉；是否保留一个版本过渡。
