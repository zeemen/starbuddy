# 讲评课在 staging 打开 · 规格草稿

- 状态：**草稿，待人确认**（第五节）。确认前不动代码。
- 来源：`specs/stage-lesson-session` lessons L8（“等插件契约 1.1.0 签字、真实讲评课内容与页面落地后，再由人决定 staging 开不开”）；`specs/stage-production-build` 第四节把这件事另起。
  用户 2026-11-27 说“按建议起草 staging 打开讲评课的规格”。
- 依据：
  - `deploy/flags/staging.yaml`：开关矩阵与 `pending` 口径（`signed_content`、`migration_*`、`human` 等）；
  - 插件契约 `packages/contracts/README.md`：v1.1 是候选版，签字前只在测试里用（`STARBUDDY_ALLOW_CANDIDATE_CONTRACT=1`，staging 一律不设）；
  - ADR 07 决定 5：讲解 DSL 是教学内容，要过发布闸门（AI 草稿 → 确定性检查 → 教研审核 → 版本发布）；
  - 待人确认的旧账：`specs/stage-point-to-ask-answer` lessons P1、P3、Q6，`specs/stage-lesson-session` lessons R1、R2、L2、L7、L8、L9；
  - CLAUDE.md 规则 4、7、9、13。

## 一、现状（2026-11-27 核对）
讲评课的代码已经全部合并（③b-1 至 ③b-5、L10），但 staging 里四层都是关的，而且关的理由各不相同：

| 层 | 现在的口径 | 关的理由 |
|---|---|---|
| 前端 `STARBUDDY_LESSON` | 只在 dev / test 生效，staging 按关 | 跟随后端 |
| BFF `STARBUDDY_LESSON` | staging 设了就拒绝启动 | 契约未签字、内容与页面未落地 |
| 学习服务 `LEARNING_LESSON_TICKET_SECRET` | 设了必须同时设候选契约开关，否则拒绝启动 | 契约 v1.1 未签字 |
| 阿鸦 `AYA_LESSON_PACK` | 只在候选契约开关打开时读，只给合成讲评课 | 没有真实讲评课，也没有内容包的构建与扫描 |

- **契约**：讲评课用到 v1.1 草稿内追加的 `TeachRequest.lesson_ref`、`TeachRequest.question`、`whiteboard_dsl` 片段的 `script`。v1.1 还含 `present_item`（PLT-12），整版没有签字。
- **内容**：
  - 仓库里没有真实讲评课；
  - 讲评课内容包没有构建脚本，也没有 C9 禁语扫描（Q6、R2）。提示阶梯有 `scripts/build-content-pack.ts` 与 `hint_scan` 记录；
  - 台词与答疑是人设话术，现在全是“（合成…）”占位（P3），没跑过人设回归。
- **计量与库**：
  - 迁移 0064（`platform.lesson_session`）是草稿，待人执行；
  - `lesson_whiteboard` 的额度：基础档 0 次、进阶档 10 次、高级档 40 次（0008、0009）。写入前的核对还依赖 0058。
- **密钥**：
  - BFF 的讲评课票据密钥由已有主密钥派生（`derive('lesson-ticket')`），不需要新密钥；
  - 学习服务要新增 `LEARNING_LESSON_TICKET_SECRET`（≥ 32 字符，与其他令牌各不相同），现在登记为只 dev / test。
- **页面**：普通页面，生产构建里有（L10 已做）；没有入口，只能按地址打开；文案全是占位（L9）。
- **发现一个既有问题（修复不在本规格里，单独 PR）**：
  - 阿鸦自 6e2c51a（③a）起导入 `packages/stage`（`src/lessons.ts`、`src/methods/teach.ts`），但 `deploy/docker/adapter.Dockerfile` 与它的 dockerignore 都没有拷 `packages/stage`；
  - 按镜像目录结构实测启动：`ERR_MODULE_NOT_FOUND ... packages/stage/src/index.ts`。也就是说，按现在的 Dockerfile 构建，阿鸦镜像起不来，staging 已开的数学科目也会受影响；
  - staging 集群还暂停着没建，没有造成实际影响。镜像构建（`DEPLOY_BUILD=1`）不在 CI 里，所以没被发现。

## 二、上 staging 之前要过的关（不是代码）
1. **契约签字**：插件契约 v1.1 签字。签字范围已经扩大到 `present_item` 与讲评课的三项（P1），由签字人决定整版签，还是把讲评课的三项挪到 v1.2 候选版。签字后照 v1.0 的先例（`specs/contract-v1.0-signoff`）把 1.1.0 移出候选版本表，讲评课就不再需要候选契约开关。
2. **讲评课内容**：
   - 教研产出第一批真实讲评课；
   - 过发布闸门：确定性检查（`packages/stage` 校验、文字安全、答案守门）→ C9 禁语扫描 → 教研审核 → 人设审核 → 版本发布。
   - 在签字的讲评课到位之前，讲评课模块照 `signed_content` 的口径：开关开着，但没有讲评课时一律 404（“这节课打不开”）。
3. **人设回归**：台词与答疑是点点或阿鸦老师的话术，入包前跑 `make eval-persona`，并附前后对比（规则 7）。
4. **迁移**：人执行 0064（以及它依赖的 0058）。
5. **文案**：讲评课页面文案由人写人审（W6，L9）。staging 只给内部成员，可以先带占位；pilot 之前必须换掉。

## 三、默认做法：关过完之前先把代码备好，按三个 PR 走
1. **PR 0：修阿鸦镜像（前置，独立，不等任何关）**：
   - `adapter.Dockerfile` 与 dockerignore 把 `packages/stage` 的 `package.json`、`src`、`schema` 放进镜像，保持仓库相对结构；
   - 加一条静态测试：每个适配器 `src` 里指向适配器目录以外的相对导入，目标都必须在该镜像的拷贝清单里（web 的 Dockerfile 一并核对）；
   - 验收：按镜像目录结构启动阿鸦不再报错；有条件时 `DEPLOY_BUILD=1` 构建一次。
2. **PR 1：讲评课内容包的构建与扫描（不等契约签字）**：
   - 照提示阶梯的做法，给讲评课内容包加构建脚本；
   - 产物带 C9 禁语扫描记录（台词与答疑原文）、教研与人设审核记录的摘要，没有记录的包启动时拒绝；
   - 阿鸦加载“已发布的讲评课内容包”（仓库内 `adapters/aya/content/` 下，与提示阶梯同一个目录口径），不再只认合成包；合成包仍然只在候选开关下读；
   - 包里一节讲评课都没有时照旧为空，所有讲评课 404；
   - 人设回归（`evals/persona`）把讲评课内容包里的台词与答疑纳入用例来源。具体纳入方式按 evals 现有结构定，可能涉及人设评测脚本，属于必停范围。
3. **PR 2：开关放到 staging（等契约签字之后）**：
   - 学习服务：`LEARNING_LESSON_TICKET_SECRET` 不再要求候选契约开关；v1.1 签字时由契约签字 PR 一并改，本 PR 只核对；
   - BFF `parseLessonFeature`：允许 staging，pilot、生产仍然拒绝启动；
   - 前端 `lessonEnabled`：允许 staging，pilot、生产仍然按关；
   - 开关矩阵：从 `stage_whiteboard` 拆出 `lesson` 一项，`state: "on"`，`pending: [migration_0058, migration_0064, signed_content, human]`（`human` 指文案 L9）；新增 `migration_0064` 这个 `pending` 键；
   - `env-inventory`：三个变量改为 `envs: all`，`LEARNING_LESSON_TICKET_SECRET` 在 staging 由密钥管理注入（`secret: true`）；
   - compose / k8s 的 staging 编排设这几项；`deploy/tests` 的开关语义测试改为：staging 能开，pilot、生产拒绝或按关；
   - `stage_whiteboard` 一项的说明同步更新：现在还写着“讲评课页面也是开发页”，L10 之后已经过时。
4. **入口**：本规格不加入口，内部成员按运行手册里的地址打开。讲评课目录与推荐另起。
5. **额度**：不改。staging 的测试账号按档位拿额度（进阶 10 次、高级 40 次），基础档仍是 0 次，进不来时显示“现在不能开始”。

## 四、不做
- 契约 v1.1 签字本身（另起 `specs/contract-v1.1-signoff`，由人签）。
- 写真实讲评课内容与台词（教研、人设）。
- 讲评课目录、推荐与入口；家长端讲评课视图（L2）。
- 2b 模型回答。
- pilot 与生产打开讲评课。

## 五、待人确认（括号里是默认）
1. 先修阿鸦镜像（PR 0），不等本规格的其他部分（先修；这是既有问题，修复很小）。
2. 契约签字范围：v1.1 整版签，还是把讲评课的三项挪到 v1.2 候选版（整版签；由签字人定，P1）。
3. 签字的讲评课到位之前，讲评课照 `signed_content` 的口径开着、没有课时 404（是）。另一种做法是等真实讲评课到位再开。
4. staging 可以带着占位文案打开，pilot 之前必须换掉（可以）。
5. R1 “作答提交前讲解含答案就不下发”在 staging 照旧从严（照旧）。
6. 限流 `lesson.session.minute` 每分钟 60 次（L7）在 staging 不改（不改）。
7. PR 1 里人设回归纳入讲评课台词的方式，在动 `evals/` 之前单独请人确认（单独确认）。

## 六、验收
- PR 0：按镜像目录结构启动阿鸦成功；静态测试能抓住“适配器导入了镜像里没有的目录”；`make deploy-check` 通过。
- PR 1：
  - 构建脚本产出的讲评课内容包带 C9 扫描与审核记录；
  - 缺记录、扫描不过、`packages/stage` 校验不过的包，阿鸦拒绝启动；
  - 空包时讲评课一律 404；
  - `make check` 与 `make eval-persona` 通过。
- PR 2：
  - 开关语义测试：staging 能开，pilot、生产拒绝或按关；
  - staging 口径的编排冒烟里讲评课路由注册；没有签字讲评课时打开回 404；
  - 端到端照旧；`make check`、`make deploy-check` 通过。
