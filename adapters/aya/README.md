# 阿鸦（数学）适配器

实现学科插件 API v0.9（`packages/contracts/openapi/plugin-api.v0.9.yaml`）的三个接口；按 v1.0 候选版实现全部六个方法（阿鸦 6/6）。
迁移期同时支持 v1.0 候选版（`plugin-api.v1.0.yaml`，`specs/plugin-api-v1.0-step2/spec.md` 第 3 条）：按请求的
`context.contract_version` 校验与应答（v1.0 下作答类证据带 `attempt_id`），其他版本返回 400 `CONTRACT_VERSION_UNSUPPORTED`；
能力声明默认报 0.9.0，可用 `--contract-version 1.0.0 --allow-candidate-contract`（或环境变量 `AYA_CONTRACT_VERSION`、
`STARBUDDY_ALLOW_CANDIDATE_CONTRACT=1`）改报 1.0.0，签字前只在测试与演练里用。
规格见 `specs/adapter-aya-sprint1/spec.md`（capabilities、grade、diagnose）与 `specs/adapter-aya-methods-sprint4/spec.md`
（plan、teach、generate_variation、report），实现记录与待人确认见各自目录的 `lessons.md`。

| 接口 | 状态 |
|---|---|
| `GET /plugin/v0/capabilities` | 已实现 |
| `POST /plugin/v0/grade` | 客观题（单选、多选、填空）。过程题（拍照）在后续冲刺 |
| `POST /plugin/v0/diagnose` | `mode = intake / gap_test` |
| `POST /plugin/v0/plan` | 只按 v1.0：学科内候选任务（复测按 `next_retest_at` 时刻排序、练习按 legacy 优先级公式），输出只有 ID、编码、数字 |
| `POST /plugin/v0/teach` | 只按 v1.0：`hint` 为确定性提示阶梯（作答提交前最多 3 级，配置 `max_hint_level_before_submit`；v1.0 没有“已提交”信号，4 级及以上一律不给；含标准答案的那一级只返回题目引用），`lesson` 只直出请求里的已发布知识页面；不调模型、不写话术 |
| `POST /plugin/v0/generate_variation` | 只按 v1.0：从 `item_family` 或内容包**选**已准入的题（目前 721 道全部未准入，一律返回空结果 `no_admitted_candidate`；准入随平台导出的准入决定重建，见下文“内容包格式 3”） |
| `POST /plugin/v0/report` | 只按 v1.0：时间窗内的能力点趋势与作答数、错因分布、亮点编码；不写结论句、没有预测 |

四个新方法在 0.9.0 下能力声明为未实现，0.9.0 请求返回 501 `METHOD_NOT_IMPLEMENTED`。它们都是确定性的：不调模型、不读系统时钟、
不产生证据、不置 `human_review_required`（引用了未准入的题时 `semantics = practice`）。

## 边界
- **不读写任何平台表**（CLAUDE.md 规则 1）。证据与推断放在响应里，由学习服务落库。
- **不输出掌握度**（规则 2）。只给能力点观测与错因；plan、report 只读平台给的掌握状态，不重算。
- **不写关于学生的陈述**（规则 11）。plan 的 `rationale_code`、report 的 `trend` / `highlight_codes` 是封闭白名单编码，结论句由洞察服务按判断模板生成。
- **只读请求里的平台数据**：`learner_context`、`node_path`、`exam_config`、`knowledge_page`、`item_family` 由编排层放进请求，
  适配器不调用知识服务、洞察服务，不读考情库；`learner_context`、`conversation`、`recent_evidence` 只参与请求体指纹，不保存、不记日志、不回显。
- **判分只来自验证器** `aya-symbolic-ts@0.1.0`（规则 3）。响应里没有自由文本。
- **不返回答案、解析、评分标准**（decisions #4）。
  - 响应里的字符串只能是 ID、编码、版本、时间，或学生自己的作答。
  - 日志只记方法、状态码、错误码、request_id、耗时。
- **不调用任何模型**，不涉及数据出境。

## 结构
```
src/core/        legacy 纯函数副本（grading.ts、symbolic.ts、types.ts），文件头注明来源提交与改动
src/verifier.ts  验证器 v0：在 legacy 判分上加边界原因码、采样点数与置信度
src/adapter.ts   六个接口的入口、信封与字段映射（spec“映射规则”）
src/methods/     冲刺 4-D 四个方法的纯函数：plan.ts、teach.ts、variation.ts、report.ts、shared.ts（不读时钟、不做 IO）
src/problem.ts   契约 Problem 错误类型
src/contract.ts  直接加载契约 YAML，用 ajv 校验请求与响应；响应不合契约就不发出（返回 500）
src/content.ts   服务端内容包加载与引用完整性检查
src/taxonomy.ts  读取 docs/mapping/error-taxonomy.v0.9.yaml（根因 → 学科编码 → 跨科大类）
src/server.ts    node:http，服务间令牌，路径前缀 /plugin/v0
src/pack/        内容包格式 3 的构建逻辑：admission.ts（准入决定文件的解析与校验、按决定重建、规范化序列化）、
                 hint-scan.ts（提示原文的 C9 词表检查）
content/pack.json   由 legacy 生成的内容包（格式 3）：判分字段 + 题目用途、预估用时、提示阶梯；逐题来源与准入依据；节点板块；
                    板块中考占比（Q1 推导值，待教研校准）；阿鸦节点 → KGE 知识节点对应表（KGE-01 之前为空）；
                    所用准入决定文件的摘要；C9 检查记录。没有题干、解析、前置关系
scripts/build-content-pack.ts    从 legacy 生成内容包（legacy 有未提交改动时拒绝）
scripts/rebuild-content-pack.ts  按准入决定重建内容包（不需要 legacy；只改准入与来源，重做 C9）
scripts/pack-cli.ts              两个构建脚本共用的收尾：决定文件 → 重建 → C9 → 校验 → 写文件
scripts/register-content.ts      输出内容登记 SQL（课程版本、能力点、题目），只增不改
scripts/mutations.ts             四个新方法与内容包格式 3 的变异测试（make adapter-aya-mutation）
conformance/fixtures.yaml      一致性测试夹具（1.0.0，六个方法）；fixtures.v0.9.yaml 是去掉四个新方法的副本（0.9.0 那一遍用）
tests/           单测。端到端（学习服务 → 本适配器 → 平台库）在 services/learning/tests/test_e2e_aya.py
```

## 常用命令
```bash
npm ci                                   # 安装依赖（ajv、ajv-formats、yaml；开发依赖 typescript）
make adapter-aya-test                    # 在仓库根目录：类型检查 + 单测（端到端见 make learning-test）
make adapter-aya-mutation                # 四个新方法与内容包格式 3 的变异测试（65 条，4 核约 9 分钟）
AYA_ADAPTER_SERVICE_TOKEN=... npm start  # 启动（令牌 ≥ 32 字符，由部署环境注入）
AYA_LEGACY_ROOT=/path/to/ayaheshui npm run build:pack   # 从 legacy 重新生成内容包（参数同下）
npm run rebuild:pack -- --decisions <准入决定文件>        # 按平台导出的准入决定重建（--c9 strict|flag、--report、--out）
npm run register:sql > aya-register.sql  # 生成登记 SQL；目标库由执行人决定（pilot 与生产由人执行）
```

## 内容包格式 3：逐题来源与准入决定（冲刺 4-G，`specs/release-gate-sprint4`）
- **准入的唯一真相源是平台题目表的准入列**，内容包里的准入是副本。平台导出“准入决定文件”，人执行构建脚本读入，重建内容包。
  没有决定文件时 721 道题全部是 `source_unverified`（来源待核，`specs/inventory-aya/decisions.md` #3）。
- **题目上的字段**：`source`（`source_ref` 来源编号或 null、`source_type`、`license_status`）、`admission`，
  以及决定文件里有这道题时的 `admission_review`（`review_level` 学科审核等级、`review_ids` 审核记录编号）。
- **整包字段**：`admission_decisions`（决定文件的格式、导出批次、导出时刻、SHA-256 摘要、条数；没有时为 null）、
  `hint_scan`（C9 记录）。整包的 `source_verified` 已去掉。
- **版本号**：`pack_version` 为 `0.3.0-<legacy 提交前 7 位>`；套用了准入决定时加 `.d<摘要前 8 位>`，
  所以准入变化会体现在 `source_version` 的 items 段。六段标识的 `content_version` 不变（题目与答案没变）。
- **决定文件**（`aya-admission-decisions/1`，由平台导出、人执行）：
  ```json
  { "format": "aya-admission-decisions/1", "export_ref": "<导出批次编号>", "exported_at": "<RFC 3339>",
    "curriculum": { "region": "nanjing", "exam_year": 2027, "grade": 9, "textbook_version": "suke-2024", "subject": "aya", "content_version": "0.1.0" },
    "decisions": [ { "item_id": "Q-…", "admission": "admitted", "source_ref": "<来源编号>", "source_type": "textbook",
                     "license_status": "cleared", "review_level": "Q2", "review_ids": ["<审核记录 UUID>"] } ] }
  ```
  - 是当前状态的完整快照：文件里没有的题一律回到默认，重建时先全部重置再套用，旧决定不残留。
  - 整份校验，任何一条不合格都不写文件：
    - 字段不能多也不能少；编号只能是字母数字与 `. _ : -`，不能是网址、人名或材料原文（来源与授权材料不进仓库，spec 待人确认 #15）；
    - 题目必须在内容包里、不重复；六段标识必须与内容包一致。
  - 自洽规则：
    - `admitted` 须有来源编号、已知来源类型、授权 `cleared`、审核等级 ≥ Q2、至少一条审核记录；
    - `pending_review` 须来源已核、有审核记录；`rejected` 须有审核记录；`source_unverified` 的授权只能是 `unverified`。
  - 准入升降的方向由平台的准入函数保证，这里不重复。
  - 缺省读 `content/admission-decisions.json`（存在时），与内容包一起提交；`--no-decisions` 明确不套用。
- **适配器行为不变**：只有 `admitted` 的题给 `semantics = formal` 与满置信度，其余按现有 `content_not_admitted` 规则
  （grade 复核 + practice；四个新方法 practice）。判分逻辑没有改。
- **确定性**：规范化序列化（键按名称排序），不读时钟。同一 legacy 提交、同一决定文件、同一词表，两次构建逐字节相同；
  `rebuild:pack` 与 `build:pack` 的结果也逐字节相同。

### C9 词表检查（提示原文）
- 构建时把全部提示阶梯原文（每题 0–6 级，共 4447 条）逐条过禁语扫描。
  - 词表复用 `packages/personas`（冲刺 3-F 收拢的单一真相源），只读调用：`python -m personas.guard scan --persona aya --stdin-json`；
  - 阿鸦口径是 A–F 类（F 取数学）与全平台禁语，与 decisions #7 一致。
- 报告（`--report`，格式 `aya-hint-scan/1`）只列题号、等级、规则编号，不含原文。
- 两种模式：
  - 严格（默认，`--c9 strict`）：有命中就构建失败，不写文件；
  - 非严格（`--c9 flag`，只用于本地迭代）：放行，命中的题记在 `hint_scan.flagged_items`。带标记的内容包不得进入发布。
- 加载时拒绝“严格模式却有标记”的记录。失败即关闭：解释器不可用、guard 报错、结果条数或顺序对不上，都当作失败。
- 需要 `python3` 与 PyYAML（`PYTHON` 环境变量可指定解释器）；单测里没有时非严格模式跳过，`CONTRACT_STRICT=1` 下失败。
- 词表更新后，用 `npm run rebuild:pack` 重建，刷新 `hint_scan` 里的词表版本。

## 依赖与许可证（规则 10）
| 包 | 版本 | 许可证 | 用途 |
|---|---|---|---|
| ajv | 8.20.0 | MIT | 按契约校验 |
| ajv-formats | 3.0.1 | MIT | uuid、date-time 等格式 |
| yaml | 2.9.1 | ISC | 读取契约与映射表 YAML |
| typescript（开发） | 5.9.3 | Apache-2.0 | 类型检查 |
| @types/node（开发） | 22.20.4 | MIT | 类型 |

传递依赖（fast-deep-equal、fast-uri、json-schema-traverse、require-from-string、undici-types）都是 MIT，只有 fast-uri 是 BSD-3-Clause。
