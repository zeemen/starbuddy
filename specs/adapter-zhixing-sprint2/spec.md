# ADP-道法-01：知行（道法）适配器 grade（F3 采分点）、diagnose、generate_variation

状态：**已确认（2026-09-29，用户“按默认建议，开始实现”）**，待人确认 #1–#6 按默认执行；实现记录见 lessons.md
任务包：开发计划 v2.0 冲刺 2（W5–W6）D 流“ADP-道法-01：grade（F3 采分点）、diagnose、generate_variation”；
验收（道法部分）“一道道法主观题逐点判分写入平台，带 evidence_ids 与 confidence”。语文 ADP-语文-01 另起 spec。
关联：
- 盘点：`specs/inventory-zhixing/`（inventory.md、decisions.md A1–A6 与 KD-01~10、adapter-design.md v0.2）；
- 契约：`packages/contracts/openapi/plugin-api.v1.0.yaml`（候选版，GradeRequest / GradeResult / DimensionScore / ScoringPoint / GenerateVariation*）与 v0.9；
- 模板：`adapters/aya/`（服务端、契约自检、幂等、内容登记脚本、conformance 夹具）；
- 模型路由：`services/learning/learning/model_router/`（冲刺 1-F，ADR 04）；KGE `kge.scoring_point`（迁移 0011）；
- 规则：CLAUDE.md 规则 1、3、4、8、13、14。

## 目标
道法第一次以插件身份接入平台：一道主观题按采分点逐点判分，结果带 evidence_ids、confidence、source_version、model_version、human_review_required，
经学习服务落库；诊断只输出观测；变式只从已准入题库选题。模型调用全部经平台模型路由，适配器不持有任何供应商 Key。

## 现状（调研结论）
- `adapters/` 只有阿鸦；**仓库里没有 `legacy/zhixing`**，道法外壳与判分代码在 `legacy/wenli/初中语文_AI_class/wenli/apps/daofa` 与同仓 `packages/shared/lib`（只读）。
- legacy F3：逐点判定整体由模型完成（Prompt 里给关键词，代码层没有规则匹配）；服务端做 clamp、0.5 倍数、总分重算、满分强制二评；`model:"routed"`、Prompt 无版本、无幂等、无计量（KD-07 等）。
- **没有已准入的道法内容**：legacy 的 2 道内置题与 3 份样卷“来源待核”，不作金标准（A4）；契约示例已用合成题 `zhixing:synthetic-m3-01`。
- 模型路由是学习服务里的 Python 进程内模块，TypeScript 适配器调不到；ADR 04 第 1 条要求适配器不能自己持有 Key 或 BaseURL。
- `platform.grade_dimension.verifier_reason_codes` 白名单只有阿鸦的六种（0005）。

## 设计要点

### 1. 适配器骨架（`adapters/zhixing/`，按阿鸦模板）
- node:http 服务，Bearer 令牌先 SHA-256 再定长比较，前缀 `/plugin/v0/`；ajv 直接加载契约 YAML 做请求校验与响应自检（不合契约回 500，fail closed）；
  同 idempotency_key 不同请求体回 409；只依赖 ajv、ajv-formats、yaml（与阿鸦一致，不新增依赖）。
- 契约版本与阿鸦同一套开关：默认 v0.9，`STARBUDDY_CONTRACT_VERSION=1.0.0` 且允许候选版时走 v1.0。
- 没实现的方法（teach、plan、report）回契约规定的“未实现”，不伪造结果。

### 2. 内容：只用合成题（规则 8、14）
- `adapters/zhixing/content/pack.json`：合成道法主观题 3–5 道（`zhixing:synthetic-m3-0N`），每题带采分点，字段对齐 KGE `scoring_point`：
  `point_id`、`score`、`subject_dimension`、`keyword_family`（三档）、`required_logic`、`material_linkage`、`partial_credit_rule`、`common_errors`（合成，不写真实作答，规则 13）；
  另有 `rubric_version`（例如 `rubric:zhixing-synthetic@0.1.0`）。
- 题目状态一律 `pending_review`：登记脚本 `scripts/register-content.ts` 只输出 SQL（与阿鸦同法），准入由教研在审核台做。
- legacy 的内置题、样卷、定位题、M1 案例都不进仓库（来源待核）。

### 3. grade（F3 采分点逐点判分）
- 只接 `zhixing:` 题；`rubric_version` 必须等于内容包里该题的版本，否则 400。
- **两段式**，判分只来自 Rubric（规则 3）：
  1. **模型逐点判定**（`judged_by: rubric_model`）：经平台模型路由调用（见第 5 节），任务类型 T2、数据级别 L2（学生作答原文）；
     Prompt 从 legacy `buildM3GradePrompt` 移植，去掉 PERSONA_TONE、去掉总评类自由文本，带版本号 `prompt:zhixing-m3-grade@0.1.0`；
     模型只输出结构化 JSON（每点：判定档、得分、证据句在作答中的起止位置、归因类别），按 schema 校验，不合格即判“待复核”。
  2. **确定性后处理**（从 legacy 移植）：逐点得分 clamp 到 [0, 分值]、取 0.5 的倍数、重算总分；证据句必须能在作答原文里找到（找不到该点记待复核）；
     满分、归因“无”却得 0 分等矛盾情况强制复核。
- 输出：`dimensions` 每维 `judged_by: rubric_model`、标主观、confidence ≤ 0.5（契约与 0005 的 CHECK）；`scoring_points` 逐点给出 `achieved`、`points_awarded` 与证据；
  `human_review_required: true`，`review_reasons` 含 `subjective_dimension`（决定 A2：M3 一律低置信并复核）；
  `model_version` 取路由返回的真实快照名，`source_version` 写 `rubric:…;prompt:…`；**不返回任何自由文本**（GradeResult 不允许）。
- 证据：证据句只记作答里的位置（`scoring_point_span`），不复制原文到结果里。
- 可返回 202 异步（契约 JobAccepted / JobStatus）：同 key 重试返回同一 job_id 与同一批 evidence_id；默认同步，超时预算按契约 grade 10 秒。

### 4. diagnose 与 generate_variation
- **diagnose**（mode = process，对应知行 F1+F3）：移植 legacy 纯函数 `m3ToDimensionRates`、`scoreLocate`，只把已有判分结果汇总成 SkillObservation（观测，不下结论），
  不调模型、不写任何关于学生的陈述（规则 11）；`scoreLocate` 里违反禁语的 coaching 文案不移植。skill_node 坐标用内容包里的合成节点，正式坐标待教研定（盘点 #7/A1）。
- **generate_variation**：语义是**选题不生成**。只从内容包里 `admission = admitted` 的题中按同节点或同错因选；没有合格候选时返回空结果与 `empty_reason`。
  legacy 的模型命题接口（KD-02）不移植。仓库里的题都是 pending，所以线上默认返回空；测试用单独的合成“已准入”夹具验证选题逻辑。

### 5. 模型调用：经学习服务的内部路由接口
- 学习服务新增内部接口 `POST /internal/v1/model/route`（服务间令牌鉴权，只监听内网），把请求原样交给 `ModelRouter.route()`，返回 output、model_version、source_version、model_mismatch；
  适配器只配这个接口的地址与令牌，不持有任何供应商信息。
- 这个接口是平台内部接口，OpenAPI 写进 `packages/contracts/openapi/learning-internal.v1.yaml` 的新路径（**契约变更，必停项**，见待人确认 #1）。
- 测试环境用模型路由的假供应商：按用例返回预设的结构化 JSON，从而覆盖“正常逐点判定”“JSON 不合格”“证据句找不到”“超时”“换备选”几种情况。

### 6. 测试与验收演示
- 适配器单测（`node --test`）：请求校验、rubric_version 不符、后处理（clamp、0.5 倍数、重算、证据位置核对、满分复核）、幂等 409、响应自检、未实现方法、禁语文案不出现。
- conformance：`adapters/zhixing/conformance/fixtures.yaml`，v0.9 与 v1.0 都跑，并入 `make conformance`；变异并入 `conformance-mutation`。
- 端到端（学习服务测试里）：一道合成道法主观题经学习服务 → 适配器 → 学习服务内部路由（假供应商）→ 判分写入 `platform.grade_result` / `grade_dimension` / 证据表，
  断言 evidence_ids、confidence ≤ 0.5、human_review_required、model_version 是真实快照名（v2.0 验收）。
- `make adapter-zhixing-test` 并入 `make check`。

## 范围
### 做
1. `adapters/zhixing/` 骨架、三个方法、合成内容包与登记脚本、单测、conformance 夹具。
2. 学习服务内部路由接口与测试（含 OpenAPI 路径，必停项）。
3. 端到端测试；Makefile；README；lessons。

### 不做
- 任何真实供应商、真实题目或样卷；金标准与一致率评测（教研提供合成标注集后另做，规则 14）。
- M2 定位题判分（`MORAL.LOCATE_BOOK` 学科码待映射表签字，对照文档 :52）、M1、历史判分（契约 v1 不含，A5）、teach / plan / report。
- 道法 Rubric 入 `kge.scoring_point`（KGE S4，W9–10）；本任务只让内容包字段与它对齐。
- 上线门禁类的阻断项（A6：命题接口门禁、错误透传等随上线处理；本适配器不移植那些接口）。

## 验收标准（可测试）
- [ ] 一道合成道法主观题逐点判分并写入平台：带 evidence_ids、confidence（主观维 ≤ 0.5）、source_version、真实 model_version、human_review_required = true 与 reasons。
- [ ] 判分结果只来自 Rubric 与确定性后处理：模型输出不合 schema、证据句找不到、满分等情况都转复核；结果里没有自由文本。
- [ ] 适配器不持有任何供应商地址或 Key，模型调用只经学习服务内部路由接口（测试断言）。
- [ ] diagnose 只输出观测；generate_variation 只选已准入题，没有候选时给 empty_reason。
- [ ] conformance（v0.9、v1.0）与变异通过；严格 `make check` 通过；安全审查、契约审查意见处理完。

## 涉及的必停项（见 CLAUDE.md）
- **packages/contracts**：学习服务内部路由接口的 OpenAPI 路径（待人确认 #1）。PR 首行 `[NEEDS-HUMAN]`。
- **判分逻辑**：M3 判分 Prompt 的移植与后处理规则（clamp、0.5 倍数、复核条件）属于判分逻辑，需要人确认口径（待人确认 #2）。
- migrations：默认不改（待人确认 #5）；人设 Prompt：否（判分 Prompt 去掉了人设语气）；第三方与模型供应商：否（只用假供应商）。

## 依赖与输入
- 插件契约 v0.9 / v1.0 候选；模型路由（1-F）；legacy 道法代码（只读移植）；合成内容由本任务起草，教研后续替换。

## 待人确认
1. **模型调用路径**：默认学习服务开内部接口 `POST /internal/v1/model/route`，适配器经它调用路由（契约变更，必停项）。
   另一个选择：本冲刺 grade 不调模型，只做 `rubric_rule` 关键词族匹配 + 一律复核，模型判分等路由接口签字后再接。
2. **判分口径**：沿用 legacy 的 clamp、0.5 倍数、满分强制复核，外加“证据句必须在作答里找得到”；M3 一律低置信、一律复核（A2）。是否还要“主备双模型不一致转复核”（ADR 04 增补第 8 条）留给判分 spec。
3. **诊断的能力坐标**：默认用内容包里的合成节点（五个判分维度各一个），正式坐标（五维还是过程四步）待教研定（盘点 #7/A1）。
4. **合成内容由谁写**：默认由工程起草 3–5 道合成道法主观题与采分点（明确标“合成、非真题”），教研抽查；真题与样卷来源核实后由教研替换。
5. **reason code 白名单**：`grade_dimension.verifier_reason_codes` 只收阿鸦的六种。本适配器不走 verifier 路径，默认不改迁移；复核原因走信封的 `review_reasons`。如果要让道法的原因码进平台表，需要迁移（必停项）。
6. **契约版本**：默认与阿鸦一样同时支持 v0.9 与 v1.0 候选；另一个选择是只做 v1.0。
