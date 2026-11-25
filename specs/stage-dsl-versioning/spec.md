# 讲解 DSL 的版本规则与迁移阶梯（packages/stage） · 规格草稿

- 状态：**草稿，待人确认第五节**（2026-11-25）。
- 来源：OpenMAIC 借鉴分析 v1.1 第八节第 2 条（用户 2026-11-25“按建议落地 6 点，先做 DSL 版本与迁移阶梯，起草规格”）。
  借鉴 OpenMAIC `packages/@openmaic/dsl/src/version.ts` 的做法：序列化格式有独立的版本号；格式一变就升版本、在有序的迁移阶梯上追加一级纯函数迁移；
  读入旧文档时沿阶梯升到当前版本；比当前更新的版本一律拒绝；CI 强制“改格式必升版本”（它用 `scripts/check-package-version-bumps.mjs`）。
  只借思路，不引入它的代码或包（ADR 07 选项 A 仍未启动，见借鉴分析 v1.1）。
- 不涉及必停项：只改 `packages/stage`（零新依赖），不改契约（`TeachSegment.dsl_version` 已是 semver 字符串）、迁移、计量、判分与人设文本。

## 一、为什么现在做
- 讲解 DSL 现在是 v0.1.0（`packages/stage/src/dsl.ts` 的 `DSL_VERSION`），校验器只认这一个版本，其他一律 `bad_version`（`src/validate.ts`）。
- 接下来 DSL 要升到 v0.2（借鉴分析第 1 点“说 / 画 / 指”配对），随后讲评课的讲解要经教研审核后**存下来复用**。一旦有已审核内容入库，
  再改格式就会让旧内容要么读不进来、要么被悄悄改样。版本规则与迁移机制必须在第一批内容入库之前定好。
- 现在格式只有一个版本、还没有任何存量内容，是代价最小的时机。

## 二、默认做法
1. **版本号的含义**：`DSL_VERSION` 只描述**序列化格式**（字段、动作类型、枚举、上限、id 规则），与 `@starbuddy/stage` 的包版本无关。
   - 任何会让“旧校验器拒收新文档”或“新校验器拒收旧文档”的改动——加字段、加动作类型、改枚举、**收紧或放宽上限**、改 id 规则——都是格式变化，
     必须升版本（0.x 阶段升 minor：0.1.0 → 0.2.0）。
   - 只改渲染、播放、文档注释而不改格式的，版本不动。
2. **格式指纹，CI 强制**（对应 OpenMAIC 的版本检查脚本，改为单测实现）：
   - 新文件 `packages/stage/schema/versions.lock.json`：每个已发布版本一行，记录 ① 格式指纹（`TOP_FIELDS`、`STEP_FIELDS`、`CANVAS_FIELDS`、`ACTION_TYPES`、
     `ACTION_FIELDS`、`ROLES`、`FONT_SIZE`、`LIMITS`、`ID_RE` 按固定顺序序列化后的 sha256）② 该版本冻结的 JSON Schema 文件名与 sha256。
   - 单测核对：当前常量算出的指纹必须等于锁文件里 `DSL_VERSION` 那一行；已发布版本的 schema 文件内容不得改动（sha256 不变）；
     每个已发布版本都有自己的 `stage-dsl-<版本>.schema.json`。于是“改了格式却没升版本”“改了已发布的 schema”都会让 `make stage-test` 失败。
   - 升版本的流程写进 `packages/stage/README.md`（新）：改常量 → 升 `DSL_VERSION` → 新增该版本的 schema 文件 → 在锁文件追加一行 → 在迁移阶梯追加一级迁移与测试。
3. **迁移阶梯**（新文件 `packages/stage/src/version.ts`）：
   - `DSL_MIGRATIONS`：有序数组，每级 `{ from, to, migrate }`；`migrate` 是**纯函数、幂等**，输入输出都是普通 JSON；`from → to` 首尾相接、不跳级。
   - `migrateStageDsl(raw)`：读 `dsl_version`，沿阶梯逐级升到 `DSL_VERSION`，再交给 `validateStageDsl` 做完整校验；只有校验通过的结果才能播放
     （播放器与白板现在就只收校验过的 DSL，这一点不变）。
   - 拒收规则（fail-closed，新错误码进 `ValidationError`）：比当前更新的版本 `future_version`；阶梯上找不到的版本 `unknown_version`；
     某级迁移抛错或升级后校验不通过 `migration_failed`（同时带出原版本号，便于进重审）。**不降级、不猜测**。
   - **文字不动**：迁移只改结构，不改任何文字与公式内容（`write_text.text`、`write_formula.latex`、标题）。每级迁移的测试都要核对迁移前后
     全部文字逐字相同；需要改文字才能表达的变化，不能写成迁移，只能让旧内容进重审（见第五节第 3 条）。
   - 本期阶梯为空（只有 0.1.0）：机制用测试里注入的两级合成迁移（0.1.0 → 0.1.9 → 0.2.0，只存在于测试）验证；第一级真实迁移随 DSL v0.2 落地。
4. **调用方**：`apps/web` 的 `/dev/stage` 样张页改为经 `migrateStageDsl` 读入合成 DSL（行为不变）；`TeachSegment.dsl_version` 的契约不改。
5. **测试与检查**：
   - 单测：指纹与锁文件一致；已发布 schema 未被改动；阶梯首尾相接；`future_version` / `unknown_version` / `migration_failed` 三种拒收；
     合成两级迁移能把 0.1.0 文档升到 0.2.0 且文字逐字保留、幂等（再迁一次结果相同）；迁移后仍经完整校验；
   - 变异测试：在 `scripts/mutations.ts` 加几条（指纹比较改成恒真、跳过未来版本检查、迁移后不再校验），断言能被发现；
   - `make stage-test`、`make stage-mutation`、`make check-fast` 通过；零新依赖，`license-check` 不变。

## 三、不做
- DSL v0.2 本身（“说 / 画 / 指”配对）：下一个任务，那时追加第一级真实迁移。
- 讲解内容的存储、发布闸门、审核台：还没有，等讲评课任务；本任务只保证“存下来的内容以后能被可靠地读回”。
- 引入 `@openmaic/dsl` 或任何依赖；契约、BFF、学习服务、适配器的改动。

## 四、验收
- 改任何格式常量而不升版本、改已发布版本的 schema，`make stage-test` 都失败；
- 旧版本文档能沿阶梯升到当前版本并通过完整校验，文字逐字不变；未来版本、未知版本、迁移失败一律拒收；
- `make stage-test`、`make stage-mutation`、`make check-fast` 通过；零新依赖。

## 五、待人确认（括号里是默认）
1. **版本规则**：任何序列化格式变化（含收紧或放宽上限）都升 minor 并加迁移（默认）；还是只在“新校验器拒收旧文档”时才升？
2. **旧版本支持窗口**：阶梯保留全部历史版本，旧内容永远能读回（默认）；还是只保留最近两个版本，更早的内容必须重审后重存？
3. **已审核内容迁移后要不要重审**：只改结构、文字逐字保留（测试证明）的迁移保留原审核记录；迁移失败或需要改文字的，进重审（默认）。
   发布闸门与审核台还没有，这条先记进规则，等它们落地时实现。
