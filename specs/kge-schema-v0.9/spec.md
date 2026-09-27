# KGE schema v0.9：知识地图骨架与原子层的迁移草案、ID 与六段标识规范

状态：**已确认（按默认建议）**，实现中。确认：用户，2026-09-27（“按默认建议，开始实现”，待人确认 #1–#7 全部按默认）。实现记录见 lessons.md
任务包：KGE-00 的第二部分（KGE V1.1 第九章 S0：“kge schema v1 迁移草案；ID 与六段标识规范”）；开发计划 v2.0 冲刺 0 · W2“Schema v0.9：platform.*、kge.*、insight.* 三个 schema 的 ERD 与迁移草稿”。

关联：
- **KGE V1.1**：
  - 第三章：层级（学科 → 领域 → 主题/单元 → 知识节点 → 细目）、七种节点类型、三种学科型、三根轴（认知层次、掌握状态、能力视图）、六条粒度标准、范围属性；
  - 第四章：原子层与编译层、页面体系、两种重建、分级发布闸门；
  - 第五章：地图迭代信号与变更流程，版本号“主.次.修订”；
  - 第七章：统一词汇表、ID 命名（`MATH.FUNC.QUAD.VERTEX_FORM`）；
  - 第八章：kge schema 结构草案（本 spec 的起点）。
- **完整方案 v1.1**：4.2 星图统一定义（星 = knowledge_node，能力坐标为能力视图 skill_node）、5.5 数据迁移与坐标映射、第七章关键对象。
- **开发计划 v2.0**：冲刺 1-K“KGE-01 骨架：版本、节点、边、投影、迁移表；发布前校验（环、传递边、孤点、包含 / 不包含必填）”。本 spec 只交付其 schema 部分，导入与服务是 KGE-01。
- **仓库现状与已确认的默认做法**：
  - `specs/plan-v2-alignment/spec.md`：
    - #3：已有个人层表留在 `platform.*`，`kge.*` 只放新增对象；
    - #4：3-H 星图等 ADR“星图统一定义”后改指 knowledge_node；
    - #5：evidence_event 的 `evidence_kind`、`map_version` 与本草案一起评审；
  - 内容登记 0003：`platform.curriculum_version`（六段标识，即“内容版本”）、`platform.skill_node`（阿鸦 959 个图谱节点暂登记在这里）、`platform.item`（题目登记与准入）。
- **KGE-00 盘点报告**（`specs/kge-00-asset-inventory/report/`）：
  - 数学：959 个节点；1041 条可解析前置边，另有 156 条自然语言前置；74 条传递边；1 个环；题目 0/721 带六段标识；
  - 道法：36 个大概念，页码 0/36 绑定版次；
  - 语文：没有可打标的能力点。
- **SIE-00**：判断账本（`insight.claim`）经 `basis_kind / basis_id` 引用 platform 推断表。KGE-01 以后节点类判断的 `target_id` 改指 knowledge_node。
- **ADR**：02 共库分 schema（需补 kge 一节）；拟编号 11“星图统一定义”、12“编译层试点期用 Git”（均待起草）。
- **CLAUDE.md**：规则 1、2、13；必停项“db/migrations、RLS 与权限策略”。

## 目标
让知识地图有一个能被数据库校验的家：节点、边、投影、迁移表、原子（题目标签、采分点、时政）、地图变更与审核记录都有表和约束，发布前的结构校验（环、传递边、孤点、包含 / 不包含必填）能在库里跑。这样 KGE-01 导入阿鸦图谱、审核台 v0 发布第一个地图版本时，规则已经在库层。

## 设计要点

### 1. 与 platform 内容登记的边界（不做第二份）
KGE 第八章的草案里有 `curriculum_version`、`item`、`skill_node`，仓库的 `platform.*` 里已有同名或同义的表。按规则 2（任何新 schema 不得存放同一事实的副本）处理如下：

| KGE 草案对象 | 本仓库的处理 |
|---|---|
| `curriculum_version`（地图版本：学科、课标年份、主.次.修订） | 新表 `kge.map_version`，改名以免和 `platform.curriculum_version`（六段标识的**内容版本**）混淆。一个内容版本引用一个地图版本（新增列，见第 6 节） |
| `item`（题干、答案、解析、标签、六段标识） | 题目的**身份与准入**仍只在 `platform.item`（证据外键依赖它）。新表 `kge.item_tag` 与它一对一，只存打标与元数据：主考点、节点、认知层次、先验难度、题目族、来源与版权状态、质量分级。**题干、答案、解析 v0.9 不进库**，仍在各科内容包（0003 的既定口径、版权未定，见待人确认 #3）。六段标识由 `platform.curriculum_version` 继承，不在题目上重复存 |
| `skill_node` / `skill_link` | 能力视图的登记仍是 `platform.skill_node`。新表 `kge.skill_link(node_id → subject + local_id, weight)` 把知识节点挂到能力视图 |
| `evidence_event`、`attempt`、`mastery_estimate`（个人层） | 留在 `platform.*`（对照文档 #3）。`evidence_kind`、`map_version`、`cognitive_level` 三列按开发计划 v2.0 在冲刺 1-C 加（涉及学习服务写入与契约 `EvidenceDraft`），本 spec 只在 ERD 里画出，不改 platform 迁移 |
| `wiki_page`、`page_source`、`compile_job`、`lint_issue`（编译层） | 试点期页面放单独的 Git 仓库（ADR 12 待起草），v0.9 不建。学情档案页属于个人层，随 KGE-04 建 |

### 2. kge.* 表（迁移 `0011_kge__knowledge_map`，必停项）
字段以 KGE 第八章为准。以下只列本仓库的取舍与约束：

- **版本与课标**：
  - `kge.map_version(id, subject, standard_year, version, status, published_at)`：`version` 为“主.次.修订”；状态 draft → published → retired；同一学科同一时刻只能有一个 published。
  - `kge.curriculum_item(id, subject, domain, text, verb_level, source_ref)`：课标条目原子。
- **骨架**：
  - `kge.knowledge_node`：`kind` 七选一（kp、ability_kp、viewpoint、material、method、pitfall、exam_point）；`level` 四选一（domain、topic、node、detail，即领域 / 主题 / 知识节点 / 细目）；`scope` 五选一；`cognitive_levels` 取课标四级（知道、理解、运用、综合）的子集；`includes` / `excludes` / `examples`；`error_type`；`map_version_id`；状态。
    - 约束：pitfall 必须写 `error_type`，且取值是 `platform.cross_subject_error` 的七大类之一；只有 kp / ability_kp / viewpoint 能带 `cognitive_levels`，即参与掌握度计算（KGE 第三章表）。
  - `kge.knowledge_edge(from_id, to_id, type, strength, evidence_ref, map_version_id, status)`：`type` 七选一，`strength` 分强 / 弱。
    - 约束：不能自环；前置边两端必须同学科、同一地图版本。
  - `kge.textbook_projection`：教材版次、年份、印次、册、章、起止页、`quote_text`、`quote_hash`、核对人、核对时间、映射状态。
    - 约束：`quote_hash` 由库计算（`sha256(quote_text)`）；状态为 verified 时核对人与核对时间必填。这是 KGE V1.1 修订 3“原句在投影表锁定与核对”。
  - `kge.region_projection(node_id, region, exam_year, weight, typical_slots, style_tags, source_ref)`。
  - `kge.skill_link`（见第 1 节）。
  - `kge.id_migration(old_id, new_id, change_id, rule)`：旧编码 → 新 ID。KGE-01 导入时，阿鸦的 959 个节点、道法的 m1 / xr1 这类短编码都进这张表。
- **原子**：
  - `kge.item_tag`、`kge.item_family`（见第 1 节）。
  - `kge.scoring_point(id, subject + item_id, rubric_version, score, node_ids, subject_dimension, keyword_family, required_logic, material_linkage, partial_credit_rule, common_errors)`：`rubric_version` 必填，判分结果才能绑定版本（KGE 第七章“重要”项）。
  - `kge.current_affair(id, node_ids, source, valid_from, valid_until, review_status)`：生效与失效日期都必填，失效晚于生效。
  - `kge.paper`（同卷识别用的指纹）：推迟到 KGE-05 上传通道，v0.9 不建。
- **成长治理**：
  - `kge.map_change(id, change_type, payload, evidence_pack, confidence, status, reviewers, target_version)`：结构性变更（节点拆分、合并、前置边增删）需两人确认。库层约束为：状态 approved 时 `reviewers` 至少两人。
  - `kge.content_review(id, object_type, object_id, risk_level, diff, decision, reviewer, decided_at)`：高风险变更必须有审核记录。

所有表都开 ENABLE + FORCE RLS（攻击面测试要求）。公共层不含任何学生数据，也没有 `student_id` 列（规则 13）。

### 3. 发布前校验：库内函数
`kge.validate_map_version(p_version uuid) RETURNS TABLE(check_name, severity, object_id, detail)`，只读，由发布流程调用。检查项：
1. 环：前置边上的强连通分量；
2. 传递边：可由其他路径推出的直连前置；
3. 孤点：没有边、没有题目标签、也没有投影的 node 层节点；
4. 包含 / 不包含 / 典型题例缺失：node 层的 kp、ability_kp、viewpoint；
5. 悬空引用：边、投影、题目标签、采分点指向其他地图版本或不存在的节点；
6. 主考点少于 3 道题的节点（粒度信号，只报警告，不阻断）；
7. 时政已过期仍标为 published。

阻断级问题不为零时，`kge.publish_map_version()` 拒绝发布。发布函数是 SECURITY DEFINER，属主 `starbuddy_authz`，只有 KGE 写入角色能调用。图算法与 KGE-00 工具使用同一套定义，正反例对齐。

### 4. 角色与权限（必停项）
- 新增角色 `starbuddy_kge`：知识服务与审核台后端，公共知识层的唯一写入方。写进 `db/bootstrap/roles.sql`；与其他应用角色互斥，与 SIE-00 的做法相同。
- 读取：
  - `starbuddy_learning`（学科服务、编排）可以读 kge 全部表；
  - `starbuddy_bff` 只读已发布地图版本里的节点、边、投影（星图渲染），不读草稿、审核记录与变更单；
  - `starbuddy_insight` 只读节点与边（判断的 `target_id` 核对）。
- 不向任何角色开放公共层的 DELETE：撤下走状态（retired），与内容登记的做法一致。

### 5. ID 与六段标识规范（文档交付）
`docs/design/kge/id-and-segments.md`：
- **节点 ID**：`学科.领域.主题.节点`，全大写，段内用字母、数字、下划线，例如 `MATH.FUNC.QUAD.VERTEX_FORM`、`CHN.READ.NARR.CHARACTER`、`MORAL.LAW.CONST.SUPREME`。库里用 CHECK 约束正则。学科前缀与 `platform.subject` 的对应写成表：MATH ↔ aya、CHN ↔ wenli、MORAL ↔ zhixing、ENG ↔ echo（搁置）；HIST、PHY、CHEM 预留。
- **学科码区分**：“定位”在语文与道法的错因学科码分别是 `CHN.LOCATE_TEXT`、`MORAL.LOCATE_BOOK`（KGE 第七章）。
- **六段标识**：
  - region、exam_year、grade、textbook_version、subject、content_version 只存在 `platform.curriculum_version`；
  - 题目、采分点、页面经内容版本继承；
  - `content_version` 与地图版本 `map_version` 是两回事：前者是内容包发布的版本，后者是骨架版本，一个内容版本引用一个地图版本。
- **版本号**：地图版本“主.次.修订”——修订按周、次版本按月、主版本按学期，并尽量避开学期中。
- **旧编码迁移规则**：
  - 阿鸦 `K-FUN-0901` 类编码；
  - 157 条自然语言前置：不自动映射，列入 KGE-01 的人工映射清单；
  - 道法 `m1` / `xr1` 类短编码：进 `id_migration`；
  - 无法可靠迁移的掌握度标为“待复测”（KGE 第五章）。

### 6. 对 platform 的最小改动
只加一列：`platform.curriculum_version.map_version_id uuid REFERENCES kge.map_version`，可空；现有行为空，发布新内容版本时由 4-G 发布流程填写。这是 platform 迁移的改动，也放在 0011 里一起评审。其余 platform 表不动。

### 7. 测试
- 迁移 up → down → up；
- 约束：
  - pitfall 必须有错因类型；
  - 认知层次只给计掌握度的类型；
  - 节点 ID 正则；
  - 边不能自环，前置边不能跨学科或跨地图版本；
  - 投影 verified 必须写核对人，`quote_hash` 由库计算；
  - 采分点必须有版本；
  - 时政的有效期；
  - 变更单 approved 至少两人；
- 发布校验函数：用合成夹具构造环、传递边、孤点、缺包含、悬空引用、少于 3 题、过期时政，每项正例与反例各一；有阻断问题时拒绝发布；
- 权限：
  - 只有 `starbuddy_kge` 能写；
  - BFF 只看得到已发布版本；
  - insight 与 learning 的读取范围；
  - 没有任何角色能 DELETE；
- 攻击面：`app_roles` 加 `starbuddy_kge`；可见性矩阵豁免 kge 表（公共层没有学生数据）；快照更新；SECURITY DEFINER 函数表更新；
- 变异：每条关键约束、发布阻断、授权各一个变异。

## 范围
### 做
- 迁移 `0011_kge__knowledge_map`：kge schema、表、约束、RLS、发布校验与发布函数、`starbuddy_kge` 角色断言；`platform.curriculum_version.map_version_id`。
- `db/bootstrap/roles.sql` 增加 `starbuddy_kge`。
- 测试 `db/tests/76_kge_map.sql`（在 80 之前运行）、快照、变异、攻击面文档。
- `docs/design/kge/id-and-segments.md`；ERD 增加 kge 一节；ADR 02 增补 kge 一节（草稿）。
- `specs/kge-schema-v0.9/lessons.md`。

### 不做
- 导入阿鸦图谱或任何真实内容：属于 KGE-01。测试只用合成节点。
- 知识服务代码、OpenAPI（`node.*` / `page.*` / `item.*` / `rubric.*` / `evidence.write` / `mastery.*`，冲刺 0 下一项）。
- 编译层表、学情档案页、同卷指纹表。
- 个人层表的任何改动，包括 evidence_event 的三列（冲刺 1-C）。
- 改 3-H 星图：等 ADR“星图统一定义”。
- 不在任何共享环境执行迁移。

## 验收标准（可测试）
- [ ] 迁移在本机临时库 up → down → up 通过，只用标准 Postgres 16 能力。
- [ ] 第 7 节列出的每条约束各有至少一个被拒绝的反例与一个通过的正例。
- [ ] `kge.validate_map_version` 对合成夹具的七类检查各给出预期结果；有阻断问题时 `kge.publish_map_version` 拒绝发布，修好后能发布；同一学科同一时刻只有一个已发布版本。
- [ ] 只有 `starbuddy_kge` 能写 kge 表，任何角色都不能 DELETE；BFF 只读得到已发布版本的节点、边、投影；审核记录与变更单对 BFF 不可见。
- [ ] kge 表里没有 `student_id` 或任何个人信息列（静态检查：攻击面测试加一条“公共层不含学生键”）。
- [ ] `platform.curriculum_version.map_version_id` 可空，现有测试全部通过，星图与计量不受影响。
- [ ] 攻击面测试、权限快照、SECURITY DEFINER 函数表、变异测试全部更新并通过；严格 `make check` 通过。
- [ ] `docs/design/kge/id-and-segments.md` 写明节点 ID 正则、学科前缀对应、六段标识的归属、版本号规则与旧编码迁移规则；节点 ID 的正则与迁移里的 CHECK 一致，由脚本比对。

## 涉及的必停项（见 CLAUDE.md）
- db/migrations（0011）、RLS 与权限、新数据库角色：是。PR 首行写 `[NEEDS-HUMAN]`。
- 契约、计量、人设、判分与掌握逻辑、判断模板：否。发布校验只管结构，不涉及掌握计算。
- 第三方与出境：否。

## 依赖与输入
- KGE V1.1 第三、四、五、七、八章；KGE-00 盘点报告。
- 对照文档已确认的默认做法 #3、#4、#5。
- SIE-00（迁移 0010）在前，本迁移编号 0011。

## 待人确认
1. **新角色 `starbuddy_kge`**：默认新增，作为公共知识层唯一写入方；审核台后端（4-G）也用它。另一种做法是由学习服务写，但那样学习服务同时写个人层与公共层，与规则 13 的分层精神不符。
2. **地图版本改名 `kge.map_version`**：避免与 `platform.curriculum_version`（内容版本）同名。是否同意？
3. **题干、答案、解析进不进库**：默认 v0.9 不进（0003 的既定口径，版权与来源待核）。KGE 第八章草案把它们放在 `item` 里，是否在 KGE-02 导入题库时改为进库，需要人定。
4. **BFF 读公共层的范围**：默认只读已发布版本的节点、边、投影，供星图使用。是否还要开放采分点（给学生看“答题要点”）？默认不开放，由学科服务经 teach 给出。
5. **`platform.curriculum_version` 加列**：只加一列可空的地图版本引用。属于 platform 迁移，与 0011 一起评审。
6. **阿鸦 959 个节点现在登记在 `platform.skill_node`**：KGE-01 导入 kge 后，这 959 行是保留为能力视图（多数其实是知识节点）还是退役？依赖 ADR“星图统一定义”，本 spec 不动。
7. **细目（detail）层**：KGE 第三章说细目只作证据标签、通常由易错点承担。默认 `level = detail` 只允许 kind 为 pitfall 或 exam_point。是否同意？
