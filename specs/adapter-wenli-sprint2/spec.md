# ADP-语文-01：文砺（语文）适配器 grade（作文五维）、diagnose（原生自适应测评，拍照为第三通道）、plan

状态：**已确认（2026-09-29，用户“直接按默认方案实现，不用等我看 spec”）**，待人确认 #1–#8 按默认执行；实现记录见 lessons.md。
PR 首行 `[NEEDS-HUMAN]`（判分逻辑、判分 Prompt；见“必停项”），人事后补签。
任务包：开发计划 v2.0 冲刺 2（W5–W6）D 流“ADP-语文-01：diagnose 改接原生自适应测评（OCR 为第三通道）、grade（五维量表，主观维带不确定度）、plan”；
验收（语文部分）“一篇作文五维结果写入平台，带 evidence_ids 与 confidence”。
关联：盘点 `specs/inventory-wenli/`（决定 A1–A7，KD-01~06）；契约 `plugin-api.v1.0.yaml` / v0.9（DimensionScore、Uncertainty、DiagnoseRequest、PlanRequest）；
ADR 15（输入通道与作答证据）；ADP-道法-01（`specs/adapter-zhixing-sprint2/`，骨架、合成内容、路由接口的做法全部沿用）。

## 现状（调研结论）
- legacy 作文批改：一次模型调用给五维等级（L1–L4）与“主判 / 参考”标记、一类～五类档、45 分制估分与区间、`uncertainty_note` 文字；有卷面图时再调视觉模型改写“卷面书写”维；`model:"routed"`；量表没有版本、没有签字。
- legacy 原生自适应测评：选缺口能力格 `pickGapCells` 与练习目标 `recommendTargets` 是**确定性纯函数**；题目由模型现场出（KD-02，不入库、不审核）；客观题判分是确定性匹配。
  “OCR 为第三通道”即信号来源三级：原生测评 1.0、引导录入 0.75、拍照确认 0.7（可靠性权重）。
- legacy plan：模型生成三层处方，含“预计提分”（须去掉）；可复用的确定性部分是 `recommendTargets`、阶段周期与考试权重。
- 平台：`grade_dimension` 没有 `uncertainty` 列；`verifier_reason_codes` 只有数学的六个；没有 plan 表；路由接口 messages 只收文本（不能传图片）。
- 仓库里没有合成的语文题、作文题或量表数据包。

## 设计要点
### 1. 骨架与内容（照 ADP-道法-01）
- `adapters/wenli/`：node:http、ajv 契约自检（fail closed）、幂等 409、令牌 SHA-256 定长比较、v0.9 与 v1.0 候选版开关、只依赖 ajv / ajv-formats / yaml；
  不走代理（检测到代理开关拒绝启动）、路由令牌与服务令牌相同拒绝启动；teach / generate_variation / report 回契约规定的未实现。
- 合成内容包 `content/pack.json`（标“合成、非真题”，全部 pending_review，登记脚本只输出 SQL）：
  - 作文题 2 道（`wenli:synthetic-essay-0N`）与五维量表 `wenli-essay-5d@0.1.0`：五维 = 审题立意、内容材料、结构层次、语言表达、卷面书写，每维 L1–L4 四档与分值、锚点描述均为合成；
  - 客观题 8–12 道（选择、填空，`wenli:synthetic-qNN`），每题挂合成能力节点（KGE 命名空间 CHN，如 `CHN.SYN.*`）与标准答案；
  - 合成能力格 8–12 个（替代 legacy 28 格，带域与 ROI 权重），正式格子待教研（盘点 A1、KGE-00 待核）。

### 2. grade（作文五维）
- 两段式，判分只来自 Rubric 与确定性后处理（规则 3）：
  1. 模型逐维定档（经学习服务内部路由接口，任务 T4、数据级别 L2，Prompt `wenli-essay-grade@0.1.0` 从 legacy `buildGradePrompt` 移植、去掉人设语气与“主判 / 参考”区分、去掉位次与提分）：
     每维只输出 `level`（L1–L4）、`evidence_start/end`（作文原文里的位置）、`uncertainty_levels`（最低档、最高档）；作文单独放一条 user 消息，特征词（“满分”“忽略”等）触发复核原因；
  2. 确定性后处理：档位 → 分值（量表里的表）、不确定度档位 → `uncertainty {low, high}` 分值区间；证据位置必须在原文内；重算总分；
     卷面书写维需要图片，路由接口只收文本 → 该维 `score: null`、原因 `dimension_unavailable`；一类卷（最高档）强制复核。
- 输出：四个可判维 `judged_by: rubric_model`、主观、confidence ≤ 0.5（契约与 0005 CHECK），带 `uncertainty`；一律 `human_review_required` 且含 `subjective_dimension`；无自由文本。
- 另有一个确定性维度（待人确认 #3）：**字数规范**（`rubric_rule`、`rule_ref: wenli-convention-rules@0.1.0`）按作文题要求的最低字数判，不调模型，confidence 0.9。
- `uncertainty` 只在响应里，不落库（`grade_dimension` 没有列；加列是迁移，必停项，待人确认 #2）。

### 3. diagnose（原生自适应测评，拍照为第三通道）
- mode = gap_test / intake：请求里的 attempts 都是内容包里的客观题；**确定性判分**（选择按选项、填空按规范化匹配，移植 legacy quiz grade）→ SkillObservation；
  观测置信度按作答通道加权：键盘（原生测评）1.0、拍照已确认（B 级）0.7；**C 级或需要确认的拍照作答列入 `ocr_confirmation_required`，不判分**（契约与 ADR 15）。
- 缺口选择：移植 `pickGapCells`（确定性），在结果里给出下一轮建议测评的能力格（用契约已有的结构，不新增字段；没有合适字段就不输出、记 lessons）。
- 不调模型、不出题（KD-02 不移植）、没有位次与提分、没有关于学生的陈述（规则 11）。

### 4. plan（学科处方）
- 确定性：输入 mastery 列表与 constraints（时间预算、考试日期、重点节点）→ 移植 `recommendTargets` 选目标（每域一个、取最低未掌握层）→ 生成 PlanTask
  （kind、skill_nodes、item_refs 只取内容包里的题、priority、estimated_minutes、rationale_code 编码）；不调模型、不写文字、不含预计提分；
  时间预算内按优先级截断；只输出学科处方，今日计划编排由平台做（A7）。

### 5. 测试与验收
- 适配器单测（node --test）：后处理（档位映射、不确定度区间、证据位置、卷面维不可判、一类卷复核、字数规则）、注入、路由客户端、幂等、代理与令牌启动检查、
  diagnose 的确定性判分与通道加权、C 级进 `ocr_confirmation_required`、plan 的确定性与时间预算、禁语与供应商信息扫描。
- conformance（v0.9、v1.0）并入 `make conformance`；`make adapter-wenli-test` 并入 `make check`。
- 端到端（学习服务测试）：一篇合成作文经学习服务 → 文砺适配器 → 路由接口（假供应商）→ 平台库；断言 evidence_ids、四个主观维 confidence ≤ 0.5、字数维 rubric_rule、真实 model_version、复核标记。

## 范围
### 做
`adapters/wenli/` 骨架与三个方法、合成内容包与登记脚本、单测与 conformance、学习服务端到端、Makefile、README、lessons。
### 不做
- 真实题目、量表与作文；金标准（A6，教研合成标注）；卷面书写的图片判读（需要多模态路由，另起任务）；OCR 识别本身（平台输入通道，ADR 15）；
- generate_variation、teach、report；模型现场出题（KD-02）；`uncertainty` 落库（迁移，待人确认 #2）；20 格 / 28 点旧编码映射（KGE-00 待核）。

## 验收标准（可测试）
- [x] 一篇合成作文五维结果写入平台：带 evidence_ids、四个主观维 confidence ≤ 0.5 与 uncertainty、真实 model_version、复核标记；卷面维不可判时带 dimension_unavailable。
- [x] 判分只来自量表与确定性后处理；无自由文本；注入与越界都转复核。
- [ ] diagnose 客观题确定性判分、按通道加权，C 级作答不判分并列入 ocr_confirmation_required；plan 确定性、不含提分。
  （部分满足：单测成立；经平台端到端时通道标记走不通，见 lessons“审查意见处理”第 1 条，另起任务）
- [x] 适配器不持有任何供应商信息；conformance v0.9 / v1.0 通过；严格 `make check` 通过；安全审查、契约审查意见处理完。

## 涉及的必停项
- 判分逻辑：作文判分 Prompt、档位到分值的映射、复核条件、字数规则（待人确认 #1、#3）。PR 首行 `[NEEDS-HUMAN]`。
- 不改 contracts、migrations；不新增第三方与模型供应商（只用假供应商）；人设 Prompt：否（去掉了人设语气）。

## 待人确认（默认已执行）
1. 五维都由模型定档时全是主观维（契约不认 legacy 的“结构、语言主判”），整篇一律复核；默认如此。
2. `uncertainty` 默认只在响应里，不加迁移。
3. 默认加一个确定性“字数规范”维（rubric_rule）；错别字、标点等规则需要词表，本任务不做。
4. 卷面书写维默认不可判（路由只收文本）；多模态路由另起任务。
5. 合成能力格 8–12 个替代 28 格；正式格子与 ROI 权重待教研（A1）。
6. 通道加权沿用 legacy 可靠性权重（原生 1.0、拍照确认 0.7），未校准。
7. 缺口建议若契约没有合适字段就不输出（不改契约）。
8. 合成内容由工程起草，教研抽查后替换。
