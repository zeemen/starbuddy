# 再练与复测：被闸门拒或被限流后按 retry-after 冷却 · 实现记录

- 2026-11-20 用户“按建议，默认值都同意，开始实现”：冷却有 `retry-after` 用它、封顶 30 秒，没有用 2 秒；冷却结束只恢复按钮；不显示倒计时、不新增文案。

## 做了什么
- `src/lib/retry-after.ts`：`parseRetryAfter`（只认 1–300 的整数秒）、`cooldownMs`（封顶 30 秒、缺省 2 秒）。
- `src/lib/practice-api.ts`、`retest-api.ts`：失败返回多一个 `retryAfterSec`（只读 `retry-after` 这一个响应头）。
- `src/practice/actions.ts`、`src/retest/actions.ts`：作答被映射成 `rate_limited` 时把 `retryAfterSec` 带回组件；别的错误不带。
- `src/practice/use-cooldown.ts`：组件内存里的定时器，卸载时清掉。
- `PracticeItem.tsx`、`RetestItem.tsx`：被限流后开始冷却；冷却中提交按钮 `disabled`、带 `data-cooldown="on"`，提交函数直接返回；换题时清掉冷却；
  冷却结束只恢复按钮。文案、提示句、锁定作答控件的逻辑都没变。
- `e2e/dom-leak.ts` 登记 `data-cooldown`（只许 `on`、`off`）。
- **端到端**（practice-on）：
  - `scripts/e2e.sh` 给第五个 BFF 打开分学科闸门（`STARBUDDY_SUBJECT_GATE=subject_gate`），用一份临时配置（`BFF_CONCURRENCY_CONFIG`，
    由 `config/concurrency.json` 改出：每科每生同时 1 个、`retry-after` 1 秒）。这一套端到端串行，平时同一学生同一科不会并发。
  - 合成学习服务桩：填空作答 `SLOW-SYN` 晚 3 秒才答。
  - 新用例：同一学生开两页，第一页交 `SLOW-SYN` 占着名额，第二页的作答被 BFF 拒（503 `SUBJECT_BUSY`）：按钮冷却、禁用、文字是“再提交一次”，
    提示句与作答控件锁定照旧；冷却中强点一下不发请求；约 1 秒后恢复；按桩的请求记录核对被拒与冷却中的点击都没到桩；学生再点一次用同一张票据成功。
- 单测 `tests/retry-after.test.ts`：解析、时长、两个客户端读头、两个 action 只在限流时带回、两个组件的冷却接线。

## 与 spec 的差异
- 端到端打开了第五个 BFF 的闸门（spec 只写“合成学习服务桩让一次作答回 503 SUBJECT_BUSY”）：`SUBJECT_BUSY` 是 BFF 闸门给的，桩回 503 只会被 BFF
  当成上游不可用（`UPSTREAM_UNAVAILABLE`），测不到这条路径，所以改为真实闸门 + 慢作答造出被拒。附带好处：这一套的再练、复测端到端现在都经过闸门。

## 验证
- 见提交说明与 CI。

## 待人确认
1. **诊断探针被拒**：`src/diagnosis/Diagnosis.tsx` 探针提交失败（含闸门被拒）时跳过这一步的探针，这一步就没有探针记录。组件目前只挂在开发演示路由下，
   学生端上线诊断前请产品与 SIE 负责人定：失败时是重试一次、按 retry-after 冷却后再给学生一次机会，还是维持跳过。
