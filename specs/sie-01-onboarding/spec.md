# SIE-01：5 分钟建档（学生、家长分别填写）、考试记录与成绩录入、自述与家长观察分表

状态：**草稿，等人确认后才开始实现**。确认人、日期：＿＿＿

任务包：开发计划 v2.0 冲刺 1（W3–W4）L 流“SIE-01 建档：5 分钟表单（学生、家长分别填写，可跳过可返回）；exam_record 与成绩录入；自述与家长观察分表”，
验收“建档完成率与时长测试；两方填写互不可见”。SIE V1.1 第九章把 SIE-01 排在 W3–6，另含“行为事件埋点”，埋点按 v2.0 放冲刺 2 L，不在本 spec。

关联（现行依据）：
- **SIE V1.1**（`docs/plan/SIE-学生洞察引擎-V1.1.html`，画像、判断、呈现以它为准）：第三章“初中采集清单”（建档必填 / 建议 / 可选）、“冷启动三阶段”（本 spec 只做第一阶段“建立基本认识”）；
  “三层数据、四种来源，永不混算”；第九章数据模型增量 `exam_record`、`self_report`、`guardian_report`；接口 `insight.record(event)`。
- **字段字典** `docs/design/sie/field-dictionary.v0.9.yaml`（SIE-00，已确认）：本 spec 实现其中 `collect.timing = onboarding` 的字段与 `inner.g2.exam_*` 四个字段，
  存储位置、来源类型、可见范围一律以字典为准；字典里 `built_by: SIE-01` 的字段本 spec 交付后改为 `status: live`。
- **对照文档** `specs/plan-v2-alignment/spec.md` 决定 #3：已有个人层表留在 `platform.*`，`insight.*` 只放新增对象；#7：`platform.guardian_report`（0006，五个自由文本主题）保留，新增的结构化家长观察另建。
- **CLAUDE.md** 规则 2（单一真相源）、5（家长按档案授权）、6（原始证据、推断、学生自述、家长观察分表，标来源，两边都存不取平均）、8（合成数据）、11（判断只由洞察服务写，本 spec 不写判断）、12（不推断）、13。
- **ADR 13**（判断账本唯一写入方，草稿）；迁移 0010（`insight` schema、`starbuddy_insight` 角色）；0002（家庭、档案、同意、选科与授权函数）；0006（`platform.guardian_report`）。
- 统一壳与家庭登录在 4-H（冲刺 4）：本 spec 的页面先挂在 3-H 的开发身份桩下（学生、家长合成账号），4-H 换真实会话。

## 目标
学生与家长各自用约 5 分钟完成建档：每一页都可跳过、可返回，任何一项都不阻断建档。学生的自述进 `insight.self_report`，家长的观察进 `insight.guardian_report`（或既有主题进 `platform.guardian_report`），
客观资料（学校考试成绩）进 `insight.exam_record`；每行都带来源类型。**学生看不到家长填了什么，家长看不到学生自述的原文**；同一字段两边说法不同时两边都存。
建档过程记下开始、完成、跳过，能算完成率与时长。本 spec 不产生任何判断，不改掌握度。

## 设计要点

### 1. 表（两个迁移：platform 一个、insight 一个，ADR 02）
**`0021_platform__onboarding_facts`**（既有事实扩展，留在 platform）：
- `platform.student_subject_textbook(student_id, subject, textbook_version, updated_by, updated_at)`：各科教材版本（字典 `outer.g1.textbook_version` 注“按科分开存由 SIE-01 决定”）。
  `student_profile.textbook_version` 保留为默认值（不删，已有读取方）。学生本人、已绑定家长都能写（两边说法相同的客观事实，后写覆盖，记 `updated_by`）。
- 年级、地区、出生年月沿用 `student_profile`（家长建档时已填，`create_student`）；建档表单只展示与更正，更正经已有授权（已绑定家长）。

**`0022_insight__onboarding`**（新增对象）：
- `insight.self_report(id, student_id, field_id, value jsonb, respondent_account_id, created_at, superseded_at)`：学生自述。`field_id` 必须是字典里 `source_type` 含 `self`、
  存储为 `insight.self_report.value` 的字段；`value` 按字段的取值形状校验（枚举、数值范围、文本长度）。改答案不覆盖：旧行写 `superseded_at`，新增一行（保留变化轨迹）。
- `insight.guardian_report(id, student_id, guardian_account_id, field_id, value jsonb, created_at, superseded_at)`：家长观察（结构化字段）。五个既有自由文本主题仍写 `platform.guardian_report`（字典已定，同一主题只存一处）。
  多个监护人各存各的，不合并。
- `insight.exam_record`（按 SIE 第九章，去掉本期用不到的上传列）：`id, student_id, subject, exam_type（monthly / midterm / final / mock1 / mock2 / other）, exam_date, full_score, raw_score,
  scope_node_ids text[]（可空；KGE 节点 ID，知识地图发布前为空）, scope_note, difficulty_note, rank_band（top10 / top30 / middle / bottom30 / unknown）, source（manual）, entered_by_role（self / guardian）,
  entered_by_account_id, confirmed_by（null | self | guardian）, created_at, superseded_at`。学生与家长各录各的；同一场考试两边分数不同两边都存。
- `insight.onboarding_progress(student_id, respondent_role（self / guardian）, respondent_account_id, step, status（started / completed / skipped）, at)`：只追加，用于完成率与时长；不存答案。
- 所有表：强制 RLS；来源类型由表本身决定（`self_report` = 学生自述，`guardian_report` = 家长观察，`exam_record` 按 `entered_by_role`），另加 `source_type` 生成列便于统一查询（规则 6）。

### 2. 谁能写、谁能读（RLS 与函数）
- 写入只经函数（SECURITY DEFINER，属主 authz，`search_path` 固定），授予 `starbuddy_bff`（BFF 以当前账号调用）：
  - `insight.submit_self_report(student, field_id, value)`：只允许 `is_self_student(student)`；
  - `insight.submit_guardian_report(student, field_id, value)`：只允许 `is_linked_guardian(student)`；
  - `insight.submit_exam_record(student, …)`：学生本人或已绑定家长，按调用者身份写 `entered_by_role`；
  - `insight.record_onboarding_step(student, step, status)`：学生本人或已绑定家长；
  - `platform.set_subject_textbook(student, subject, version)`（在 0021）：学生本人或已绑定家长。
  函数核对字段在字典允许的清单里（清单由检查脚本从字典生成为迁移里的常量表，字典改了检查会报不一致）。
- **互不可见**（v2.0 验收）：
  - 学生只能读自己的 `self_report`、自己录的 `exam_record`；读不到 `guardian_report`（两张都不行）。
  - 家长只能读自己写的 `guardian_report`、全部已绑定孩子的 `exam_record`（成绩与范围字典可见范围含家长）；**读不到 `self_report` 原文**。
  - 字典里 `visibility` 含 guardian 的自述字段（最想改善的学科、近期目标），家长以后经判断账本与画像视图看到（规则 11），不直接读自述行（待人确认 #2）。
  - 学科服务（learning）与洞察服务（insight）按现有策略读全部（按学生隔离由调用方负责，与 SIE-00 一致）；BFF 只经上述读函数 / 策略读。
- 不推断（规则 12）：表单没有性格、健康、家庭经济、智力等任何栏目，只收字典里列出的字段；自由文本（近期目标、痛点补充说明）≤ 200 字，
  存原文，不做任何推断，不进判断。是否在写入时过禁用推断词表做标记，见待人确认 #5。

### 3. BFF 与页面（3-H 开发身份桩下，4-H 换真实会话）
- BFF 新增 `/bff/v0/students/{id}/onboarding`（GET 读自己可见的已填内容与进度；PUT 提交一页；POST 录考试成绩），只转发到上述函数，身份用现有开发桩；
  OpenAPI 写进 `services/bff/openapi/onboarding.v0.yaml`（BFF 内部接口，不是插件契约）。
- 前端 `apps/web`：`/onboarding/[studentId]`，按登录身份显示学生版或家长版；每页 1–3 个问题，“跳过”“上一步”“下一步”都有；
  学生版：最想改善的学科、近期目标、每周可用时间、学习方式偏好、自评痛点（建议）、各科教材版本确认、最近一次大考成绩（建议）；
  家长版：每周可用时间（家长看法）、学习设备、校外补习（自愿）、可配合程度、既有五个主题的原文（可选）、最近一次大考成绩（建议）。
  文案与选项是“问题与文案”，按 SIE 第九章由教研写，本 spec 只放占位文案并标“待教研定稿”（人设与话术不涉及，不是 Prompt）。
- 不缓存（no-store）、不用浏览器存储，填写进度只存服务端（`onboarding_progress`）。

### 4. 完成率与时长
- `insight.onboarding_stats(from, to)`（只授洞察服务与运营报表身份，待人确认 #6）：按学生、按填写方算“开始 → 完成”的比例与时长中位数，跳过次数按步骤汇总；只出聚合数。
- 端到端测试用合成学生、合成家长各走一遍：全部作答、全部跳过、中途返回改答案三条路径；断言每条路径都能完成，步骤数与页面数在上限内，进度表记录齐全。
  **“约 5 分钟”的真实时长要人做可用性测试**（合成数据跑不出真人的阅读与思考时间），见待人确认 #7。

## 范围
### 做
1. 迁移 0021（platform）、0022（insight）：表、RLS、函数、授权；DB 测试（写入授权、互不可见矩阵、来源类型、改答案保留轨迹、两边说法不同都存、字段白名单与取值校验）、变异、授权快照、攻击面清单。
2. 字段字典：`built_by: SIE-01` 的字段改 `status: live`，检查脚本加“迁移里的字段白名单与字典一致”。
3. BFF 建档接口与集成测试；前端建档页（学生版、家长版）与端到端（三条路径、互不可见、axe、no-store）。
4. 完成率与时长统计函数与测试。

### 不做
- 行为事件埋点、“能做对吗”探针（冲刺 2 L）；资料上传与解析（KGE 上传管道）；自适应诊断与首份画像（SIE-02a，冲刺 3）。
- 任何判断、画像视图、对学生或家长的结论性陈述（规则 11）；家长端看学生自述（经判断账本，后续）。
- 学生 PIN 登录、真实家庭登录（PLT-01 / 4-H）；`purpose_grant`（对照文档待决 #6）。
- 问题与文案定稿（教研）；5 分钟可用性测试（人）。

## 验收标准（可测试）
- [ ] 学生只能写自己的自述与考试记录，家长只能写已绑定孩子的家长观察与考试记录；其他家庭、未绑定家长、兄弟姐妹一律拒绝（DB 测试 + 渗透用例）。
- [ ] **互不可见**：学生读不到任何家长观察；家长读不到学生自述原文；两边的考试记录按字典可见范围（排名区间学生看不到）。
- [ ] 每行都能得出来源类型（学生自述 / 家长观察 / 客观记录）；同一字段两边不同时两边都在；改答案保留旧行（`superseded_at`）。
- [ ] 字段白名单与字典一致（检查脚本）；取值不合形状被拒；表单上没有字典之外的栏目。
- [ ] 端到端：学生版、家长版各三条路径（全答、全跳过、返回改答案）都能完成；每页都有跳过与返回；进度表记录齐全；统计函数给出完成率与时长。
- [ ] 严格 `make check` 通过；安全审查、契约审查意见处理完。

## 涉及的必停项（见 CLAUDE.md）
- db/migrations：**是**（0021、0022）；RLS 与权限：**是**；画像可见范围：**是**（互不可见的实现方式，待人确认 #2）；
- 计量、契约（packages/contracts）、人设、第三方：否。PR 首行写 `[NEEDS-HUMAN]`。

## 依赖与输入
- 迁移 0002、0006、0010（草稿，由人执行）；字段字典 v0.9；3-H 开发身份桩与合成家庭种子（`db/seeds/dev/star-map-synthetic.sql`）。

## 待人确认
1. **表放在哪里**：新增的自述、家长观察、考试记录、建档进度放 `insight.*`（默认，按字段字典与对照文档决定 #3）；各科教材版本属于既有事实的扩展，放 `platform.*`（默认）。
2. **“互不可见”与字典可见范围怎么同时成立**：默认原始行两边互不可见；字典里对家长可见的自述字段（近期目标、最想改善的学科），家长以后只经判断账本与画像视图看到，不读原文。
   另一个选择：这两个字段的原文直接对家长可见（与 v2.0“两方填写互不可见”冲突，需要改验收口径）。
3. **写入由谁执行**：洞察服务还没有代码（ADR 13 待人确认 #1）。默认 BFF 以当前账号调用库函数写事实层（不经洞察服务）；判断仍只由洞察服务写。
   另一个选择：先起洞察服务的 `insight.record` 接口，BFF 调它。
4. **各科教材版本的取值**：沿用 `platform.curriculum_version.textbook_version` 的编码（例如 `suke-2024`、`bubian-2024`）；学生不知道时可选“不清楚”（存 null，由家长或教研后补）。
5. **自由文本**：近期目标与痛点补充说明允许 ≤ 200 字自由文本（默认），原文存档不做任何推断；是否在写入时过禁用推断词表做标记（只标记，不拦截、不改写）。
6. **统计由谁看**：建档完成率与时长只出聚合数，默认授给洞察服务与一个只读的运营报表身份（新角色，改权限）；另一个选择是只给洞察服务，报表由人用 SQL 查。
7. **5 分钟时长的验收方式**：默认工程侧只验证路径可走通与步骤上限；真实时长由产品组织 5–8 户合成身份的内部可用性测试记录（人）。
8. **考试成绩录入的分制**：七八年级 100 分、九年级 120 分（字典注）；默认满分由录入者填写，不按年级强制；排名区间是五档枚举，不收具体名次。
