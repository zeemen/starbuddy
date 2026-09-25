# 学科插件 API OpenAPI v0.9 起草

状态：草稿 v0.2（待产品负责人与资深工程师确认；确认人、日期：＿＿＿）
关联：完整方案 v1.0 第五章 5.4 / 5.5 / 5.8、第七章 · 开发计划 冲刺 0 W2、冲刺 1 A 工作流 · ADR 01 / 03 / 04 / 08 / 09 · 四科 decisions（2026-09-25 已确认）

## v0.2 变更
按四科盘点 decisions 与 ADR 09 草稿对齐：generate_variation 改为选题语义；confidence 口径改为各科已定规则；契约 v1 不含历史判分；英语 D 维可为 null；新增数据级别字段；删除“英语 Gemini 链路”前提。

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
  - **已由四科 decisions 定下的语义**（写进契约描述，不再讨论）：
    - `generate_variation` 在契约 v1 中**只从已审题库选题**，不生成新题；没有合格候选时返回空结果与原因码（aya #9、wenli #6/A5、zhixing、echo 一致）。
    - `grade` 的 confidence 口径：主观维度（作文、阅读、道法 M3 主观题、英语 LLM 判分）一律低置信，并置 `human_review_required = true`；确定性验证器（quiz key 比对、道法 M2 定位、阿鸦验证器 v0）给出高置信（wenli A2、zhixing A2、aya adapter-design §2）。
    - 契约 v1 的 zhixing `grade` 只接道法，不含历史判分（zhixing #13/A5）。
    - 英语 `grade` 的维度分允许为 `null`（D 维发音，评测供应商到位前），总分标注“不含该维”（echo A3）。
    - 学科私有的游戏化数值（如英语 XP、成就）不得出现在任何表示掌握度的字段里（echo #12/A6）。
  - 请求中的数据级别 `data_class`（`L0|L1|L2|L3`，定义见 ADR 09 §2），供模型路由判断能否走境外供应商（ADR 04 / 09）。
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
- 四科盘点与 decisions：`specs/inventory-{aya,wenli,zhixing,echo}/`（分支 `docs/sprint0-inventory`，已确认）。
- ADR 09 数据驻留（分支 `docs/adr-09-data-residency`，草稿）。
- `specs/inventory-summary.md`（W1 周三产出，尚未产出）。
- 错因跨科七大类映射表 v0.9（教研签字前，契约先用枚举占位）。

## 待人确认
1. “低置信 / 高置信”的具体数值：decisions 只定了口径，数值要随契约定。草案建议：确定性验证器 = 1.0；主观维度 ≤ 0.5，并且固定触发复核；另外加可选的 `uncertainty` 区间，承接文砺的 `uncertainty_note`。
2. `teach` 是否计量：按完整方案 5.8，讲评课 / 白板按节计量，普通提示不计。需确认 `teach` 是否拆成两个操作。
3. 异步方法的回调方式：轮询 `GET /jobs/{id}`，还是平台 webhook？草案倾向于轮询优先。
4. 七科枚举是现在就全列（含 `history|physics|chemistry`），还是只列四科、以后加（加枚举是非破坏性变更）？
5. `model_version` 的写法（供应商/模型/版本）需要与 ADR 04、08 一致。英语没有 Gemini 链路（echo #1），不需要为它单独设计。
6. 阿鸦验证器 v0 的版本号与原因码枚举是否写进契约（aya adapter-design 待人确认 #6）？草案倾向于：原因码作为枚举进契约，验证器版本走 `source_version`。
7. `data_class` 由调用方声明还是由平台按字段推断？草案倾向于调用方声明、平台校验，声明低于实际级别时拒绝。
