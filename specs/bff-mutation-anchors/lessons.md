# 修 BFF 变异表里过时的原文 · 实现记录

## 交付
- `services/bff/tests/mutations.sh`：6 条变异的原文换成当前写法。
  - 事件无权当成功：`{ kind: 'forbidden' }` → `{ kind: 'not_found' }`。
  - 诊断：开始不核对学生本人：`return FORBIDDEN;` → `return NOT_FOUND;`。
  - 诊断：取当前题不核对会话归属：同上。`ownsSession` 这一行在文件里出现 6 次，原文带上 `currentStep` 的函数头尾与下一行注释，钉在“取当前题”那一处。
  - H6：事件与探针的“不是本人”：
    - 意思反过来了，从“改回 404”改成“改回 403”；
    - 原文带上 `writeReply` 的函数头，因为 `onboardingReply` 里有同样一行，而 H6 要求原文唯一；
    - `EXPECT_H6` 登记的第一条失败改为“不存在 POST /students/{student_id}/events（x-sb-not-self 404）”。
  - 闸门：放行不记日志：`observe?.('gate', …)` → `observe?.('gate_result', …)`。
  - 内测：开关关也注册路由：路由表末尾多了 `...(lesson ? LESSON_ROUTES : [])`。
- 新增 `services/bff/tests/mutation-anchors.test.ts`，在 `bff-test` 里跑，轻量 CI 每次都跑：
  - 用 bash 展开 `SM` 与 `EXPECT_H6`，转义与运行时一致；
  - 逐条核对原文在当前源码里，H6 要求唯一；
  - H6 源码变异必须在 `EXPECT_H6` 登记。
- 结果：
  - 新测试在修复前的表上报出这 6 条，在修好的表上通过；
  - 6 条逐条 `BFFMUT_FILTER` 的结果、`make bff-test` 的结果见提交说明与 PR。

## 经验
- 只在手动触发时跑的检查会悄悄过时。给它配一个便宜的静态前置检查，放进每次都跑的那一份 CI：这里是“原文还在不在”，不起库、不跑变异。
- 变异的原文是源码里的一段文字，与源码之间没有编译期的联系。改源码时 `grep` 一下 `tests/mutations.sh`；有了这个测试以后，会直接在 PR 上失败。
- 库函数变异（`M`、`HM` 两组，用 `fr()` 改函数定义）的原文在迁移里，运行时核对（`pattern not found`），没有放进这个静态测试。

## 待人确认
- 无。
