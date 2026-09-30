# 文砺（语文）适配器

ADP-语文-01。实现学科插件 API v0.9（`packages/contracts/openapi/plugin-api.v0.9.yaml`），迁移期同时支持 v1.0 候选版
（`plugin-api.v1.0.yaml`）：按请求的 `context.contract_version` 校验与应答，其他版本返回 400 `CONTRACT_VERSION_UNSUPPORTED`。
版本开关与阿鸦、知行相同：能力声明默认报 0.9.0，可用 `--contract-version 1.0.0 --allow-candidate-contract`（或环境变量
`WENLI_CONTRACT_VERSION`、`STARBUDDY_ALLOW_CANDIDATE_CONTRACT=1`）改报 1.0.0，签字前只在测试与演练里用。
规格见 `specs/adapter-wenli-sprint2/spec.md`（待人确认 #1–#8 按默认执行）。骨架、路由客户端、幂等、启动检查照搬知行（`adapters/zhixing/`）。

| 接口 | 状态 |
|---|---|
| `GET /plugin/v0/capabilities` | 已实现 |
| `POST /plugin/v0/grade` | 作文五维（两段式，见下）。只接 `wenli:synthetic-essay-0N` 与文字作答；客观题送 grade 回 422 |
| `POST /plugin/v0/diagnose` | `mode = gap_test / intake`：客观题确定性判分 → 能力格观测；不调模型、不出题。`paper` / `process` 回 422 |
| `POST /plugin/v0/plan` | 确定性学科处方（移植 `recommendTargets`），不调模型、不写文字、不含提分 |
| `POST /plugin/v0/generate_variation` | 选题，不生成：只从已准入的题里选同能力格、同题型的题；没有候选时返回空与 `empty_reason` |
| `POST /plugin/v0/report` | 只给逐能力格作答计数、错因逐类计数与证据 ID；不写结论句与评价；不调模型 |
| `teach` | 返回 501 `METHOD_NOT_IMPLEMENTED`（等人设 Prompt 与 3-F 治理，另起任务） |

generate_variation、report 的规格见 `specs/adapter-zhixing-wenli-methods-sprint4/spec.md`（待人确认 #1–#4 按默认）。

## grade：作文五维（两段式）
1. **模型逐维定档**（`judged_by: rubric_model`）：Prompt `prompt:wenli-essay-grade@0.1.0` 移植自 legacy `buildGradePrompt` 与 `essay-rubric.ts`，
   去掉身份设定与人设语气、“主判 / 参考”、一类～五类档、45 分制估分、评语与升格建议、`needs_human_review`、`uncertainty_note`。
   模型只对四个维度（审题立意、内容材料、结构层次、语言表达）输出严格 JSON：每维 `level`（L1–L4）、`evidence_start / evidence_end`
   （作文中的字符位置）、`uncertainty_levels {low, high}`（最低档、最高档）。判分规则、题目与量表锚点放 system，**作文单独一条 user 消息**
   （JSON 字符串转义）。作文里出现指令特征（`INJECTION_PATTERNS`：“满分”“一类卷”“忽略以上”、JSON 字段名、花括号等，NFKC 归一后比对）
   时复核原因加 `low_confidence`。
2. **确定性后处理**（`src/core/essay-postprocess.ts`）：档位 → 分值（内容包量表的表）；不确定度档位 → `uncertainty {low, high}` 分值区间；
   证据位置必须 `0 ≤ start < end ≤ 字数`（按码点）；`low ≤ level ≤ high` 不满足、证据越界、缺维、模型输出不合 schema → 该维 `score: null`、
   `confidence: 0`，verdict `needs_review`（契约要求带 `verifier_boundary_case`）；四维都判定后才重算总分；**一类卷**（四维都是 L4）→
   `full_score_second_review`。
3. **卷面书写**需要卷面图片，路由接口只收文本 → 一律 `score: null`，列入 `excluded_dimensions`，复核原因 `dimension_unavailable`（待人确认 #4）。
4. **字数规范**（确定性，`judged_by: rubric_rule`、`rule_ref: wenli-convention-rules@0.1.0`、`subjective: false`、confidence 0.9，不调模型）：
   去掉空白后按码点计字数，达到作文题最低字数得 2 分；每少 50 字扣 1 分（不足 50 按 50 计），最多扣 2 分（legacy DEDUCTION_RULES，待人确认 #3）。

输出：四个模型维 `subjective: true`、confidence 0.4（≤ 0.5）、带 `uncertainty`；`human_review_required` 恒为真，`review_reasons` 至少含
`subjective_dimension`、`dimension_unavailable`（内容未准入时加 `content_not_admitted`，申诉加 `student_appeal`）；verdict 全部可判时为 `partial`
（作文没有对错，同契约示例）；`score.max_total = 50`、`calibrated: false`；整体 `uncertainty` 是四维区间之和加字数分（有维不可判时不给）。
`model_version` 用路由回传的快照名；`source_version = rubric:wenli-essay-5d@0.1.0;rubric:wenli-convention-rules@0.1.0;prompt:wenli-essay-grade@0.1.0;items:wenli-synthetic@0.1.0`。
证据：作文原文一条 `response_text`，每个可判维一条 `scoring_point_span`（只记位置，不复制原文）。结果里没有任何自由文本。
`uncertainty` 只在响应里，不落库（`grade_dimension` 没有这一列，加列是迁移，待人确认 #2）。

`context.data_class = L3` 的 grade 请求直接回 422 `INVALID_REQUEST`（detail `data_class L3 not supported for grade`），不送路由。
v1.0 请求带 `scoring_points` 时，`point_id` 与满分必须与量表六维（含字数维）一致，否则 400。

## diagnose：原生自适应测评（拍照为第三通道）
- 请求里的 `attempts` 必须是内容包里的客观题：选择题 `response.choice`（选项字母，或一位数字下标）按集合完全相等判；
  填空题 `response.text` 按 legacy `matchFill` 规范化匹配（去标点空白、大小写，`｜` 分隔的可接受答案，剥离括号说明）。
  对 → `demonstrated`，错 → `not_demonstrated`；`local_level` 是能力格的层；`algorithm_version: wenli-objective-diag@0.1.0`。
- **通道加权**（ADR 15；legacy 可靠性权重，未校准，待人确认 #6）：A 级（原生测评，键盘 / 点选）置信度 1.0；B 级（拍照已确认）0.7；
  **C 级不判分**，题目列入 `result.ocr_confirmation_required`，这道作答只记作答证据、不产生观测。
- **通道信息从哪里来**（契约没有字段，本适配器的约定，待人确认）：`DiagnoseRequest` / `AttemptInput` 都没有通道或证据等级字段，
  所以从平台随请求带来的 `prior_evidence` 读标记——`kind: interaction_signal`、`payload.signal_code` 为 `evidence_level_a` / `_b` / `_c`；
  或 `kind: ocr_transcription`（拍照识别）。与作答的对应：两边都有 `attempt_id` 按它，否则按 `item_ref` + `occurred_at`。
  没有标记 → A；有拍照识别证据但没有等级标记 → C；有拍照识别证据时最高只认 B；多个标记取最严。标记不被结果引用。
- `response.image_ref` / `transcript_ref`（原图、转写引用）、`paper_image_refs` 一律回 422（v1.0 为 `MODE_NOT_SUPPORTED`）：适配器不接触原图（ADR 15）。
- **缺口选择没有输出**（待人确认 #7）：`pickGapCells` 已移植（`src/core/coverage.ts`，有单测），但契约 `DiagnoseResult` 没有
  “下一轮建议测评的能力格”字段（`SkillObservation.evidence_ids` 至少一条，不能用观测表达“还没测”），按默认不输出、不改契约。
- 没有位次、提分，没有关于学生的陈述（规则 11）；`error_attributions` 为空（客观题没有确定性的错因）。

## plan：学科处方
- 平台 `mastery`（`MasterySnapshot`）→ 能力格状态：`transfer` / `retain`（星亮门槛）视为已掌握；排序用的数值取 `p_mastery`，
  缺省按状态映射（understand 0.3、execute 0.5、stable 0.75、transfer 0.9、retain 0.95）；`unseen` 与不认识的节点视为没有数据。
- 移植 `recommendTargets`：每域取最低的未掌握层，按离最优练习窗（0.4–0.6）的距离排序，空白起点优先、同距离按域顺序。
  `constraints.focus_skill_nodes` 里未掌握的已知格排最前（`focus_requested`）。
- 任务：有数据的格 `practice`（`zone_of_proximal_development`），没数据的格 `diagnose`（`cell_not_yet_assessed`）；
  `priority` 按顺序 1 → 0；`estimated_minutes` 取该格已准入题目用时之和与下限（diagnose 5、practice 10）的较大者；
  `item_refs` **只取内容包里已准入的题**（学生只能拿到已准入的题；当前内容包全部 pending_review，所以没有 `item_refs`）。
- 没有时间预算时取 3 个目标（legacy 默认）；有预算时按优先级累加，放不下的任务及其后的都不要。`exam_date` 目前不参与计算
  （需要“今天”才能算倒计时，会破坏确定性）。今日计划编排由平台做（A7）。
- 信封：`evidence_ids: []`、confidence 1、`model_version: algo:wenli-plan@0.1.0`、`human_review_required: false`。

## generate_variation：只从已准入题库选（`algo:wenli-item-select@0.1.0`）
- 候选必须同时满足：**同能力格**（源题的格，或请求的 `skill_node`；两者都给且不同 → 422）、**同题型**（题型编码严格相等：
  客观题取内容包 `type`，作文取 `essay_<genre>`；只给 `skill_node` 时没有参照题型，只按格选）、**已准入**、不是源题、
  不在 `exclude_item_refs`；编排层给了 `item_family` 时只在其中选（`difficulty` 取 `prior_difficulty`）。按题号排序、取前 `count` 道。
- `match`：`same_skill_node: true`；内容包没有错因标注，`same_error_category` 一律 false（`subject_error_category` 目前不参与筛选）。
- 空结果：未知能力格 → `skill_node_unknown`；有合格题但全被排除 → `all_excluded`；其他 → `no_admitted_candidate`。
  当前内容包全是 pending_review，所以线上一定返回空；选题逻辑由单测的合成“已准入”夹具覆盖。不调模型、不生成新题。
- 信封：`evidence_ids: []`、confidence 1、`human_review_required: false`、`semantics: formal`。

## report：只给计数与证据 ID（`algo:wenli-report@0.1.0`）
- 时间窗 `[from, to)`，按带时区的时刻比较；`to` 不晚于 `from` → 422。请求里出现别科引用 → 400 `SUBJECT_MISMATCH`。
- `skill_summary`：窗内作答类证据按 `attempt_id` 去重（没有时按 `evidence_id`），按题目所在能力格计数；`trend` 只比较平台给的
  `mastery_history` 状态序（窗前最后一条为基线：没有 → `new`，窗内没有记录 → `flat`），不重算掌握度。
- `error_distribution`：只列出现过的语文编码（平台层六类与阅读错因大类、标签，`docs/mapping/error-taxonomy.v0.9.yaml`），按映射表顺序；
  编码不是语文的（例如道法 `dingwei_cuo`）或跨科大类与映射表不符 → 422。
- **五维量表逐维统计做不了**：契约 `ReportFields` 没有量表维度的字段，`ReportRequest` 也不带判分维度得分；主观维的不确定度同样无处可放。
  本版只按能力格给作答计数（见 spec lessons 待人确认）。
- 没有 `highlight_codes`、等级、趋势描述或任何文本；三种受众输出相同字段。`evidence_ids` = 计入统计的窗内证据 + 错因记录引用的证据。
- 信封：confidence 取错因记录置信度的最小值（没有时 1）；计入的证据有未准入的 → `practice`；`human_review_required: false`。

## 与学习服务模型路由接口的关系
适配器**不持有任何模型供应商的地址或 Key**（ADR 04 第 1 条）。只有 grade 调模型，经学习服务的内部接口（约定同知行）：
```
POST {WENLI_ROUTE_URL}/internal/v1/model/route
Authorization: Bearer {WENLI_ROUTE_TOKEN}
{ "task": "T4", "data_class": "L2", "subject": "wenli", "prompt_template": "wenli-essay-grade", "prompt_version": "0.1.0",
  "messages": [...], "usage_id": <请求的 usage_id>, "idempotency_key": <由请求 key 与请求体派生的小写 UUIDv4>, "deadline_ms": 9000 }
→ 200 { "output", "model_version": "model:<供应商>/<模型>@<版本>", "source_version": "prompt:wenli-essay-grade@0.1.0", "model_mismatch" }
```
学习服务侧需要为文砺配路由客户端令牌（`LEARNING_ROUTE_CLIENT_WENLI_TOKEN`，令牌绑定学科）与 T4 的路由表，这不在本目录。

| 路由返回 | 适配器返回 |
|---|---|
| 200，结构正确 | 按上面两段式判分 |
| 超时（客户端 9.5 秒） | 504 `UPSTREAM_TIMEOUT`，`retryable: true` |
| 503 / 502 / 504、网络不通 | 503 `UPSTREAM_UNAVAILABLE`，`retryable: true` |
| 400 `ROUTE_REJECTED`、401 / 403 | 502 `UPSTREAM_UNAVAILABLE`，`retryable: false` |
| 200 但结构不对（`model_version` 不是 `model:` 形态、`source_version` 不是本 Prompt 等） | 502 `UPSTREAM_UNAVAILABLE` |
| 响应体超过 256KB（先按 content-length 预检，再流式累计；超限立刻中止连接） | 502 `UPSTREAM_UNAVAILABLE`，`retryable: true` |
| 启动时没配或配置不合格 | 503 `UPSTREAM_UNAVAILABLE`，`retryable: false`；diagnose、plan 照常 |

上游响应原文不写进错误体与日志；不跟随重定向（令牌不会被转发）。
**不走代理**：设置了 `NODE_USE_ENV_PROXY`（任何非空值），或命令行 / `NODE_OPTIONS` 里有 `--use-env-proxy` 时拒绝启动（`src/startup.ts`）；
`WENLI_ROUTE_TOKEN` 与 `WENLI_ADAPTER_SERVICE_TOKEN` 相同也拒绝启动。错误信息不含地址与令牌。

## 幂等
同知行：同 `idempotency_key` 同请求体（不含 `request_id`）→ 同一结果与同一批 `evidence_id`（plan 为同一批 `task_id`），模型只调一次；
同 key 不同请求体 → 409 `IDEMPOTENCY_CONFLICT`。缓存只存请求体指纹与判定结果（维度、档位、分值、字符位置、模型版本），不存作文原文与模型原文；
失败不缓存；缓存满时淘汰最早的已完成条目，全是进行中时回 429 `RATE_LIMITED`。带 `attempt_id` 时作答证据 ID 按作答本身派生。

## 环境变量
| 变量 | 说明 |
|---|---|
| `WENLI_ADAPTER_SERVICE_TOKEN` | 平台到适配器的服务间令牌（必填，≥ 32 字符，由部署环境注入） |
| `PORT` | 监听端口，默认 8789；只监听 127.0.0.1 |
| `WENLI_ROUTE_URL` | 学习服务内部路由接口地址：https，或本机回环的 http；不得带凭据、查询串 |
| `WENLI_ROUTE_TOKEN` | 调用该接口的服务间令牌（≥ 32 字符；必须与 `WENLI_ADAPTER_SERVICE_TOKEN` 不同，否则拒绝启动） |
| `NODE_USE_ENV_PROXY` | 不得设置（设置了就拒绝启动；`--use-env-proxy` 同） |
| `WENLI_CONTRACT_VERSION` | 能力声明报的默认契约版本（缺省 0.9.0） |
| `STARBUDDY_ALLOW_CANDIDATE_CONTRACT` | 设为 1 时接受候选版 1.0.0 |

## 内容（全部合成、非真题，待教研抽查与替换，待人确认 #5、#8）
`content/pack.json`：
- 2 道合成作文题（`wenli:synthetic-essay-01`–`02`，最低字数 600 / 500）；
- 五维量表 `wenli-essay-5d@0.1.0`：审题立意 10、内容材料 12、结构层次 10、语言表达 12、卷面书写 4，每维 L1–L4 的分值与锚点都是合成的；
  字数规范维 2 分（`wenli-convention-rules@0.1.0`），总分 50；
- 10 道合成客观题（`wenli:synthetic-q01`–`q10`，选择 6、填空 4，带标准答案），每题挂一个合成能力格；
- 10 个合成能力格（5 个域 × L1–L2，替代 legacy 28 格）：ID 为 `CHN.SYN.D<n>_L<m>`，符合 `kge.node_id`（迁移 0011，CHN 命名空间），
  同时是平台能力视图节点的 `local_id`；域带合成的 ROI 权重。
全部 `pending_review`，没有真实学生、学校、手机号（规则 8）。加载时做完整性检查，不合格拒绝启动。
`scripts/register-content.ts` 只输出登记 SQL（课程版本、能力格、题目；只增不改，题目一律 pending_review）。

## 边界
- **不读写任何平台表**（规则 1）；**判分只来自量表与确定性规则**（规则 3）；**不输出掌握度**（规则 2）；不写关于学生的陈述（规则 11）。
- 响应、日志、错误体里不出现作文题、量表锚点、客观题题面、选项、标准答案、模型原文；学生原文只出现在作答证据里。
- 不移植：人设语气、位次、提分、模型现场出题（KD-02）、掌握度回流（`reflowMastery`）与建议文案。

## 结构
```
src/core/essay-prompt.ts       作文定档 Prompt（移植 legacy buildGradePrompt / essay-rubric，去人设、估分与自由文本）与注入特征
src/core/essay-postprocess.ts  模型输出 schema 校验 + 确定性后处理（档位→分值、不确定度区间、证据位置、一类卷）+ 字数规范规则
src/core/objective.ts          客观题确定性判分（移植 legacy normalize.ts：matchFill、matchChoice / matchMulti）
src/core/channel.ts            作答证据等级（A / B / C）与通道权重
src/core/coverage.ts           可靠性权重与 pickGapCells（移植 legacy coverage.ts；缺口建议暂无输出字段）
src/core/adaptive.ts           recommendTargets（移植 legacy adaptive.ts）
src/adapter.ts                 方法实现与字段映射
src/route-client.ts            学习服务内部模型路由接口的客户端（同知行，任务 T4）
src/contract.ts / content.ts   契约加载与 ajv 校验（fail closed）/ 内容包加载与完整性检查
src/server.ts / main.ts / startup.ts   node:http 与服务间令牌 / 启动入口 / 启动前检查（不走代理、两个令牌不同）
scripts/register-content.ts    输出内容登记 SQL
tests/                         单测（node --test）；tests/route-stub.ts 是路由接口的本机 HTTP 桩（档位桩判定器）
conformance/                   一致性测试夹具与专用启动器（起路由桩再加载 src/main.ts）
```

## 常用命令
```bash
npm ci                                        # 安装依赖（ajv、ajv-formats、yaml；开发依赖 typescript）
npx tsc -p tsconfig.json && npm test          # 类型检查 + 单测
WENLI_ADAPTER_SERVICE_TOKEN=... WENLI_ROUTE_URL=... WENLI_ROUTE_TOKEN=... npm start
npm run register:sql > wenli-register.sql     # 生成登记 SQL；目标库由执行人决定
# 一致性测试（仓库根目录；路由接口用桩）：
python packages/contracts/conformance/cli.py --adapter-cmd "node --no-warnings conformance/harness.ts" \
  --adapter-cwd adapters/wenli --token-var WENLI_ADAPTER_SERVICE_TOKEN --fixtures adapters/wenli/conformance/fixtures.yaml
STARBUDDY_CONTRACT_VERSION=1.0.0 STARBUDDY_ALLOW_CANDIDATE_CONTRACT=1 python packages/contracts/conformance/cli.py \
  --adapter-cmd "node --no-warnings conformance/harness.ts --contract-version 1.0.0 --allow-candidate-contract" \
  --adapter-cwd adapters/wenli --token-var WENLI_ADAPTER_SERVICE_TOKEN --fixtures adapters/wenli/conformance/fixtures.yaml
```

## 待人确认
1. **spec #1–#8 按默认执行**：五维全部主观、整篇复核；`uncertainty` 不落库；加字数规范维（“每少 50 字扣 1 分”按不足一段也计 1 段）；
   卷面维不可判；10 个合成能力格；通道权重 1.0 / 0.7 未校准；缺口建议不输出；合成内容由工程起草。
2. **通道标记的约定**（上面 diagnose 一节）：契约没有通道 / 证据等级字段，本适配器从 `prior_evidence` 的 `interaction_signal`
   （`evidence_level_a/b/c`）与 `ocr_transcription` 读。平台要按这个约定传，或在契约里给 `AttemptInput` 加 `evidence_level`（必停项）。
3. **plan 的掌握状态映射**：已掌握 = `transfer` / `retain`；状态到数值的映射；任务分钟下限。均未经教研确认。
4. **待复核时的复核原因**：契约要求 `verdict = needs_review` 带 `verifier_boundary_case`，模型维不可判时照此填写，语义上不是“验证器边界”（同知行）。
5. **一致性夹具**由实现会话起草，需要独立会话复核、教研抽查；`make adapter-wenli-test`、`make conformance` 的文砺部分与学习服务端到端
   不在本目录，由 Makefile 与 `services/learning` 的任务补上。
6. 主观维置信度 0.4、字数维 0.9，未经校准。

## 依赖与许可证（规则 10）
与阿鸦、知行完全相同：ajv 8.20.0（MIT）、ajv-formats 3.0.1（MIT）、yaml 2.9.1（ISC）；开发依赖 typescript 5.9.3（Apache-2.0）、@types/node 22.20.4（MIT）。
传递依赖 fast-deep-equal、json-schema-traverse、require-from-string、undici-types 为 MIT，fast-uri 为 BSD-3-Clause。
