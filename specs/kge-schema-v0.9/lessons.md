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
  - 节点 ID 域 `kge.node_id`，以及辅助函数 `subject_prefix`、`id_depth`、`id_parent`、`distinct_count`、`quote_sha256`。
  - 14 张表：
    - 版本与课标：`map_version`、`curriculum_item`；
    - 骨架：`knowledge_node`、`knowledge_edge`、`textbook_projection`、`region_projection`、`skill_link`、`id_migration`；
    - 原子：`item_family`、`item_tag`、`scoring_point`、`current_affair`；
    - 成长治理：`map_change`、`content_review`。
  - 5 个保护触发器函数（全部 SECURITY INVOKER）。
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
- **变异**：新增 57 个，共 245 个，全部被发现。其中 2 个攻击面变异只由 80 发现。
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
7. **严重程度**：
   - 阻断：环、传递边、孤点、缺边界、悬空引用、过期但仍发布的时政；
   - 警告：主考点少于 3 题。
   过期时政设为阻断，是因为道法时政过期后仍展示是内容风险；它不阻断的话要改成警告，需人确认。
8. **没有建 `node_curriculum`**：课标条目表已建。节点与课标条目的对齐关系，以及 `aligned_to` 边是否指向课标条目，留给 KGE-01；0011 的 `aligned_to` 只连节点。
9. **跨学科边**：只有前置边与从属边要求两端同版本，关联、易混、对齐等边可以跨学科、跨版本（`to_map_version_id`）。BFF 只看两端版本都已发布的边。
10. **原句哈希**：`convert_to` 在 Postgres 里只标为 STABLE，不能直接用于生成列。改用标为 IMMUTABLE 的包装函数 `kge.quote_sha256`，前提是库编码固定为 UTF8，迁移开头已断言。

## 待人确认
1. **发布的人审点**：发布函数只查结构，不查“是否有人批准”。审核台 v0（4-G）上线前，是否要求发布前必须有一条已批准的 `content_review`（对象为该地图版本）？默认不在库层强制，由审核台流程保证。
2. **审核人身份**：`map_change.reviewers`、`content_review.reviewer`、投影的 `verified_by` 都是知识服务自报的工号，库层只能保证“两个不同的字符串”。是否要改为引用 staff 账号，并由授权函数取当前账号？现在的授权函数面向家庭账号，这个改动要连同审核台一起设计。
3. **证据包与 diff 的内容边界**：`map_change.evidence_pack`、`content_review.diff` 是 jsonb，库层只限大小。只能放聚合统计与样本 ID、不能放个人层数据（规则 13）的要求，现在靠知识服务与评审保证。是否要加最小人数门槛的库层检查，等 KGE 的群体信号回流（KGE-05）时定。
4. **过期时政的严重程度**：见偏差 7。
5. **`item_tag` 的地图版本与题目内容版本的一致性**：题目的内容版本经 0012 引用地图版本后，标签的地图版本应当与之一致。0012 的列可空、现有行为空，所以 v0.9 不强制，留给 KGE-02 导入题库时加。
