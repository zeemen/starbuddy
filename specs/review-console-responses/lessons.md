# 审核台响应模型与前后端响应类型核对（T35 后一半）· 实现记录

2026-11-11 用户确认（“按建议，默认值都同意，开始实现”）：spec 第六节待人确认 1–3 全部按默认——运行时严格（`extra="forbid"`，
模型外的键 500）；42 个接口一次做完、分四个提交；请求体与响应各一个导出文件。不涉及必停项。

## 交付
1. **后端响应模型**（`services/learning/review_console/responses.py`）：42 个接口各一个 pydantic 模型，一律 `extra="forbid"`；
   `ROUTES` 表登记“方法 路径模板 → 模型”，`app.py` 的每个路由装饰器经 `_resp()` 按表挂 `response_model`（漏登记的接口建应用时即 KeyError）。
   同时设 `response_model_exclude_unset=True`：只输出服务层给了的键，**输出与挂模型之前逐字相同**；有时不给的键写成 `x: T = None`
   （可选、给了不能为空，pydantic 不校验默认值）。返回值与模型不符时新的 `ResponseValidationError` 处理器回 500 `INTERNAL`，
   日志只记路由模板与出错位置、类型（`review_response_invalid`），不记值。
2. **覆盖核对**：`tests/test_responses_coverage_review.py` 在子进程里带收集插件（`tests/review_response_collector.py`）把
   `tests/test_review_*.py` 跑一遍，断言 `ROUTES` 的每个接口至少一次 2xx（即通过了响应模型校验）、收集到的接口都在 `ROUTES` 里。
   原来没被打到的只有“放回队列”（`POST /queue/{task_id}/release`），在复核闭环（`tests/review_loop.py`）里补了一步：领取 → 放回 → 再领取。
3. **导出**：`services/learning/scripts/export_review_responses.py` → `apps/web/src/lib/review-api.responses.json`（递归结构：对象、数组、
   map、any；`--check` 只比较）。导出前核对应用注册的接口集合与 `ROUTES` 相同、每层模型都是严格模型。
   `tests/test_review_responses_export.py` 核对提交的文件就是现在的导出。
4. **前端**（`apps/web/src/lib/review.ts`）：新增 `ReviewResponses`；`send()` 的返回类型改由路由键决定，新增 `fetchRoute()` 给不带请求体的调用，
   原来 19 处 `call<T>()` 与 22 处 `send<T>()` 的手写类型参数全部去掉。`tests/review-responses.test.ts` 用 TypeScript 编译器 API 递归比较
   （接口、字段、必填、可空、标量种类、数组元素、map 的值、枚举），并核对每个调用的路径与路由模板对得上、不再直接用 `call<T>()`。
5. **变异**：后端 4 条进 `review-console-mutation`（“响应导出”组：模型删字段不重新导出、枚举加取值不重新导出；“响应模型库上”组：
   服务层多拼一个键、有时不给的字段改必填——后两条在运行时就被响应校验拦下，测试里是 500）。前端 7 条写在
   `apps/web/tests/review-responses-mutations.test.ts` 里逐条跑（少一个字段、可空写成不可空、枚举少一个取值、后端没有枚举前端写成字面量、
   少登记一个接口、调用路径与模板不符、导出文件过期）。

## 核对发现的前端类型问题（51 处，已按后端真实返回改正）
- **前端声明了后端不给的字段**：内容版本差异的 `version` 带 `skill_nodes` / `items`（快照里的版本行没有登记计数，只有列表行有）；
  内容版本与考情方案的差异条目带 `issues` / `rubric_version`（那是地图差异才有的）。改为 `ContentVersionHead`、`ContentChange`。
- **必填 / 可空写错**：页面合并的 `merge_commit` 写成可选（后端每条都给，待审的为 `null`）；被取代的合并 `merge_commit` 写成不可空。
- **前端缺字段**（页面没用到，但类型说谎）：节点 `parent_id` / `error_type`、变更单 `created_at`、打标候选 `status`、准入记录的六段与
  `applies_review_id` / `affected_rubric_versions`、考情方案的 `subjects` / `batches` / `transitions` 与 `source_document_id`、
  考情记录的对象与城市届别、方案摘要的 `review_level`、页面报告的 `format`、内容版本所指地图版本的 `subject`。
- 页面代码没有读到任何后端不给的字段（改类型后 tsc 与 `next build` 通过），所以**没有页面行为变化**。
- 服务层没有多拼的键：挂上 `extra="forbid"` 后学习服务全量测试一次通过（spec 第五节要求“逐条列出”的多余字段：无）。

## 与 spec 的偏差
1. **枚举口径反过来了**：spec 写“前端是后端的子集（与请求体同一口径）”。响应是前端接收，子集会漏处理后端可能给的取值，方向不对。
   实现为：后端有枚举时，前端要么是宽泛的 `string`，要么是**取值完全相同**的字面量联合；后端没有枚举时前端不能写成字面量联合
   （前端以为窄、后端不保证）。这条反过来倒逼后端把枚举写进模型（`Literal`），运行时也会核对。
2. **前端变异**：spec 列的“枚举写成 string”在新口径下是允许的，换成“枚举少一个取值”与“后端没有枚举前端写成字面量”，另加“调用路径与模板不符”，共 7 条（spec 4 条）。
3. **导出不走 `app.openapi()`**：FastAPI 的响应 schema 是序列化模式，会把带默认值的字段标成必填，与 `exclude_unset` 的实际输出不符；
   改为按 `ROUTES` 用校验模式生成，另核对应用的接口集合与 `ROUTES` 相同。导出写成单独的脚本（spec 允许）。
4. **自由结构不展开**：差异的旧值 / 新值、变更单签字内容、复核任务的来源快照（`platform.review_task_detail` 的 JSON，按来源种类结构不同）
   在后端写成 `Any`，比较时不往下比；前端仍保留 `GradeSource | ClaimSource | DiagnosisStepSource` 的手写联合。要核对来源快照，
   需要后端按来源种类拆模型（另起）。

## 可空的判断口径
基表列看 NOT NULL（在测试库的 information_schema 里查过 kge.map_version、tag_candidate、knowledge_node、policy.exam_scheme 等）；
库函数返回的表与 JSON、服务层拼的字典看代码；拿不准的按可空——运行时宁可前端多处理一个空值，也不因一个空值让整页 500。
例外：`kge.content_review.risk_level` 是 NOT NULL 且有 CHECK，签字记录与版本记录的 `risk_level` 定为不可空的 low / medium / high。

## 测试结果（本机）
- 学习服务全量：3994 项通过（中途重新导出过一次，导致那一轮覆盖核对与导出核对失败；重跑这两项与请求体导出共 7 项通过）。
- 前端单测全量通过（含 `review-responses.test.ts` 2 项、`review-responses-mutations.test.ts` 对照组 + 7 条变异；
  `review-requests-mutations.test.ts` 的“枚举写成 string”一条原文因 `ReviewResponses` 里有同一行而不唯一，原文带上前一行）。
- `make review-console-mutation` 八组全部被发现：纯函数 19、内测纯函数 4、发布门禁 C9 5、请求体导出 3、响应导出 2、库上 17、内测库上 11、
  响应模型库上 2（这一组第一次的对照组没过：`test_review_pages.py` 有一条要读知识服务代码、变异的临时副本里没有，改为只跑
  `test_review_console.py`、“多拼一个键”放到地图差异视图上之后单独重跑通过）。
- `make web-test`（端到端）：运行中，结果另记。

## 经验
- 响应校验与请求校验方向相反：请求体“前端可以更窄”，响应“前端必须覆盖全部取值”。照搬请求体的口径会让核对放过真问题。
- `exclude_unset` 是挂响应模型而不改输出的关键：否则可选字段会多出一串 `null`，前端的 `x?: T` 也会变成“总是给、可能为空”。
- 覆盖核对要在子进程里跑：写成同一进程里的会话钩子，`-k` 或单文件运行时就得特判，不如单独一条测试清楚。

## 待人确认
- 无新增。复核任务来源快照的按种类建模、错误响应（`application/problem+json`）的类型核对，需要时另起任务。
