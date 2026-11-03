# 冲刺 1-C 证据补列：作答情境、证据等级、输入通道、地图版本、来源类型

状态：**已确认（2026-09-28，用户“按默认建议，开始实现”）**，待人确认 #1–#5 按默认执行；实现记录见 lessons.md
任务包：开发计划 v2.0 冲刺 1（W3–W4）C 流“KGE 证据写入：attempt、evidence_event（含 evidence_kind、提示等级、地图版本）、error_attribution；幂等键”，
验收“重复事件不重复入库”（幂等已在旧冲刺 1-C 交付，本任务只补列）。
关联：
- 对照文档 `specs/plan-v2-alignment/spec.md` 第三节 #5、第七节 #1；
- ADR 15（证据等级 A / B / C、输入通道），ADR 11（只有 A 级推动星亮）；
- `specs/kge-schema-v0.9/spec.md`（三列在冲刺 1-C 加）；
- 插件 API v1.0 候选版 svc-#2 / kge-#5（证据草稿不带这些字段，由平台补齐）；
- `specs/plugin-api-v1.0-step2/`（学习服务 v1 路径已就位，“本任务只保留位置”）；
- `specs/mastery-estimate-sprint4/`（KGE-04 按这些列计算；缺列旧证据按保守口径）；
- CLAUDE.md 规则 1、2、6、11。

> 涉及迁移、契约（learning-internal v1 请求与摘要）、以及“哪些证据进入学习者模型”的口径（掌握计算的输入，必停项）。PR 首行写 [NEEDS-HUMAN]。

## 目标
每条证据落库时都带上“在什么情境下作答（evidence_kind）、可信到哪一级（evidence_level）、从哪条通道来（channel）、对应哪版知识地图（map_version）、
是什么来源（source_type）”，KGE-04 掌握计算与 SIE 画像可以直接按列取用，不再从载荷里猜。

## 范围
### 做
1. **迁移 0017（platform，必停项）**：
   - `platform.evidence_event` 加列：
     - `evidence_kind`：`independent` / `hinted` / `transfer` / `delayed_retest`；
     - `evidence_level`：`A` / `B` / `C`；
     - `channel`：`keypad` / `canvas` / `voice` / `photo`；
     - `map_version_id`：引用 `kge.map_version`（按 `(subject, map_version_id)`，同 0012 的写法）；
     - `source_type`：`objective`（客观记录）/ `student_self_report` / `guardian_observation` / `model_inference`（规则 6）。
   - `platform.attempt` 加 `evidence_kind`、`evidence_level`、`channel`、`map_version_id`、`source_type`，与它的作答证据一致；
   - 全部可空：已落库的旧证据为空，**不回填**（KGE-04 对空值按保守口径，见 `specs/mastery-estimate-sprint4/` 缺属性旧证据一节）；
   - 一致性约束：`evidence_kind` 只出现在作答类与轨迹类证据上（`interaction_signal`、`speech_features` 为空）；新写入的行 `source_type` 非空（用 `recorded_at` 之后的约束或触发器，旧行豁免）。
2. **学习服务补齐（平台侧，不经适配器）**：
   - learning-internal v1 的请求体改为信封：`{ "request": <插件 GradeRequest / DiagnoseRequest>, "ingest": IngestContext }`（v1 仍是候选版，BFF 尚未切换，改动成本最低）。
     `IngestContext`：`channel`（必填）、`purpose`（`practice` / `retest` / `transfer_check`，默认 `practice`）；
   - 推导规则：
     - `evidence_kind`：`hint_level_used > 0` → `hinted`（优先，最保守）；否则 `purpose = retest` → `delayed_retest`，`transfer_check` → `transfer`，其余 `independent`；
     - `evidence_level`：请求里的 `evidence_level` 优先；没有时 `channel = keypad` → A，其余通道必须显式给出，否则 400；`appeal = true` 一律 C（ADR 15“申诉中”）；
     - `map_version_id`：由 `context.content_version` 对应的 `platform.curriculum_version.map_version_id`（0012）取得，未绑定时为空；
     - `source_type`：作答与验证轨迹为 `objective`；其余适配器产出的证据按载荷类型定（`ocr_transcription` / `asr_transcription` 为 `model_inference`）；
   - **C 级证据**（ADR 15“只展示批改，不进入学习者模型”）：照常写 `evidence_event`、`attempt`、`grade_result`（批改要展示、申诉要回溯），
     **不写** `skill_observation` 与 `error_attribution`；摘要里标明；
   - 幂等指纹包含 `ingest`（同 key 换通道算冲突）；重放接口不变。
   - v0 路径不改语义：只按提示等级写 `evidence_kind`（`hinted` / `independent`），`evidence_level`、`channel` 为空，`source_type` 照常写。
3. **契约（必停项）**：`learning-internal.v1.yaml` 的请求改为信封、新增 `IngestContext`；`GradeSummary` / `DiagnoseSummary` 加 `evidence_level` 与
   `learner_model_updated`（C 级为 false）；反例与 `make contract-test` 同步。插件 API 不改。
4. **测试**：迁移 up → down → up；约束与授权测试、变异测试；学习服务集成测试覆盖推导规则每一分支（含 C 级不写观察与错因、同 key 换通道 409、地图版本取值）；
   渗透测试夹具自检不受影响（新列可空）。

### 不做
- `node_id`（skill_node → knowledge_node 映射）与 `cognitive_level`：依赖 KGE-01 骨架与 KGE-02 打标，另起任务；
- 输入通道服务、作答证据对象（原始输出、校正日志、确认记录）：ADR 15 后续任务；
- BFF 切到 `/internal/v1` 写入（沿用第二步的决定，签字后另开）；
- 掌握计算、B 级权重（KGE-04，必停项）；旧证据回填。

## 验收标准（可测试）
- [ ] 迁移 0017 up → down → up 通过；新列的取值约束、`evidence_kind` 与载荷类型的一致性约束各有正反例；变异测试覆盖。
- [ ] v1 写入：键盘作答 → A、`independent`；用过提示 → `hinted`；复测 → `delayed_retest`；拍照未给等级 → 400；申诉 → C；每条都有集成测试。
- [ ] C 级：`evidence_event`、`attempt`、`grade_result` 有行，`skill_observation`、`error_attribution` 零行；摘要 `learner_model_updated = false`。
- [ ] 同一 key 换 `channel` → 409 `IDEMPOTENCY_CONFLICT`，零写入；重复请求仍只入库一次。
- [ ] `map_version_id` 与内容版本绑定的地图版本一致；未绑定时为空。
- [ ] v0 路径行为不变（现有测试全绿），新列按上面的 v0 规则写入。
- [ ] 契约测试通过（信封、`IngestContext`、摘要新字段的示例与反例）；严格 `make check` 通过。

## 涉及的必停项
- migrations：是（0017，先在副本演练，由人执行）；RLS：不新增策略，列级授权随表；
- contracts：是（learning-internal v1 请求与摘要）；计量：否；
- 判分与掌握计算口径：是（“C 级不写观察与错因”“缺等级时键盘记 A”决定哪些证据进入学习者模型）。

## 依赖与输入
- 迁移 0011、0012（`kge.map_version`、`curriculum_version.map_version_id`）尚为草稿、由人执行；0017 依赖它们。
- ADR 15 待签字；本任务按其草稿口径实现，签字有改动时跟改。

## 待人确认
1. 请求体改成信封（默认），还是在插件 `GradeRequest` 里加平台字段（会让适配器看到通道与用途，违背 ADR 15“适配器不接触通道细节”）？
2. C 级证据不写 `skill_observation`、`error_attribution`（默认）；还是照写、由 KGE-04 按等级过滤？默认更保守：错因也是画像输入，C 级不该进。
3. 缺 `evidence_level` 时键盘记 A（默认），画布、语音要求显式给出——画布“经回显确认”、语音“经复述确认”由前端保证，平台无法核实。
4. `source_type` 只加在 `evidence_event` 与 `attempt`（默认）；推断类表（`grade_result`、`error_attribution`、`skill_observation`、`mastery_estimate`）整表都是模型推断，是否也加常量列？
5. `platform.attempt.self_report`（作答把握度，学生自述）放在客观作答表里，与规则 6“学生自述与客观证据分表”不符：本任务只登记，迁出另起任务（默认），还是一并迁出？
