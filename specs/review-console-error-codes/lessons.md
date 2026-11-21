# 审核台错误码与前端提示的一致性核对 · 实现记录

2026-11-15 用户确认（“按建议，默认值都同意，开始实现”）：spec 第五节待人确认 1–3 全部按默认——两条提示由工程写；单独的导出文件；静态扫描后端源码。
不涉及必停项。

## 交付
- **补提示**（`apps/web/src/lib/review.ts` 的 `RESULT_LABEL`）：`TOO_LARGE`“提交的内容太长了，请删减后再试”、`MISDIRECTED`“请求地址不对，请联系技术负责人”。
  `DETAIL_CODES` 加 `export`（测试引用，逻辑不变）。
- **导出**：`services/learning/scripts/export_review_errors.py` 按语法树扫描 `review_console/*.py` 里 `ReviewError(状态, "码")` 的调用，
  加上库错误映射 `store._PG_MAP` 的取值与 `db_error` 的兜底 `INTERNAL`，写到 `apps/web/src/lib/review-api.errors.json`（19 个码，带状态码）；
  错误码不是字面量的调用只允许在 `store.db_error` 一处，别处出现直接报错（免得漏收）。`tests/test_review_errors_export.py` 核对提交的文件就是现在的导出。
- **前端核对**：`apps/web/tests/review-errors.test.ts`——每个后端错误码在 `RESULT_LABEL` 里都有非空提示；`DETAIL_CODES`（会显示后端说明的码）只能是后端真会发的码。
- **变异**：前端 3 条（删一条提示、后端加码前端无提示、显示说明的码后端不会发），写在 `apps/web/tests/review-errors-mutations.test.ts` 里逐条跑；
  后端 2 条进 `review-console-mutation` 的“错误码导出”组（代码里新增错误码不导出、库错误映射加码不导出）。

## 测试结果（本机）
- 前端单测 255 项、tsc 通过；学习服务三份导出核对 11 项通过；“错误码导出”组 2 条变异全部被发现。

## 与 spec 的偏差
- 前端变异多一条（“显示说明的码后端不会发”，spec 写两条）。

## 经验
- 按码查提示的页面，兜底文案会掩盖缺口：缺提示不报错，只是所有人看到“出错了”。这类映射表要有“对出处”的核对。

## 待人确认
- 无。各页在 `RESULT_LABEL` 之上覆盖的提示表（队列、打标、反馈）不在本任务范围。
