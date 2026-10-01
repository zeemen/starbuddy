# KGE-04 第二步 · lessons

## PR 1 实现记录（迁移 0037 草稿与 DB 测试）

状态：文件已写，未提交。迁移是草稿，**由人执行，先在副本演练**；PR 首行须写 `[NEEDS-HUMAN]`（迁移、RLS 与权限、掌握落库形状、画像可见范围）。

### 产物
| 文件 | 内容 |
|---|---|
| `db/migrations/0037_platform__mastery_node_keys.up.sql` | `mastery_estimate` 加 `node_id`、`cognitive_level`、`map_version_id`；`skill_local_id` 可空；`mastery_estimate_node_key_complete`（三列同空同填）、`mastery_estimate_one_key`（两种键恰好一种）；部分唯一索引 `mastery_estimate_node_current_uq`；`check_supersede_target` 的掌握分支按两种键比较；新表 `retest_task`、`profile_page`、`profile_page_check`（ENABLE + FORCE RLS、`student_id` ON DELETE CASCADE、learning 读写不删、BFF 按 `can_read_student` 只读）；登记个人层表清单；`mastery_estimate` 导出列补节点键 |
| `db/migrations/0037_platform__mastery_node_keys.down.sql` | 完整回滚；库里有节点键掌握记录时以明确错误中止（见下“回滚”） |
| `db/tests/79s_mastery_step2.sql` | 新增：表与清单、权限矩阵、节点键约束与更正链、复测任务、档案页与阻断记录、RLS 矩阵（本人、兄妹、已绑定家长、另一户、未绑定、未登录、撤销绑定、各服务角色）、删除级联；最后提交两户各一套行给 80 的可见性矩阵 |
| `db/tests/79q_erasure_coverage.sql` | `t.q_seed` 给两名学生各写节点键掌握记录、复测任务、档案页、阻断记录；第六段由“档案页尚无表”改为逐表点名确认删除后零行 |
| `db/tests/79p_erasure.sql` | 导出列元数据白名单补 `node_id`、`cognitive_level`、`due_at`、`closed_at`、`compiled_at`、`check_status` |
| `db/tests/run.sh` | 新增断言：有节点键记录时 0037 回滚中止且库不变 |
| `db/tests/mutations.sh` | 新增“掌握第二步”37 条变异 |
| `db/tests/expected/privileges.txt`、`policies.txt` | 快照更新（+37 条授权、+6 条策略；没有新增 SECURITY DEFINER 函数，仍 94 个） |
| `db/pentest/fixtures.sql` | 每个学生补节点键掌握记录、复测任务、档案页、阻断记录（夹具自检要求每张带 `student_id` 的表覆盖每个学生） |
| `docs/security/attack-surface.md` | A1 的表清单补 0037 三张表与节点键记录，补 79s 引用与变异 |
| `tools/e2e-cohort/sample/checks.json`、`summary.md` | 联调隔离检查按个人层表清单逐表比对，清单多了 3 张表：`tables_compared` 26 → 29，`summary.md` 里 checks.json 的摘要随之变化（在 scratchpad 用 `make gate0-evidence` 重生后逐字节比对，只有这两处不同，只拷了这两个文件）。是否在 `gate-0-evidence.md` 记一笔，见待人确认 G |
| `docs/security/pentest/scope.md` | `check_supersede_target` 一行补 0037 的比较口径（SECURITY DEFINER 计数不变，94） |

### 设计落点（spec 未写死、按最保守处理的）
1. **节点键不设外键，只做格式 CHECK**（与 0027 `diagnosis_step.node_id` 同写法）。理由写在迁移头：`kge.knowledge_node` 主键带地图版本，掌握记录要跨版本延续；公共层与个人层分离（规则 13）；`run.sh` 的 kge 角色断言测试库不执行 0011。节点存在、层次在节点的 `cognitive_levels` 里，由学习服务写前核对（PR 2）。`map_version_id` 同理不设外键。
2. **更正链比较**：每行恰好一种键，`IS NOT DISTINCT FROM` 同时比较能力点键、节点、层次，跨键、跨单元、跨学生都拒绝；`map_version_id` 不参与比较（地图版本切换后同一单元的新记录带新版本）。
3. **复测任务**：枚举与 `gates.py` 一致（rung0/1/2；scheduled/early/decay/hint_followup/verify_in_platform/verify_self_report；open/passed/failed/cancelled）。另加：
   - `scheduled`/`early` 必有轮次、其余原因没有轮次（`gates.Task.rung` 为 None）；`early` 只对 rung2；
   - `scheduled`/`early` 必须指向掌握记录，且经复合外键保证是**同一学生、同一单元**的节点键记录；
   - 通过或失败必须记 `closing_attempt_id`，撤销不记；`closing_attempt_id` 不设外键（`attempt` 是分区表，同 0027）；
   - 守卫：新任务只能是 open、创建时间由库写；只能从 open 关闭一次、关闭时间由库写；任务内容不能改（学习服务也只有 `status`、`closing_attempt_id` 的列级 UPDATE）。重排 = 撤销旧任务 + 建新任务。
4. **档案页存储形状**：`sections` 固定六节（`current_stage / mastered / working_on / root_cause / recent_progress / next_step`），每节 `{key, connective, statements, todo}`，每项只有 `{claim_id, template_key, template_version}`；衔接语 ≤ 60 字、不含数字；`blocked_fallback` 没有衔接语。守卫（调用者身份）核对：引用的判断属于该学生、模板键与版本对得上、学习服务读得到；陈述只引用成立或假设，`todo` 只引用待验证（规则 11）。每生每科一行（`UNIQUE (student_id, subject)`，重编原地更新），编译时间由库写。
5. **阻断记录**：`violations` = `[{code, section, claim_ids, fragment}]`，编码与 `consistency.py` 的 `Violation.code` 一致；片段 ≤ 60 字；**禁用推断命中不存片段**（命中词不进个人层，规则 12）。只追加（学习服务无 UPDATE / DELETE）。可挂在页上（复合外键保证同一学生，页删则级联）或页尚不存在时为空。
6. **权限例外**：spec 第 15 节写“其他角色无权限”。为让 0035 的删除与导出机制覆盖新表，只给了两个函数属主最小权限：`starbuddy_eraser` 三表 DELETE + `student_id` 列；`starbuddy_authz` 只读导出白名单列（复测任务、档案页；阻断记录不导出、authz 读不到）。79p 的“清单表只有 eraser 能删”“eraser 恰好能删 delete 表”断言照旧成立。
7. **删除顺序**：阻断记录 36 → 档案页 37 → 复测任务 38（引用掌握记录，须先于 `mastery_estimate` 的 45）。回执范围：档案页与阻断记录 `reports`，复测任务 `inference`。

### 与 spec 的偏差
- **档案页导出不导引用**（spec 待人确认 #7 默认“只导引用与时间”）：只导 `subject, check_status, compiled_at`。原因：`sections` 里含 Agent 版待办（待验证判断）与可见范围不含 `guardian` 的判断引用，按引用导出会让家长看到本不可见的判断 ID；要导引用需按可见范围过滤，留给 PR 4 与 #18 一并定。
- **复测任务导出列**含 `node_id`、`cognitive_level`（作为“ID”），不含轮次、原因、引用的掌握记录与关闭作答。
- **79p 白名单**：新导出列不在 SIE 字段字典里，按既有做法加进 79p 的“通用元数据”白名单（对象键、时间、状态），没有改字段字典（字典属画像可见范围，必停）。
- **衔接语存原文**：spec 第 14 节写“只存引用与衔接语”，按此存短语原文（来自人审的固定短语表），库里只能约束长度与不含数字，不能核对是否在短语表里（短语表在仓库配置里，由编译器核对）。
- **BFF 对阻断记录无权限、对档案页只读元数据列**（偏离 spec 第 15 节“三表 BFF 只读”）：按审查意见修订，见下。

### 回滚
`down` 先检查 `mastery_estimate` 是否有节点键记录：有就以 `object_not_in_prerequisite_state` 中止并报条数。理由：节点键行没有能力点键，恢复 `skill_local_id NOT NULL` 必然失败；既不能补假的能力点键，也不应在回滚里静默删掉学习者模型的唯一真相源（规则 2）。由人决定先导出再删、还是放弃回滚。`run.sh` 有对应断言。回滚会删除全部复测任务、档案页与阻断记录（可由对账与重编重建）。

### 测试结果（本地一次性 Postgres）
- `make db-test`：通过（2168 项断言；37 个迁移 up → down → up、最新迁移单独 down → up）。
- `DBTEST_ATTACK_ONLY=1 DBTEST_KEEP_SNAPSHOT=1 bash db/tests/run.sh`（渗透夹具 + 80）：通过。
- `DBMUT_FILTER=掌握第二步 bash db/tests/mutations.sh`：37 条全部被发现（第一次跑“更正链不比能力点键”漏检，补了“K-QF-0301 → K-QF-0399 被拒”的断言后发现）。
- `make attack-surface-check`：通过（94 个 SECURITY DEFINER 函数与快照一致）。
- 全量 `make db-mutation`：通过（707 个变异全部被发现）。
- `make learning-test`（含联调样例比对，样例同步后）、`make insight-test`、`make bff-test`、`make knowledge-test`、`make pentest-env-test`：通过。未跑全量 `make check`。

### 待人确认
- A. **迁移演练**：生产（或副本）上 `mastery_estimate` 不应有旧能力点键行以外的东西；演练时核对旧行只剩 3-H 合成种子（spec #10）。`skill_local_id` 改可空不影响现有读法（3-H BFF 按 `skill_local_id` 连接，节点键行连不上）。
- B. ~~BFF 读阻断记录~~：已按审查意见收回（见下“审查意见修订”第 2 条）。
- C. **档案页导出口径**：现只导状态与时间；是否导按可见范围过滤后的引用，随 #18 与 PR 4 定。
- D. **六节编码**：`current_stage / mastered / working_on / root_cause / recent_progress / next_step` 是本 PR 定的英文编码，第一步代码里 `Section.key` 未固定，PR 4 编译器须用同一套。
- E. **复测任务的作答引用不设外键**：`closing_attempt_id` 只由学习服务核对（`attempt` 分区表的通例）。
- F. **profile_page 每生每科一行、原地重编**：旧版本不留历史；如需审计历次编译，另议（阻断记录已只追加）。
- G. **Gate 0 合成样例**：本 PR 已同步 `checks.json` 的隔离表数（26 → 29）与 `summary.md` 的摘要；spec #9 的“重生样例并在 gate-0-evidence.md 记一笔”按原计划留到 PR 3，届时一并记录。

### 审查意见修订（用户决定“按默认建议处理”，同一 PR 1）
1. **档案页对 BFF 的可见形状**：BFF 对 `profile_page` 改为列级只读 `id, student_id, subject, map_version_id, compiled_at, check_status`，读不到 `sections`。
   新增视图 `platform.profile_page_statement_v`：只展开 `statements`（`todo` 一律不暴露），按 `can_read_student` 过滤页，按 0010 `claim_front_read` 的同一条件过滤判断（本人看可见范围含 student 的、有效绑定家长看含 guardian 的；未过期、模板仍在），只给成立或假设。只授 BFF。
   - **偏差：没有用 security_invoker**。调用者身份的视图要求 BFF 对 `sections` 列有 SELECT，等于把待办与不可见引用整列交出去，与本条目的冲突。改为属主视图（属主 `starbuddy_authz`、`security_barrier`），把两条策略的条件写进视图；为此给 authz 补了 `profile_page (id, student_id, subject, sections)` 的列级 SELECT。
     79s 逐账号断言“视图里的判断 = 该账号经 `claim_front_read` 读得到的 ∩ 页里成立或假设的陈述”，策略与视图分叉即失败；另有学生 / 家长 / 另一户 / 未绑定 / 兄妹 / 未登录 / 撤销绑定逐项断言，待办从不出现。
   - 80 可见性矩阵：属主视图对超级用户直读为空，新增 `t.visibility_present_override` 按底表算“有陈述的学生”；矩阵按 equal 比较，要求夹具里的陈述判断对学生与家长都可见（79s 提交的与渗透夹具都是）。
2. **阻断记录**：收回 BFF 授权与 `profile_page_check_read` 策略。新增插入守卫 `profile_page_check_guard`（调用者身份）：一般编码的 `claim_ids` 必须是学习服务读得到的本学生判断；`claim_unreadable` 按定义读不到，只要求它不是学习服务读得到的别的学生的判断（库里无法核对它的存在，见待人确认 H）。片段禁止数字。检查时间由库写。
3. **复测轮核对**：`retest_task_guard` 插入时，`scheduled` / `early` 的到期时间必须等于所引掌握记录的 `next_retest_at`，轮次必须与该记录门槛按 `RUNG_OF_GATE` 对应（execute → rung0，stable → rung1，transfer / retain → rung2）。所引记录不是同学生同单元时交给复合外键报错，没给轮次时交给 `retest_task_rung_reason`。`due_at`、`scheduled_by_estimate_id` 加了 COMMENT。
4. **档案页学科核对**：引用的判断须 `c.subject = NEW.subject OR c.subject IS NULL`。
5. **plan.py**：`_mastery` 只读 `skill_local_id IS NOT NULL` 的行（注释写明 PR 2 改读节点键）；`test_plan.py` 种一条节点键行并断言快照只有两个能力点（去掉过滤后该测试失败，已验证）。
6. `gates.py` 的 `Task.reason` 注释补 `verify_self_report`。79q 的阻断记录片段去掉了内容标记（标记含数字）。
7. 变异：“掌握第二步”组从 37 条增到 46 条（新增 BFF 可读阻断记录、BFF 可读 sections、陈述视图含待办与待验证、陈述视图不看可见范围、到期时间不核对、轮次不核对、档案页不核对学科、阻断记录不核对属主两条、片段可含数字；删去“阻断记录对 BFF 不按学生过滤”，其策略已不存在）。
8. 联调样例：BFF 读不到阻断记录后 `tables_compared` 29 → 28、`tables_bff_cannot_read` 27 → 28。两文件（`checks.json`、`summary.md`）仍是在 scratchpad 用 `make gate0-evidence` 重生后**逐字节比对**所得，其余文件与 `run_metadata.json`（去掉易变字段）完全一致，只拷了这两个。

#### 修订后的测试结果
- `make db-test`：通过（2179 项断言）。渗透夹具上只跑攻击面（`DBTEST_ATTACK_ONLY=1 DBTEST_KEEP_SNAPSHOT=1`）：通过。
- `DBMUT_FILTER=掌握第二步`：46 条全部被发现；`DBMUT_FILTER=攻击面`：24 条全部被发现。修订后没有重跑全量 `make db-mutation`（修订前全量 707 条通过）。
- `make attack-surface-check`：通过（94 个 SECURITY DEFINER 函数不变）。`make learning-test`：2521 passed。`make pentest-env-test`、`make bff-test`：通过。

#### 新增待人确认 / PR 4 验收待办
- **PR 4 验收待办**：判断状态变化（推翻、撤回、过期、转待验证）后，档案页里的旧引用不会自动失效；陈述视图已按判断现状过滤，但 PR 4 的读取与渲染须在读取时重核状态、可见范围与模板，Agent 版同理。
- H. **claim_unreadable 的引用无法在库里核对存在**：只能保证不是别的学生的可读判断。
- I. **已删除学生仍可写入**：删除执行后（`erased_at` 已置位），学习服务仍能给该学生写复测任务、档案页等；这是所有个人层表的共性问题（0035 只在执行事务内锁档案行），建议统一处理（例如写入守卫拒绝 `erased_at IS NOT NULL` 的学生），不在本 PR 单独补。
- J. **陈述视图复制了 `claim_front_read` 的条件**：将来改 0010 策略时须同步改视图；79s 的一致性断言会提示，但要人记得改。
