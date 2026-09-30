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
| `POST /plugin/v0/generate_variation` | 只按 v1.0：从 `item_family` 或内容包**选**已准入的题（目前 721 道全部未准入，一律返回空结果 `no_admitted_candidate`） |
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
content/pack.json   由 legacy 生成的内容包（格式 2）：判分字段 + 题目用途、预估用时、提示阶梯；节点板块；板块中考占比（Q1 推导值，
                    待教研校准）；阿鸦节点 → KGE 知识节点对应表（KGE-01 之前为空）。没有题干、解析、前置关系
scripts/build-content-pack.ts  从 legacy 生成内容包（legacy 有未提交改动时拒绝）
scripts/register-content.ts    输出内容登记 SQL（课程版本、能力点、题目），只增不改
scripts/mutations.ts          四个新方法的变异测试（make adapter-aya-mutation）
conformance/fixtures.yaml      一致性测试夹具（1.0.0，六个方法）；fixtures.v0.9.yaml 是去掉四个新方法的副本（0.9.0 那一遍用）
tests/           单测。端到端（学习服务 → 本适配器 → 平台库）在 services/learning/tests/test_e2e_aya.py
```

## 常用命令
```bash
npm ci                                   # 安装依赖（ajv、ajv-formats、yaml；开发依赖 typescript）
make adapter-aya-test                    # 在仓库根目录：类型检查 + 单测（端到端见 make learning-test）
make adapter-aya-mutation                # 四个新方法的变异测试（约 3–4 分钟）
AYA_ADAPTER_SERVICE_TOKEN=... npm start  # 启动（令牌 ≥ 32 字符，由部署环境注入）
AYA_LEGACY_ROOT=/path/to/ayaheshui npm run build:pack   # 重新生成内容包
npm run register:sql > aya-register.sql  # 生成登记 SQL；目标库由执行人决定（pilot 与生产由人执行）
```

## 依赖与许可证（规则 10）
| 包 | 版本 | 许可证 | 用途 |
|---|---|---|---|
| ajv | 8.20.0 | MIT | 按契约校验 |
| ajv-formats | 3.0.1 | MIT | uuid、date-time 等格式 |
| yaml | 2.9.1 | ISC | 读取契约与映射表 YAML |
| typescript（开发） | 5.9.3 | Apache-2.0 | 类型检查 |
| @types/node（开发） | 22.20.4 | MIT | 类型 |

传递依赖（fast-deep-equal、fast-uri、json-schema-traverse、require-from-string、undici-types）都是 MIT，只有 fast-uri 是 BSD-3-Clause。
