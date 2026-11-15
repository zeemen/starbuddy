# 审核台错误码与前端提示的一致性核对 · 规格草稿

- 状态：**草稿，待人确认**（第五节）。
- 依据：`specs/review-console-responses/lessons.md` 待人确认（错误响应的核对另起）；审核台前端 `apps/web/src/lib/review.ts` 的 `problemFrom` 与各结果提示表。
- 不涉及必停项：只加两条审核台（staff 用）的中文提示与一条测试；不改后端、契约、权限。

## 一、现状
- 错误响应统一是 `application/problem+json`：`{type, status, code[, detail]}`（`review_console/errors.py`）；结构简单，前端 `problemFrom` 已按白名单取
  `code`（`^[A-Z_]{1,40}$`）、只对少数码显示 `detail`。单独给这个结构做类型核对意义不大。
- 真正会漂的是**错误码集合**：后端能发出的码来自两处——代码里的 `ReviewError(状态, "码")`（16 个）与库错误映射 `store._PG_MAP`（另 3 个：
  FORBIDDEN、CONFLICT、INVALID_REFERENCE）。前端按码查中文提示（`RESULT_LABEL`，队列、打标、反馈页另有在它之上覆盖的表），查不到落到 `INTERNAL`
  的“出错了，请稍后再试”。
- 2026-11-15 比对：19 个码里前端缺 2 个——`TOO_LARGE`（413，请求体超过上限，例如改节点时文字过长）与 `MISDIRECTED`（421，Host 不对，部署配置问题，
  正常使用碰不到）。其余 17 个都有提示。

## 二、默认做法
1. **补提示**：`RESULT_LABEL` 加 `TOO_LARGE: '提交的内容太长了，请删减后再试'`、`MISDIRECTED: '请求地址不对，请联系技术负责人'`（审核台是 staff 页面，不是面向学生家长的文案）。
2. **核对测试**：学习服务导出“后端能发出的错误码”（静态扫描 `ReviewError(…, "码")` 与 `_PG_MAP` 的取值）写进现有的 `review-api.responses.json` 旁的
   `review-api.errors.json`（或并入响应导出，见待人确认第 2 条）；前端测试断言每个码在 `RESULT_LABEL` 里都有提示，且 `DETAIL_CODES` 只含后端会发的码。
3. **变异**：后端新增一个错误码不重新导出、前端删一条提示，各一条。

## 三、不做
- 错误响应结构（`type/status/code/detail`）的类型核对（结构简单、前端已按白名单取值）。
- 各页覆盖表（队列、打标、反馈）的文案调整。

## 四、验收
- 19 个码都有前端提示；导出与后端一致、前端测试与变异通过；`make learning-test`、`make web-test` 的单测部分通过。

## 五、待人确认（括号里是默认）
1. **两条新提示的文字**（按第二节第 1 条；staff 页面，工程写、不走人设审核）：还是交给产品定？
2. **导出文件**：单独一个 `review-api.errors.json`（默认）；还是并进 `review-api.responses.json`？
3. **扫描方式**：静态扫描后端源码里的 `ReviewError(…, "码")` 与 `_PG_MAP`（默认，简单、不需起服务）；还是把错误码收成一个登记表常量、各处只引用常量（改动面大）？
