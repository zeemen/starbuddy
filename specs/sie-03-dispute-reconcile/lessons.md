# SIE-03 异议、预测对账、触发规则、导出与删除 · lessons

## PR① 迁移草稿与库上测试（本 PR，含契约审查与安全审查的修改）

### 做了什么
- 迁移草稿（每个都有 up / down，全部待人在副本演练后执行，PR 首行 `[NEEDS-HUMAN]`）：
  - `0032_insight__dispute`：`insight.dispute`，类型码与契约一致（`i_know` / `one_off_slip` / `data_error`）；来源按提出人记（学生 self、家长 guardian）；
    `one_off_slip` 只收学生本人、可带属于该学生的作答（`attempt_id`，不另建自述表）；`data_error` 必须带原始事实引用（`fact_kind` + `fact_id`，
    作答或考试记录须属于该学生）并进 0023 统一复核队列；验证任务引用 1–5 个；流转表 + 守卫（终态不可改、不可变列、结论码只收枚举、
    结案时判断状态必须对上，事实更正还要求原始事实已有更正行：考试记录被替换或作答的判分被改判）；
    `insight.raise_dispute`（属主 authz，只授洞察服务；学生与家长分开限额）。“我其实会”的判断转待验证由洞察服务在同一事务按 0010 状态机做，
    库用延迟约束触发器在提交时核对（函数属主不再有改判断的权限）；`platform.erasure_in_progress`（删除守卫判定，认 `starbuddy_eraser`）。
  - `0033_insight__intervention_log`：动作记录只引用同一学生、未过期的成立 / 假设判断，记 `params_version`；下发要求同规则同参数版本已签字，
    并在下发时再核一次判断（已转待验证、终结、过期的拒绝）。
  - `0034_insight__exam_prediction`：`exam_record.raw_score` 可空（考试安排）、新增 `plan_record_id`（成绩行 → 安排行）；同一场考试（学生、学科、类型、日期）
    只有一条当前安排（重复录入复用），所以每场考试一条预测；满分沿用 0022 的 (0, 300]；`submit_exam_plan`、`submit_exam_score`（BFF）；
    0022 的 `onboarding_view` 在本迁移里 `CREATE OR REPLACE`，成绩列表不含考试安排；预测带 `claim_id`（考试类判断）、`level` 固定 0.80；
    对账卡按 `plan_record_id` 匹配成绩，字段对齐契约（`deviation_directions` 数组四值、`deviation_nodes`、`adjustments`、来源 inferred、算法版本、证据 ID），
    每句所引判断须模板已审核、未过期、成立或假设，且可见范围覆盖卡片读者（`audience`）。
  - `0035_platform__erasure`：清单加 `erase_scope`（生成回执 `scopes`）与 `export_topic`（导出包用稳定主题键）；预测与对账卡不导出；
    导出任务表 `platform.export_job`（状态、一次性下载凭据摘要、24 小时失效，兼作审计）；删除执行函数属主改为新角色 `starbuddy_eraser`，
    33 张表的 DELETE 只授它（authz 不再有任何清单表的 DELETE 与相关读权限）；执行时锁档案行、删完停用学生账号并标记 `student_profile.erased_at`；
    已删除的档案不能再发起删除。
- 测试：79m–79p 随之重写；`run.sh` 新增 0035 的角色断言（eraser 可登录、能建角色、有成员、继承别的应用角色时 0035 拒绝执行）；
  `80_attack_surface` 加 eraser 进应用角色、属主与 BYPASSRLS 例外、eraser 无成员、只拥有一个对象、除删除函数外没有函数删除清单表；
  快照重新生成；`mutations.sh` 共 64 个 `SIE-03` 变异 + 2 个攻击面变异。
- 其他：`db/pentest/fixtures.sql` 同步；`scope.md` 函数 93 个（新增 `submit_exam_score`，删除函数属主与可执行角色写明 eraser）；
  字典 `through.g9.interventions` 改为推断（source_type inferred、经判断对外）。

### 范围外改动（请确认）
- **`db/bootstrap/roles.sql`** 新增 `starbuddy_eraser`（NOLOGIN、BYPASSRLS）。不在协调方列的范围里，但 M12 要求新建专用角色，
  按仓库约定角色只由 bootstrap 创建、迁移只断言；不改它 0035 无法执行。

### 对审查意见的取舍（按默认建议，说明几处解读）
1. M12 “claim UPDATE 只授 eraser”：删除不需要改判断；我理解为“authz 不再有改判断的权限”。raise_dispute 不再改判断，改成洞察服务同事务改、
   库在提交时核对（延迟约束触发器）。eraser 没有授 claim UPDATE。
2. 契约审查“测试里的 careless 全部去掉”：只保留两处反例（断言 `careless` 作为异议类型、偏差方向都被拒绝，以及全库没有这类标签列）。
3. H3 / N1 的导出列：规则是“字典里对家长可见的落库列，或通用元数据列”；判断账本按行过滤（对家长可见、模板已审核、非终态、未过期），
   同意与订阅是家长本人的记录，三者表级豁免写在 79p；判分结果与掌握概率不在字典里对家长开放，已从导出移除。
4. 确认 1 的竞态：执行时对档案行 `FOR UPDATE`（挡住带外键的并发写入），删完逐表复核为零；另停用学生账号，学生的写入口因此全部拒绝。
   家长侧写入口（如家长观察、考试安排）没有逐个改成检查 `erased_at`，见待人确认 #6。
5. M6 与 spec“复核队列”并存：`data_error` 同时带原始事实引用并进复核队列（spec 默认保留）；契约写“在原录入通道更正”，两者关系见待人确认 #3。

### 待人确认
1. **迁移执行**：0032–0035 均为草稿，须先在副本演练（含 down）；0034 的 down 在已有考试安排时会失败（有意不静默删）；0035 的 down 会删掉回执与导出任务记录，
   回滚前须留档；`starbuddy_eraser` 由人在各环境执行 bootstrap 创建。
2. **冷静期与时限**（spec #3）：确认窗口 24 小时、冷静期 7 天（约束 1–30 天）；“到期后 24 小时内执行”靠调度频率保证。
3. **事实更正通道**：契约说事实在原录入通道更正（`open_fact_for_correction`），库里只认“考试记录被替换 / 判分被改判”为更正行；上传（`upload`）事实
   在上传管道（KGE-05）建表前不能被指明。复核队列与原录入通道谁主导，待人定。
4. **推翻与撤回判断**（spec #4）：随删除一并删除，含判断历史。
5. **清单豁免项**：档案骨架（年级、地区、出生年月，删除时只标 `erased_at`）、学生账号（停用）、监护绑定、同意记录、计量七表（订阅、额度、预留、账本、自练计次、
   试用两表）、家庭两表、staff 五表、回执与导出任务。档案骨架是否去标识、账号何时注销，随 PLT 账号注销流程与合规意见。
6. **删除后的家长侧写入**：学生账号已停用，但家长仍能写家长观察、考试安排等（这些函数未检查 `erased_at`）；是否统一拒绝待定（需改 0022 等既有函数）。
7. **导出**（spec #5）：家长导出不含学生自述、学生提的异议、行为事件、探针、诊断、证据原文、推断参数、预测与对账卡；学生本人的导出入口未做；
   导出包由函数一次性返回，下载凭据的兑换与存储（对象存储）在 PR②/③。
8. **契约差异（只登记，不改契约）**：
   - erase 契约要求一次性确认码（8 位、10 分钟、错 5 次锁定）；库里是“发起 + 发起人 24 小时内确认 + 冷静期内可撤回”（spec 要求撤回，保留）；
     BFF 二次确认是否用短信验证码待定；
   - export 契约是异步任务（queued / running / ready / failed）；库里同步生成、直接 ready；
   - 契约 `exam_type` 是 `mock`，0022 是 `mock1` / `mock2`，映射在洞察服务做；契约预测与实得分上限 150，库里满分到 300；
   - 契约 `scope_node_ids` 最多 400 个，0022 的列约束最多 64 个；
   - `uploads` 回执范围：上传管道建表后登记。
9. **对象存储原件、备份、日志中的学生数据**，14 周岁以上学生本人的删除与导出权利：本 PR 只覆盖库内个人层表，其余待合规方案。
10. **异议可见范围**：学生、家长各看自己提的；`p_raised_by` 与发请求的人是否一致由洞察服务在 PR② 核对（库里只核对该账号与学生的关系）。
11. **“我其实会”卡在待验证**：验证任务（KGE-04 第二步）落地前，判断会停在待验证直到到期或撤回；24 小时内推送验证题的指标由 PR② 统计。
12. **80 的 raiser 模式**：预期集合按 `raised_by` 推，与策略同构，只能防跨户越权；“看不到家长替自己提的”等逐行断言在 79m。
13. 80 的函数越权矩阵只循环 platform schema；insight 下授给 BFF 的函数（含新增的两个）在 79o 手工断言“另一户 / 不存在同一个 42501”，
    `submit_exam_score` 登记了豁免；把矩阵扩到 insight 需给 `onboarding_view`（无权返回 NULL）加特例。
14. 数值：异议学生、家长各 24 小时 50 条，删除请求 5 次，导出 10 次，验证题 1–5 道，动作引用 1–16 条，区间级别 0.80。

### 验证
- `bash db/tests/run.sh`：通过（35 个迁移 up → down → up、最新迁移单独 down → up、0035 角色断言，1851 项断言）。
- `DBTEST_ATTACK_ONLY=1 bash db/tests/run.sh`：通过（27 项）。
- `DBMUT_FILTER=SIE-03 bash db/tests/mutations.sh`：64/64 被发现；`DBMUT_FILTER=攻击面`：24/24 被发现。
- `make attack-surface-check`、`make check-fast`、`make pentest-env-test`：通过（contract-test 因本机缺 jsonschema 跳过，既有情况）。
- 全量 `db/tests/mutations.sh` 与 `make check` 未跑（本机 4 核、同时有其他会话在跑），提交前需在 CI 或空闲机器上跑一遍。

## PR② 洞察服务（services/insight）

### 做了什么
- 库层只用 0032–0035 授给 `starbuddy_insight` 的表与函数，不写任何 platform 表；判断只由洞察服务写（规则 11）。
- `insight.dispute`（契约工具，只给 BFF）：`insight.raise_dispute` 按库里关系核对 `actor_ref` → 洞察服务再核对库里记下的提出人身份
  （`raised_by_role`、`raised_by`）与请求 actor 一致（待人确认 #10），不一致整笔回滚、按同一个 404；
  - `i_know`：同一事务把判断转待验证（0010，原因 dispute）并登记验证任务引用（配置 2 道；`verify_by` = 提出后 24 小时）；
    验证任务走 `VerificationScheduler` 接口，KGE-04 第二步前是接口桩 `StubVerificationScheduler`（确定性占位，题目引用带
    `stub-verify` 前缀，**不是真实题目、不产生真实任务**）。验证结果以 KGE 掌握行回来，在判断生成同一入口
    （`InsightStore.sync_in`）里结案：门槛高于判断所述 → 原判断推翻、异议成立、同事务按新行重算出新判断；相同 → 原判断回到成立
    （需策略已签字）、异议维持；比较用异议时的历史快照（其间的常规修改可能已并入验证证据）；
  - `one_off_slip`：只学生本人，只记异议行（来源 self），不改判断、不产生任何标签；观察期（配置 14 天）满后 `self_statement_recorded` 结案；
  - `data_error`：由判断推出原始事实（见偏离 2）→ 0023 复核队列；`jobs.settle_fact_corrections`：事实已更正（考试记录被替换、判分被改判）
    → 相关判断（被异议的、同一依据行生成的、同对象同模板的）全部撤回（fact_corrected）→ 异议 `fact_corrected` → 同事务重算；
    `jobs.confirm_data`：复核结论“数据无误”→ 维持结案；
  - `jobs.sweep_disputes`、`jobs.dispute_metrics`（“我其实会 24 小时内推送验证题”的比率）。
- `insight.predict`（只给编排层）：找学生 / 家长录入的考试安排（0034 `submit_exam_plan`）→ 合成掌握行（`MasterySource`，缺省没有 → 409
  证据不足）× 权重（缺省等权）× 题型系数（缺省 1）→ 向先验收缩、80% 区间（证据少则宽，有性质测试）→ 写考试类判断（只给 agent）
  与预测；考试当天零点起返回原结果不再重算；冻结由调度器入口 `scheduled.freeze_due_predictions` 调库函数。
- 对账：`jobs.reconcile_exam`（成绩上传后）→ 区间内外、偏差节点（调用方给的逐节点得分率）、偏差方向（契约四值）、调整
  （区间外偏差节点的判断转待验证，原因 exam_deviation → `claims_to_verify`，否则 `none`）→ 写对账判断（`syn.exam.reconcile`，
  对 agent / 学生 / 家长可见）与对账卡（每句只存判断 ID 与模板键，服务侧与 0034 守卫同一条件先查一遍）；渲染命中禁用推断词表、
  没有合适方向等情况整笔回滚、返回阻断编码；`insight.reconcile` 只读卡片，陈述只由判断渲染。
- 触发规则 v1（`jobs.evaluate_triggers` / `dispatch_triggers`）：提醒（判断将到期）、证据化鼓励（窗口内升为成立、仍成立）、降负
  （连续中断、连错后退出增多，引用中圈指标判断）；只写 suggested、去重；下发要求配置已签字，库里再核签字登记与判断状态。
- `insight.export` / `insight.erase`：`GuardianRequests` 接口 + 库函数返回形状 → 契约形状的映射；缺省实现不接线（501，见契约差异 7）。
  测试里以 BFF 角色调真实 0035 函数走通导出、删除申请与确认，调度器入口 `scheduled.execute_due_erasures` 执行后该学生零行。
- 禁用推断 0：新增配置 `config/sie03.synthetic.json` 的全部占位文案（异议回应、偏差方向、平台调整、触发动作说明）与编码加载时过
  SIE-00 词表（命中拒绝加载，有阻断测试）；对账判断渲染命中词表不出卡；测试夹具的考试模板过词表（另有一条反例模板只为验证阻断）。
- 测试：`tests/test_sie03_units.py`（40 项）、`tests/test_sie03_db.py`（12 项，库上 + 契约严格校验）；`tests/mutations.sh` 新增 44 条
  SIE-03 变异，另把“五个工具不回 501”换成“SIE-03 未配置不回 501”（共 94 条）。既有两处测试随 501 → 实现改写。

### 按安全审查与契约审查的修改（用户授权按默认建议）
- F1：预测判断的可见范围 = 配置 ∩ 模板上限，交集为空报 `Unavailable("prediction visibility")`（409），不回退到模板上限；反例测试 + 变异。
- F2：验证任务接口桩只由 `create_app` 在 test 环境注入（`dispute.default_scheduler`）；其他环境没有注入真实实现时 `i_know` 先回 501、
  不写任何东西，另两路照常；ToolCore 自身缺省 `UnavailableVerificationScheduler`。测试 + 2 条变异。
- F3 + 契约 M1：先取学生锁，再按与 `insight.raise_dispute` 同条件预检判断（属于该学生、非终态、未过期、模板已审核、可见范围含受众，
  `FOR UPDATE`），不可见一律 404；之后才做类型检查与事实定位，所以 `_locate_fact` 在核权前不读 platform。身份（本人 / 有效绑定家长）
  洞察服务读不到（没有 account、guardian_student_link 权限），由工具层的 `ActorVerifier` 先核、库函数再核。测试（不可见且事实定位
  不了的判断回 404 而不是 409）+ 变异。
- F4：BFF 代表的所有写入类工具（dispute、export、erase）先经 `self.verifier` 核对 actor（家长含 link_id），缺省一律不通过（与 explain
  同口径）。结果：接上真实 verifier 之前，`insight.dispute` 在生产里同样是 404。把 link_id 传进库函数需要改 0032（迁移，必停）。
- F5：`fact_ref` 契约要求返回（`open_fact_for_correction` 时必填），保留，标注“原始事实的内部引用，BFF 不得转发给前端或第三方”
  （代码与 README）；精确定位需要授 `platform.attempt.response_evidence_id` 给洞察服务（迁移，必停），否则仍是启发式。
- F6 + 契约差异 6：命中幂等缓存前先重新核对 actor（授权撤销后同 key 重试回 404）；持久幂等表需要迁移（登记，未做）。
- F10：非 test 环境不设 `INSIGHT_SIE03`（缺省指向合成占位）会拒绝启动；README 与本节写明，另有测试。
- 低：`store.OTHER_APP_ROLES` 加 `starbuddy_eraser`；异议入口先取学生锁再 `FOR UPDATE` 读判断。
- M2：对账判断的 value 记下 `directions` 与 `deviation_nodes`（超过 1800 字节整笔回滚），卡片的方向与偏差节点只从写入后的判断读取；
  服务侧句子检查要求每个方向都在所引判断的 value 里。测试“卡片每个方向都能在判断账本找到”+ 2 条变异。
- M3：已有预测时只更新 `exam_prediction.claim_id` 那条判断；它已终结回 409，不另建。测试 + 变异。
- 6：预测用的掌握行经 `InsightStore.current_basis` 过滤（只要该学生库里的当前行）。测试 + 变异。
- 7：`dispute_metrics` 把接口桩生成的任务（可按异议 ID 复算）单独计 `stub_scheduled`，有接口桩时 `valid = false`、`rate = null`。测试 + 变异。
- 8：异议响应的 `evidence_ids` 是被异议判断的证据。测试 + 变异。
- 10：执行前的删除范围（`erase_scopes`）仍取配置：0035 清单只授 authz 与 eraser，洞察服务和 BFF 都读不到；保留配置与清单一致的库上测试，
  授读权限需迁移（登记）。

### 偏离（请确认）
1. **“我其实会”冲突**：验证门槛低于判断所述时，学生的异议不成立、原判断也不成立；0032 流转表里“验证中”只能到 resolved_supported
   （判断须推翻）或 resolved_unsupported（判断须成立），都不对。现做法：异议保持验证中、判断按常规生命周期推翻，`sweep_disputes`
   计 `i_know_stuck`。需要在 0032 加一行流转（例如 verifying → resolved_unsupported 且判断 refuted，结论码另定），必停。
2. **“数据有误”的原始事实**：契约请求不带事实引用。考试类判断取考试记录（安排已有成绩时取成绩行）；知识节点判断取证据里最近一条
   能“同一学生、学科、题目、发生时间”唯一对上的作答（0028 没授 `attempt.response_evidence_id`，只能这样对）；对不上回 409，不猜。
   这是启发式，待人确认（或授权 response_evidence_id，或契约加 fact_ref）。
3. **“粗心”异议触发一次同节点练习**（spec #2）未做：0033 的动作没有“练习”编码，一次性练习也不是触发规则；待编排层定接口。
4. **不成立时要求策略已签字**：未签字的策略下判断不能回到成立（SIE-02b 的规则），异议会停在验证中直到判断到期或撤回。
5. **预测口径**：洞察服务读不到年级（没有 student_profile 的读权限），“七八年级 100 分制、九年级 120 分制”按满分标注口径（配置 scales），
   只在同口径之间比较；地区考情权重（policy.*）读不到，缺省等权，由调用方注入；题型系数缺省 1（KGE-04 #4 未定）。
6. **可见范围（必停，spec #6）**：预测判断只给 agent（契约与字段字典）；对账判断默认对 agent / 学生 / 家长可见（0034 要求卡片读者在
   所引判断的可见范围内），这是新的学生、家长可见陈述，由配置 `reconcile_visibility` / `card_audience` 控制，待人确认。
7. **对账方向**：没有逐题解析时，低于区间默认“此前诊断偏乐观”；高于区间四方向都不贴切，不出卡（`no_direction_for_above_interval`）。
   偏差节点只来自调用方给的逐节点得分率（上传解析 KGE-05）；调整只会是 `claims_to_verify` 或 `none`（复测调度等随 KGE-04 第二步）。
   对账判断置信度写 1（成绩与冻结区间的确定性比较）。
8. **触发规则**：“验证题待做”（plan_verification_item）不生成——0033 不许动作引用待验证判断，而被异议的判断正是待验证；降负引用
   中圈指标判断（持续性、计划完成率），没有就不生成；“连错后退出”事件由编排层写（0025 注明未实现），接上后才会触发。
9. **异议只给 BFF**：契约上下文允许编排层，但 0032 要求提出人，编排层没有 actor，改为准入 403。
10. **判断生成入口改动**：`InsightStore.sync_mastery_claims` 拆出事务内的 `sync_in`（事实更正重算复用），并在生成前先结案验证中的
    “我其实会”异议；`SyncResult` 加 `settled`。没有异议时行为与原来一致（既有测试全过）。

### 契约差异（只登记，未改契约；必停）
1. `InsightDisputeRequest` 没有事实引用与作答引用（0032 的 `fact_kind` / `fact_id`、`attempt_id`）：见偏离 2；`one_off_slip` 的作答只能为空。
2. 家长访问的 `link_id` 洞察服务核对不了（没有 guardian_student_link 的读权限）；库函数认“任一有效绑定”，不认具体哪一条。
3. `insight.predict`：请求满分 1–300、响应 low / high 上限 150 → 满分超过 150 回 400；`scope_node_ids` 契约 400 个、库里 64 个，
   且必须与录入的考试安排一致（否则 400）；`exam_type = mock` 对应 0022 的 mock1 / mock2，同一天两场都有时回 400；
   预测要求学生或家长先录入考试安排（找不到回 404），契约没写；`frozen_at` 必填，未冻结时返回预定冻结时间（考试当天零点，上海）。
4. `insight.reconcile`：`exam_record_id` 接受成绩行或考试安排的 ID；卡片由 `jobs.reconcile_exam` 生成，没有卡回 404；契约只给编排层，
   学生版 / 家长版对账卡的读取（0034 audience）没有契约工具，经 BFF（PR③）还是契约新增工具待定。
5. 错误码：Problem 的 code 枚举没有“冲突 / 证据不足 / 窗口已关”，用 409 + `INVALID_REQUEST`；限额用 429 + `RATE_LIMITED`。
6. 幂等：契约要求同 key 异体 409；本步是进程内缓存（调用方 + 工具 + 学生 + key，24 小时，1 万条），重启与多实例靠库里的天然幂等
   （同一人同一判断同一类的未结束异议、每场考试一条预测、删除请求的未结束唯一）；持久幂等表需要迁移。
7. `insight.export` / `insight.erase`：0035 把库函数只授 BFF、按 BFF 会话的 `app.account_id` 核对家长，洞察服务既没有执行权限也不能
   代表家长会话（不能继承其他应用角色），所以缺省不接线回 501。另：契约的一次性确认码（8 位、10 分钟、错 5 次锁定）库里没有，
   库里是“发起 → 24 小时内确认 → 冷静期 7 天可撤回”；撤回没有契约工具；导出契约是异步任务，库里同步生成、直接 ready，导出内容
   没有契约通道（下载存储在 PR③）。接线方式——BFF 直接调库函数并把契约这两个工具移出洞察服务，或给洞察服务授权——由人决定。
8. 异议撤回（withdrawn_by_raiser）没有契约工具。
9. `insight.dispute` 只给 BFF（契约审查 9）：契约上下文允许编排层，但 0032 要求提出人（学生本人或家长），编排层准入 403（偏离 9）。
10. `fact_ref` 契约必填但属于内部引用：建议契约注明“BFF 不得转发”，或改为不透明句柄（安全审查 F5）。
11. 需要迁移才能做的（必停，未做）：持久幂等表；link_id 传入 `insight.raise_dispute`；授 `attempt.response_evidence_id` 给洞察服务；
    授删除清单读权限；0032 加“验证低于所述”的结案流转（偏离 1）。

### 待人确认
1. 上面偏离 1（冲突流转）、2（事实定位）、6（对账可见范围）、7（高于区间）与契约差异 7（导出与删除接线）。
2. 调度器进程（`insight/scheduled.py` 的两个入口）由谁跑、多久一次：冻结在考试当天零点后尽快，删除在冷静期满后 24 小时内。
3. 数据无误（`jobs.confirm_data`）由谁调：复核台以“转洞察服务”结案后通知洞察服务，洞察服务读不到复核任务。
4. `config/sie03.synthetic.json` 全部是合成占位：验证题道数与时限、区间参数、观察期、触发参数由 SIE 负责人与教研签字；全部文案人写人审。
5. 验证任务接口桩在 KGE-04 第二步落地后替换；替换前 `verification_item_refs` 是占位引用，BFF 不应当作真实题目展示。
6. 不需要新的 make 目标：新增测试都在 `make insight-test` / `make insight-mutation` 里。

### 验证
- `make insight-test CONTRACT_STRICT=1`：通过（服务 290 项 + 影子评审 15 项；审查修改后）。
- `make insight-mutation CONTRACT_STRICT=1`：审查修改前 83 / 83（首轮漏检两条，补断言后全过）；审查修改后 94 / 94。
- `make check-fast`：通过（contract-test 因本机各解释器都缺 openapi_spec_validator / jsonschema 跳过，既有情况；本 PR 未改契约）。
- 未跑全量 `make check`。

## PR③ BFF 读写接口（services/bff）

### 做了什么
- 新路由（`src/http/trust-routes.ts`，接口定义 `openapi/trust.v0.yaml`），全部挂在 4-H 信任界面开关上（`src/trust/features.ts`，
  `STARBUDDY_TRUST_UI=dispute,reconcile,data_rights`，只在 dev / test 可开，其余环境设了就拒绝启动）。开关缺省全关：路由不注册，
  与表外路径逐字节相同的 404，4-H“信任界面预留”验收继续成立（`shell-pages` 的预留测试改为断言缺省注册表 `activeRoutes()`）。
  - 异议（dispute）：`POST /students/{id}/claims/{claim_id}/disputes`（学生本人、有效绑定家长；`one_off_slip` 家长会话 403，不到洞察服务）
    → 先在库里按 0032 同条件核对判断对当前受众可见（不可见与不存在同一个 404）→ `insight.dispute`（caller bff、actor、家长带 link_id；
    幂等键按账号 HMAC 派生后转发，原键不出 BFF）；响应只有 `dispute_id`、`kind`、`next_step`、`verify_by`，**不转发 `fact_ref` 与验证题引用**。
    `GET /students/{id}/disputes`：按 0032 RLS 只读自己提出的，只给编码列。
  - 考试（reconcile）：`POST …/exams`（`insight.submit_exam_plan`）、`POST …/exams/{exam_id}/score`（`insight.submit_exam_score`）；
    `GET …/exams/{exam_id}/prediction`、`GET …/exams/{exam_id}/reconciliation`：经 `ExamViewClient` 取契约形状，按契约核对；
    预测所引判断、对账卡每句所引判断都在库里再核一次（可见、成立或假设、未过期、模板已审核、在对外白名单里、模板对得上、实线），
    其余句子（含没有 `claim_id` 的）丢弃，丢弃数记访问日志 `dropped_statements`；方向或调整出现契约外编码整张卡不显示。
  - 数据权利（data_rights，只给家长）：`POST …/exports` 直连 0035 `platform.export_student_data`（按当前授权：有效绑定家长，规则 5），
    导出包暂存在 BFF（键是凭据 sha256，绑定任务、学生、发起账号，24 小时失效），`POST …/exports/{export_job_id}/download` 兑换一次
    （兑换前再核绑定；no-store、附件；内容不含凭据）；`POST …/erasure` 发起、`POST …/erasure/{request_id}/confirm` 会话内二次确认、
    `POST …/erasure/{request_id}/cancel` 撤回、`GET …/erasure` 状态与回执（回执只在执行后给“表名 → 行数”与范围编码）。
    监护操作留痕：库里 `erasure_request`（发起人、撤回人）与 `export_job` 即审计；访问日志另记 `guardian_op` 编码（不记凭据）。
  - 限流桶（`config/auth.json`）：`dispute.account.hour` 30、`exam_write.account.hour` 30、`exam_view.session.minute` 30、
    `export.account.hour` 5、`export_download.account.hour` 20、`erasure.account.hour` 10；错误码新增 `STATE_CONFLICT`（409）、
    `CONFIRMATION_REQUIRED`（403）、`UNAVAILABLE`（503）。
- 测试：`tests/trust-units.test.ts`（7 项）、`tests/trust.integration.test.ts`（14 项：开关关 404、从路由表生成的授权矩阵——未登录 401 /
  学生调家长路由 403 / 缺 CSRF、错 Origin、别的会话 CSRF 403 / 另一户、未绑定、不存在、非 UUID 同一个 404，另写死“导出删除只给家长”；
  异议、考试、预测、对账、导出、删除、限流、源码无中文显示文案）；`shell-openapi` 把 trust.v0.yaml 纳入一一对应与不变量；
  `tests/mutations.sh` 新增 16 条 `信任：` 变异（开关、授权、CSRF、异议三条、考试、预测、对账两条、导出三条、删除两条），
  另加 `BFFMUT_FILTER`（只跑名字含该文字的变异）。

### 偏离（请确认）
1. **导出 / 删除接线：默认 BFF 直连 0035（与现授权一致），洞察服务的 export / erase 工具保持 501，待人定**（PR② 契约差异 7）。
2. **删除的二次确认没有用短信验证码**：现有接口不支持“本会话内重新验证”——只有 `auth_otp_issue` / `auth_otp_login`（后者登录并签发新会话），
   BFF 也读不到家长手机号。改用会话内二次确认：发起时返回一张绑定“本会话令牌 + 这条请求”的确认凭据（带密钥 HMAC），确认时必须在同一会话
   提交它（换会话、换请求 403）；库函数另核“发起人本人、仍有效绑定、24 小时内”。短信复核需要新的库函数（例如只核对不登录的
   `auth_otp_step_up`，并能按会话账号找号码），属迁移与权限，必停。
3. **预测与对账卡没有读取通道**：契约 v0.9 的 `insight.predict` / `insight.reconcile` 只给编排层（且 predict 是写入），0034 也没给 BFF 这两张表
   的权限。BFF 只定义了 `ExamViewClient` 接口与按契约的核对，缺省不配，两个 GET 回 503。需要人定：契约新增 BFF 可用的只读工具
   （例如 `insight.exam_view`，按 0034 `audience` 取学生版 / 家长版），或给 BFF 读授权（迁移）。另外预测判断目前只给 agent（PR② 偏离 6），
   即使接上，BFF 的判断核对也会让学生、家长看不到区间，直到可见范围签字。
4. **导出包暂存在 BFF 进程内**：库函数一次返回整包，没有别处可再取；多实例部署时只有发起的实例能兑换，重启即失效（用户重新导出，
   库里每学生 24 小时 10 次）。pilot 前须换成对象存储（新增存储服务，必停）。暂存上限 200 包、单包 8 MB，超了整笔回滚、回 503。
5. **考试录入的学科用产品命名空间**（aya / wenli / zhixing / echo，与建档的考试录入同一口径），不是学科目录码（math 等）；`exam_type` 用 0022 的
   mock1 / mock2（契约是 mock，映射在洞察服务，PR② 契约差异 3）。
6. **异议对判断的可见核对在 BFF 与库里各一次**（与 `insight.raise_dispute` 同条件），不要求判断在对外白名单里：学生、家长只能对界面上
   看得到的判断提异议，白名单外的判断界面上不出现；是否要求白名单待 5-H 定。
7. **预测 `frozen`**：BFF 只标记冻结时间是否已过，冻结前也显示区间（5-H“考前显示已冻结的预测区间”的时点由人定）；BFF 没有改预测的入口。
8. **对账卡的读取**接受考试安排或成绩记录的 ID（与 PR② 契约差异 4 一致），由读取通道解释。

### 待人确认
1. 上面偏离 1（导出 / 删除接线）、2（短信复核需新库函数）、3（预测与对账卡的读取通道与可见范围）、4（导出包存储）。
2. 限流阈值（上面六个桶）与错误码是否合适；删除冷静期、确认窗口沿用 0035（24 小时、7 天）。
3. 异议的幂等：BFF 把前端给的键按账号派生后转发，洞察服务的幂等是进程内缓存（PR② 契约差异 6）；持久幂等表需迁移。
4. 删除执行后的回执展示（表名 → 行数）是否要按范围（`scopes`）合并成人能读的条目：属 5-H 文案，必停。
5. 学生本人的导出与删除入口（14 周岁以上）未做（PR① 待人确认 7、9）。
6. 异议撤回（`withdrawn_by_raiser`）没有契约工具，BFF 也没有撤回路由（PR② 契约差异 8）。
7. “数据有误”之后原始事实在原录入通道更正（考试记录替换等）：BFF 没有新增更正入口（PR① 待人确认 3）。

### 按安全审查与契约审查的修改（用户授权按默认建议）
- M1：兑换前核对 `platform.is_linked_guardian`、`student_profile.erased_at IS NULL`、没有已确认（冷静期中）或已执行的删除请求，否则 404 并清掉暂存
  （解除绑定只清这位家长发起的，删除相关清该学生全部）；删除请求已确认后拒绝新导出（409 `STATE_CONFLICT`）；删除确认成功时清掉该学生的暂存
  （`ExportStore.purgeStudent`）。家长解除绑定不经 BFF，靠兑换时的核对清掉。测试 + 5 条变异。
- M2：**兑换前查 `platform.export_job` 做不到**——0035 只把它授给 authz，BFF 没有任何列的读权限；兑换也没有库内留痕列 / 函数。
  现做法：访问日志记账号摘要（`logHash`）与 `export_job`；库内兑换留痕（例如 `platform.redeem_export(job, digest)`：核对摘要、过期、状态并记兑换时间，
  只授 BFF）需要迁移，必停，待人确认。测试 + 变异（日志不记账号摘要）。
- M3：对账卡按 `prediction_id` 找预测所引的判断（经同一读取通道取预测），经 `shownClaims` 核对；不可见、预测对不上或取不到时整卡为 null。测试 + 变异。
- H1：删除确认要求本会话 10 分钟内经验证码登录过（否则 403 `REAUTH_REQUIRED`）。0029 的 `auth_session_resolve` 不返回签发时间、BFF 读不到会话表，
  所以登录时刻记在进程内（`src/trust/proof.ts`，键是会话令牌的带密钥摘要）：重启、换实例后一律要求重新登录（从严）；落库需会话表加列或新函数（必停）。
  **冷静期内通知其他绑定家长：没有通知通道**（4-H 只有短信捕获桩、没有推送），登记待人确认；短信复核仍是必停（见偏离 2）。测试 + 2 条变异。
- H2 + L4：暂存时长取 min(1 小时, 库里的 expires_at)；总字节上限 64 MB（另有 200 包、单包 8 MB），超了 503。单测 + 2 条变异。
- L1：删除确认凭据与异议幂等键改用专用密钥 `BFF_TRUST_SECRET`（`Keys.trust`，规则与 `BFF_AUTH_SECRET` 相同：dev / test 缺省随机，其余环境不设或太短拒绝启动）。
  **部署影响**：staging 以上要新配这个密钥，否则 BFF 起不来。轮换影响：未确认的删除凭据失效（重新发起即可）；异议重试会换一个派生幂等键
  （洞察服务进程内缓存认不出，但库里同一人同一判断同一类的未结束异议仍只有一条）。启动测试 + 单测。
- L2：兑换改用 `platform.is_linked_guardian($1)`。L3：暂存移到事务提交之后，放不进回 503（导出任务照样留作审计）。
- L4b：`GET …/disputes` 与 `GET …/erasure` 挂 `trust_read.session.minute`（每会话每分钟 60 次）。测试 + 变异。
- 契约 L1：`open_fact_for_correction` 必须带合格式的 `fact_ref`（仍不转发）；核对统一信封（contract_version、evidence_ids、confidence、两个版本、
  human_review_required），不合按不可用（503）。单测 + 2 条变异。契约 L2：`interval_level` 注释写明与契约、0034 三处同步。
  契约 L3：trust.v0.yaml 的考试安排与成绩补 409（导出补 409、两个 GET 补 429、问题码补 `REAUTH_REQUIRED`）；`shell-openapi` 新增测试：
  handler 用到的问题码都在枚举里且状态码出现在接口里，每条路由按路由表列出 401 / 403 / 404 / 422 / 429（逐条 handler 的状态码对照没做）。
- 契约 L5（补记）：**异议列表是 BFF 直读 `insight.dispute`**（0032 列授权 + RLS），契约没有列表工具；判断可见性的核对实际是三次——
  BFF（库里同条件）、洞察服务（预检）、`insight.raise_dispute`。
- 契约 M2：维持现状（开关关闭），已登记。

### 验证
- `make bff-test`：通过（含 trust-units 9 项、trust.integration 16 项、shell-openapi 新增 1 项、启动测试新增信任密钥断言）。
- `BFFMUT_FILTER=信任：`：29 / 29 被发现（审查后新增 13 条；首轮漏检“导出下载不查删除状态”——确认时已清暂存，补了“另一实例确认、本实例暂存未清”的用例后被发现）。
  `make bff-mutation` 全量：97 / 97 被发现。
- `make check-fast`：通过（contract-test 因本机缺 jsonschema 跳过，既有情况）。未跑 `make check`（另有进程在别的工作树跑严格检查）。

## PR④ 删除覆盖测试（全库扫描）与禁用推断测试

**验收第 3 条未全部完成**：“删除覆盖……档案页”的档案页部分没做——档案页（KGE-04 第二步）还没有表，79q 只做了按表名启发的现状检查（未验证）。
PR 描述须写明这一点。

### 做了什么（只新增测试文件，没改实现、迁移、run.sh、mutations.sh、Makefile）
- `db/tests/79q_erasure_coverage.sql`：run.sh 按 `[0-9]*.sql` 自动发现，排在 79p 之后、80 之前。整段一个事务、结束回滚，不给 80 留数据。
  - 构造：一户两名合成学生（同一家长、同一家庭）。
    - 清单里每张表给两人各写入行：delete 32 张全写；exempt 里能写的全写；家庭级三张一户一行。
    - 有真实入口的一律经函数写：验证码登录注册两位家长、`create_student`、同意（BFF 策略写入）、`meter_open_subscription`（一人试用、一人试点）、
      `enroll_subject`、`set_time_guard`、`create_guardian_invite` → 另一位家长 `redeem_guardian_invite`、`auth_issue_student_session`、
      `meter_reserve` → `meter_settle`、`meter_practice`、异议（`raise_dispute`）→ 复核 `take_review_task` / `resolve_review_task`、
      建档事实、行为事件、信心探针、考试录入、`export_student_data`。
    - 学习服务的表以 `starbuddy_learning` 写，洞察层以 `starbuddy_insight` 写。
    - 只有这些用超级用户写（逐处注明）：模板与试点名单（人工维护）；学生登录手机号（没有入口）；试用学生的选科（学科都是 beta、只对试点开放）。
  - 流程：申请 → 二次确认 → 冷静期内不执行 → 冷静期满（时钟沿用 79p）→ 执行。
  - 按值全库扫描，两类标记：
    - ID 标记：档案与账号 ID，加 delete 表各行里的全部 uuid 值，去掉兄弟姐妹也有的、别的账号与家庭；
    - 内容标记：每名学生唯一的文本标记，写进 12 张表的文本列与键；另加 delete 表的幂等键、令牌与摘要 hex、学生手机号。
  - 断言：
    - 执行前两类标记只出现在清单表里，公共层没有；
    - 执行后内容标记全库零处，ID 标记只剩理由已确认的 11 张豁免表；`trial_claim` 单列为“现状，待人确认”；
    - 全库逐表行数的减少量恰好等于回执；清单外全部表（含 kge、policy）整表摘要不变；
    - 豁免表行数与整行内容不变（只许档案删除标记、账号停用与回执变化）；
    - 兄弟姐妹与另两户学生逐行不变；两位家长的账号、联系方式与家庭行整行不变；
    - 回执只有表名 → 行数与范围编码。
  - 反例：未登记、没有 `student_id` 列与外键、在 jsonb 里存了学生 ID 或内容的表，按列名扫描漏掉，按值扫描能发现。
  - 与 79p 分工：“新表未登记即失败”在 79p 第一段，未重复。
- `services/insight/tests/test_forbidden_inference_sie03.py`（43 项，在 `make insight-test` 里，不需要新的 make 目标）：
  - 先断言匹配函数就是 SIE-00 词表：每类禁止示例都命中，允许示例都不命中。
  - 来源按 glob 发现，另有显式排除（写明理由），并断言发现的文件数不少于下限。五类来源都零命中，每类都有注入反例：
    - 洞察服务：配置的全部键值、SIE-03 模块的字符串字面量；
    - 迁移 0032 起的全部字符串字面量（枚举、COMMENT、异常消息，含中文）与回执表名；
    - BFF：`trust.v0.yaml` 的 enum / description / example，`src/trust/**` 与 trust-routes 的字面量，问题码；
    - 契约：`insight-service.v0.9.yaml` 的全部 enum 与 const；
    - 前端：`copy.ts`、`apps/web/src/trust/**`（.ts 与 .tsx）、`lib/trust-api.ts`。
  - 渲染路径静态扫描：编码集合从契约与 BFF 枚举派生。覆盖 JSX 文本、字符串与模板字符串、title / aria-* / alt / placeholder 属性，
    以及 `{x.kind}`、`${x.kind}`、`String()`、字符串方法、映射缺省回退；非渲染的 .ts 只许编码作为完整字面量出现。
  - 每个前端拿到的编码都有占位映射。

### 发现的覆盖缺口与待人确认（只登记，未改实现；涉及迁移、词表的都是必停项）
1. **staff 四表只靠函数路径保证“学生账号不会出现”**：
   - 清单把 `staff_session`、`content_signoff`、`map_change_signoff`、`tag_candidate_signoff` 以“只含教研账号”豁免；
   - 但只有 `staff_credential` 有库层守卫，另外四张只靠属主 authz 的函数核对；
   - 误写入的学生账号行在删除时会按豁免保留。建议加守卫触发器或改为 delete 策略（迁移）。
2. **`trial_claim` 的豁免理由与实际不符**：理由写“档案删除时学生键置空”，但删除执行不删档案，所以执行后 `student_id` 仍是被删学生
   （79q 单列断言“现状”）。改删除函数置空（迁移）还是改写理由，待人确认。
3. **验证码与限流表留存手机号摘要**：`auth_otp_challenge`（家长手机号 sha256）、`auth_rate_counter`（限流键摘要）不在清单里，删除不动。
   - 它们是家长 / 设备侧的数据，但摘要可被枚举还原成号码；
   - 是否登记进清单、留存多久，待人确认（迁移、合规）。
4. **词表漏拦“粗心 / 马虎 / 粗心大意”**：单独出现与“这次粗心了”“你很马虎”都不命中，只拦“是 / 总是 / 太…粗心”等特质句式。
   七科合同第 5 条要求“粗心”全平台禁用于对学生的评价，是否把这几个词整体列入词表，待人确认（词表必停）。
   目前前端文案另有 `trust.test.ts` 的专门正则兜底。
5. **库外存储不在覆盖范围**：BFF 导出包的进程内暂存（PR③ 偏离 4）、将来的对象存储、备份与日志，79q 覆盖不到。
   BFF 在删除确认时清暂存（PR③ M1），pilot 前换对象存储时要一起登记。
6. **79p 漏检**：把清单里 `insight.confidence_probe` 的谓词改坏（`AND false`）时 79p 全部通过，79q 的前置断言能发现。未改 79p。
7. **档案页**（KGE-04 第二步）仍无表：建表时须登记清单，并在 79q 的 `t.q_seed` 里给两名学生各写一页。
8. **新增个人层表的维护约定**：79q 要求清单里每张表两名学生都有行（staff 五表、删除请求、X2 的试用领取除外）。
   - 新迁移登记新表后，须在 `t.q_seed` 的“周报等后续新增”处补写入，否则 79q 失败（有意为之）；
   - 工作树里另一会话进行中的 0036（`insight.weekly_report`）合入前需要这一步。
9. **契约审查补记**：
   - M1：原扫描没有覆盖契约 `insight-service.v0.9.yaml`，已加入；
   - L3：原扫描只收编码样式字面量，漏了中文、COMMENT、异常消息和 yaml 的 description / example，已改为全部字符串。
10. **运行时渲染**：本测试只做静态扫描；真实 DOM 文本节点的核对（含 `data-*` 属性是否该留编码，如 `DisputesPage` 的 `data-dispute-status`）
    由 apps/web 的 web-test 负责，登记为后续，未改 apps/web。
11. **更正**：0035 头注释说“33 张表的 DELETE 只授 eraser”，实际清单是 32 张 delete 表（GRANT 也是 32 张），注释有误，本 PR 不改迁移。

### 验证
- `bash db/tests/run.sh`：在“HEAD + 79q”的干净副本上通过（35 个迁移，1895 项断言）。
  - 工作树里有另一会话未完成的 0036、79r 与改过的 79e，直接在工作树跑会在 79e 失败，与本 PR 无关。
- `test_forbidden_inference_sie03.py` 43 项通过；`make insight-test` 与 `make check-fast` 通过（contract-test 因本机缺 jsonschema 跳过，原本就这样）。
- 没有跑 `make check`。
