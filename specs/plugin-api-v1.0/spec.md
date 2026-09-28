# 学科插件 API v1.0 冻结

状态：**已确认（2026-09-28，用户“按默认建议，开始实现”）**，待人确认 #1–#6 按默认执行；实现与偏差见 lessons.md。v1.0 文件仍是候选版，评审会签字后才算冻结
任务包：开发计划 v2.0 冲刺 0 → 冲刺 1（W4）“契约 v1.0 冻结”；对照文档第八节 8.2 冲刺 0 后续顺序第 4 项
关联：
- 评审清单 `specs/contract-conformance-sprint1/v1.0-review.md`（第一至七节，共 61 项）；
- v0.9 契约 `packages/contracts/openapi/plugin-api.v0.9.yaml`、`learning-internal.v0.yaml`；README“版本策略”；
- ADR 04 增补、ADR 11、ADR 13、ADR 15（草稿）；技术蓝图 v2.1 专章第二章；计量 v2（`specs/metering-v2/`）；
- CLAUDE.md 规则 1、3、4、11、12

> 契约变更属于 CLAUDE.md 必停项。本任务的 PR 首行必须写 [NEEDS-HUMAN]；冻结需评审会逐项签字（清单“冻结流程”第 2 步）。本 spec 给出每一项的默认结论，供评审会推翻。

## 目标
产出 `plugin-api.v1.0.yaml`：把 v0.9 冲刺 1–3 实现中确认的规则写进契约，并为 v2.0 的两个核心（知识服务、洞察服务）、输入通道（作答证据）、考情库留好接口位置，冻结后破坏性变更须走迁移期。

## 分两步交付
- **第一步（本任务）**：契约文件、检查脚本、反例、变异、一致性测试与参考适配器改到 v1.0；v0.9 文件保留（迁移期）。
- **第二步（另起任务，ADP-数学-xx 与学习服务）**：学习服务同时接受 v0.9 与 v1.0，阿鸦适配器切到 v1.0，迁移期结束后删除 v0.9。本任务不改 `services/learning` 与 `adapters/aya` 的业务代码，只保证它们在 v0.9 下照常通过。

## 默认结论（逐项；“破坏”指相对 v0.9 的破坏性变更）

### A. 冲刺 1 实现中发现的（第一节）
| 项 | 默认结论 | 破坏 |
|---|---|---|
| N1 | 新增 Problem 编码 `UNAUTHENTICATED`（401） | 否 |
| N2 | 维持：描述写明“needs_review 时维度分 null 表示待复核”；v1.1 再议 `pending_review_score` | 否 |
| N3 | `EvidenceDraft` 新增 `attempt_id`；作答类证据必填 | 是 |
| N4 | 描述写明 `attempt_id` 由平台生成、适配器原样使用 | 否 |
| N5 | 描述写明 `rule_ref` 以注册表为准：采分点型以 `kge.scoring_point` 为准（kge-#2），验证器型以 `capabilities.verifiers` 做弱校验 | 否 |
| N6 | 描述写明总分 = 维度分加权和，权重由 Rubric 定；上限取采分点分值之和（kge-#3），平台校验 | 否 |
| N7 | schema 按 subject 用 if/then 约束 `item_ref` / `skill_node` 前缀 | 是 |
| N8 | `Problem.retryable` 描述写明“可重试 = 结果未知，须同 key 重试后再结算或回滚” | 否 |
| N9 | 新增 `MODE_NOT_SUPPORTED`（422） | 否 |
| N10 | 时间字段加 RFC 3339 带时区的 pattern | 是 |
| N11 | 维持平台侧校验（schema 看不到请求级别） | — |
| N12 | 不改（随 curriculum_version 迁移定，必停项另议） | — |
| N13 | 全部写进契约：未实现方法 501；`request_id` 不计入同一请求体比较；`student_ref` 强制 uuid；错误体 `application/problem+json` | 否（明确已有行为） |
| N14 | 平台负责同 key 冲突检测，适配器尽力而为；一致性测试的 409 断言保留为尽力而为 | 否 |
| N15 | 写进“证据 ID 的来源”：证据只记一次，推断可以多次 | 否 |
| N16 | 维持（人设与评测抽检） | 否 |
| N17 | `learning-internal` 新增只查询不执行的重放接口（`GET /internal/replay/{method}/{key}`） | 否（新增） |
| N18 | 写明 5xx 一律是结果未知，`retryable: false` 只对 4xx 有意义；学习服务的行为修正放第二步 | 否 |

### B. v0.9 待人确认与留给评审的（第二、三节）
- 维持起草默认：v09-#1、#3、#4、#6、#7、#8、#9、#10。
- v09-#2（teach 计量）：按计量 v2 已定的资源类型（`ai_active_time`、`lesson_whiteboard`、`voice_realtime`）写进描述；`mode = hint` 计入学时，不单列。
- v09-#5（`model_version` 写法）：维持 `model:供应商/模型@版本`，快照版本写在 `@` 后（ADR 04 增补第 4 条）。
- 超时约定写进描述：grade 10 秒、diagnose 30 秒，其余 30 秒。
- 一次结果用两个模型：`model_version` 写主模型，其余写进 `source_version` 的 `algo:` / `prompt:` 段。
- 内容指纹字段：v1.0 不加；计量 v2 的去重由 BFF 按平台侧内容摘要做，不依赖适配器。
- 其余按清单“建议”一栏写进描述。

### C. 判断账本（第四节，规则 11）
| 项 | 默认结论 | 破坏 |
|---|---|---|
| sie-#1 | `ReportFields.trend`、`highlight_codes` 保留为字段加证据引用，描述写明“不直接展示给学生家长，结论句由洞察服务生成” | 否 |
| sie-#2 | `forecast` 的 `audience` 去掉 student、parent，只给 agent | 是 |
| sie-#3 | teach 讲解描述写明“只讲内容，不写关于学生的判断” | 否 |
| sie-#4 | `verified_claims` 改名 `verified_statements` | 是 |
| sie-#5 | 维持 | — |

### D. 知识服务与采分点（第五节）
- kge-#1、#3、#4：写进描述（维度 ID = 采分点 ID；各维之和上限；`source_version` 地图段 `graph:<学科>-map@x.y.z`）。
- kge-#5 与 svc-#2：`EvidenceDraft` **不加** `evidence_kind`、`map_version`、`cognitive_level`、证据等级、通道；这些由平台（学习服务按请求上下文与输入通道）补齐（ADR 15 默认“平台补齐”）。
- svc-#7：`ScoringPoint.point_id` 改为只允许小写（是，破坏）。

### E. 两个核心与编排层的衔接（第六、七节）
编排层把平台侧数据放进插件请求，适配器不直接调用两个核心、不读 policy 表（规则 1）。v1.0 在请求里新增**可选**字段，平台上线对应服务后开始填：
| 项 | 新增字段（可选） | 破坏 |
|---|---|---|
| svc-#1 | diagnose / plan / report 请求的 `learner_context`（洞察服务 `insight.context` 的裁剪结果，推断条目带 `claim_id`） | 否 |
| svc-#5 | grade 请求的 `scoring_points`（`rubric.get` 的结果） | 否 |
| svc-#6 | teach 的 `knowledge_page`；diagnose、plan 的 `node_path`；generate_variation 的 `item_family` | 否 |
| pol-#1 | plan、generate_variation、grade 请求的 `exam_config`（满分、时长、题型，取自学生届别的已发布方案） | 否 |
| svc-#3 | 同 sie-#1 | — |
| svc-#8 | 不涉及插件 API | — |
| pol-#2 | 洞察服务 `ExamTarget.full_score` 放宽为 1–300 的整数，由服务端对照政策库校验（改 `insight-service.v0.9.yaml`，同属必停项） | 否（放宽） |

### F. 输入通道（ADR 15、svc-#4）
- grade 请求新增可选 `answer_evidence_ref` 与 `evidence_level`（A / B / C），`response` 新增归一化形态（`latex`、`mhchem`、`semantic`）。
- `response.image_ref`、`response.transcript_ref` 标为**废弃**（v1.0 仍接受，迁移期结束后删除），不在 v1.0 直接删（ADR 15 写“v1.0 删除”，这里改为先废弃，给阿鸦迁移期）。
- C 级证据：描述写明平台不写入学习者模型，适配器照常判分。

### G. 规则 12
- svc-#9：`EvidencePayloadSignal.signal_code` 的 pattern 排除 `voiceprint`、`face`、`gaze`、`emotion` 开头的编码（是，破坏）。

## 范围
- 做：`plugin-api.v1.0.yaml`（`info.version = 1.0.0`）、`learning-internal.v1.yaml`（N17、N18）、`insight-service` 的 pol-#2 放宽；`check_contract.py` / `check_services.py` 支持 v1.0；反例与变异覆盖每条破坏性变更与新字段；一致性测试与参考适配器切到 v1.0；README 版本策略写明迁移期（默认：v0.9 保留到冲刺 2 结束，W6）；评审清单每项后面写“默认结论（待签字）”。
- 不做：学习服务、阿鸦适配器、BFF 的业务代码（第二步）；判断账本与知识服务的实现；输入通道服务。

## 验收标准（可测试）
- [ ] `plugin-api.v1.0.yaml` 是合法的 OpenAPI 3.1，三处版本号一致；v0.9 文件不变且检查照常通过。
- [ ] 每条破坏性变更（N3、N7、N10、sie-#2、sie-#4、svc-#7、svc-#9）至少有一个反例被 v1.0 拒绝、被 v0.9 接受。
- [ ] 每个新增可选字段有合法示例；新增错误码有示例。
- [ ] 变异测试：去掉任一条新约束，检查脚本能发现。
- [ ] 一致性测试在参考适配器上按 v1.0 通过；阿鸦按 v0.9 照常通过。
- [ ] 评审清单 61 项每项都有“默认结论（待签字）”。
- [ ] 严格 `make check` 通过。

## 待人确认
1. **评审会**：61 项的默认结论需技术负责人、四科负责人、教研代表签字；签字前 v1.0 文件标“候选版”。
2. **迁移期长度**：默认 v0.9 保留到冲刺 2 结束（W6）。
3. **svc-#2 / kge-#5**：证据的等级、通道、情境由平台补齐（默认），还是适配器直接给。
4. **`image_ref` / `transcript_ref`**：先废弃再删除（默认），还是按 ADR 15 在 v1.0 直接删。
5. **N7 的前缀约束**：知行 skill_node 坐标还没定（v09-#9），道法先只约束 `item_ref`。
6. **可选上下文字段的形状**：`learner_context`、`knowledge_page` 等直接复用两个服务契约的 schema（`$ref` 跨文件），还是在插件契约里复制一份精简版。默认跨文件引用，保证单一定义。
