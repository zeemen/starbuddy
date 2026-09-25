# 阿鸦（数学）适配器

实现学科插件 API v0.9（`packages/contracts/openapi/plugin-api.v0.9.yaml`）的三个接口。
规格见 `specs/adapter-aya-sprint1/spec.md`，实现记录与待人确认见同目录的 `lessons.md`。

| 接口 | 状态 |
|---|---|
| `GET /plugin/v0/capabilities` | 已实现 |
| `POST /plugin/v0/grade` | 客观题（单选、多选、填空）。过程题（拍照）在后续冲刺 |
| `POST /plugin/v0/diagnose` | `mode = intake / gap_test` |
| `plan` `teach` `generate_variation` `report` | 返回 501 `METHOD_NOT_IMPLEMENTED`（冲刺 4） |

## 边界
- **不读写任何平台表**（CLAUDE.md 规则 1）。证据与推断放在响应里，由学习服务落库。
- **不输出掌握度**（规则 2）。只给能力点观测与错因。
- **判分只来自验证器** `aya-symbolic-ts@0.1.0`（规则 3）。响应里没有自由文本。
- **不返回答案、解析、评分标准**（decisions #4）。
  - 响应里的字符串只能是 ID、编码、版本、时间，或学生自己的作答。
  - 日志只记方法、状态码、错误码、request_id、耗时。
- **不调用任何模型**，不涉及数据出境。

## 结构
```
src/core/        legacy 纯函数副本（grading.ts、symbolic.ts、types.ts），文件头注明来源提交与改动
src/verifier.ts  验证器 v0：在 legacy 判分上加边界原因码、采样点数与置信度
src/adapter.ts   三个接口与字段映射（spec“映射规则”）
src/contract.ts  直接加载契约 YAML，用 ajv 校验请求与响应；响应不合契约就不发出（返回 500）
src/content.ts   服务端内容包加载与引用完整性检查
src/taxonomy.ts  读取 docs/mapping/error-taxonomy.v0.9.yaml（根因 → 学科编码 → 跨科大类）
src/server.ts    node:http，服务间令牌，路径前缀 /plugin/v0
content/pack.json   由 legacy 生成的内容包：只有判分需要的字段，没有题干、解析、提示
scripts/build-content-pack.ts  从 legacy 生成内容包（legacy 有未提交改动时拒绝）
scripts/register-content.ts    输出内容登记 SQL（课程版本、能力点、题目），只增不改
tests/           单测；tests/e2e/ 端到端（本地临时 Postgres）
```

## 常用命令
```bash
npm ci                                   # 安装依赖（ajv、ajv-formats、yaml；开发依赖 typescript）
make adapter-aya-test                    # 在仓库根目录：类型检查 + 单测 + 端到端
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
