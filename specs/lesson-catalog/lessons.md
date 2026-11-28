# 讲评课目录与入口 · 实现记录

## PR A：契约 `list_lessons` 与阿鸦实现
### 做了什么
- **插件契约 v1.1 草稿内追加（再续）**（`packages/contracts/openapi/plugin-api.v1.1.yaml`）：
  - `Method` 枚举与能力声明各多一项 `list_lessons`（能力声明里必填，与 `present_item` 一样）；
  - 新路径 `/list_lessons`；
  - 新 schema：`ListLessonsRequest`（`context` 复用 `PresentItemContext`：与学生无关、不计量，另加 `content_version`）、`LessonCatalogEntry`、`LessonCatalog`、`ListLessonsResponse`（不产生证据）；
  - 新示例 `ListLessonsRequestAya`、`ListLessonsResponseAya`；`CapabilitiesAya` 多一项；契约开头说明加“草稿内追加（再续）”一节。
- **目录条目**：只有 `lesson_ref`、`skill_node`、`item_ref`（可选）、`title`、`step_count`。
  - 标题上限 40，与 `packages/stage` 的 `LIMITS.maxTitleChars` 一致；字符口径与台词正文一致（拒收尖括号、控制字符、不可见字符，不收换行）；
  - 步数 1–100，与 `LIMITS.maxSteps` 一致；目录最多 500 条。
- **契约检查**（`scripts/check_contract.py`）：
  - `check_v11_additive` 把只读方法写成 `V11_READ_ONLY_METHODS`（`present_item`、`list_lessons`），逐项放过 Method、能力声明、路径、schema 与示例；
  - 新增 `check_list_lessons`：
    - 目录树里不许出现讲解内容与答案类字段名（`dsl`、`script`、`lines`、`answers` 等）；
    - 对象一律 `additionalProperties: false`，数组一律有 `maxItems`；
    - 条目字段恰好是那五个；请求只收 `context`（`PresentItemContext`）与 `content_version`；响应不带证据；
    - 标题、步数上限与 `packages/stage` 一致（读 `dsl.ts`）；
  - 标题加进受限文字探针表。
- **反例与变异**：
  - 反例 +16（带学生、带计量、缺内容版本、夹带过滤条件、条目夹带 DSL、台词或答案、标题 HTML、超长、换行、步数越界、产生证据、信封外夹带评语或推荐理由）；
  - 契约变异 +13。
- **一致性测试**（`packages/contracts/conformance`）：
  - 只读方法的上下文、坏请求变体、幂等检查改按 `READ_ONLY_METHODS` 判断；
  - `list_lessons` 另加一个坏请求变体（要求讲解内容）；
  - 新增 `lesson_catalog_problems`：条目字段白名单、答案类字段名、不产生证据、秘密与受控值；
  - 参考适配器实现 `list_lessons`，并加 4 类违约（夹带台词稿、产生证据、带学生也应答、两次目录不一样）；参考夹具加一条；
  - `mutation.py` 的分母检查两个只读方法都单列。
- **阿鸦**：
  - `src/methods/list-lessons.ts`：从讲评课投影目录，按编号与版本排序；
  - `src/adapter.ts`：`list_lessons` 路由，只按 1.1.0；内容版本核对同 `present_item`；响应自检；能力声明 1.1.0 声明它；
  - 测试 `tests/list-lessons.test.ts` 5 条；变异 +5；一致性夹具加一条（那一遍不带讲评课包，目录为空）。
- **知行**：1.1.0 能力声明写 `list_lessons: { implemented: false }`，请求回 501。

### 验证
- `check_contract.py` 通过：v1.1 的 107 个反例全部被拒绝，其中新加 16 个；`contract-mutation` 357 个变异全部被发现，其中新加 13 个。
- `make conformance`（严格模式，全部适配器、全部契约版本）通过：
  - 阿鸦 v1.1：`list_lessons` 实现，3 条测试通过；
  - 知行 v1.1：声明未实现，回 501；
  - Gate 0 分母仍是六个学科方法。
- `conformance-mutation`：39 类违约、67 次注入全部被发现，其中新加 4 类。
- 阿鸦单测 179 条、知行单测 126 条全过；`check-group-contracts`（严格模式：契约、变异、一致性、db-test、渗透环境自检）通过。
- 阿鸦全量变异与 `check-group-adapters` 的结果见 PR 评论。

### 决定与差异
1. **请求复用 `PresentItemContext`**：它的口径（只读、与学生无关、不计量、幂等键可选、最低 L1）正合目录，不另起一个上下文 schema。名字带 present 稍显别扭；签字时若要改名，两个方法一起改。
2. **能力声明里必填**：与 `present_item` 一致；不实现的适配器写 `implemented: false`（知行）。文砺不按 1.1.0 应答，不受影响。

### 待人确认
已定（2026-11-28，用户“按建议，默认值都同意”）：C1 接受签字范围再扩大一项；C2 在 `build:lessons` 构建时就按契约口径核对标题与台词，另起小 PR，不放进 PR A。

- **C1**：v1.1 的签字范围又多一项（`specs/stage-point-to-ask-answer` lessons P1 同一个问题）。
- **C2**：契约的标题口径比 `packages/stage` 严，例如 `x < y` 在 stage 里可以，契约里任何尖括号都不收。讲评课标题若含尖括号，阿鸦的响应自检会让整份目录回 500；台词稿现在也是同一口径。是否要在讲评课构建（`build:lessons`）时就按契约口径核对标题与台词，请定。

## PR B：学习服务目录接口、打开按目录补全、条目带 `lesson_ref`
### 做了什么
- **新模块 `learning/lesson/catalog.py`**：经适配器 `list_lessons`（契约 1.1.0）取一科的讲评课目录。
  - 请求用 `PresentItemContext`：不带学生、不计量，L1；内容版本与诊断判分、题面请求同一组六段；
  - 响应按契约 `ListLessonsResponse` 校验，再逐条投影：只留五个字段，节点与题的学科前缀必须是这一科，同一节课不能出现两次。不合格整份拒绝（502），不缓存；
  - 按学科在进程内缓存 5 分钟（时钟可注入）；
  - 适配器暂时失败 → 503（不缓存）；适配器没有这个方法（上游 501）→ 空目录；
  - `by_item`：题目引用 → 讲这道题的那节课，同一道题有几节时取编号、版本最小的一节；目录取不到时为空，不影响调用方。
- **新内部接口 `POST /internal/v1/lesson/catalog`**：
  - 请求 `{student_ref, subject}`，`student_ref` 只用于库层处理判据；
  - 只回编号、版本、标题与步数，节点与题不出学习服务；
  - 只认 BFF 会话令牌，登记进令牌矩阵；讲评课没配置时 501。
- **打开讲评课**：
  - `skill_node`、`item_ref` 改为可选，由学习服务按目录补全；
  - 目录里没有这节课，或带来的节点、题与目录不一致 → 422 `LESSON_NOT_AVAILABLE`。这两种情况不建会话、不调 `teach`，BFF 释放预留；
  - 目录暂时取不到 → 503，BFF 用同一个键重试；
  - BFF 过渡期（PR C 之前）照旧带节点与题，所以本 PR 合并后现有流程不变。
- **再练与复测的条目视图**：原题有讲评课时另带 `lesson_ref`（只有编号与版本）。
  - 题目引用本身不变：学习服务给 BFF 的 `source_item_ref` 照旧，BFF 不转给前端；
  - 目录在事务外先取，取不到时条目照常列出，只是不带 `lesson_ref`；
  - app 在讲评课开着时把同一个目录交给再练服务，复测经 `practice` 用同一个。
- **接口定义**：`lesson-internal.v0.yaml` 加目录接口、`LessonCatalog`，open 的 `skill_node` 改为可选；`practice-internal`、`retest-internal` 的 `Entry` 加可选 `lesson_ref`。
- **测试**：
  - `tests/test_lesson_session.py`：假适配器加 `list_lessons`；新增 11 条——目录字段与缓存、5 种不合格目录不缓存、没有方法为空与暂时失败 503、坏请求、按目录补全、4 种目录对不上不建会话、目录暂时失败可重试；
  - `tests/test_lesson_catalog.py` 4 条（不连库）：缓存过期与请求形状、`by_item` 取最小的一节与降级为空、没有讲评课的学科不调适配器、投影拒收；
  - 再练、复测各 1 条：条目带 `lesson_ref` 并合接口定义；
  - 令牌矩阵加目录接口。

### 验证
- `tests/test_lesson_session.py` 48 条、`tests/test_lesson_catalog.py` 4 条、令牌矩阵 55 条通过；再练与复测新加的 2 条通过。
- `check-group-learning-a`、`check-group-learning-b`（严格模式）通过：学习服务全量单测 4165 条；诊断、掌握、再练、复测、今日编排、learning-internal v1、审核台、模考、staging 的变异全部被发现；`deploy-check` 通过。

### 决定与差异
1. **目录接口带 `student_ref`**：目录与学生无关，但学习服务的每个内部接口都先过库层处理判据（退出、删除、没有有效同意 → 404），这里照旧。
2. **同一道题有几节讲评课时取编号、版本最小的一节**：规格没写；在有真实讲评课之前不会碰到，见 B1。
3. **过渡期两种请求都收**：节点与题可带可不带，带了必须一致。等 PR C 之后 BFF 不再带，再删掉这两个可选字段（另起小改动）。

### 待人确认
- **B1**：同一道题有几节讲评课时，条目只带一节（编号、版本最小的那节）。若要按版本取最新一节、或者都列出来，请定。

## PR C：BFF 目录路由、打开只收讲评课引用、条目转出 `lesson_ref`
### 做了什么
- **新学生端路由 `GET /bff/v0/students/{student_id}/lessons/catalog/{subject}`**：
  - 转学习服务 `POST /internal/v1/lesson/catalog`（`{student_ref, subject}`，学生只进请求体）；
  - 只回 `{subject, lessons: [{lesson_ref: {lesson_id, lesson_version}, title, step_count}]}`，逐条按白名单投影。学习服务的形状不对（学科不对、标题空或超过 40 个字、步数不在 1–100、引用多键）整份 503，不挑着给；
  - 只认学生本人：家长 403；兄妹、另一户、不存在、没有讲评课的学科码一律同一个 404，都不调学习服务；
  - 不扣次、不预留、不受宵禁限制；限流用讲评课已有的每会话桶 `lesson.session.minute`；
  - 学习服务回 501 → 503 `LESSON_UNAVAILABLE`；结果未知 → 重试后仍未知 503 `UPSTREAM_UNAVAILABLE`。
- **打开讲评课**：请求体只收 `subject`、`lesson_ref`、`idempotency_key`。带了节点或题 422 `INVALID`；转学习服务时也不再带。计量指纹去掉节点与题，只剩学生、学科与讲评课引用。预留、结算、释放的规则不变。
- **再练与复测的列表条目**：讲评课开关开着且配了学习服务时，转出学习服务给的可选 `lesson_ref`（按 `src/lesson/ref.ts` 校验，只有编号与版本；不合格整页不给）；开关关着或没配学习服务时不转，也不看这个字段。
- **讲评课引用的校验抽成 `src/lesson/ref.ts`**：打开、目录、条目三处共用。
- **接口定义**：`lesson.v0.yaml` 升到 0.2.0（目录路径、`LessonRef`、`LessonCatalog`，打开请求体去掉节点与题）；`practice.v0.yaml`、`retest.v0.yaml` 的 `Entry` 加可选 `lesson_ref`。
- **前端两处跟着改**（不然 e2e 断）：`apps/web/src/lesson/actions.ts` 打开时不再带节点与题；e2e 的假学习服务 `lessonOpen` 改为只收新形状；`e2e/practice-off.spec.ts` 的请求体同步。讲评课地址里的 `node`、`item` 照旧能读，PR D 改地址时一并去掉。
- **测试**：
  - `tests/lesson-units.test.ts`：打开请求体（节点与题 422）、指纹（换课、换版本不同）、讲评课引用、目录投影、客户端目录请求；
  - `tests/lesson.integration.test.ts` 加 3 条：目录白名单与不扣次、只认本人与有讲评课的学科（含 POST 405）、形状不对整份 503 与 501、未知；开关关与没配学习服务两条改为覆盖三条路由；
  - `tests/practice-units.test.ts`、`tests/retest-units.test.ts` 各 1 条；`tests/practice.integration.test.ts`、`tests/retest.integration.test.ts` 各 1 条（开关开、没配学习服务、开关关三种外壳）；
  - 路由矩阵登记目录样例；计量静态检查加“目录不预留、不结算、不释放”与“打开拒收节点与题”；
  - `tests/mutations.sh`：改 4 条过时锚点，新增 15 条（讲评课 9 条、再练 3 条、复测 3 条）。

### 验证
（见下方“检查结果”）

### 决定与差异
1. **路径与规格不同**：规格写的是 `GET .../lessons?subject=`。BFF 的路由表只按路径分发，查询参数不交给 handler（再练、复测都是这个原因把学科码放路径里），所以学科码放路径。
   - 先试过 `.../lessons/{subject}`：`open`、`ask` 也合 `{subject}` 的形状，`GET .../lessons/open` 会落到目录、回 404，而不是像其他表内路径那样 405。路由矩阵的“表内路径的其他方法 405”拦住了这一点；
   - 所以多一段 `catalog`，三条路由的形状互不重叠。
2. **路径里是前端学科码**（`math`），与再练、复测同一套；只有 `math` 有讲评课，其余学科码 404（不是空列表），前端只在有讲评课的学科调。
3. **计量指纹变了**：以前含节点与题，现在只有讲评课引用。只影响开关打开（dev / test）时、本 PR 之前用某个键打开、本 PR 之后又用同一个键重试的那一次（409 换内容），重新打开即可；staging 以上开关一律关闭，没有影响。
4. **条目上的 `lesson_ref` 跟讲评课开关走**：学习服务那边在讲评课配好时才带；BFF 这边再看自己的讲评课开关与学习服务配置，两边都开着才转，免得前端出现点了打不开的入口。
5. **学习服务打开接口的可选 `skill_node`、`item_ref`**：BFF 从本 PR 起不再带。删除这两个字段另起小改动（学习服务与它的接口定义），不放在本 PR。

## PR D：前端科目页列表、再练与复测判分之后的入口、新地址与端到端
### 做了什么
- **讲评课地址**改为 `/s/{学生}/learn/{学科}/lesson/{编号}?v={版本}`：
  - 页面只认编号与版本；
  - 以前带 `node` 或 `item` 的地址一律 404，在读会话之前判断；
  - 打开时 server action 只收 `{lessonId, lessonVersion}` 两个键。
- **E1 科目页“讲评课”一节**（`src/lesson/LessonList.tsx`）：
  - 开关开着、学科是数学时才渲染，并取一次目录（`GET .../lessons/catalog/math`）；开关关时不渲染、也不发请求；
  - 每节一行：标题（链接到讲评课页面）与步数；
  - 目录为空时显示“这一科还没有讲评课”，取不到时只在这一节显示“暂时无法获取”，科目页其余部分照常；
  - 前端按同一份白名单再挑一遍（编号、版本、标题与步数），任何一条不合格整份不显示。
- **E2 再练与复测判分之后的“看这道题的讲评课”**：
  - 作答页在讲评课开关开着、学科是数学时，先取一次列表，找到这一条的 `lesson_ref` 交给作答组件；
  - 组件只在判分结果那一段渲染入口，作答前不渲染；
  - 复测条目作答后会离开列表，所以在作答之前取引用。
- **BFF 客户端**：`src/lib/lesson-api.ts` 的 `call` 加 GET（目录），仍只有一处 `fetch`；目录用读接口的超时（10 秒）。
- **文案**：`LESSON` 加 6 条 `{{TODO_COPY:lesson.*}}` 占位（W6），已并入壳文案的禁语扫描。
- **e2e 假学习服务**：
  - 加 `POST /internal/v1/lesson/catalog`：两节合成讲评课 `syn.lesson.e2e`、`syn.lesson.broken`；
  - 再练 106、复测 402 两条合成条目带 `lesson_ref`。选这两条，是因为它们判分待复核、条目仍是 due，可以反复作答，不改列表行数与截图基线。
- **测试**：
  - `tests/lesson.test.ts`：地址、白名单、开关闸门与“入口只在判分结果那一段”的静态检查；
  - `e2e/lesson.spec.ts`：目录列表 → 打开播放；再练、复测作答前后的入口；旧地址 404；
  - `e2e/practice-off.spec.ts`：开关关时科目页没有列表与链接，BFF 目录路由 404。

### 验证
（见下方“检查结果”）

### 决定与差异
1. **E2 的引用在作答前就交给了作答组件**：引用会出现在页面数据里，但不渲染。
   - 原因：复测条目作答后会离开列表，作答之后再取就找不到了；
   - 引用只有讲评课编号与版本，打开讲评课仍要点“开始”并计一次。
   规格第五节第 7 条说的是“作答前不显示”，这一点满足；若要求作答前页面数据里也不能有，需要 BFF 在作答结果里带引用，另起。
2. **入口文案“看这道题的讲评课”**：讲评课讲的是原题，再练、复测给的是同类题，文案暂按规格写，待文案定稿（W6）。
3. **科目页每次多一次目录请求**：只在开关开着、数学科目时有；BFF 不缓存，学习服务按学科缓存 5 分钟，所以不会每次都调到阿鸦。
