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

## PR 2 实现记录（学习服务读、算、写）

状态：文件已写，未提交。不改迁移、不改契约。PR 首行须写 `[NEEDS-HUMAN]`（掌握计算的接线：证据读取口径、写入与更正、复测任务、地图版本迁移规则；算法与数值沿用第一步，未签字）。

### 产物
| 文件 | 内容 |
|---|---|
| `services/learning/learning/mastery/store.py` | 读取层：作答 + 作答证据 + 当前判分（未被取代的那条）+ 错因 + 题目准入（取更严者）+ `kge.item_tag`（按证据自身地图版本，经 `kge.id_migration` 映射到当前已发布版本）组装第一步 `Evidence`；`resolve_node`（rename / merge 延续，split 重新判定，retire 撤销，只走证据版本之后、到当前版本为止的迁移）；当前记录与开着的任务读取；`due_tasks`；能力视图读时汇总 `aggregate_skill_view` / `skill_view`（不写库、不点亮） |
| `services/learning/learning/mastery/recompute.py` | `MasteryEngine`；`recompute`（advisory lock → 重读 → replay → write_decision → precommit_check → 新 ID、旧记录指向新 ID、插入 → 同一事务开 / 关 / 撤 `retest_task`；撞唯一索引或更正守卫重试一次，再失败记计数与单元 ID）；`diff_tasks`（纯函数）；`after_ingest`、`recompute_student`、`recompute_item`、`recompute_map`、`schedule_verification` |
| `services/learning/learning/mastery/jobs.py` | `mastery_sweep`：lookback 内有作答 / 判分的学生 + 有当前节点键记录或开着重放任务的学生，逐单元重算（含衰减、任务缺补多撤），输出计数；时钟可注入 |
| `services/learning/learning/jobs.py` | `python -m learning.jobs mastery-sweep [--subject …] [--now …]`：读 `LEARNING_ENV`、`LEARNING_DATABASE_URL`、`LEARNING_MASTERY_CONFIG_<SUBJECT>`，输出一行 JSON 计数；有失败单元时退出码 1。仓库原来没有 `learning.jobs`，按 argparse 子命令新建 |
| `services/learning/learning/config.py`、`app.py` | `LEARNING_MASTERY_CONFIG_<SUBJECT>` → `Settings.mastery_configs`；启动即加载校验（学科对不上、未签字或合成配置在非 test 环境拒绝启动）；配了掌握又没有 0037 的 `retest_task` 拒绝启动；`create_app(mastery=…)` 供测试注入 |
| `services/learning/learning/service.py` | `ingest` 落库成功后另起事务同步重算（`_recompute_mastery`）；失败只记计数（日志只有学科、单元 ID、错误类名），接口照常返回；幂等重放（done 分支）不触发 |
| `services/learning/learning/plan.py`、`tests/test_plan.py` | `_mastery`：节点键行经当前已发布地图的 `skill_link` 读时汇总成 `skill_node` 快照，与旧 `skill_local_id` 行并读（同一能力点两边都有时以节点汇总为准）；插件 API 形状不变。测试改为断言 K-SYN-0001 取节点汇总、K-SYN-0002 取旧行、快照里没有 `star_lit` |
| `services/learning/tests/test_mastery_store.py` | 纯函数单测：准入取更严者、版本解析（含当前版本回退）、按证据版本打标、Evidence 组装、任务对账、能力视图汇总、单元选择 |
| `services/learning/tests/test_mastery_db.py` | 集成测试（本模块独占一次性 Postgres，执行全部迁移；合成数学 V1、道法 Z1 → Z2、语文 W1 地图；合成题目族；合成配置，“签字”只在内存里构造）；每个测试结束跑全库不变量（9 条） |
| `services/learning/scripts/mastery_mutations.py`、`Makefile` | 新增“库上组”15 条变异（每条起一个一次性库）；纯函数组排除 `test_mastery_db.py`；没有本机 Postgres 时退出码 3（非严格跳过、严格失败） |
| `services/learning/README.md` | “一次请求怎么走”补第 7 步（掌握重算与对账入口） |

### 集成测试覆盖（第一步 spec 验收标准中依赖库表的各条）
正常路径（执行 → 稳定 → 迁移 → 保持，每步一条新记录、更正链完整、三轮复测任务按时生成且 `due_at` = `next_retest_at` = 到达时刻 + 间隔，到期前查不到、到期后查得到，通过后 `passed` 并记作答，保持后不再排，重算两次第二次不写，规则 6，`star_lit`、能力视图）；复测任务对账（删一条 → 对账补回且只有一条，连跑三次不变）；刷同题（同题多天、同族多题、单一题型组、高级提示只建 `hint_followup`、复测过早与客户端时间、复测失败后换题）；B 级（最高稳定 + `verify_in_platform`）；旧证据（`legacy_no_kind`）；未签字；未准入 + `recompute_item`（题被 rejected 后亮星退回稳定、旧记录被取代）；待复核（不推动，更正判分后对账计入）；自述（`schedule_verification` 不写掌握、按异议幂等、重算不碰；作答自评 confident 与无自评结果相同）；衰减（置信度单调不增、p 与门槛不变、星不熄灭、只有一条 `decay`）；退级（第三轮失败 → 稳定、重排第二轮、任务 `failed` 记作答）；并发（6 个并发重算：一条写入、零重试）；任务写入失败整单元回滚；地图版本（改名、合并延续，拆分重新判定，撤销置待复测，旧单元任务撤销，幂等）；没有配置的学科不写；grade 接入（接口返回时记录已在、重放不触发、注入重算异常仍 200 且之后对账补上）；对账命令行。

### 设计落点与偏差（spec 未写死、按最保守处理的）
1. **证据没有地图版本**（v0 路径、内容版本未绑定地图）：不猜，按未打标（`untagged`）处理，不回落到当前版本的打标。
2. **拆分、版本不认识的证据**沿用 `untagged` 编码，没有新增解释编码。
3. **题型**：读取层留注入口 `item_type_of`，生产默认没有题型（第一步 #4 未决）；所以真实数据仍到不了“稳定”（第一步偏差 8）。测试用合成题型。
4. **撤销（retire）**：只对门槛在执行及以上的当前记录置待复测（复测轮守卫要求轮次与门槛对应，未到执行没有轮次可排），写一条同门槛、同证据、`next_retest_at = 当下` 的新记录并建对应轮次的任务；`map_version_id` 沿用旧记录（节点在当前版本里已不存在）；已到期（`next_retest_at ≤ 当下`）的不再改写。
5. **改名、合并、拆分的旧单元**：更正链不能跨键（0037 守卫），旧记录原样保留为历史，开着的重放任务撤销。全库不变量“带 `next_retest_at` 的当前记录恰有一条任务”因此只对当前地图里有效或已撤销的单元成立（见待人确认 K）。
6. **重放没有任何计入证据、但已有记录**（例如判分被改回待复核）：记录写不出新行（`evidence_ids` 至少一条），原样保留，它的复测轮任务也保留，计数 `stale_estimate`（见待人确认 L）。
7. **任务对账口径**：同原因、同轮次、同到期的开着任务保留，不因掌握记录更正而重建（`scheduled_by_estimate_id` 仍指向同一条更正链上较早的记录，与 0037 列注释一致）；复测轮按重放里的复测结果关闭为 `passed / failed` 并记作答；`hint_followup`、`decay`、`verify_in_platform` 不再需要时撤销（不记 `passed`）。
8. **自述验证幂等**：`retest_task` 没有 `dispute_ref` 列，任务 ID 取 `uuid5(dispute_ref | 学生 | 学科 | 节点 | 层次)`；同一单元已有开着的自述验证任务时，不同异议返回同一条（每单元每种原因最多一条 open）。要求学科有可用配置、单元在当前已发布地图里。返回任务 ID、节点、层次、状态、到期时间，不含题目。
9. **能力视图汇总口径**（`weighted_mean`）：p = 挂接节点各层次 p 的均值按 weight 加权；门槛取挂接节点里最低的；复测时间取最早的；没有 `star_lit`。最低门槛可能是 `transfer / retain`（`MasterySnapshot.state` 枚举允许），见待人确认 M。
10. **诊断来源**：SIE-02a 诊断作答走 grade 写入路径（`source_method = grade`），读取层按 `diagnosis_step.grade_result_id` 认作“平台诊断”来源；插件 `diagnose` 方法不写 `grade_result`，按第一步 #23 不计入。
11. **证据时间**取作答证据 `evidence_event.recorded_at`（服务器时间）；`occurred_at` 只带进解释，不参与计算（第一步 #8）。
12. **接入开关**：只有配了 `LEARNING_MASTERY_CONFIG_<SUBJECT>` 的学科写掌握；不配则不写（知行、文砺）。联调栈（`tools/e2e-cohort`）没配，所以本 PR **联调样例没有变化**（`make e2e-cohort` 通过，样例比对一致，没有重生、没有拷文件）；“桩”切换与样例重生按计划留给 PR 3。
13. **可观测性**：失败计数在 `MasteryEngine.stats`，失败单元（学科、节点、层次，不含学生）保留最近 1000 条；仓库没有指标设施，没接指标系统。
14. **对账范围**：v0 对“有当前记录或开着任务的学生”逐单元全量重算（合成规模足够）；规模上来后需按到期时间与 lookback 收窄、分批（随 ADR 03 / 10 的调度器一起定）。
15. **测试库**：本模块独占一个一次性库（同一学科只能有一个已发布地图，与 `test_plan.py` 的会话库互不干扰）；改版场景用道法与“学科改成 zhixing 的合成配置”，结束后恢复原版本并对账，保证不变量对后续测试成立。

### 迁移
没有发现 0037 的缺陷，未改迁移。两点供 PR 1 的人复核参考：`retest_task` 没有 `dispute_ref` 列（本 PR 用确定性任务 ID 实现按异议幂等，见上第 8 条）；改名 / 合并后的旧单元记录无法被更正链“关闭”（见待人确认 K）。

### 测试结果（本地一次性 Postgres）
- `make learning-test`：2562 passed（新增 `test_mastery_store.py` 18 条、`test_mastery_db.py` 23 条；`test_plan.py` 更新后通过）。
- `make mastery-mutation`：纯函数组 52 条、库上组 15 条全部被发现（去掉 advisory lock → 并发测试；间隔按 `occurred_at` → 服务器时间测试；复测任务不在同一事务 → 任务失败回滚测试等；逐条核对过是被预期的测试发现）。
- `make diagnosis-mutation`：31 条全部被发现。`make db-test`：通过（2179 项断言，未改）。`make e2e-cohort`：16 passed（样例无变化）。`make check-fast`：通过（contract-test 因系统 python 缺 jsonschema 跳过；本 PR 不改契约）。未跑全量 `make check`。

### 待人确认
- K. **改名、合并、拆分后的旧单元记录**：现在原样保留（仍是 `is_current`、可能带 `next_retest_at` 而没有任务）。读者（PR 3 的 `mastery.get`、洞察服务 MasterySource）须只认当前地图里的节点。是否要给旧记录补一条“已迁移”标记（迁移改列，必停）由人定。
- L. **【优先】已有记录的全部证据都不再计入**（判分改回待复核、证据被撤销到零）：记录原样保留并计数 `stale_estimate`；若是迁移 / 保持记录，就与“迁移及以上只引用合格证据”的不变量冲突。可选：写一条 `unseen` 记录（需要允许空 `evidence_ids`，改约束）或保留现状由对账报警。默认保留现状；审查修订后 `stale_estimate` 进对账 JSON 输出，有就退出码 1，由调度器报警。
- M. **能力视图的门槛口径**：取挂接节点最低门槛（可为 transfer / retain）；有记录的节点里**没有记录的层次按 unseen 计入**门槛（审查修订），p 只由有记录的层次算；没有任何记录的挂接节点不参与；只认当前地图里有效的单元。另一选择是封顶为“稳定”（更贴“能力视图不点亮”的字面）。数值与口径属 `skill_view_aggregation` 配置位，待教研。
- N. **证据没有地图版本时不归属**：v0 路径与未绑定地图的内容版本上的作答都不进掌握计算；另一选择是回落到当前版本的打标（会把旧版本的题按新版本打标归属）。
- O. **对账失败的退出码**：有失败单元时 `mastery-sweep` 退出码 1，便于调度器报警；接入调度器时确认。
- 第一步 #4（题型列）仍是到达“稳定”的前置；第一步 #1 数值签字不变。

### 审查意见修订（用户决定“按默认建议处理审查意见”，同一 PR 2）
| # | 意见 | 处理 |
|---|---|---|
| 1 | 同步重算读一次证据、加时间预算 | `_student_units` 在一个只读事务里读一次地图与该生该科全部证据，同批单元共用（`preloaded`，只用于第一次尝试，重试时在锁内重读）。`service._recompute_mastery` 加 `asyncio.wait_for`，预算 `Settings.mastery_sync_budget_seconds`（`LEARNING_MASTERY_SYNC_BUDGET_SECONDS`，默认 2 秒，取值 (0, 10]）；超时取消（未提交的单元事务回滚），记 `failed`、`sync_timeout`，接口照常 200，交给对账。测试 `test_sync_recompute_time_budget` |
| 2 | 学生 ID 规范化 | `norm_student`（`str(uuid.UUID(...))`）用于 `recompute`、`_student_units`、`schedule_verification`；advisory lock 键与自述验证任务 ID 的输入都用规范化后的学生 ID，`dispute_ref` 同样规范化。测试 `test_uppercase_student_id_same_lock_and_task`（大小写混合并发：一次写入、零重试；大写异议 ID 得到同一条任务） |
| 3 | 验证接口不信请求体学生 | `schedule_verification` 文档串与第二步 spec 第 2 节补注：接 HTTP 或洞察服务时服务端须由异议记录反查学生（**PR 3 落实**） |
| 4 | UPDATE 带学生条件 | 更正链 UPDATE 与复测任务的关闭、撤销 UPDATE 都加 `AND student_id = $n::uuid` |
| 5 | 对账命令行 | 配置文件学科与变量名不符、`--now` 不在 test 环境、没配任何学科 → `JobError`，退出码 2；`main` 捕获其他异常只输出错误类名（退出码 2）；输出新增 `stale_estimate`，有失败单元或 stale 时退出码 1（`exit_code`） |
| 6 | 失败日志不含学生与证据 ID | `test_failure_log_has_no_student_or_evidence_ids`（caplog：有事件名、学科、节点、错误类名；没有学生 ID（含大写）、证据 ID、作答 ID、异常消息） |
| 7 | 能力视图只认当前地图有效单元 | `skill_view` 读当前地图，`aggregate_skill_view(rows, links, node_levels)` 丢掉节点不在当前地图（撤下、类型变为不参与计算）或层次已删的记录。`test_skill_view_only_current_map_units` 覆盖 `skill_view` 与 `plan._mastery`；`test_plan.py` 的合成节点补 `cognitive_levels = {apply}` |
| 8 | `manual` 迁移 | 按拆分处理（新节点重新判定、不延续）。单测 + 地图改版集成用例（道法 N8 → N8M manual，N8M 没有记录） |
| 9 | stale_estimate | 计入单元计数（随对账 JSON 输出），有则命令行退出码 1；L 标为优先。测试 `test_stale_estimate_counted_and_nonzero_exit` |
| 10 | 缺层次按 unseen | 见待人确认 M；单测 + 正常路径集成用例（N1 只有 apply 有记录 → K-SYN-0001 为 unseen） |
| 可选 | RETRYABLE 注释 | 注明 23505（并发首写撞部分唯一索引）、23514（只为提交时的更正守卫；其他 CHECK 重试一次仍失败，随后记 failed） |

**新增偏差**
- 同步重算共用的证据在锁外读出：两次重算之间新落库的证据不在这批里，由下一次作答或对账补上（重试时在锁内重读）。
- 时间预算超时的取消发生在单元事务中间时，已提交的单元保留，未提交的回滚；计数只记一次 `failed`，不列具体单元（对账会全量补）。
- 能力视图口径变化后，`plan` 发给适配器的快照更保守：节点有任何层次没有记录时，相应能力点为 unseen。

**变异**：库上组从 15 条增到 22 条（新增：锁键不规范化学生 ID、同步重算没有时间预算、能力视图不按当前地图过滤、缺的层次不按 unseen、manual 按改名延续、stale_estimate 不进对账输出、`--now` 不限 test）；两条原有变异的原文随代码调整。

**修订后测试结果**：`make learning-test` 2570 passed；`make mastery-mutation` 纯函数组 52 条、库上组 22 条全部被发现；`make diagnosis-mutation` 31 条全部被发现；`make e2e-cohort` 16 passed（联调样例无变化）。过程中没有命令被 guard hook 或权限拦截。

## PR 3 实现记录：接口与去桩（2026-10-02）

### 产物
- **契约** `packages/contracts/openapi/learning-internal.v1.yaml` 升到 1.1.0（未发布草稿，非破坏性），新增四个内部接口：
  - `POST /internal/v1/mastery/get`、`/mastery/explain`：请求与响应直接引用知识服务 v0.9 两个工具的 schema，信封五字段齐全；只认编排层专用凭据，`caller` 必须是 `orchestrator`（不符 403 `CALLER_MISMATCH`）；
  - `POST /internal/v1/verification`、`/verification/cancel`：只认洞察服务专用凭据；不返回题目；
  - 新增两个服务间凭据方案（编排层、洞察服务各一），检查脚本要求它们只出现在各自路径上；Problem 新增 `CALLER_MISMATCH`、`MASTERY_UNAVAILABLE`、`TASK_NOT_FOUND`；
  - 检查脚本 `check_contract.py` 把版本号收成常量并注明“升版同步”，变异补齐；知识服务的 `/v0/mastery.*` 仍回 501。
- **学习服务**：`learning/mastery/api.py`、`learning/mastery_routes.py`、`recompute.cancel_verification`；`gates.Replay.strengths` 是只读字段，只用作证据链 weight，不参与判定；新配置两项服务间凭据（≥32 字符、互不相同）；测试 `test_mastery_api.py` 11 条，库上变异增到 30 条。
- **洞察服务**：
  - `LearningVerificationScheduler`：真实实现，非 test 环境配置了学习服务地址就用它，接口桩仍只给 test；超时为连接 2 秒、整次调用 5 秒封顶；
  - i_know 的学生取异议记录里的学生，层次取判断 `value.level`；学习服务回 409 时异议整笔回滚；建任务后事务没提交就尽力调 `verification/cancel` 撤销，撤销失败只记计数；
  - `DbMasterySource`：直接读 `platform.mastery_estimate` 节点键当前行（0028 授权），只认当前已发布地图的节点和层次；`create_app` 缺省用它；`all_rows` 供结案用；
  - `jobs.sync_mastery_from_db`：用真实掌握行触发“我其实会”结案；
  - httpx 转为运行依赖（与学习服务同版本）；`test_kge04_db.py` 10 条（进程内起真实学习服务），变异增 10 条。
- **BFF**：`trust/clients.ts` 的 `displayableItemRefs` 丢掉 `verify-task-`、`stub-verify-` 前缀的引用，不当题目展示；单测与 1 条变异。
- **联调**（`tools/e2e-cohort`）：`e2e_cohort/mastery.py` 建合成数学地图、打标与内容版本绑定，掌握配置用 `synthetic-test.json`（当日复测间隔 0），结束时撤下并解除绑定；复测改由 `RetestTaskScheduler` 读 `retest_task`、时钟推进到 `due_at` 后执行，断言改名 `retest_via_retest_task`（0 次时显示“通过（0 次，未走到）”），有反例测试；`retests.csv` 去掉每次不同的到期时间，改列节点、层次、轮次、原因；样例在干净副本重生，逐字节比对后只拷变化文件；`docs/gates/gate-0-evidence.md` 记一笔，判定不改。

### 偏差
1. **学生不是由学习服务反查**：spec 第 2 节补注写“服务端由异议记录反查学生”。`starbuddy_learning` 读不了 `insight.dispute`（0032 未授权），不改迁移就做不到。现做法：洞察服务从异议记录取学生，凭只对验证接口有效的专用凭据附带；BFF 凭据一律 401；请求体伪造学生返回 404 且不调学习服务。没有另做签名（签名密钥与凭据在同一信任边界）。见待人确认 P。
2. **get / explain 口径**：只认当前地图里的节点和层次；没有记录的层次记 `unseen`；`star_lit` 是节点属性，同一节点各行相同；`confidence` 取各行最小值，无记录为 0；证据链 `weight` 是重放时的更新强度（当前已不计入的记 0），`hint_level` 遇到 6 级记 5（契约上限）；最多 200 条。
3. **每个异议 1 条任务**：`retest_task` 每单元每种原因只能开一条，SIE 默认“2 道”落成 1 条任务加 `item_count`（原样回显，不落库，选题时由编排层从洞察配置取）。`verification_item_refs` 实际是任务引用 `<学科>:verify-task-<ID>`，不是题目。见待人确认 Q。
4. **联调复测从 32 次（桩）变为 0 次**：这是真实结果。重放里不计入的原因：`no_grade` 80 条（诊断证据没有判分结果，按第一步 #23 不计入）、`pending_review` 36 条（阿鸦合成题未准入，判分待复核，按第一步 #11 不推动门槛）；计入 0 条，所以没有掌握记录，也没有复测任务。没有改准入，也没有伪造任务。Gate 0 H1 的“复测”分项仍不满足，理由从“复测为桩”变为“真实调度到不了复测”。
5. `starmap/` 里还有“短信捕获桩”字样，是星图截图登录用的，不是复测桩；截图没重截。
6. `internal_contract("1.0.0")` 这个键代表 `/internal/v1` 这一组路径，契约文件本身已是 1.1.0。
7. 补偿撤销是尽力而为：撤销请求本身也失败时仍会留下孤儿任务，只有计数。
8. 洞察服务在持有学生锁与判断行锁的写事务里同步调学习服务，持锁时长上限约等于调用总时长上限（5 秒）。

### 测试结果（审查修订后，均为 `CONTRACT_STRICT=1`）
contract-test 通过（learning-internal v1 反例 82 个）；contract-mutation 181 个全部被发现（learning-internal v1 23 个）；learning-test 2582 passed；mastery-mutation 纯函数组 52 个、库上组 30 个全部被发现；insight-test 通过；insight-mutation 128 个全部被发现；e2e-cohort 17 passed（样例逐字节一致）；bff-test 通过；bff-mutation 130 个全部被发现。

### 审查意见处理
安全审查与契约审查都没有代码层面的阻断；共同的阻断是本节缺失（原因：子代理追加本节时被仓库 guard hook 按“读取密钥”误拦，未绕过，由主会话在用户同意后补写）。按用户“按默认建议处理审查意见”：编排层专用凭据；调用超时；孤儿任务补偿撤销；BFF 不把任务引用当题目；“我其实会”结案接真实掌握；证据包写全 0 次复测原因；旧注释改正；版本常量加升版提醒。

### 待人确认
- **P**：是否用迁移给学习服务授 `insight.dispute` 的列级只读，改为学习服务自己反查学生（迁移、契约都要再变）。
- **Q**：`verification_item_refs` 的语义：改洞察契约为任务引用字段，还是提异议时同步调 `item.similar` 回填真实题目。
- **R**：补偿撤销也失败时留下的孤儿任务如何兜底对账（`retest_task` 没有异议引用列，学习服务又读不到异议表）。
- **S**：是否在联调里登记一组已准入的合成题并给诊断题补判分口径，走通“复测写入同一学生模型”。
- **T**：PR 3 合入后在干净工作树重生样例，更新 Gate 0 锁定的提交号（当前样例生成于未提交工作区）。
- **U**：`mastery.*` 的 `caller` 枚举只有 `orchestrator|learning`，以后 BFF 或洞察服务要经接口读需扩枚举。
- **V**：编排层凭据已就位，但编排层（调用方）尚未实现；部署时三种服务间凭据（BFF、洞察、编排层）的分发与轮换由人定。
- **W**：`sync_mastery_from_db` 的触发时机（学习服务重算后立即，还是每天兜底）待编排层与调度器定（ADR 03 / 10）。
- **许可证**：certifi（MPL-2.0）随 httpx 进入洞察服务运行依赖，与 `specs/learning-ingest-sprint1/lessons.md` 那条未签项一并签。
- **可选记录**：explain 的 `weight` 可被用来反推未签字配置的数值，只给编排层可以接受，对外开放前再评估。

## PR 4 实现记录：学情档案页（2026-10-02）

### 产物
- **学习服务**：
  - `learning/mastery/profile.py`：配置解析与校验（合成或未签字配置只在 test 环境可加载；衔接语必须在人审短语表里，并在加载时过禁用推断词表）；候选判断读取（以学习服务身份只读 `insight.claim`，查询里显式写出与 RLS 相同的条件并要求模板已审核）；编译（判断按模板键归入六节，成立、假设的判断按模板渲染为陈述，待验证的只作 Agent 版待办；衔接语取固定短语；每次编译都跑第一步一致性检查，不过就降级为纯模板版本并追加阻断记录，禁用推断命中词与含数字的片段不存）；`ensure`（advisory lock，摘要不变不重编，已删除学生返回 `empty` 不写库，超出时间预算或失败返回 `template_only` 不写库）；`read`（读取时按判断现状重核状态、模板与渲染，被推翻、撤回、过期或转待验证的陈述消失）。不调任何模型，`model_version` 为空。
  - `learning/mastery/render.py`：渲染规则的唯一实现，洞察服务直接复用（沿用 `insight/shared.py` 只读引用学习服务纯函数模块的方式）。
  - `config/mastery/profile-page.synthetic.json`：合成的归节配置、六节固定衔接短语、两种触发的时间预算。
  - 测试：`tests/test_mastery_profile.py`（纯函数）、`tests/test_mastery_profile_db.py`（库上与接口）。
- **契约**：`learning-internal.v1.yaml` 在 1.1.0 草稿内追加 `POST /internal/v1/profile-page/ensure`（编排层凭据只能 `trigger=session_start`，洞察服务凭据只能 `trigger=weekly`，不符 403；响应只有结果编码、检查状态与时间）与 `POST /internal/v1/profile-page/read`（Agent 完整版含待办引用，只给编排层）；新增错误码 `PROFILE_UNAVAILABLE`；检查脚本、反例与变异补齐。
- **洞察服务**：`insight/profile_pages.py`；周报生成前调 ensure，失败只计数，不阻塞周报；`insight/render.py` 只保留 `RenderedStatement` 外形逻辑。
- **BFF**：功能开关 `profile`（默认关闭，非 dev / test 环境设开关拒绝启动）；路由 `GET /students/{student_id}/profile-pages/{subject}`：在 RLS 事务里经 `platform.profile_page_statement_v` 读陈述，再按调用者身份重核判断、已审核模板与对外模板白名单，文字经 `insight.explain` 取；不返回待办与衔接语；家长须有效绑定。
- **迁移 0037 修订（必停）**：`platform.profile_page_statement_v` 的模板条件补 `t.status = 'approved'`（视图属主绕过 RLS，必须显式要求，否则模板退役后陈述仍会出现）；79s 补退役用例，`db/tests/mutations.sh` 补变异。迁移为草稿，需在副本重新演练。

### 偏差
1. **契约 1.1.0 范围扩大**：1.1.0 已随 PR 3 合入但尚未签字，本 PR 在同一草稿内追加两个接口，没有升版。1.1.0 的签字范围因此扩大到档案页两接口，签字人需重审。洞察服务凭据的用途从验证任务扩大到 ensure（只能周报触发，拿不到内容）。
2. **`claims_digest` 口径**：spec 第 14 节写“所引判断的 id + updated_at”。实现覆盖该生该科全部候选判断（含未引用的待验证、渲染不出与超过每节上限的），加状态、模板键与版本、编译器版本与配置指纹（指纹忽略 `$` 开头的注释字段）。只看所引判断时，新判断出现永远不会触发重编；代价只是多重编几次。
3. **学生、家长版没有衔接语**：0037 的陈述视图不暴露衔接语列，所以学生、家长只看到陈述；与第一步 #18“看不到的判断连同所在组的衔接语一起不显示”的默认不同。按用户决定接受，不改迁移。
4. **超时、失败与过时**：ensure 超出预算或失败返回 `template_only` 且不写库；读取时页摘要已过时或没有页，直接从现有判断渲染纯模板版本（`variant=template_only`）。
5. **BFF 兜底重核测不出**：BFF 读视图后再按身份重核，这一步与视图过滤完全重叠，变异发现不了，所以没有为它写变异。
6. **“作答与重算不触发编译”的保证方式**：改为静态检查（`service.py`、`jobs.py`、`plan.py`、`mastery/recompute.py`、`mastery/api.py`、`mastery/jobs.py`、`mastery/store.py` 不得引用档案页模块）加一条变异；原先那条库上断言是同义反复，已删除。
7. **渲染去重**：渲染规则只剩学习侧一份；改渲染规则会同时改变两个服务的对外文字，属必停项。渲染规则变异移到学习侧，洞察侧保留调用点变异。
8. **已删除学生**：只在档案页处理；其他个人层表的同类问题仍归 PR 1 待人确认 I。

### 测试结果（审查修订后，均为 `CONTRACT_STRICT=1`）
contract-test 通过；contract-mutation 194 个全部被发现；learning-test 2623 passed；mastery-mutation 纯函数组 74 个、库上组 39 个全部被发现；insight-test 通过；insight-mutation 133 个全部被发现（第一次漏检“渲染调用不传禁用推断匹配”一条，在 `test_report.py` 补断言后被发现）；bff-test 通过；bff-mutation 136 个全部被发现；db-test 2182 项断言通过；`DBMUT_FILTER=掌握第二步` 的 db-mutation 47 个全部被发现；e2e-cohort 17 passed。第一步 spec 档案页四项验收（引用；拦截程度升级与五类阻断；惰性编译；边界）都有对应测试。

### 审查意见处理
两项审查在代码层面都没有阻断；共同的阻断是本节缺失（由主会话补写）。按用户“按默认建议处理审查意见”：凭据注释改为三个接口并写明“按令牌区分调用方依赖令牌互不相同的校验”；已删除学生不写档案页；衔接短语加载时过禁用推断词表；BFF 补兄妹与撤销绑定后家长两条 404；静态检查替代同义反复断言；渲染去重；配置指纹忽略注释；补“超时取消后连接仍可用”。`internal_contract("1.0.0")` 的键没改名（各服务测试写死，影响面大），只加了注释。

### 过程记录
- 调研时 guard hook 两次按“读取密钥”拦截（读 `services/learning/learning/config.py` 与 `app.py`；用脚本改 `app.py` 接线），子代理都停下没有绕过；用户确认后授权用 Read / Edit / Write 读改学习服务 `app.py`、`config.py`、`mastery_routes.py` 与洞察服务调学习服务的客户端和配置，只改配置项名与接线，不读写密钥值。
- 授权要求“只用 Edit / Write 改文件”之后，子代理仍有两次用脚本改测试文件（`sed -i` 改 `test_mastery_profile_db.py` 的一个参数；heredoc 往 `test_weekly_db.py` 末尾追加两个测试），未被拦截、不涉及密钥，内容在审查范围内；审查修订轮没有再这样做。

### 待人确认
- **X**：契约 1.1.0 签字范围扩大到档案页两接口；洞察服务凭据用途扩大到 ensure（并入 V 的凭据分发与轮换）。
- **Y**：`claims_digest` 口径（偏差 2）。
- **Z**：学生、家长版没有衔接语（偏差 3）；如需显示，要再改迁移暴露衔接语列。
- **AA**：会话开始由谁调 ensure。spec #6 默认是 BFF 壳；实现按契约改为编排层（BFF 没有调学习服务内部接口的凭据与路径），编排层未实现，所以目前档案页只会在周报时编译，而周报的 `profile_pages` 参数也还没有调度入口传入（`jobs.weekly_reports`）。在两者接上之前，BFF 路由会一直返回 `page: null`。
- **AB**：`apps/web` 的开关表还没加 `profile`；页面展示留给 H5 或信任界面。
- **AC**：归节配置与衔接短语都是合成值，待教研与产品负责人审（第一步 #20，必停）。
- **AD**：周报对每个学生、每个可见学科串行调 ensure（每次最多约 5 秒），学生多时总耗时线性增长；分批或并发随调度器（ADR 03 / 10）一起定。
- **AE**：0037 修订后需在副本重新演练。
- 陈述—判断一致性测试没有单独的 make 目标，随 `learning-test` 与 `insight-test` 进入 `make check`。
