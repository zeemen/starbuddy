# 学科插件 API OpenAPI v0.9 起草

状态：草稿（待产品负责人与资深工程师确认；确认人、日期：＿＿＿）
关联：完整方案 v1.0 第五章 5.4 / 5.5 / 5.8、第七章 · 开发计划 冲刺 0 W2、冲刺 1 A 工作流 · ADR 01 / 03 / 04 / 08

## 目标
在 `packages/contracts/` 形成一份可评审的 OpenAPI 3.1 草案（v0.9）。四科适配器都实现同一套
diagnose / plan / teach / grade / generate_variation / report 六个方法，并按同一种返回结构写回平台。
W2 周五评审进入 v0.9，W4 冻结为 v1.0。

## 范围
- 做：
  - `packages/contracts/openapi/plugin-api.v0.9.yaml`：六个方法的请求与响应模型。
  - 统一返回信封 `PluginResult<T>`：`result`、`evidence_ids[]`、`confidence`（0–1）、`source_version`、
    `model_version`、`human_review_required`，另加 `review_reason`（`human_review_required` 为真时必填）。
  - 公共请求头与字段：`usage_id`、`idempotency_key`（消耗类方法必填，对应 CLAUDE.md 规则 4）、
    `student_ref`（平台学生 ID，不带手机号等 PII）、`subject`（`wenli|aya|zhixing|echo`，枚举预留七科）、
    `content_version` 六段标识 `region/exam_year/grade/textbook_version/subject/content_version`。
  - 统一错误码表与错误体；超时与重试语义：哪些方法幂等、重试时 `idempotency_key` 不变；
    `grade` 与 `diagnose` 允许异步返回 `202 + job_id`，这是英语异步批改的要求。
  - `human_review_required` 的触发约定：低置信度阈值由各科配置、学生申诉、抽检命中、OCR 低置信度。
  - `skill_node` 引用格式 `subject:local_id`，错因引用同时带学科分类与跨科七大类。
  - 契约示例：每个方法至少一组合成数据示例，供契约测试生成。
  - `packages/contracts/README.md`：版本策略（语义化，破坏性变更升主版本）。
- 不做：
  - 不写任何适配器实现，不写平台服务端代码。
  - 不写 `db/migrations`（学习者 Schema v0.9 另立 spec）。
  - 不定计量账本的内部一致性模型（ADR 06），契约里只约定字段。
  - 不生成代码类型（冻结 v1.0 后再接入生成器）。

## 验收标准（可测试）
- [ ] `plugin-api.v0.9.yaml` 通过 OpenAPI 3.1 校验（如 `redocly lint`，引入前先核许可证）。
- [ ] 六个方法的响应都 `$ref` 同一个 `PluginResult` 信封，六个必需字段全部 `required`。
- [ ] `grade` 的响应 schema 里没有“讲解 / 人设文本覆盖分数”的字段；讲解文本与分数分属不同字段（规则 3）。
- [ ] 所有消耗类方法都把 `usage_id` 与 `idempotency_key` 设为必填；非消耗类方法写明理由。
- [ ] 示例全部为合成数据，不含任何真实姓名、学校、手机号（规则 8）。
- [ ] 5.4 映射表中四科 × 六方法的每一格，都能在契约里找到对应的请求字段；映射不上的列入“待人确认”。
- [ ] 由 contract-guardian 子代理只读审查，结论附在 PR 中。

## 涉及的必停项（见 CLAUDE.md）
- packages/contracts：**是**，PR 首行写 `[NEEDS-HUMAN]`。
- migrations / RLS / 计量逻辑 / 人设 / 第三方：否（计量只约定字段，不实现）。

## 依赖与输入
- 四科盘点：`specs/inventory-aya/`、`specs/inventory-wenli/`（已起草）；知行、开口成章缺仓库，待补。
- `specs/inventory-summary.md`（W1 周三产出，尚未产出）。
- 错因跨科七大类映射表 v0.9（教研签字前，契约先用枚举占位）。

## 待人确认
1. `confidence` 的语义：统一做成 0–1 概率，还是允许各科带区间（如文砺主观维“参考分 ± 不确定度”）？草案倾向于在 `confidence` 之外加可选的 `uncertainty` 区间。
2. `teach` 是否计量：按完整方案 5.8，讲评课 / 白板按节计量，普通提示不计。需确认 `teach` 是否拆成两个操作。
3. 异步方法的回调方式：轮询 `GET /jobs/{id}`，还是平台 webhook？草案倾向于轮询优先。
4. 七科枚举是现在就全列（含 `history|physics|chemistry`），还是只列四科、以后加（加枚举是非破坏性变更）？
5. 开口成章保留 Gemini 链路时，`model_version` 的写法（供应商/模型/版本）需要与 ADR 04、08 一致。
