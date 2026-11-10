# 审核台前后端请求体核对（W13 技术债 T35 的“前端类型手写”一项）· 规格草稿

- 状态：**草稿，待人确认**（第六节）。
- 依据：`docs/gates/w13-debt-and-decisions.md` T35；`specs/release-gate-sprint4/lessons.md` 待人确认第 5 条（契约审查 S10 后半：
  “前端类型是手写的，没有与后端模型自动核对；审核台接口进契约或导出 schema 时再做”）；CLAUDE.md 规则 10（引入依赖前查许可证）。
- 不涉及必停项：审核台接口不在 packages/contracts，不改迁移、权限、判分、人设。

## 一、现状
- 审核台后端（`services/learning/review_console/app.py`，FastAPI）：**请求体**都是严格的 pydantic 模型（`extra="forbid"`，共 17 个，
  如 `SignBody`、`AdmissionSignBody`、`NodeEdit`）；**响应**是服务层手拼的字典，没有响应模型。
- 前端（`apps/web/src/lib/review.ts`，870 行，43 处调用）：请求体与响应的 TS 类型全部手写，散在各个调用函数的参数里
  （如 `signContentVersion(staff, id, body: { decision: string; basis_code?: string; expected_digest: string })`）。
- 风险：后端加了必填字段、改了枚举、或前端多带了一个键（后端 `extra="forbid"` 即 422），只有点到那个按钮才发现；单测与端到端只覆盖部分路径。

## 二、为什么不直接“自动生成前端类型”
- 响应没有模型，FastAPI 导出的 OpenAPI 里没有响应结构，生成不出响应类型；给 40 多个接口补响应模型是一次较大的后端重构。
- 生成器（如 openapi-typescript）是新依赖，要查许可证并锁版本；而请求体这一半用仓库已有的 TypeScript 编译器 API 就能核对。

## 三、默认做法：只核对请求体，不引入新依赖
1. **后端导出**：新脚本 `services/learning/scripts/export_review_requests.py`，从 `app.openapi()` 取每个接口的请求体 schema，
   归一成 `{ "POST /review/v0/...": { 字段: { required, type, enum? } } }`，写到 `apps/web/src/lib/review-api.requests.json`（提交进仓库）。
   学习服务单测核对“提交的文件 = 现在导出的结果”：后端改了模型而没重新导出即失败。
2. **前端集中声明**：`review.ts` 新增一个 `ReviewRequests` 接口（键是“方法 路径模板”，值是请求体类型），各调用函数的 `body` 参数改为
   `ReviewRequests['POST /review/v0/...']`；不改任何调用逻辑与页面。
3. **前端核对**：`apps/web/tests/review-requests.test.ts` 用 TypeScript 编译器 API（`typescript` 已是前端依赖）读出 `ReviewRequests` 每个键的
   属性名、是否可选、字符串字面量联合，与导出的 JSON 逐项比较：路由集合相同、字段集合相同、必填一致、枚举一致（后端有枚举的字段，前端必须是同一组字面量，
   不能是宽泛的 `string`）。格式核对（pattern、长度）不在前端重复。
4. **变异**：后端模型加一个必填字段、前端多一个字段、前端把枚举写成 `string`、导出文件过期，各一条，断言对应测试发现（接进 review-console-mutation 与
   web 的静态测试）。

### 顺带的小事
- 仓库根 `.gitignore` 补 `node_modules/`（各子目录各自忽略了，根目录没有；`specs/launch-test-2026/blockers.md` 第 15 行）。

## 四、不做
- 响应类型的核对与生成（要给后端补响应模型，见待人确认第 1 条）。
- 审核台接口进 packages/contracts（契约变更，必停）。
- T35 的其余几项（撤下依据编码清单、逐条确认高风险差异、错别字识别、地区投影风险级别）：都要教研或产品定口径。

## 五、验收
- 导出文件与后端一致（学习服务单测）；`ReviewRequests` 与导出文件一致（web 静态测试）；四条变异都被发现。
- `make learning-test`、`make web-test`（单测与静态检查部分）、`review-console-mutation` 通过；页面行为不变（端到端照常通过）。

## 六、待人确认（括号里是默认）
1. **范围**：只核对请求体（默认）；还是同时给后端补响应模型、连响应类型一起核对（后端较大重构，单独一个任务更合适）？
2. **方式**：用已有的 TypeScript 编译器 API 核对、不引入生成器（默认）；还是引入 openapi-typescript 之类的生成器（新依赖，要查许可证、锁版本）？
3. **导出文件放哪**：提交进 `apps/web/src/lib/`（默认，前端测试直接读，改动在 PR diff 里看得见）；还是只在测试时临时生成？
