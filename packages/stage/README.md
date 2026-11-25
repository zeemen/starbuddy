# @starbuddy/stage · 舞台

讲解 DSL 的类型、JSON Schema、确定性校验器、版本迁移、播放状态机与最小 SVG 白板（ADR 07；`specs/stage-whiteboard-step1`、`specs/stage-dsl-versioning`）。
其他模块只经 `src/index.ts` 与 `src/Whiteboard.tsx` 使用本包；读入讲解 DSL 一律经 `migrateStageDsl`（当前版本直接校验，旧版本沿迁移阶梯升级后再校验）。

## 版本规则
- `DSL_VERSION`（`src/dsl.ts`）只描述**序列化格式**：字段、动作类型、枚举、上限、id 规则、公式命令表。与本包的 `package.json` 版本无关。
- 任何会让“旧校验器拒收新文档”或“新校验器拒收旧文档”的改动都是格式变化，必须升版本（0.x 阶段升 minor），包括收紧或放宽上限。
  只改渲染、播放、注释、不改格式的，版本不动。
- 格式指纹与已发布的 schema 锁在 `schema/versions.lock.json`，由 `tests/version.test.ts` 核对：
  改了格式常量却没升版本、改了已发布版本的 schema，`make stage-test` 都会失败。

## 升版本的步骤（例：0.1.0 → 0.2.0）
1. 改 `src/dsl.ts` 的格式常量与类型，`DSL_VERSION` 改为 `0.2.0`；校验器跟着改。
2. 新增 `schema/stage-dsl-0.2.0.schema.json`（旧版本的 schema 文件**不改**），`package.json` 的 `exports["./schema"]` 指向新文件。
3. 在 `schema/versions.lock.json` 末尾追加一行：`version`、`format_sha256`（`formatSpec()` 的 sha256）、`schema`、`schema_sha256`。已有行不改。
4. 在 `src/version.ts` 的 `DSL_MIGRATIONS` 末尾追加一级 `{ from: '0.1.0', to: '0.2.0', migrate }`：
   - 纯函数、幂等，输入输出都是普通 JSON；`dsl_version` 由执行器盖章，不用自己写；
   - **只改结构，不改任何文字与公式**（标题、`write_text.text`、`write_formula.latex`）；字段改名时用 `textKeys` 写明旧版本的字段名，
     执行器会核对每一级迁移前后文字逐字相同，不同即拒收；需要改文字才能表达的变化不能写成迁移，旧内容进重审。
5. 为这一级迁移补测试（旧文档能升上来、文字逐字保留、幂等、升级后通过完整校验），跑 `make stage-test`、`make stage-mutation`。

## 拒收规则（fail-closed）
- 比当前更新的版本：`future_version`；阶梯上找不到的版本：`unknown_version`；迁移抛错、改了文字、升级后不合格、阶梯不合规：`migration_failed`（升级后不合格时同时带出校验错误）。
- 不降级、不猜测；已审核内容迁移失败或需要改文字时进重审（发布闸门与审核台落地后实现）。
