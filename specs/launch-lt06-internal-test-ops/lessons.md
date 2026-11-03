# LT-06 内部成员测试运营支撑 · 实现记录

2026-10-24 由 Claude 推进（specs/launch-test-2026）。提交首行 `[NEEDS-HUMAN]`：迁移草稿 0059（新表、RLS、SECURITY DEFINER 函数、触发器、授权）、同意规则（内测开关下两份文书核对；库层拒绝已退出档案再签同意与签发学生会话）、各服务新增处理判据、白名单 OTP 审计格式扩展。不改契约、适配器、人设 Prompt。

## 交付
- **白名单工具** `tools/internal-test/`：generate / revoke / summary（`--for-ticket` 不含手机号 HMAC）；复用 BFF 的条目解析、审计重放与码生成；号码只经标准输入；`--out` 只新建、强制 0600；台账用从日志 HMAC 密钥派生的子密钥；make 目标 `internal-test-tool-test` 接入 check。
- **迁移草稿 0059**：
  - `platform.feedback`（来源 student_self_report / guardian_observation / staff；固定分类；≤500 字；状态）：BFF 只有列级 INSERT，RLS 要求提交者即会话账号、来源与关系一致、已退出档案不收；审核台只经 SECURITY DEFINER 函数读列表与改状态；学生用 `it-` 前缀独立不透明指针（与复核队列指针不互通）；insight / learning / kge / scheduler 对该表无任何权限（有测试）。
  - `platform.internal_test_exit` 与 `exit_internal_test`：撤回同意、作废学生会话、写退出记录，幂等；只返回需作废的家长账号 ID（有效绑定且无其他在测孩子），不返回任何号码摘要。
  - `platform.student_processing_allowed(uuid)`：档案存在、未删除、未退出、最新隐私政策为同意、且满 14 岁或最新监护人同意为同意；只授 learning / insight / scheduler。
  - 触发器：已退出档案不能再签“同意”（撤回照常）、不能签发学生会话。`review_sample_spot_checks` 按判据过滤。
  - 登记个人层表清单（随 0035 删除流程删除）；权限 / 策略快照、db 测试 79zg、删除覆盖 79q、db 变异、攻击面清单同步。down 注释：测试结束统一删除前不得回滚、不得导出明文。
- **处理判据（全服务）**：学习服务 `learning/processing.py`（v1 ingest、今日计划、诊断 / 再练 / 复测、今日编排、档案页 ensure、建验证任务；判据为假回 404 `STUDENT_NOT_FOUND`，契约已有码），mastery-sweep 选人过滤；洞察服务 `insight/processing.py`（周报候选与点名、`jobs_cli --all` 与点名）；BFF 退出守卫不再挂内测开关，开关打开但判据函数不可用时拒绝启动。跨服务测试：退出后跑 spot-check / mastery-sweep / metrics / weekly，断言无新行。
- **BFF**：`POST /students/{id}/feedback`（每账号每日 20 条，来源由会话决定）；退出路由；已退出档案在所有带 `{student_id}` 的路由一律 409 `TEST_EXITED`（数据权利与退出相关路由放行）；所有相关 OpenAPI 文件枚举补 `TEST_EXITED` 与 409，有断言测试。学生模式两份文书核对只在内测开关下生效（关时沿用 14 岁以下核对监护人同意），`shell.v0.yaml` 已改。staging-otp：登录成功时记“条目 → 账号”（`account_hmac` 只在 consumed 行，旧格式兼容），退出按账号作废。
- **前端与审核台**：学生 / 家长反馈页（提示不填个人信息，文案为 `TODO_COPY` 占位）、家长退出确认页（勾选“我已了解”）、审核台反馈与内测退出页。
- **开关**：BFF / 前端 `STARBUDDY_INTERNAL_TEST`、审核台 `REVIEW_CONSOLE_INTERNAL_TEST`，缺省关，pilot / 生产拒绝；staging 矩阵 `internal_test_ops` off、pending `migration_0059`。
- **手册** `docs/ops/internal-test-runbook.md`：开放前置、招募口径、签署、发码（`umask 077` 子 shell、管道导入凭据管家）、反馈处理、值守、分级时限、退出、测试结束统一删除；作废后已签发验证码最多再有效 5 分钟；有退出记录后关开关不能止损；退出家长可新建档案；RDS 错误日志可能含 CHECK 失败整行。告警 `StarbuddyStagingOtpRevokeAuditFailed` 已加入。

## 测试
子代理逐目标：db-test 3243、db-mutation 内测组 40/40、attack-surface-check、bff-test、bff-mutation 相关组、web-test、learning-test 3865 与 internal-v1 / today / mastery / diagnosis / practice / retest 变异、insight 测试与变异 253、scheduler 测试与变异、review-console-mutation、staging-mutation、deploy-check、internal-test-tool-test、e2e-cohort 单测、contract-test 通过。严格 `make check` 见提交说明。

## 审查意见的处理
- 契约审查阻断：退出后 BFF 以外仍写入与模型处理 → 用户决定库层统一判据、全服务过滤；新 409 未进其他 OpenAPI 枚举 → 补齐并加断言。
- 安全审查阻断：退出判据挂开关 → 不再挂开关、库层触发器拒绝再签同意与发会话；定时任务仍处理 → 同上；退出函数返回可穷举的手机号摘要并波及他人 → 用户决定按账号、仅无其他在测孩子时作废；手册输出文件权限 → umask 077 与管道。建议全部采纳（独立指针、子密钥、告警、down 注释、权限断言）。
- 同意核对对全环境生效 → 用户决定只在内测开关下生效。

## 教训
- “停止处理”这类跨服务约束，判据必须放在共同的真相源（库）并由每个批量入口调用；只在网关层拦，定时任务会绕过去。
- 运营开关只能控制入口，不能承载合规状态；关开关不得让已撤回 / 已退出的状态失效。
- 给服务返回联系方式的任何派生值（无盐摘要）都等于开新的读通道；按账号 ID 关联即可。
- 新增全局问题码时，封闭枚举要在所有相关接口文件同步，并有测试防漏。
- 严格检查中 db-mutation 失败：攻击面夹具（db/pentest/fixtures.sql）自检要求每张带 student_id 的个人层表覆盖每个学生，新表未铺数据，攻击面组变异全部因夹具失败而“被发现”。已为每个学生补一条合成家长反馈，`internal_test_exit` 加入豁免（铺满等于全员退出），重跑 1196 个变异全部被发现。新增个人层表时要同步攻击面夹具。
- 子代理违反约束：空 heredoc 一次；部署检查的一次性库演练与 db-mutation 并发运行一段（违反“一次只跑一个改库目标”）；一条命令含禁用词被钩子拦下后停下未重试（符合约定）。

## 待人确认
1. 迁移 0059 草稿：副本演练后执行；执行后三处开关一起打开；**执行后处理判据对所有环境生效**（无有效同意的档案不再被处理）。
2. 学生本人单独确认同意：需新迁移或契约变更；两份文书核对的库层同口径另起迁移。
3. 员工来源只有枚举，没有写入路径；没有单独值守角色，反馈页与退出页对全部在职员工只读、改状态全部员工可做（新增角色属必停）。
4. 反馈不进数据导出包（查阅权是否满足）；反馈文本不过禁用推断词表（只作反馈）；库里无反馈每日上限，BFF 限流（每日 20 条、退出每小时 10 次）为合成缺省。
5. 白名单当前条目文件（`--existing`，含全部有效号码与码）长期存放在跳板加密盘是否可接受。
6. 从未用固定码登录过的家长无账号对应，退出时不作废其条目；仍有其他在测孩子的家长不作废；作废前已签发验证码最多再有效 5 分钟，如需立即切断的做法。
7. 已退出档案无恢复路径；退出家长可新建档案重新进入。
8. 测试结束统一删除时家长需登录（管理员须重新发码）；联系不上的家长无管理员代删路径；同意记录与退出记录的留存期限；测试结束日。
9. 手册默认值：招募措辞、值守时段、分级时限。
10. RDS 错误日志可能含 CHECK 失败整行数据：访问范围与留存（同时记入 LT-05 待人确认）。
11. 取消验证任务与 `admission_recompute` 未加判据；已删除学生调今日编排在迁移执行后由 200 空列表变为 404。
12. e2e-cohort 样例：replay 补了同意记录，`tools/e2e-cohort/sample` 未重新生成，哈希可能变化（严格检查若发现不一致须重新生成）。
13. 跨服务测试中“周报无档案页调用”的断言较弱（调度器测试环境未配学习服务），实质由洞察服务自身测试覆盖。
