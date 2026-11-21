# 知行（道法）适配器

ADP-道法-01。实现学科插件 API v1.0（`plugin-api.v1.0.yaml`，2026-10-09 签字）与迁移期旧版 v0.9
（`packages/contracts/openapi/plugin-api.v0.9.yaml`）：按请求的 `context.contract_version` 校验与应答，其他版本返回 400 `CONTRACT_VERSION_UNSUPPORTED`。
版本开关与阿鸦相同：能力声明缺省报 1.0.0（签字后切换，`specs/contract-v1.0-signoff`），不需候选版开关；迁移期可用
`--contract-version 0.9.0`（或环境变量 `ZHIXING_CONTRACT_VERSION=0.9.0`）改报 0.9.0。候选版开关现在只管 1.1.0。
规格见 `specs/adapter-zhixing-sprint2/spec.md`（待人确认 #1–#6 按默认执行）。

| 接口 | 状态 |
|---|---|
| `GET /plugin/v0/capabilities` | 已实现 |
| `POST /plugin/v0/grade` | 道法 M3 主观题，F3 采分点逐点判分（两段式，见下）。只接 `zhixing:` 题与文字作答；历史不在契约 v1 范围 |
| `POST /plugin/v0/diagnose` | 只支持 `mode = process`：把请求里已有的判分证据汇总成能力点观测，不调模型 |
| `POST /plugin/v0/generate_variation` | 选题，不生成：只从 `admission = admitted` 的题里按同节点或同错因选；没有候选时返回空与 `empty_reason` |
| `POST /plugin/v0/plan` | 按“领域权重 × 失分 × 可提升”排序的任务列表（KGE 第八章）；领域权重是合成值、待教研校准；不调模型 |
| `POST /plugin/v0/report` | 只给逐维作答计数、四类失分归因计数与证据 ID；不写结论句与评价；不调模型 |
| `teach` | 返回 501 `METHOD_NOT_IMPLEMENTED`（等人设 Prompt 与 3-F 治理，另起任务） |

plan、report 的规格见 `specs/adapter-zhixing-wenli-methods-sprint4/spec.md`（待人确认 #1–#4 按默认）。

## plan：领域权重 × 失分 × 可提升（`algo:zhixing-plan@0.1.0`）
- 规划单元是能力视图节点（`zhixing:syn-dim-*`），只有挂了道法采分点的节点才有单元（`syn-dim-shenti` 目前没有题，不排）。
- **领域权重**：`content/plan-weights.json`（合成值，`calibration: pending_research_calibration`，notice 标“待教研校准”；
  `source_version` 带 `algo:zhixing-plan-weights@0.1.0-uncalibrated`）。采分点按第一个 `node_ids` 的前缀归到领域；
  节点的领域权重 = 该节点全部道法采分点的领域权重按分值加权平均。加载时要求每个道法采分点恰好落在一个领域，否则拒绝启动。
  **不改 `content/pack.json`**（采分点须与原子层一致，`make zhixing-rubric-check`）。
- **失分**：有 `p_mastery` 用 `1 − p_mastery`，没有按状态取默认值（unseen 0.5、understand 0.7、execute 0.5、stable 0.25）；
  **可提升**：unseen 1、understand 1、execute 0.8、stable 0.5、transfer / retain 0（已星亮，不排）。常数都待教研校准（`src/core/plan.ts`）。
- `priority` = 三者乘积（取 3 位小数）；同分按内容包节点顺序。没有掌握记录或 unseen → `kind: diagnose`、`not_yet_assessed`；
  其余 `practice`，领域权重 ≥ 0.2 → `high_exam_weight`，否则 `weak_mastery`。
- `item_refs` 只取已准入的道法题（至多 3 道），`estimated_minutes = 8 × max(1, 题数)`；当前内容包全是 pending_review，所以没有 `item_refs`。
- 约束：`focus_skill_nodes` 非空时只在其中排；有 `time_budget_minutes` 时按优先级累加，放不下的及其后的都不要；没有预算最多 8 个任务。
- **前置**：只认编排层给的 `node_path`（知识服务 `node.path` 只走强前置边）。观点之间只有 related 边时 `node_path` 为空，
  不产生任何 `prerequisite_task_ids`；有强前置边时，边的两端落在两个不同任务的知识节点上才连，成环的边跳过；前置不改变排序。
- `exam_config`、`learner_context`、`exam_date` 目前不参与计算，只参与请求体指纹。
- 信封：`evidence_ids: []`、confidence 1、`human_review_required: false`、`semantics: formal`。

## report：只给计数与证据 ID（`algo:zhixing-report@0.1.0`）
- 时间窗 `[from, to)`，按带时区的时刻比较；`to` 不晚于 `from` → 422。请求里出现别科引用 → 400 `SUBJECT_MISMATCH`。
- `skill_summary`：窗内作答类证据（`response_text` / `response_choice` / `ocr_transcription` / `asr_transcription`）按 `attempt_id`
  去重（没有时按 `evidence_id`），挂到题目涉及的全部维度节点；`trend` 是契约必填编码，只比较平台给的 `mastery_history` 状态序
  （窗前最后一条为基线：没有基线 → `new`，窗内没有记录 → `flat`，否则 up / down / flat），不重算掌握度。
- `error_distribution`：知行四类（`shenti_cuo`、`dingwei_cuo`、`zhuanhua_cuo`、`guifan_cuo`）逐类计数，四类都列出；
  错因编码不是这四类（例如语文的 `dingwei`）或跨科大类与映射表不符 → 422，不与语文口径混算。
- 没有 `highlight_codes`、等级、趋势描述或任何文本；三种受众输出相同字段。`evidence_ids` = 计入统计的窗内证据 + 错因记录引用的证据。
- 信封：confidence 取错因记录置信度的最小值（没有时 1）；计入的证据有未准入的 → `practice`；不判分，`human_review_required: false`。

## grade：两段式
1. **模型逐点判定**（`judged_by: rubric_model`）：Prompt `prompt:zhixing-m3-grade@0.2.0` 移植自 legacy `buildM3GradePrompt`，
   去掉人设语气与总评等自由文本；模型只输出 JSON（每点 `point_id`、判定档、`score_awarded`、证据在作答中的字符位置、归因编码）。
   判分规则、题目与 Rubric（含每点可给分值）放 system；**学生作答单独一条 user 消息**，只含 JSON 字符串转义后的作答。
   作答里出现指令特征（`INJECTION_PATTERNS`：“满分”“判…full/满分/正确”“忽略以上/前面”、`score_awarded`、`judgment`、`point_id`、花括号等，
   NFKC 归一后比对）时，复核原因加 `low_confidence`。
2. **确定性后处理**（`src/core/m3-postprocess.ts`）：模型输出不合 schema → 整题待复核；逐点 clamp 到 [0, 分值]、取 0.5 倍数；
   得分**不在该点 `partial_credit_rule.allowed_scores` 里** → 该点转复核（不采用模型分数）；得分点的证据位置必须落在作答原文内且非空；
   判定档与得分、归因按一致性表 `JUDGMENT_CONSISTENCY` 执行（full ⇒ 满分且归因 none；material_unconverted ⇒ 0 < 分 < 满分且归因 zhuanhua_cuo；
   partial ⇒ 0 < 分 < 满分且有失分归因；miss ⇒ 0 分且有失分归因）；得分 > 0 的点，**证据片段必须包含该点 keyword_family 任一档的任一词**
   （兜住 Prompt 注入）。不满足的点 `achieved = null`；全部判定后重算总分；满分 → `full_score_second_review`。

输出：每维 `judged_by: rubric_model`、`subjective: true`、置信度 0.4（≤ 0.5）；`human_review_required = true`，`review_reasons` 含
`subjective_dimension`；`model_version` 用路由回传的快照名；`source_version = rubric:<rubric_version>;prompt:zhixing-m3-grade@0.2.0;items:<内容包>@<版本>`。
证据：作答原文一条 `response_text`，每个得分点一条 `scoring_point_span`（只记位置，不复制原文）。结果里没有任何自由文本。

请求的 `rubric_version` 是契约 `VersionedName`（例如 `zhixing-synthetic@0.1.0`，不带 `rubric:` 前缀），必须等于内容包里该题的版本，否则 400。
`context.data_class = L3` 的 grade 请求直接回 422 `INVALID_REQUEST`（detail `data_class L3 not supported for grade`）：路由表里判分任务只接到 L2，
不送路由（否则会变成 502）。

## 与学习服务模型路由接口的关系
适配器**不持有任何模型供应商的地址或 Key**（ADR 04 第 1 条）。模型调用只经学习服务的内部接口：
```
POST {ZHIXING_ROUTE_URL}/internal/v1/model/route
Authorization: Bearer {ZHIXING_ROUTE_TOKEN}
{ "task": "T2", "data_class": "L2", "subject": "zhixing", "prompt_template": "zhixing-m3-grade", "prompt_version": "0.2.0",
  "messages": [...], "usage_id": <请求的 usage_id>, "idempotency_key": <由请求 key 与请求体派生的小写 UUIDv4>, "deadline_ms": 9000 }
→ 200 { "output", "model_version": "model:<供应商>/<模型>@<版本>", "source_version": "prompt:zhixing-m3-grade@0.2.0", "model_mismatch" }
```
学习服务把请求交给平台模型路由（`ModelRouter.route`），由路由按数据级别选境内模型、记调用账。这个接口由学习服务一侧实现，
它的 OpenAPI 进 `packages/contracts/openapi/learning-internal.v1.yaml` 属于契约变更（spec 待人确认 #1，必停项），不在本目录。

| 路由返回 | 适配器返回 |
|---|---|
| 200，结构正确 | 按上面两段式判分 |
| 超时（客户端 9.5 秒） | 504 `UPSTREAM_TIMEOUT`，`retryable: true` |
| 503 / 502 / 504、网络不通 | 503 `UPSTREAM_UNAVAILABLE`，`retryable: true` |
| 400 `ROUTE_REJECTED`、401 / 403 | 502 `UPSTREAM_UNAVAILABLE`，`retryable: false` |
| 200 但结构不对（`model_version` 不是 `model:` 形态、`source_version` 不是本 Prompt 等） | 502 `UPSTREAM_UNAVAILABLE` |
| 响应体超过 256KB（先按 content-length 预检，再流式累计；超限立刻中止连接，不看状态码） | 502 `UPSTREAM_UNAVAILABLE`，`retryable: true` |
| 启动时没配或配置不合格 | 503 `UPSTREAM_UNAVAILABLE`，`retryable: false`；其他方法照常 |

上游响应原文不写进错误体与日志；不跟随重定向（令牌不会被转发）。
**不走代理**：设置了 `NODE_USE_ENV_PROXY`（任何非空值），或命令行 / `NODE_OPTIONS` 里有 `--use-env-proxy` 时拒绝启动（`src/startup.ts`，
退出码 1，错误信息不含地址与令牌）；`ZHIXING_ROUTE_TOKEN` 与 `ZHIXING_ADAPTER_SERVICE_TOKEN` 相同也拒绝启动。

## 幂等
同 `idempotency_key` 同请求体（不含 `request_id`）→ 同一结果与同一批 `evidence_id`，模型只调一次（并发请求共享一次调用）；
同 key 不同请求体 → 409 `IDEMPOTENCY_CONFLICT`。缓存只存请求体指纹与判定结果（点号、分数、字符位置、模型版本），
不存学生原文与模型原文；失败不缓存。缓存满时淘汰最早的**已完成**条目，进行中的条目不淘汰；全是进行中时新请求回
429 `RATE_LIMITED`（`retryable: true`，稍后用同一个 key 重试）。请求带 `attempt_id`（v1.0 必带）时，作答证据 ID 按作答本身派生：同一道作答在 grade 与 diagnose
里是同一个 `evidence_id`（契约“证据只记一次”）。

## 环境变量
| 变量 | 说明 |
|---|---|
| `ZHIXING_ADAPTER_SERVICE_TOKEN` | 平台到适配器的服务间令牌（必填，≥ 32 字符，由部署环境注入） |
| `PORT` | 监听端口，默认 8788；只监听 127.0.0.1 |
| `ZHIXING_ROUTE_URL` | 学习服务内部路由接口地址：https，或本机回环的 http；不得带凭据、查询串 |
| `ZHIXING_ROUTE_TOKEN` | 调用该接口的服务间令牌（≥ 32 字符；必须与 `ZHIXING_ADAPTER_SERVICE_TOKEN` 不同，否则拒绝启动） |
| `NODE_USE_ENV_PROXY` | 不得设置（设置了就拒绝启动；`--use-env-proxy` 同） |
| `ZHIXING_CONTRACT_VERSION` | 能力声明报的默认契约版本（缺省 1.0.0；迁移期可设 0.9.0） |
| `STARBUDDY_ALLOW_CANDIDATE_CONTRACT` | 设为 1 时接受候选版 1.1.0（1.0.0 已签字，不需要） |

## 内容
`content/pack.json` 是 4 道**合成**道法主观题（`zhixing:synthetic-m3-01`–`04`，标注“合成、非真题”，材料与人物都是虚构），
题目一律 `pending_review`，`rubric_version = zhixing-synthetic@0.1.0`。采分点字段对齐 KGE `kge.scoring_point`：`point_id`（小写）、
`score`、`node_ids`（合成的知识节点 ID，格式同 `kge.node_id`，`MORAL.SYN.…`，1–8 个）、`subject_dimension`、
`keyword_family`（`core` / `accepted` / `colloquial` 三档）、`required_logic`、`material_linkage`、
`partial_credit_rule`（结构化：`mode`、`allowed_scores`、`description`；`allowed_scores` 升序、含 0 与满分、0.5 倍数、不超过分值，
`all_or_nothing` 只允许 0 与满分；加载时校验）、`common_errors`（合成的错误形态）。`skill_node` 是五个判分维度各一个的合成节点（审题、定位、转化、规范、时政），
正式坐标待教研定（spec 待人确认 #3）。legacy 的内置题、样卷、定位题、M1 案例都没有进仓库（来源待核）。

## 边界
- **不读写任何平台表**（规则 1）：证据与推断放在响应里，由学习服务落库。
- **判分只来自 Rubric**（规则 3）：模型逐点判定 + 确定性后处理；人设文本不参与。
- **不输出掌握度**（规则 2）；diagnose 只给观测，不写关于学生的陈述（规则 11）。
- 响应、日志、错误体里不出现题面、材料、Rubric、模型原文；学生原文只出现在作答证据里。

## 结构
```
src/core/m3-prompt.ts        判分 Prompt（移植 legacy buildM3GradePrompt，去人设与自由文本）
src/core/m3-postprocess.ts   模型输出 schema 校验 + 确定性后处理（移植 legacy m3/grade 的 clamp、0.5 倍数、重算、满分复核）
src/core/ability-signal.ts   m3ToDimensionRates（移植；暂未接入 diagnose，见“待人确认”）
src/core/locate.ts           scoreLocate 的对错判定（移植，去掉 coaching 文案与快慢比较；M2 不在本任务范围）
src/adapter.ts               方法实现与字段映射
src/route-client.ts          学习服务内部模型路由接口的客户端
src/contract.ts              直接加载契约 YAML，ajv 校验请求与响应；响应不合契约回 500（fail closed）
src/content.ts / taxonomy.ts 内容包加载与完整性检查 / 错因映射表（docs/mapping/error-taxonomy.v0.9.yaml）
src/server.ts / main.ts      node:http，服务间令牌，路径前缀 /plugin/v0 / 启动入口
src/startup.ts               启动前检查：不走代理、两个服务间令牌不同
（内容登记 SQL 的生成已迁到 tools/content-register/zhixing.ts，W13 T11；题目一律 pending_review，只增不改；npm run register:sql 照旧可用）
tests/                       单测（node --test）；tests/route-stub.ts 是路由接口的本机 HTTP 桩
conformance/fixtures.yaml    一致性测试夹具；conformance/harness.ts 是一致性测试专用启动器（起路由桩再加载 src/main.ts）
```

## 常用命令
```bash
npm ci                                        # 安装依赖（ajv、ajv-formats、yaml；开发依赖 typescript）
npx tsc -p . && npm test                      # 类型检查 + 单测
ZHIXING_ADAPTER_SERVICE_TOKEN=... ZHIXING_ROUTE_URL=... ZHIXING_ROUTE_TOKEN=... npm start
npm run register:sql > zhixing-register.sql   # 生成登记 SQL；目标库由执行人决定
# 一致性测试（仓库根目录；路由接口用桩）：
python packages/contracts/conformance/cli.py --adapter-cmd "node --no-warnings conformance/harness.ts" \
  --adapter-cwd adapters/zhixing --token-var ZHIXING_ADAPTER_SERVICE_TOKEN --fixtures adapters/zhixing/conformance/fixtures.yaml
STARBUDDY_CONTRACT_VERSION=0.9.0 python packages/contracts/conformance/cli.py \
  --adapter-cmd "node --no-warnings conformance/harness.ts --contract-version 0.9.0" \
  --adapter-cwd adapters/zhixing --token-var ZHIXING_ADAPTER_SERVICE_TOKEN --fixtures adapters/zhixing/conformance/fixtures.yaml
```

## 待人确认
1. **diagnose 的输入**：契约 `DiagnoseRequest` 没有携带既往逐点判分（得分与归因）的字段，`prior_evidence` 里只有作答与采分点片段（不带分数），
   所以 diagnose 只能按“有无命中片段”给 `partial` / `inconclusive`，移植的 `m3ToDimensionRates` 暂时喂不进去。要按它算，需要契约加字段（必停项）。
2. **待复核时的复核原因**：契约规定 `verdict = needs_review` 必须带 `verifier_boundary_case`，维度分为 null 必须带 `dimension_unavailable`；
   模型输出不合格时本适配器照此填写，语义上不是“验证器边界”。是否给模型判分增加专门的原因码，由契约评审决定。
3. **一致性夹具**由实现会话起草，需要独立会话复核、教研抽查。`make adapter-zhixing-test` 与 `make conformance`（v0.9、v1.0）已并入 `make check`；端到端在学习服务的 `tests/test_e2e_zhixing.py`。
4. 主观维度置信度取 0.4、错因置信度 0.4、diagnose 观测置信度 0.3 / 0.1，都未经校准。

## 依赖与许可证（规则 10）
与阿鸦完全相同：ajv 8.20.0（MIT）、ajv-formats 3.0.1（MIT）、yaml 2.9.1（ISC）；开发依赖 typescript 5.9.3（Apache-2.0）、@types/node 22.20.4（MIT）。
传递依赖 fast-deep-equal、json-schema-traverse、require-from-string、undici-types 为 MIT，fast-uri 为 BSD-3-Clause。
