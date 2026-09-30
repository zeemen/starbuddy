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
