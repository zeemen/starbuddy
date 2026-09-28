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

## 经验
- 证据表的 `kind`（载荷类型）与 KGE 的 `evidence_kind`（作答情境）名字相近、含义不同；新列、函数、测试都用 `evidence_kind` 全名，避免混用。
- 地图版本的测试最初复用共享内容版本、`ON CONFLICT DO NOTHING`，绑定可能被跳过而“空对空”通过；改为每次用专用内容版本并断言等于新建的地图版本。

## 待人确认
1. 迁移 0017 依赖 0011（草稿、由人执行）；两者需一起在副本演练（必停项）。
2. spec 待人确认 #1–#5 按默认执行，签字时复核：信封、C 级不写观察与错因、键盘缺省 A、`source_type` 只加两张表、`self_report` 迁出另起任务。
3. BFF 仍走 v0（第二步待人确认 #2），v1 信封的 `channel`、`purpose` 由谁给出（前端还是编排层）随 BFF 切换任务定；`purpose` 不能由客户端直接指定。
4. C 级诊断仍返回 `observation_count`（适配器给出的观察数），但一条都不落库：摘要里以 `learner_model_updated = false` 区分，是否改成 0 待定。
