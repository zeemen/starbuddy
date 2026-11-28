# 讲评课在 staging 打开 · 实现记录

## PR 0：修阿鸦镜像
### 做了什么
- `deploy/docker/adapter.Dockerfile`：运行阶段拷 `packages/stage/package.json` 与 `packages/stage/src`，保持仓库相对结构；dockerignore 白名单放行这两项。
  - `package.json` 必须拷：它声明 `"type": "module"`，Node 按它决定 `.ts` 文件按 ES 模块加载；
  - `schema` 运行时不读，不拷；
  - 三科共用一份镜像定义，知行、文砺不导入它，多拷无害。
- `deploy/tests/test_static.py` 新增两条，管反方向：镜像里的源码导入的东西，都在同一阶段拷进来了。
  - `test_image_relative_imports_are_copied`：
    - 逐个 Dockerfile、逐个阶段（适配器按三科展开 `${ADAPTER}`），把 COPY 换算成“仓库里的来源 → 镜像里的位置”；
    - 扫描拷进来的 `.ts` / `.tsx` / `.mjs` / `.js`（跳过 node_modules 与测试），把相对导入按镜像里的位置解析；
    - 目标必须落在这个阶段拷进来的东西里，并且仓库里真有其文件；`COPY --from` 来的只算“在镜像里”。
  - `test_image_relative_imports_check_catches_missing_package`：反例。去掉阿鸦镜像的 `packages/stage`、只拷 `package.json` 不拷 `src`、去掉 web 构建阶段的 `packages/stage/src`，都要报出来。

### 验证
- 新测试在修复前的 Dockerfile 上报出阿鸦两处导入（`src/lessons.ts`、`src/methods/teach.ts` → `packages/stage/src/index.ts`），修复后通过；`test_static.py` 33 条全过。
- 按镜像目录结构实测启动阿鸦（运行依赖照 deps 阶段 `npm ci --omit=dev --ignore-scripts` 安装）：
  - 修复前：`ERR_MODULE_NOT_FOUND`；
  - 修复后：越过模块加载，给合成服务令牌后端口正常监听。
- `make deploy-check`（严格模式）通过，`deploy/tests` 335 条全过。镜像构建（`DEPLOY_BUILD=1`）本机没有 docker 守护进程，没有执行。

### 经验
- 镜像构建不在 CI 里时，“源码导入了镜像里没有的目录”只有真起镜像才会暴露。静态测试把它提前到每次 PR。
- 这次的根因是 ③a 给阿鸦加了仓库外导入，却没有同时改镜像定义。新测试会直接拦住这类遗漏。

### 待人确认
- 无。

## PR 1：讲评课内容包的构建与扫描
### 做了什么
- 新增 `adapters/aya/src/pack/lesson-release.ts` 与 `scripts/build-lesson-pack.ts`（`npm run build:lessons`）：草稿（`aya-lesson-pack/1`）+ 审核记录（`aya-lesson-reviews/1`）→ 已发布的讲评课内容包 `content/lessons.json`（`aya-lesson-pack/2`）。
  1. 确定性检查：草稿整包按加载规则读入（`packages/stage` 校验与文字安全、节点与题核对），任何一节不合格整包失败；
  2. 审核：
     - 每节一行，写编号、版本、审核时的内容摘要 `digest`，以及教研、人设两项结论；
     - 只发布两项都 `approved`、且摘要与现在内容一致的讲评课，其余在报告里列原因；
     - 审核记录里有草稿中没有的讲评课 → 失败；
     - 只有结论，不写审核人（规则 8）；
  3. C9 词表检查：
     - 已发布讲评课的台词稿每一行（字幕台词与预制答疑）与板书原文（标题、文字、公式）都要扫；
     - 老师台词与板书用阿鸦的词表，点点台词用点点的；
     - 只有严格模式，有命中就失败、不写文件；报告只有编号、文字位置与规则编号；
  4. 规范化序列化，同一输入两次生成逐字节相同，不读时钟。
- `src/lessons.ts`：
  - 新增 `lessonDigest`、`lessonTexts`、`indexReleasedLessons`；
  - `loadLessonsFrom` 不设 `AYA_LESSON_PACK` 时读已发布的包，不需要候选版开关；没有这个文件为空。合成包照旧只在候选版开关下读，设了它就不读已发布的包；
  - 已发布包的版本标记是 `r` 加文件 sha256 前 8 位，进 `source_version`。
- 加载时不再跑词表（运行镜像里没有 Python），只核对发布记录与内容对得上：
  - 逐节摘要与按内容重算的一致；
  - 按角色的文字条数与重算的一致；
  - C9 记录是严格模式、带词表版本。
  - 任何一项对不上，整包拒绝，阿鸦拒绝启动。
- `src/pack/hint-scan.ts`：`guardRunner` 加可选的 `persona`（缺省仍是阿鸦），提示阶梯不受影响。
- 测试：
  - `tests/lesson-release.test.ts` 9 条：
    - 发布与五种结论；按角色分词表；命中失败且不回显原文；
    - 扫描器结果对不上；草稿与审核记录不合格；逐字节可复现；
    - 加载时的八种篡改；启动时读哪一份；文字清单；
    - 接真实词表一条（`CONTRACT_STRICT=1` 下不许跳过）；
  - 变异加 13 条（`scripts/mutations.ts`），结果见下。
- `adapters/aya/README.md` 加“讲评课内容包”一节。

### 验证
- 阿鸦单测 174 条全过（含接真实词表的一条）；类型检查通过。
- 变异测试：第一轮 120 条里 119 条被发现，1 条是变异本身写错（改法把 `LessonPersona` 类型收窄，过不了类型检查）；改为“所有台词按老师的角色取词表”，手动施加后被发现（4 条测试失败）。第二轮全量结果见 PR 评论。

### 决定与差异
1. **审核记录的格式是本 PR 自拟的**（`aya-lesson-reviews/1`）：规格只写了“教研与人设审核记录的摘要”。做法照提示阶梯的准入决定文件：记录在仓库外产生，构建时读入，发布包里只记它规范化后的摘要 `reviews_digest`。
2. **只有严格模式**：提示阶梯有 `--c9 flag`，供本地迭代用。讲评课的发布包加载时就拒绝非严格模式，所以构建也不提供 flag。
3. **没通过审核的讲评课不扫、不发布**：草稿里可以同时有在改的课，不会因为它们挡住已经审完的课。

### 待人确认
已定（2026-11-28，用户“按建议，默认值都同意”）：S1 只记结论，不加审核时间与工单号；S2 暂不在 CI 里重跑构建，等真实讲评课与草稿进仓库时再议；S3 不改词表；S4 另起，动 `evals/` 前单独请人确认。

- **S1**：审核记录的格式与来源。现在是人在仓库外写 JSON；以后若走审核台，要由审核台导出同一格式。是否要求记录审核时间与工单号（不写姓名），请定。
- **S2**：加载时只能核对摘要与条数。如果有人改了文字、同时重算了摘要、条数又不变，加载时发现不了。防线在于发布包的改动要经 PR 评审，并且构建可以逐字节复现：拿同一份草稿与审核记录重跑 `build:lessons`，与提交的文件比对。是否要在 CI 里加这一步（需要把草稿与审核记录也放进仓库），请定。
- **S3**：点点的台词不受平台级条目（例如 `PLAT-01`“粗心”）约束。这是词表本身的设计：平台级条目只对老师生效，点点由 E 类兜底（`packages/personas/forbidden/platform.yaml`）。若讲评课里点点的台词也要禁“粗心”，要改词表，属于必停项。
- **S4**：人设回归纳入讲评课台词（规格第五节第 7 条）还没做，动 `evals/` 前单独请人确认。
