# 审核台前后端请求体核对（T35）· 实现记录

2026-11-10 用户确认（“按建议，默认值都同意，开始实现”）：spec 第六节待人确认 1–3 全部按默认——只核对请求体；用已有的 TypeScript 编译器 API，
不引入生成器；导出文件提交进仓库。不涉及必停项。

## 交付
- **后端导出**：`services/learning/scripts/export_review_requests.py` 从审核台后端（全部功能打开）的 OpenAPI 取 21 个带请求体接口的 schema
  （17 个严格模型），归一成字段的 required / type / nullable / enum（数组另记元素枚举），写到 `apps/web/src/lib/review-api.requests.json`；
  `--check` 只比较。导出时核对每个请求体模型都是 `extra=forbid`。`services/learning/tests/test_review_requests_export.py` 核对提交的文件就是现在的导出。
- **前端集中声明**：`apps/web/src/lib/review.ts` 新增 `ReviewRequests`（键是“方法 路径模板”，与后端 OpenAPI 相同）与 `send()`
  （带请求体的调用一律经它，路由键决定请求体类型与 HTTP 方法）；21 处调用改用 `send()`。枚举字段改成字面量联合：`Decision`、`to_admission`、
  `source_type`、`license_status`、考情记录类型、节点编辑的认知层次与范围；`editNode` 的请求体从 `Record<string, unknown>` 改为 `NodeEditReq`。
  新增类型守卫 `isDecision`、`isAdmission`、`isSourceType`、`isLicenseStatus`、`isPolicyRecordType`；`src/app/review/actions.ts` 的
  `includes()` 检查换成守卫（校验规则不变，只是让类型收窄）。页面与调用逻辑不变。
- **前端核对**：`apps/web/tests/review-requests.test.ts` 用 TypeScript 编译器 API 读 `ReviewRequests`，与导出文件比：接口集合、字段集合、必填；
  后端有枚举的字段前端必须是字面量联合且每个取值后端都接受；另核对 `send()` 的实际路径与路由模板对得上、没有绕过 `send()` 直接用 `call()` 发请求体。
- **变异**：前端 6 条（多字段、枚举写成 string、必填写成可选、少登记接口、路径与模板不符、导出文件过期），在
  `apps/web/tests/review-requests-mutations.test.ts` 里逐条跑并断言失败，随 `make web-test` 的单测运行；后端 3 条（加必填字段、改枚举、
  可选改必填而不重新导出）进 `review-console-mutation` 的“请求体导出”组。学习服务变异脚本的副本另软链 `apps/`（只读，读导出文件）。
- 顺带：仓库根 `.gitignore` 补 `node_modules/`。

## 测试结果（本机）
- 前端单测全量、`next build`、tsc 通过；`review-requests.test.ts` 2 项、`review-requests-mutations.test.ts` 对照组 + 6 条变异全部通过。
- `make web-test`（`PW_CHROMIUM_EXECUTABLE` 指本机 Chromium 1243）：端到端 177 项中 175 通过；失败的 2 项是 glyphs DPR 2（light、dark），
  本机 Chromium 的已知环境差异（tech-debt-batch2 记录：旧代码同样失败），与本任务无关；审核台页面的用例全部通过。
- `review-console-mutation` 全组：纯函数 19、内测纯函数 4、发布门禁 C9 5、请求体导出 3、库上 17、内测库上 11，全部被发现。
- 学习服务 `test_review_requests_export.py` 2 项通过。

## 与 spec 的偏差
1. **枚举口径**：spec 写“前端必须是同一组字面量”。实现为“前端取值是后端枚举的子集、且不能是宽泛的 string”：审核台的学科下拉只有三科
  （搁置的英语 `echo` 后端仍接受，前端不出现），要求逐字相同会逼前端加一个不可用的选项。
2. **变异放的位置**：前端变异没有单独的 make 目标，写成单测内逐条运行（每条约 1.3 秒，共约 9 秒），随 web-test 的单测跑。

## 经验
- `next build` 会类型检查 `tests/` 下的文件：给子进程传 `env` 时 `ProcessEnv` 要求带 `NODE_ENV`。
- 原有静态测试（`tests/review.test.ts` 的考情记录类型一条）按旧写法找源码字符串，改成类型守卫后要同步改断言。

## 待人确认
- 无（响应类型的核对要先给后端补响应模型，spec 第六节第 1 条已定为另起任务，需要时再起草）。
