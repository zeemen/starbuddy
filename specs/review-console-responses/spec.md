# 审核台响应模型与前后端响应类型核对（W13 技术债 T35 后一半）· 规格草稿

- 状态：**草稿，待人确认**（第六节）。
- 依据：`docs/gates/w13-debt-and-decisions.md` T35（“2026-11-09 核对”：响应类型仍手写）；`specs/review-console-types-t35/` 第四节与待人确认第 1 条
  （响应类型的核对要先给后端补响应模型，另起任务）；`specs/release-gate-sprint4/lessons.md` 待人确认第 5 条（契约审查 S10 后半）。
- 不涉及必停项：审核台接口不在 packages/contracts；不改迁移、RLS、权限、计量、判分与掌握计算、人设。只给已有接口的返回加类型与核对，
  返回的内容不变（第三节第 1 条说明唯一的例外：模型之外的键会被拒，等于把返回字段收成白名单）。

## 一、现状
- 后端（`services/learning/review_console/app.py`）42 个接口，**没有一个带 `response_model`**：返回值是服务层（`service.py`、`store.py`）手拼的字典，
  OpenAPI 里没有响应结构。
- 前端（`apps/web/src/lib/review.ts`，931 行）响应类型全部手写：57 个接口 / 类型别名（`DiffView`、`ContentView`、`PolicyView`、`QueueTask`、
  `TagWorklist`、`PreflightView`、`FeedbackRow` 等），散在 `call<T>()` / `send<T>()` 的类型参数里。T35 已把请求体收进 `ReviewRequests` 并自动核对。
- 风险：后端改名、删字段、改可空或改枚举，前端编译照过，页面只在点到那一处时显示空白或 `undefined`；反过来后端多返回一个字段（例如反馈、
  退出测试档案里多带一列个人信息）也没有任何东西拦。

## 二、默认做法
1. **后端响应模型**：新模块 `services/learning/review_console/responses.py`，按接口写 pydantic 模型，一律 `extra="forbid"`；
   42 个接口都挂 `response_model=`（现在没有返回 204 或无响应体的接口）。效果：
   - OpenAPI 带上响应结构，可以像请求体一样导出；
   - **返回字段收成白名单**：服务层多拼的键会在响应校验时报错（500，日志记接口名与多出的键名，不记值），不会悄悄发给前端——
     这是唯一的行为变化，测试里会先暴露；
   - 只描述 2xx 响应；错误响应仍走 `errors.py` 现有的 `application/problem+json`，不在本任务。
2. **覆盖核对**：学习服务测试加一个收集器，记下审核台各测试文件里每个接口至少一次 2xx 响应（经过响应模型校验）；新测试断言 42 个接口都被打到过。
   没被现有测试打到的接口补最小用例（合成数据）。这样“模型与真实返回对得上”有测试兜底，不靠人眼。
3. **导出**：扩展 `services/learning/scripts/export_review_requests.py`（或同目录新脚本 `export_review_responses.py`，见待人确认第 3 条），
   把响应 schema 归一成递归结构（对象 → 字段：required / type / nullable / enum；数组 → 元素；`$ref` 展开）写到
   `apps/web/src/lib/review-api.responses.json`，提交进仓库；`--check` 只比较；学习服务单测核对提交的文件就是现在的导出。
4. **前端集中声明与核对**：`review.ts` 新增 `ReviewResponses`（键与 `ReviewRequests` 同为“方法 路径模板”），`call()` / `send()` 的返回类型由路由键决定，
   不再在调用处写 `call<T>`；现有 57 个手写类型保留名字，作为 `ReviewResponses` 的值，按核对结果改正。
   `apps/web/tests/review-responses.test.ts` 用 TypeScript 编译器 API 递归比较：接口集合相同；每层字段集合相同；必填与可空一致；
   后端有枚举的字段前端必须是字面量联合且是后端的子集（与 T35 请求体同一口径）。比较时把 `string | null` 与后端的“可空”对应。
5. **变异**：后端 4 条（服务层多拼一个键、模型删一个字段不重新导出、可选改必填、枚举加一个值）进 `review-console-mutation`；
   前端 4 条（少一个字段、可空写成不可空、枚举写成 string、少登记一个接口）写在 `apps/web/tests/review-responses-mutations.test.ts` 里逐条跑。

## 三、不做
- 错误响应的类型（统一结构，另起）；
- 审核台接口进 packages/contracts（契约变更，必停）；
- 页面逻辑与显示：核对出的前端类型错误只改类型；若某处页面依赖了后端其实不给的字段，记进 lessons 并在同一 PR 里按后端真实返回修页面，
  不改后端返回内容；
- T35 其余几项（撤下依据编码清单、高风险差异逐条确认、错别字识别、地区投影风险级别）：要教研或产品定口径。

## 四、交付顺序（每步一个提交）
1. 后端响应模型 + 覆盖核对（先让 `learning-test` 在 `extra="forbid"` 下全绿，期间发现的多余字段逐个记录：是删掉还是写进模型）；
2. 导出脚本与导出文件；
3. 前端 `ReviewResponses`、类型改正与核对测试；
4. 两侧变异。

## 五、验收
- 42 个接口都挂了响应模型，且每个都在测试里至少一次通过响应校验；导出文件与后端一致；`ReviewResponses` 与导出文件一致。
- 新增变异 8 条全部被发现；`make learning-test`、`make web-test`、`review-console-mutation` 通过；审核台端到端用例照常通过（页面行为不变）。
- 服务层多拼字段的处理（删掉 / 写进模型）逐条列在 lessons.md。

## 六、待人确认（括号里是默认）
1. **运行时是否严格**：响应模型 `extra="forbid"`，多出的键在运行时就报 500（默认：字段白名单，宁可报错也不把模型外的字段发出去；测试先兜底）；
   还是运行时放宽（`extra="ignore"`，多出的键悄悄丢掉，只在测试里严格）？
2. **范围**：42 个接口一次做完（默认，分四个提交）；还是先做只读的 GET 接口（18 个），写操作的 24 个回执下一批？
3. **导出文件**：请求体与响应各一个文件（默认：`review-api.requests.json` 不动，新增 `review-api.responses.json`）；还是合成一个？
