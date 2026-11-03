# KGE schema v0.9 实现记录

2026-09-27。用户确认“按默认建议，开始实现”，spec 待人确认 #1–#7 全部按默认执行：
1. 新增 `starbuddy_kge` 角色；
2. 地图版本改名为 `kge.map_version`；
3. 题干、答案、解析不进库；
4. BFF 只读已发布版本的节点、边、投影，不读采分点；
5. `platform.curriculum_version` 只加一列可空引用；
6. 阿鸦的 959 个 `platform.skill_node` 不动；
7. 细目只能是易错点或考点。

## 交付
- **迁移 `0011_kge__knowledge_map`**：只改 kge schema。
  - 角色断言：`starbuddy_kge` 存在、非超级用户、非 BYPASSRLS，与其他五个应用角色互斥；另断言库编码是 UTF8。
  - 节点 ID 域 `kge.node_id`，以及辅助函数 `subject_prefix`、`id_depth`、`id_parent`、`distinct_count`、`quote_sha256`、`has_personal_key`。
  - 14 张表：
    - 版本与课标：`map_version`、`curriculum_item`；
    - 骨架：`knowledge_node`、`knowledge_edge`、`textbook_projection`、`region_projection`、`skill_link`、`id_migration`；
    - 原子：`item_family`、`item_tag`、`scoring_point`、`current_affair`；
    - 成长治理：`map_change`、`content_review`。
  - 6 个保护触发器函数（全部 SECURITY INVOKER）。
  - 发布前校验 `kge.validate_map_version`（SECURITY INVOKER）与发布函数 `kge.publish_map_version`（SECURITY DEFINER，属主 authz）。
  - 授权与 RLS。
- **迁移 `0012_platform__curriculum_map_version`**：`platform.curriculum_version.map_version_id`，按（学科, 地图版本）复合外键引用，保证同学科。
- **`db/bootstrap/roles.sql`**：新增 `starbuddy_kge`。
- **测试 `db/tests/76_kge_map.sql`**：
  - 约束各有反例与正例；
  - 七项校验：在合成的问题版本上逐项命中，并且只命中构造的那一处；
  - 修好后能发布，原已发布版本自动撤下；
  - 骨架冻结、状态机、唯一已发布；
  - 各角色的读写范围；
  - 0012 的同学科引用。
- **攻击面测试 80**：
  - `app_roles` 加入 kge；
  - 新增两条断言：公共层没有个人层的键；外键只指向 kge 自身与 platform 的内容登记；
  - BFF 可读的 5 张 kge 表登记豁免理由。
- **变异**：新增 76 个，共 264 个，全部被发现。其中 4 个攻击面变异只由 80 发现。
- **其他更新**：权限与策略快照；渗透测试范围的 SECURITY DEFINER 函数表增加 1 个，共 32 个；`check_attack_surface.py` 的函数表正则加入 kge。
- **文档**：
  - `docs/design/kge/id-and-segments.md`：节点 ID、学科前缀、六段标识、版本号、旧编码迁移；
  - `docs/design/kge/check_node_id.py`：与迁移逐字比对，14 个例子，5 个自检反例。已接入 `make kge-id-check`，并加入 `check-fast` 与 `check`；
  - ERD 增加 kge 一节，ADR 02 增补 kge 一节（草稿），ADR 索引、CLAUDE.md 仓库结构一行同步更新。

## 与 spec 的偏差（按实现取舍，需要时由人改回）
1. **platform 加列拆成 0012**：spec 写“放在 0011 里一起评审”，但 ADR 02 第 2 条规定每个迁移只改一个 schema。两个迁移仍在同一个 PR 里评审。
2. **`parent_id` 代替 `domain_id / topic_id`**：层级由 ID 的段数决定（领域 2 段、主题 3 段、节点 4 段、细目 5 段），上一级就是去掉最后一段的 ID，由约束保证。领域与主题可以从 ID 前缀直接得到，不另存两列。代价是改领域或主题的 ID 会连带改下级 ID，这类改动走迁移表。
3. **骨架冻结，原子不冻结**：spec 没写发布后能不能改。实现的规则如下：
   - 地图版本发布或撤下后，节点、边、投影、能力视图挂接、旧编码迁移都不能新增或修改，改地图走新版本；
   - 题目标签、题目族、采分点、时政不随地图版本冻结，因为它们持续增长，Rubric 有自己的版本；
   - 非草稿版本只能改状态；撤下是终态。
4. **地图版本新建只能是草稿，发布只能经发布函数**：保护触发器用 `current_user = 'starbuddy_authz'` 判断调用者是不是发布函数（发布函数属主是 authz）。authz 不能被任何角色继承，这一点由 0001 断言，所以这个判断不能被伪造。超级用户直接改也会被拒绝。
5. **悬空引用的含义**：边与投影的单个节点引用有复合外键，节点数组由触发器核对，所以“指向不存在的节点”在库里写不进去。第 5 项检查实际查的是“有效的边、投影、挂接、标签、采分点、时政指向已停用的节点”，同时保留对不存在节点的检查兜底。
6. **孤点的定义**：只查知识节点层（level = node）的有效节点：没有任何有效边（不分类型，跨版本的边也算），没有题目标签（主考点或节点数组），也没有教材投影。上一级关系不算边。与 KGE-00 工具的定义一致（工具里“被题目或 Rubric 引用”对应题目标签）。
7. **严重程度**（审查后调整）：
   - 阻断：环、传递边、孤点、缺边界（含认知层次与易错点的识别特征）、悬空引用、边类型与端点不符、题目标签层次不符；
   - 警告：主考点少于 3 题、过期但仍发布的时政、指向未发布版本的跨版本边。
   过期时政原先设为阻断，契约审查指出 KGE 第七章写的是“过期由体检自动下架，不进骨架”，按默认改为警告。
8. **没有建 `node_curriculum`**：课标条目表已建。节点与课标条目的对齐关系，以及 `aligned_to` 边是否指向课标条目，留给 KGE-01；0011 的 `aligned_to` 只连节点。
9. **跨学科边**：只有前置边与从属边要求两端同版本，关联、易混、对齐等边可以跨学科、跨版本（`to_map_version_id`）。BFF 只看两端版本都已发布的边。
10. **原句哈希**：`convert_to` 在 Postgres 里只标为 STABLE，不能直接用于生成列。改用标为 IMMUTABLE 的包装函数 `kge.quote_sha256`，前提是库编码固定为 UTF8，迁移开头已断言。

## 审查与处理（2026-09-27）
用户指示“按默认建议处理审查意见”。安全审查与契约审查的意见，除下面“待人确认”里的以外都已修：

- **发布与骨架写入的竞态**（安全 B1，阻断）：
  - `guard_frozen` 对版本行加 `FOR SHARE`，与发布函数的 `FOR UPDATE` 互斥：
    - 发布会等进行中的骨架写入提交后再校验；
    - 发布进行中的写入会等发布提交后，按已发布状态被拒绝。
  - 看不到版本行时拒绝，不再放行。
  - `db/tests/run.sh` 增加两个双会话场景，另加“去掉加锁”“看不到版本放行”两个变异。
- **两次发布并发时后发布的静默撤下先发布的**（安全建议 2）：发布函数开头加事务级咨询锁，同一学科的发布串行执行。
- **0011 的角色断言**（安全建议 1）：
  - 补上 kge 不能 LOGIN、CREATEROLE、CREATEDB、REPLICATION；
  - 复核 authz 没有任何成员。发布的信任判断依赖这一点，所以不能只靠 0001 执行那一刻的断言；
  - `run.sh` 增加负向测试：kge 带 BYPASSRLS、kge 可登录、登录角色经中间角色同时继承 kge 与 learning、authz 有成员，这四种情况下 0011 都拒绝执行；角色正常时能执行。
- **采分点不足以绑定版本**（契约必须修 1，安全建议 3）：
  - 加 `point_id`，与 `grade_dimension.dimension_id` 同一正则；
  - 同一（学科, 题目, rubric_version）下 `point_id` 唯一；
  - 一个 rubric_version 只属于一个地图版本（触发器）；
  - 收回知识服务对采分点的 UPDATE：写入后不能修改，改内容发新 rubric_version。
- **发布后原子仍可挂停用节点**（安全建议 3）：非草稿版本上新增或修改原子时，节点数组里的节点必须有效。
- **文档把加列写成 0011**（契约必须修 2）：已改为 0012。spec 同步了 0012、悬空引用的口径与“156 条”，并在状态行指向本文件。
- **公共层夹带个人数据**（契约建议 7，安全建议 5、H1 的默认部分）：
  - 公共层的每个 jsonb 列都加 CHECK `kge.has_personal_key`：任何层级的键名含 student、attempt、evidence_event、account、guardian、household、learner 都拒绝；
  - 证据包、常见错误的注释收紧为“只放聚合统计与公共层对象 ID，不放作答原文或任何个人层指针”；
  - 攻击面测试新增三条：
    - 列名正则扩大到 user、profile、child、session、email、openid、device 等；
    - kge 的 uuid 列只能是本表主键或指向 kge 自身的外键；
    - 金丝雀：夹具里每个学生、账号的 ID 都不能出现在 kge 的文本与 jsonb 列里。
- **发布校验补项**（契约建议 4、5、6）：
  - 素材不进前置依赖图；`has_pitfall` 与 `uses_method` 的终点类型要对；
  - 知识节点必须带认知层次；
  - 易错点必须写识别特征（用 `includes` 承载，“在作答中表现为什么样子”）；
  - 题目标签的层次必须在主考点的层次集合里；
  - 跨版本的边指向未发布版本时报警告。
- **`item_tag.review_status` 像第二份准入**（契约建议 3）：改名为 `tag_review_status`，注释写明只是打标审核。
- **题目族可以跨版本引用**（契约建议 8）：改为（版本, 题目族）复合外键。
- **测试没守住**（安全建议 4）：
  - BFF 读投影的断言去掉按版本过滤；
  - 夹具加一条从已发布版本指向草稿版本的跨版本边，断言 BFF 看不到。
- **小项**（安全建议 7）：
  - 孤点检查不再把被拒绝的题目标签算进去；
  - BFF 只看已核对、且所在节点有效的教材投影（页码错对开卷定位是致命错误），地区投影也只看有效节点上的。
- **ERD 与契约登记**（契约建议 2、9）：
  - ERD 画出冲刺 1-C 计划给 `evidence_event` 加的三列；
  - `specs/contract-conformance-sprint1/v1.0-review.md` 增加第五节 kge-#1–#5，契约本身不改。

没有照做的：
- 安全审查建议把“变异后重新生成快照、只看专项断言”的模式固化进 `mutations.sh`。这次在本地按该模式复核过新增的授权与策略类变异。固化到 `make check` 会让变异测试的时间翻倍，留待 3-B 的变异框架统一调整时再做。

## 待人确认
1. **发布的人审点**：发布函数只查结构，不查“是否有人批准”。审核台 v0（4-G）上线前，是否要求发布前必须有一条已批准的 `content_review`（对象为该地图版本）？默认不在库层强制，由审核台流程保证。
2. **审核人身份**：`map_change.reviewers`、`content_review.reviewer`、投影的 `verified_by` 都是知识服务自报的工号，库层只能保证“两个不同的字符串”。是否要改为引用 staff 账号，并由授权函数取当前账号？现在的授权函数面向家庭账号，这个改动要连同审核台一起设计。
3. **证据包与自由文本的内容边界**（安全 H1）：库层已拦住个人层的键与学生、账号 ID（见上节）。还需要人定三件事：
   - 证据包是否限定顶层键白名单，并在库层要求 `n` 不低于最小人数门槛；
   - 是否禁止证据包的值出现 uuid 格式；
   - 常见错误、典型题例、`knowledge_node.examples` 里“去标识的易错实例”能否引用真实作答改写。
   审查建议不要等到 KGE-05，在 KGE-01 前定。
4. **撤下与版本引用**（安全 H2）：
   - 知识服务可以不经函数，直接把唯一的已发布版本改为撤下，这时该学科在前端没有星图；
   - 0012 允许内容版本引用草稿或已撤下的地图版本；
   - 跨学科的边在对端换版后会从前端消失，源版本已冻结改不了（发布校验已报警告）。
   需要人决定：撤下是否也只能经函数；已发布的内容版本是否只能引用已发布的地图版本。
5. **`item_tag` 的地图版本与题目内容版本的一致性**：题目的内容版本经 0012 引用地图版本后，标签的地图版本应当与之一致。0012 的列可空、现有行为空，所以 v0.9 不强制，留给 KGE-02 导入题库时加。
6. **题目主考点两处存放**（契约需人确认 1，规则 2；与 spec 待人确认 #6 同源）：
   - 阿鸦的 `platform.item.skill_local_id` 实际存的是题目主节点，KGE-01 导入后 `kge.item_tag.main_node_id` 会用新 ID 存同一件事；
   - `platform.skill_node.parent_local_id` 与 `kge.knowledge_node.parent_id` 也会各存一份层级；
   - `mastery_estimate` 目前按 `skill_local_id` 计算，也受这个决定影响。
   必须在 KGE-01 导入前，由 ADR 11“星图统一定义”决定 `platform.item.skill_local_id` 是改指能力视图还是退役。
7. **投影随骨架冻结**（契约需人确认 2）：与 KGE 第一章“换教材、换地区只改投影”冲突。发布后发现页码错只能发新地图版本，原子按地图版本挂接，每个修订版都要整体复制。需要人决定：
   - 投影是否单独版本化，不随骨架冻结；
   - 原子是否不绑地图版本，改为“节点 ID + 版本区间”。
8. **`aligned_to` 与课标条目**（契约需人确认 3）：`curriculum_item` 现在不与任何对象相连，KGE 第八章的“课标条目承接率 100%”算不出来。需确认 `aligned_to` 在 0011 里只连节点，并在 KGE-01 前补上节点与课标条目的挂接（偏差 8）。
9. **错因大类数目**（契约需人确认 5）：KGE 第七章说补为“第六个大类”，`platform.cross_subject_error` 是七类（多 `thinking_method`）。这是既有差异，需要在 KGE 文档或 ADR 里对齐口径。
10. **语文的“层次递进边”**（契约需人确认 6）：KGE 第三章列为语文的核心边，第八章的七种边类型里没有。需教研确认用 `prerequisite` + `weak` 表达，还是新增一种类型（必停项）。
11. **KGE 第八章的缺项**：`item.canonical_id`、`irt_params`（第五章“规范题”）与 `skill_node.curriculum_refs`（阿鸦十维映射课标）没有建。按规则 2 应补在 `platform.item` / `platform.skill_node`，不补在 kge，随 KGE-02 与 KGE-01 处理。
12. **已有问题（不在本任务）**：`adapters/aya/scripts/register-content.ts` 会生成直接写 `platform.*` 的 SQL，以 learning 身份执行，已在原 spec 登记为待人确认。
