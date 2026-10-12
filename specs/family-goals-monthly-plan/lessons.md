# PLT-14 家长诉求与月度改进计划 · 实现记录

2026-10-10 起由 Claude 按用户授权推进；用户已确认四项口径（见 spec）。第 1 步含迁移、RLS、画像可见范围，提交首行 `[NEEDS-HUMAN]`。

## 第 1 步 家长诉求收集

### 交付
- 迁移草稿 0053 `insight.guardian_goal`（只在一次性库演练，未在任何共享环境执行）：关注学科（≤2、在读）、分科目标分（家长自填满分 ≤300，目标 0–满分，最多一位小数，≤4 项）、关注学校（`policy.school` 已复核发布、在办、来源已发布、同地区，仅初三可设）、补充说明（1–200 字，拒绝 Cc/Cf/Zl/Zp）。`source_type` 固定家长自述；只追加成版，旧版标 `superseded_at`；两位家长各存各的。
- RLS 强制、无策略；只经 `insight.submit_guardian_goal` / `insight.guardian_goal_view`（属主 authz，只授 BFF），授权先于取值核对（`is_linked_guardian`）；学生、教师、学习与洞察服务均无权限。删除登记（delete、facts），导出主题 `guardian_goals` 只含本人写的行。
- BFF `GET/PUT /bff/v0/students/{id}/family-goals`：学生会话在请求体、CSRF、限流之前就回与“不存在”逐字节相同的 404；补充说明经禁用推断词表过滤（命中 422、词表不可用 503，均不存）；不计量。开关 `STARBUDDY_FAMILY_GOALS=family_goals`，只在 dev/test，缺省关。
- 前端家长“我的诉求”页（文案 `{{TODO_COPY:family_goals.*}}`）；学生端目录不引用诉求模块（静态测试）。
- ADR 02 末尾新增“增补（2026-10-10，PLT-14，待签字）”；字段字典新增 4 个字段（学生不可见，家长不可单项删除）。

### 审查意见的处理（按默认）
- 契约审查阻断（家长期望出现第二存放位置）→ 默认口径：`insight.guardian_goal` 是关注学科、分科目标分、关注学校的唯一结构化来源；`platform.guardian_report` 的 expectation 只作叙述文本，第 2 步不读；写入 ADR 02 增补与字典 note，待签字。
- 建议：学生 404 前移并补行为断言；专用问题码 `GOAL_GRADE_NOT_SUPPORTED`；映射测试（409、429、503）；导出行为测试与变异；目标分校验不依赖短路；学校名称口径与写入一致；字典 `guardian_deletable` 改 false。
- 等 PLT-11 提交后补：BFF `main.ts` 读开关、路由并入 `routes.ts` 的 `activeRoutes` 与授权矩阵 SAMPLES、BFF 变异登记、开关打开的一套 e2e。

### 待人确认
1. ADR 02 增补与字典口径（唯一结构化来源、expectation 只作叙述、另开新表、两处跨 schema 依赖）。
2. 两位家长互相看不到对方写的诉求。
3. 解除绑定后：诉求行是否删除或标记；解绑家长能否导出自己写过的内容；重新绑定后是否恢复显示。第 2 步只读当前有效绑定家长的当前版。
4. 账号注销路径出现时一并处理本表（`guardian_account_id` 无 ON DELETE）。
5. 补充说明可能含孩子姓名等个人信息：只回显给作者本人，不进计划文本、判断账本或任何视图。
6. 学校考试各科满分无真相源，由家长每科自填（≤300）。
7. 写入上限（24 小时 20 版）与限流（读每分钟 30；写每对每小时 10、每家长每小时 30）为默认值。
8. 补充说明未过“名次 / 概率 / 承诺”词表（该词表尚不存在，随 SIE 首个任务包补）。
9. 是否需要单独的“撤回诉求”（现可提交空诉求）。
10. 迁移 0053 须副本演练后由人执行；`delete_order` 36 与 0037 的 profile_page_check 重号（无外键，不影响），演练时确认。
11. 招录库无数据回填前“关注学校”选不出学校。
12. 字典状态标 planned、可见范围含 agent（检查脚本要求），但学习与洞察服务暂无读权限，第 2 步再定。

13. BFF 授权矩阵测试中 link 桶断言放宽为“家长路由或 guardianOnly404 路由”（诉求路由 auth 仍是 session，学生得 404 而非 403）。
14. e2e 合成种子以迁移执行者身份直写“已发布”学校，绕过签字发布流程（只用于 dev/test 合成数据）。
15. **既有变异测试漏检（与本任务无关，需另起任务）**：“信任：周报收已撤回的判断”“报告：不核对受众”两条变异，过去被“发现”只因变异副本里没有 `docs/`（读不到禁用推断词表，请求一律失败）；副本带上词表后两条都漏检，相应测试需补断言。目前只给“诉求：”组的副本链 `docs/`。

### 剩余接线（PLT-11 提交后完成）
- BFF `main.ts` 读开关，非 dev/test 设了以退出码 2 拒绝启动（启动测试）；路由并入 `activeRoutes`（`guardianOnly404` 标记），授权矩阵全量覆盖；`services/bff/tests/mutations.sh` 新增“诉求：”组 5 条（全量 298 个全部发现）；补充说明只在非空时读词表。
- e2e 新增 family-on 一套（种子 `db/seeds/dev/family-goals-synthetic.sql`）：家长读写与成版、校验提示、学生端各页无诉求内容、学生直接访问被转回、学生调 BFF 得逐字节相同的 404。

### 过程记录
- 子代理一次使用了空的 heredoc（未执行内容、未写文件），违反约定，如实记录。
- 子代理在 scratchpad 之外留下 `/tmp/snap-gg.sql`（生成授权快照用），列入待人清理的临时文件。

## 第 2 步 月度计划生成与落库

### 交付
- 迁移草稿 0054（只改 insight 对象；唯一的 platform 授权见待人确认 1）：`insight.monthly_plan`，同一学生同一月份唯一，只追加、不可改、删除只随删除请求；RLS 强制无策略。
  - 函数（属主 authz）：`monthly_plan_facts`、`monthly_plan_read`、`save_monthly_plan`、`monthly_plan_replay_inputs`（授洞察服务）；`monthly_plan_guardian_view`、`monthly_plan_student_view`（授 BFF）。
  - 权益：`insight.monthly_plan_entitled`，按现有套餐 `weekly_report` 取值含 monthly 判断，不新增套餐开关、不改计量。
- 洞察服务 `insight/monthly.py`（纯函数）、`store_monthly.py`、内部接口 `POST /internal/v1/monthly-plan/generate`（服务内文档 `openapi/monthly-plan-internal.v0.yaml`）；模板库 `templates/monthly_plan.v0.yaml` 全部 `{{TODO_COPY:monthly_plan.*}}`；“名次 / 概率 / 承诺”词表草稿 `docs/design/sie/no-promise.v0.draft.yaml`。
- 快照：`student_plan`（优先知识点 ≤8，按 缺口 × 考情权重 × 频率 × 关注学科系数，字段 `position`）、`guardian_notes`（≤3 条模板键）、`review`（上月点亮：按 claim_history 判定上个月进入迁移且节点在上月计划内；分差只存成绩记录与诉求引用，家长视图读时现算并再核当前版，按 5 分一档给区间）；每条关于学生的陈述带判断 ID，写入与读取各再核一次。
- 重放：落库 `input_refs`（判断与历史版本、掌握行、成绩记录、诉求、复测任务）与配置、模板库、今日计划预算的内容指纹；测试按引用重建输入逐字节重放。
- 生成接口：已有快照先返回，再查权益；caller=bff 须带 actor 并在库内再核为有效绑定家长；无 actor、不通过、档案不存在、无权益统一一个错误。
- 变异：数据库“月度计划”组 55、攻击面组 41、洞察服务 185，全部发现。

### 审查意见的处理（按默认）
- 安全审查（规则 11）：上月回顾去掉不引用判断的“完成复测次数”。
- 契约审查阻断：ADR 02 增补补上 `insight.monthly_plan` 归属与读取授权；不另设套餐开关（与 `weekly_report` 的 monthly 取值重复），迁移只改 insight；lessons 补记（本节）；字段字典 `guardian_deletable` 改 false、来源按实际标注。
- 建议：分差不存值、读时现算并给区间；`plan_basis` 与 `input_refs` 只供审计与重放，视图不返回；`rank` 改名 `position`；落库输入引用与指纹；生成接口带 actor 再核、统一错误；幂等顺序统一；补变异盲区。

### 待人确认
1. 0054 授 `starbuddy_authz` 读 `platform.retest_task.id`（为重放记录复测任务 ID），ADR 02 增补已作为例外写明。
2. 权益口径：`weekly_report` 取值含 monthly 即有月度计划。结果是 v12 的 Pro、Max 与 ch8 的 Pro 有；ch8 的 Max（`cross_subject_deep`）没有。是否需要调整。
3. 默认设计 1–7（step2-spec）：洞察服务生成、快照放 insight、BFF 打开页面时触发（第 3 步）、权益、只读输入、快照结构、可见范围。
4. 考情权重来源：0013 无节点级权重，暂全部按 1（快照标 `policy_weights: default`）；“频率”含义待定。
5. 关注学科系数 1.5 与缺口取值等合成配置、模板文案、“名次 / 概率 / 承诺”词表，均待人写人审签字。
6. 学生能从优先知识点的排序间接看出家长关注的学科（spec 口径 1 已接受“诉求只影响优先顺序”）。
7. 套餐降级后仍可读已生成的快照（视图不再查权益）。
8. 快照不进导出包（与 0036 周报同一口径）；家长解绑后其诉求引用仍留在历史快照中直到整档删除（连同第 1 步待人确认 3 一并定）。
9. 字段字典未把学生自述列为来源（考试安排、成绩可能由学生录入），因检查器要求含 self 的字段家长可删，与不可删冲突；note 已注明。
10. 第 3 步填入数字后的成品句子须再过禁用推断词表与“名次 / 概率 / 承诺”词表；BFF 调生成接口前须先确认有效绑定家长，学生会话按第 1 步做法回 404。
11. 渗透夹具与删除覆盖测试以超级用户直写快照（绕过权益与判断引用核对，表守卫仍生效），只用于合成数据。
