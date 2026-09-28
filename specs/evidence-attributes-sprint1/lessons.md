# 冲刺 1-C 证据补列 · 经验与待人确认

## 交付
- 迁移 0017：`evidence_event`、`attempt` 加 `evidence_kind`、`evidence_level`、`channel`、`map_version_id`、`source_type`；
  `platform.evidence_source_type(kind)` 由载荷类型决定来源类型，写入时未给就由触发器补齐，给了必须一致。
- learning-internal v1：请求体改为信封 `{request, ingest}`（`IngestContext`：`channel`、`purpose`、`evidence_level`）；摘要加 `evidence_level`、`learner_model_updated`；
  示例 2 个、信封与摘要反例 9 个进 `make contract-test`。
- 学习服务：`learning/attributes.py` 推导规则；C 级不写能力观察与错因；指纹含 `ingest`（缺省用途先归一）；地图版本经内容版本取。
- 测试：数据库 16 项新断言、6 个变异；学习服务 17 项新测试；手工变异（去掉 C 级守卫、提示优先、申诉记 C）都被发现。

## 与 spec 的差异（按默认处理，记在这里供评审）
1. **`source_type` 回填了旧行**：spec 写“补列不回填”，但来源类型由载荷类型唯一决定，按同一规则回填后设为非空，比留空更准确；其余四列仍不回填。
   写入时由触发器按载荷类型补齐，约束保证与载荷类型一致——所以现有夹具、种子、渗透测试夹具不用改。
2. **`IngestContext` 也能带 `evidence_level`**：插件 `DiagnoseRequest` 没有 `evidence_level` 字段，诊断的非键盘作答只能从这里给；grade 两处都给时必须一致，否则 400。
3. `attempt.source_type` 恒为 `objective`（列有缺省值与约束），只为规则 6 的“每条数据标来源”留列；学生自述的把握度 `self_report` 仍在该表（待人确认 #5，另起任务迁出）。
4. 数据库角色断言测试（0011 的 kge 角色检查）跳过 0017：它依赖 0011 的 `kge.map_version`，与 0012 同理。

## 安全审查后的调整（按默认处理）
1. **证据等级不再能自报 A**：拍照最高 B（服务端 400 + 数据库约束）；画布、语音记 A 必须带 `answer_evidence_ref`（确认记录），否则 400。
2. **C 级不能经“既有证据”间接进入学习者模型**：`prior_evidence` 引用 C 级证据的请求 422，不调适配器。
3. **缺 `hint_level_used` 不再推成独立作答**：`evidence_kind` 记空，由 KGE-04 按保守口径处理；适配器证据里有 `hint_opened` 信号时强制 `hinted`
   （信号带题目只标那道，不带题目标全部作答）。
4. 键盘通道出现识别转写证据 → 502，数据库同样约束；来源类型约束对未知载荷类型拒绝（`coalesce(..., false)`）。
5. 作答行的四个补列必须与它的作答证据一致（插入触发器）；作答表的拍照约束因此是第二道闸（证据本身不能是拍照 A 级）。
6. **v0 路径的申诉也不写能力观察与错因**（ADR 15“申诉中”）。这是对现有 v0 行为的收紧，BFF 目前正走 v0。
7. 摘要的 `evidence_level`、`learner_model_updated` 在契约里改为可选：重放 `/internal/v0` 写入的结果时没有；经 v1 写入的必带。
8. 启动时预加载 v1 内部契约，文件缺失即启动失败。

## 经验
- 证据表的 `kind`（载荷类型）与 KGE 的 `evidence_kind`（作答情境）名字相近、含义不同；新列、函数、测试都用 `evidence_kind` 全名，避免混用。
- 地图版本的测试最初复用共享内容版本、`ON CONFLICT DO NOTHING`，绑定可能被跳过而“空对空”通过；改为每次用专用内容版本并断言等于新建的地图版本。

## 待人确认
1. 迁移 0017 依赖 0011（草稿、由人执行）；两者需一起在副本演练（必停项）。
2. spec 待人确认 #1–#5 按默认执行，签字时复核：信封、C 级不写观察与错因、键盘缺省 A、`source_type` 只加两张表、`self_report` 迁出另起任务。
3. BFF 仍走 v0（第二步待人确认 #2），v1 信封的 `channel`、`purpose` 由谁给出（前端还是编排层）随 BFF 切换任务定；`purpose` 不能由客户端直接指定。
5. 画布、语音记 A 的门槛目前是“带 answer_evidence_ref”：平台还没有作答证据对象（ADR 15 输入通道服务），引用本身无法核实，
   输入通道服务上线前是否一律封顶 B 待定（安全审查必须改 1 的另一选项）。
6. v0 申诉不进学习者模型是行为变更，需确认（安全审查建议 4）。
7. `purpose` 与等级由调用方给出、学习服务无法核实：BFF 切 v1 前定下“只由编排层按复测、迁移调度记录填写，BFF 不透传前端字段”，
   以后由学习服务按调度表核验（安全审查建议 1）。
8. `skill_observation` 没有等级标记，KGE-04 须按证据的 `evidence_level` 过滤“只有 A 级点亮”（安全审查建议 5）；判断账本 `insight.claim.evidence_ids`
   同样不按等级过滤，建议 SIE 首个任务包一并处理。
9. 迁移演练注意：分区表上的 BEFORE ROW 触发器需 PG 13 及以上；`source_type` 回填是整表单事务 UPDATE，会长时间持锁。
4. C 级诊断仍返回 `observation_count`（适配器给出的观察数），但一条都不落库：摘要里以 `learner_model_updated = false` 区分，是否改成 0 待定。
