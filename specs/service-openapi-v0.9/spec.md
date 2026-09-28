# 知识服务与洞察服务 OpenAPI v0.9

状态：**已确认（按默认建议）**，已实现。确认：用户，2026-09-28（“按默认建议，开始实现”，待人确认 #1–#11 全部按默认）。实现记录与偏差见 lessons.md
任务包：开发计划 v2.0 冲刺 0 · W2“契约冻结与决策”：OpenAPI v0.9 覆盖三部分——学科插件六方法（已有 `plugin-api.v0.9.yaml`）、知识服务 `node.* / page.* / item.* / rubric.* / evidence.write / mastery.*`、洞察服务 `insight.*`。本 spec 只起草后两份。W2 周五评审，W4 与插件 API 一起冻结为 v1.0。

关联：
- **KGE V1.1** 第八章“知识服务接口”：十个工具、返回与调用方；“所有返回统一带 result · evidence_ids · confidence · source_version · model_version · human_review_required”；`human_review_required` 为真时进入共用的教研复核队列。
- **SIE V1.1** 第九章“接口”：九个工具与调用方；“diagnose、plan、report 通过这些工具读画像；grade、teach 只经 KGE 的 evidence.write 写证据，不直接调用 insight 的写接口”。第八章三端视图与“我不同意”三条路。
- **CLAUDE.md**：
  - 规则 1：契约是唯一硬接口；
  - 规则 3：判分溯源；
  - 规则 11：学科 Agent、点点、适配器只能经契约提交证据（由学习服务落库），经洞察服务接口读画像视图；
  - 规则 12：不推断；
  - 规则 13：公共层与个人层分离；
  - 必停项：`packages/contracts` 的任何变更。
- **仓库现状**：
  - `packages/contracts/openapi/plugin-api.v0.9.yaml`：信封、`EvidenceDraft`、`ModelVersion`、`VersionedName`、`SourceVersion`、`DataClass`、`Problem` 等可复用；
  - `learning-internal.v0.yaml`：BFF → 学习服务，已用跨文件 `$ref` 复用插件 API 的 schema；
  - `scripts/check_contract.py`：合法性、示例、反例、无约束文本扫描、示例无 PII；
  - 迁移 0010（`insight.claim`）、0011（`kge.*`）：两份接口的返回字段按这两个 schema 取；
  - ADR 01（学习服务是个人层唯一写入方）、ADR 13 草稿（判断账本唯一写入方是洞察服务）。
- **待登记**：`specs/contract-conformance-sprint1/v1.0-review.md` 第四节 sie-#1–#5、第五节 kge-#1–#5。
- **2026-09-28 补充的两份增补**（`specs/plan-v2-alignment/spec.md` 第七节）：
  - 技术蓝图 v2.1：作答证据与 A / B / C 证据等级，只有 A 级点亮星，C 级不进学习者模型；
  - 平台规划 v1.3：不显示个人名次、不做同伴比较、不给录取概率百分比；去向引擎、考情政策库。

## 目标
让两个核心在写代码之前就有可评审、可自动检查的接口：
- 调用方（编排层、学科服务、BFF）知道能拿到什么、要交什么；
- 实现方（KGE-01 起的知识服务、SIE-01 起的洞察服务）有明确的返回结构；
- “判断先于展示”“不推断”“公共层不含个人数据”“判分可溯源”这几条规则落到 schema 上，由检查脚本强制，而不是只写在文档里。

## 设计要点

### 1. 形态：两份 OpenAPI 3.1，工具名即 operationId
- 新文件：
  - `packages/contracts/openapi/knowledge-service.v0.9.yaml`；
  - `packages/contracts/openapi/insight-service.v0.9.yaml`。
  - 两份的 `info.version` 都是 `0.9.0`。
- 路径形如 `POST /v0/node.resolve`、`POST /v0/insight.context`。`operationId` 与 KGE / SIE 文档里的工具名一一对应（`node_resolve`、`insight_context` 等）。
- KGE-03 的 MCP 工具从同一份 schema 生成，不另写一套。
- 全部用 POST + JSON 请求体，不把学生 ID 放进 URL，免得进访问日志（规则 8）。
- 公共 schema 用跨文件 `$ref` 复用 `plugin-api.v0.9.yaml`，不复制：`ModelVersion`、`VersionedName`、`SourceVersion`、`Confidence`、`DataClass`、`EvidenceDraft`、`Problem`。

### 2. 统一信封
按 KGE 第八章，每个成功响应都是：
```
{ result, evidence_ids, confidence, source_version, model_version, human_review_required, contract_version }
```
- 纯公共知识的读取（`node.get`、`node.path`、`rubric.get` 等）也带全六项：
  - `evidence_ids` 为空数组；
  - `confidence` 为 1；
  - `model_version` 为 `algo:kge-<工具>@x.y.z`；
  - `source_version` 带地图版本段，如 `graph:math-map@1.2.3`（kge-#4 的约定）。
- 模型参与的返回（`node.resolve` 的候选、`item.similar`）按实际模型写 `model_version`，并按置信度决定 `human_review_required`。
- 信封外不允许额外字段（`unevaluatedProperties: false`），与插件 API 一致。

### 3. 知识服务：十个工具

| 工具 | 请求要点 | 返回 `result` 要点 | 数据级别 |
|---|---|---|---|
| `node.resolve` | 文本或图片引用（`upload_ref`，不内联图片），学科，可选 `map_version` | 候选节点（ID、层级、类型）+ 置信度，按置信度排序 | L1；带图片时 L2 |
| `node.get` | 节点 ID，教材版次 `edition`、地区 `region` | 节点、范围属性、认知层次、包含 / 不包含、已核对的教材投影、地区权重 | L1 |
| `node.path` | 节点 ID，方向 `up`（前置链）/ `down`（后续链），深度上限 | 只走强前置边的链 | L1 |
| `page.get` | 页面 ID，视图（教材、地区） | 已发布页面正文与原子引用 | L1；KGE-03 前返回 501 |
| `item.search` | 节点、认知层次、难度区间、题目族 | 题目引用与标签，**不含题干与答案** | L1 |
| `item.similar` | 题目引用 | 同题目族或相似题的引用 | L1 |
| `rubric.get` | 题目引用 + `rubric_version` | 采分点：`point_id`、分值、节点、部分给分规则，与 `kge.scoring_point` 一一对应 | L1 |
| `evidence.write` | 学生、证据草稿批（每条带 `evidence_level` A / B 与 `channel`）、`idempotency_key` | 写入的证据事件 ID；C 级拒收 | L3 |
| `mastery.get` | 学生、节点集合 | 节点 × 层次的掌握状态、门槛、下次复测时间 | L3 |
| `mastery.explain` | 学生、节点 | 证据链：证据 ID、时间、类型、证据等级、权重、提示等级 | L3 |

几条取舍：
- **题干、答案、解析不经知识服务返回**：kge schema v0.9 待人确认 #3 已定为不进库。`item.search` 只返回引用，内容由调用方从学科内容包取。
- **个人层三个工具（`evidence.write`、`mastery.*`）的实现仍在学习服务**：过渡期学习服务是个人层唯一写入方（ADR 01、CLAUDE.md 仓库结构）。契约只定接口，不定部署位置。
- **`evidence.write` 的调用方是平台编排层 / 学习服务，不是适配器**：见待人确认 #1。
  - 适配器仍按插件 API 在响应里返回证据草稿，由学习服务落库（规则 1、11）；
  - 这与 KGE 文档“grade、teach 经 evidence.write 写证据”的意思一致：写入经知识服务的接口、由平台执行，Agent 不直接改掌握度；
  - 请求体复用 `EvidenceDraft`，另加冲刺 1-C 的三列 `evidence_kind`、`map_version`、`cognitive_level`，在本 schema 里定义为可选。
- **证据等级（技术蓝图 v2.1 §2.1）**：
  - `evidence.write` 的每条证据必填 `evidence_level`（`A` / `B`）与 `channel`（`keypad` / `canvas` / `voice` / `photo`）；
  - C 级（低置信未确认、外部题、申诉中）不进学习者模型，`evidence.write` 以 422 拒收，schema 的枚举里就没有 C；
  - `channel = photo` 只能是 B 级（结构约束），平台内独立作答才可能是 A 级；
  - “只有 A 级点亮星”“B 级权重”属于掌握计算（必停项），不在契约里写数值，由 KGE-04 实现并按对照文档第七节 #1 由人定；
  - 原图、切片、校正日志留在平台侧的作答证据里，知识服务的接口只带 `answer_evidence_ref`。
- **公共层四类工具（node、page、item、rubric）的响应里不能有任何学生相关字段**（规则 13）。检查脚本扫描这几条路径的完整 schema 树，出现 `student`、`attempt`、`evidence_event`、`account`、`guardian` 等字段名即失败，与 0011 的 `kge.has_personal_key` 同一张词表。

### 4. 洞察服务：九个工具

| 工具 | 请求要点 | 返回 `result` 要点 | 调用方 |
|---|---|---|---|
| `insight.context` | 学生、任务类型（`diagnose` / `plan` / `teach` / `report` / `companion`）、学科、token 预算 | 按任务裁剪的上下文包：字段白名单内的条目，每条带 `claim_id` 或事实来源 | 编排层（再放进插件请求） |
| `insight.claims` | 学生、过滤（学科、对象类型、状态） | 判断列表：模板键、状态、置信度、证据 ID、到期时间 | 规划、报告 |
| `insight.explain` | 判断 ID、受众（`student` / `guardian` / `agent`） | 按模板渲染的结论句 + 证据链 + 置信度说明 | 学生端、家长端、Agent |
| `insight.dispute` | 判断 ID、类型（`i_know` / `careless` / `data_error`）、`idempotency_key` | 异议单 ID、验证题安排 | 学生端、家长端 |
| `insight.predict` | 学生、考试（范围节点、日期、满分） | 冻结的分数区间与依据 | 学生端、家长端 |
| `insight.reconcile` | 考试记录 ID | 对账卡：是否落在区间、偏差节点、四个方向的偏差分析、平台据此改了什么 | 学生端、家长端 |
| `insight.record` | 学生、事件类型（行为、学生自述、家长观察）、`idempotency_key` | 事件 ID | 前端、编排层 |
| `insight.export` | 学生 | 导出任务 ID（异步）与完成后的下载引用 | 家长端 |
| `insight.erase` | 学生、确认码、`idempotency_key` | 删除回执 | 家长端 |

规则如何落到 schema 上：
- **判断先于展示（规则 11）**：
  - `insight.explain` 与 `insight.context` 里所有面向学生或家长的文本，都是 `RenderedStatement { claim_id, template_key, template_version, text }`；
  - `claim_id` 必填，不能单独出现 `text`；
  - 检查脚本把 `RenderedStatement.text` 列为唯一允许的无约束文本（限长 300 字），并要求它总与 `claim_id` 同在一个对象里；
  - 状态只允许 `hypothesis`、`established`、`to_verify`。`to_verify` 带 `display: dashed`，由前端画虚线、不作陈述，这对应 ADR 13 待人确认 #2 / 安全 A-8，在描述里写明前提。
- **不做名次与概率（平台规划 v1.3 §3）**：
  - 洞察服务的响应树里不允许出现名次、百分位、概率类字段，由检查脚本按字段名扫描（`rank`、`percentile`、`probability`、`position` 等）；
  - `insight.predict` 只返回分数区间与依据，不返回概率。
- **不推断（规则 12）**：
  - 模板键、指标键、上下文字段都用 `enum` 或 `pattern` 约束，不开放任意键；
  - 检查脚本对两份契约的全部 `description`、`enum` 取值和示例文本跑禁用推断词表（`docs/design/sie/sie_lexicon.py`），命中即失败。
- **来源分开（规则 6）**：
  - `insight.record` 的事件分 `behavior`、`self_report`、`guardian_report` 三种，`source_type` 由事件类型决定、不由调用方填写；
  - 学生与家长对同一字段的说法在返回里并列，不合并。
- **可见范围**：
  - 请求带 `audience`，返回只含该受众可见的条目。服务按判断的 `visibility` 过滤，与 0010 的 RLS 一致；
  - `audience = agent` 的上下文包不含家庭圈详情（SIE 第八章三端视图）。
- **异议与更正**：
  - `data_error` 类异议返回“打开原始事实供更正”的引用，不在洞察服务里改事实；
  - 事实更正走原录入通道，洞察服务据此把判断转为撤回。
- **导出与删除**：只定接口；实现随 SIE-03，删除的范围与回执格式属于必停项，届时另行评审。

### 5. 鉴权、幂等、计量
- **鉴权**：两份契约都只接受平台内部服务令牌（`bearer`，按调用方类别区分：`bff`、`orchestrator`），学生与家长不直接访问。
  - 学生端、家长端的请求由 BFF 先按 `guardian_student_link` 与本人关系鉴权（规则 5），再带 `student_ref` 与 `audience` 调用；
  - 洞察服务按 `audience` 再过滤一层，与 BFF 的检查重叠是有意的。
- **幂等**：所有写入类工具必须带 `idempotency_key`，语义与 `learning-internal.v0.yaml` 相同：同 key 同体返回原结果，同 key 异体返回 409：
  - `evidence.write`；
  - `insight.dispute`、`insight.record`、`insight.predict`（冻结）、`insight.erase`。
- **计量（规则 4）**：
  - 知识服务、洞察服务的调用本身不计次；
  - 调用由已计量的插件请求或 BFF 会话触发，计量仍在插件请求与 BFF 层；
  - 两份契约都不带 `usage_id`，在描述里写明理由，避免被误用为新的计费入口。
  - `insight.dispute` 安排的验证题由后续的 `grade` 计量。

### 6. 检查与测试（`scripts/check_contract.py` 扩展）
- 两份新文件：
  - OpenAPI 3.1 合法；
  - 跨文件 `$ref` 全部可解析；
  - `info.version` 与信封 `contract_version` 一致。
- 每个工具至少一个请求示例与一个响应示例，都符合 schema，示例里的 `evidence_ids` 闭合（指向本示例的证据或请求里的引用）。
- 统一信封六项在每个 200 响应上都存在（结构检查）。
- 无约束文本扫描扩展到两份新契约，允许的例外只有：
  - `RenderedStatement.text`；
  - `node.resolve` 请求里的学生输入文本（L2，限长）。
- 公共层四类工具的响应树不含个人层字段名（第 3 节）。
- 洞察服务的响应树不含名次、百分位、概率类字段名（第 4 节）；`evidence.write` 的证据等级枚举只有 A、B，`photo` 通道只能是 B。
- 两份契约的描述、枚举与示例过禁用推断词表（第 4 节）。
- 示例无 PII：沿用现有规则，只用合成 UUID 与合成节点 ID。
- 反例集新增，每条都必须被 schema 或检查脚本拒绝：
  - 无 `claim_id` 的陈述；
  - 状态为 `refuted` 的陈述；
  - 公共层响应夹带 `student_id`；
  - 写入工具缺 `idempotency_key`；
  - 信封缺 `source_version`；
  - C 级证据或 A 级的拍照证据写入 `evidence.write`；
  - 洞察服务响应夹带 `rank_percentile` 一类字段。
- 变异测试新增：删掉任一条上述检查，变异测试必须发现。
- README 的文件表、设计要点、版本策略同步。

## 范围
### 做
- `knowledge-service.v0.9.yaml`、`insight-service.v0.9.yaml`（含示例）。
- `check_contract.py` 扩展、`tests/counterexamples.py` 与 `tests/test_mutations.py` 新增项、README 更新。
- 在 `specs/contract-conformance-sprint1/v1.0-review.md` 登记与插件 API 的衔接：
  - `EvidenceDraft` 三列；
  - 插件请求里由编排层放入的 `insight.context` 上下文包字段；
  - sie-#1 的 report 结论句改由 `insight.explain` 生成。
- `specs/service-openapi-v0.9/lessons.md`。

### 不做
- 任何服务实现、路由、MCP 工具生成（KGE-01 / KGE-03 / SIE-01 起）。
- 改 `plugin-api.v0.9.yaml` 本身。插件 API 与两份新契约的衔接只登记，W4 冻结 v1.0 时一起改。
- 一致性测试（`conformance/`）扩到两个核心：冲刺 1 的“消费者驱动契约测试进 CI”。
- 数据库变更。

## 验收标准（可测试）
- [ ] 两份新契约通过 OpenAPI 3.1 校验，跨文件 `$ref` 全部可解析，`make contract-test` 输出里列出三份契约各自的统计。
- [ ] KGE 第八章的 10 个工具、SIE 第九章的 9 个工具各有一个路径，`operationId` 与工具名对应；检查脚本核对清单齐全，少一个即失败。
- [ ] 每个 200 响应都有统一信封六项；每个写入类工具的请求都要求 `idempotency_key`。
- [ ] 面向学生或家长的文本只能以 `RenderedStatement`（含 `claim_id`）出现，状态只能是 `hypothesis` / `established` / `to_verify`。
- [ ] 公共层工具的响应树没有个人层字段名；两份契约的描述、枚举、示例零命中禁用推断词表。
- [ ] `evidence.write` 只接受 A、B 两级证据，拍照通道只能是 B 级；洞察服务响应没有名次、百分位、概率类字段。
- [ ] 新增反例全部被拒绝；新增变异全部被发现；原有契约测试、变异测试、一致性测试不受影响。
- [ ] 严格 `make check` 通过。

## 涉及的必停项（见 CLAUDE.md）
- `packages/contracts` 的任何变更：是。PR 首行写 `[NEEDS-HUMAN]`。
- 画像可见范围：`insight.*` 的 `audience` 过滤规则是可见范围的接口化，与 0010 的 RLS 一致，不新增可见范围；需要人确认（待人确认 #5）。
- 迁移、计量、判分与掌握逻辑、判断模板、人设：否。

## 依赖与输入
- KGE V1.1 第八章、SIE V1.1 第八、九章。
- 迁移 0010、0011 的字段；字段字典 `docs/design/sie/field-dictionary.v0.9.yaml`（上下文包字段白名单取自它的 `visibility` 含 agent 的字段）。
- 禁用推断词表草稿 `docs/design/sie/forbidden-inference.v0.yaml`（草稿阶段只用于契约检查，不做发布阻断）。

## 待人确认
1. **`evidence.write` 的调用方**：
   - 默认只有平台编排层 / 学习服务能调用，适配器仍在插件响应里返回证据草稿（ADR 01、规则 1、11）；
   - 另一种做法是适配器直接调用 `evidence.write`：接近 KGE 文档字面，但适配器要持有写令牌，还要改插件 API 的数据流。
2. **适配器是否直接调用两个核心**：
   - 默认 v0.9 不直接调用：编排层调用 `insight.context`、`node.get` 等，把结果放进插件请求（沿用插件 API 设计要点 1“适配器不读平台表”）；
   - MCP 工具只给编排层与点点。
   - 如果要让适配器直接调用，需要为适配器发只读令牌，并在一致性测试里覆盖。
3. **两个服务的代码位置**：契约与位置无关。知识服务是 `services/learning` 内的模块还是独立服务，另行决定（ADR 13 待人确认 #3 同类问题）。
4. **`page.get` 先列进契约、实现前返回 501**：默认列入，让 teach / report 的调用方现在就按它设计。
5. **`audience` 过滤与 `to_verify` 的虚线展示**：
   - 默认 `to_verify` 对学生、家长返回但标 `display: dashed`，前端不作陈述；
   - 这是 ADR 13 待人确认 #2 的接口化，需与之一起定。
6. **`insight.export` / `insight.erase` 在 v0.9 只定接口**：删除范围（含 platform.* 个人层与判断账本的级联）与回执格式随 SIE-03 评审。
7. **`node.resolve` 的图片输入**：只接受平台上传通道产出的 `upload_ref`（KGE-05），不内联图片，也不经第三方 OCR。上传通道之前，只支持文本。
8. **契约数量与冻结节奏**：三份契约各自有版本号，W4 一起冻结 v1.0。任一份破坏性变更都要列出受影响的调用方。
9. **去向引擎、考情政策库、中考百科的接口不在 v0.9**（平台规划 v1.3）：
   - 默认先不列。政策库的 Schema 另起 spec，接口等 Schema 定了再定；
   - 去向结论要经洞察服务写成判断（对照文档第七节 #6），届时在洞察服务契约里加判断对象类型。
10. **拍照题目匹配是否作为知识服务工具**（技术蓝图 v2.1 §3.2）：
    - “题目匹配到题库”自然落在 `item.*`，候选名 `item.match`；
    - 默认 v0.9 不加，随上传通道（KGE-05）的 spec 加，避免在样本集建成前定接口；
    - 如果要按 v2.1 的 P0 提前到阶段 0，请在确认时说明。
11. **证据等级进知识服务契约**（对照文档第七节 #1、#2）：
    - 默认按上面第 3 节写进 `evidence.write`；
    - 插件 API 的 `grade` 改为接收作答证据引用，是插件 API v1.0 的事，本 spec 只登记。
