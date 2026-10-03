# 诊断演示页端到端间歇失败 · lessons

## 现象
- H5 PR 3 期间，3 次完整 `make web-test` 里有 1 次失败：学生 D1 打开诊断演示页后显示“现在还不能开始，请稍后再来。”
  （`Diagnosis.tsx` 的 `unavailable`），单独重跑该文件通过。

## 排查范围（开始会话失败 / 视图无 step 的全部路径）
- 前端：`startAction` 里身份、Host、学生 ID、幂等键校验失败 → `{ ok: false }`；BFF 非 2xx（宵禁 402 走 `curfew` 分支，不是 `unavailable`）；
  取视图 `step` 为 null 且会话不是 finished / abandoned / paused。
- BFF `startDiagnosis`：不是本人 403；已有未结束会话 → 续做（宵禁 402、学习服务失败）；预留结果 `conflict` 409、`insufficient` /
  `no_entitlement` / `daily_cap_reached` / `curfew` / `curfew_soon` / `retained_no_ai` / `dedup_in_progress` 402、`existing` 已释放或过期 409；
  学习服务结果未知 503、确定失败（404→403、501→503、409、`NO_TARGETS` 422）。诊断开始没有请求频率桶（认证限流在端到端放宽 1000 倍）。
- 跨文件副作用：全部 E2E 文件里只有 `diagnosis.spec.ts` 用 D1 / gD（`grep` 核对）；`workers: 1`、`fullyParallel: false`，三个项目串行，
  没有并行消耗额度或建会话的情况。服务就绪：e2e.sh 等端口就绪后才建库与构建，失败那次学习服务、BFF 都在正常响应。

## 根因（测试环境的计量时钟每逢整点倒退）
- `apps/web/scripts/e2e.sh` 原来把一次性测试库的 `platform.meter_now()` 换成
  “当天北京时间 10:00 + 当前小时内已过的分秒”。按整点前后两个真实时刻算（一次性库里直接算该表达式）：
  `05:59:41Z → 10:59:41`，`06:00:05Z → 10:00:05`（倒退约 59 分钟）；`15:59:50Z → 10-03 10:59:50`，`16:00:10Z → 10-04 10:00:10`。
- 诊断种子开通试点会员时 `subscription.period_start` 与 `entitlement.period_start` = 当时的计量时钟。种子在整点前执行、诊断用例在整点后运行时，
  `meter_reserve` 里 `v_now >= en.period_start` 不成立 → `no_entitlement` → BFF 402 `QUOTA_UNAVAILABLE` → 前端 `unavailable`。
  整套跑一次约 3.5 分钟，种子到诊断用例约 1–1.5 分钟，落在整点窗口里就失败；单独重跑时种子是新的，所以通过。
- 复现（未修复，2026-10-03）：让单文件运行在 05:59:35Z 开始（种子在 06:00 前，用例在 06:00 后），学生用例失败，页面快照是
  “现在还不能开始，请稍后再来。”；BFF 访问日志里学生的 `POST /students/{student_id}/diagnosis/sessions` 为 **402**（14 ms），
  学习服务日志里没有任何诊断开始请求（BFF 在预留这一步就拒绝了）。同一天远离整点的 20 次单文件、3 次整套全部通过。
- 不是产品代码问题：生产的 `meter_now()` 是真实时间，单调；问题只在端到端夹具的时钟替身。

## 修复（只改测试夹具）
- `apps/web/scripts/e2e.sh`：建环境时算一次偏移（当天北京时间 10:00 − now()），把 `meter_now()` 定义成 `now() + 常量偏移`。
  计量时钟从 10:00 起单调连续，不再在整点、午夜跳变；一次端到端远短于 12 小时，不会走进 22:00 宵禁。
- 没有改断言、超时、用例或产品代码；没有改迁移与契约。其他依赖计量时钟的端到端（统一壳的额度、试用等）同样受益。

## 验证（2026-10-03）
- 修复前：单文件 20 次 + `make web-test` 3 次（均远离整点）全过；跨整点单文件 1 次 → 复现失败（见上）。
- 修复后：单文件 `bash apps/web/scripts/e2e.sh diagnosis` 连跑 20 次全过（每次 2 passed）；`make web-test` 3 次全过（每次 55 passed、1 skipped，
  skipped 为原有的跳过项，修复前相同）。
- 修复后跨整点：`make web-test` 于 06:58:50Z 开始，种子在 07:00 前执行完（测试构建目录 06:59:07 出现），Playwright 06:59:18 启动，
  07:02:33 结束，55 passed、1 skipped（整套跨过整点；诊断用例是否恰在 07:00 之后没有单独打点）。新定义是 `now() + 常量`，按构造单调。
- 严格 `make check CONTRACT_STRICT=1`（契约开发依赖装在临时 venv，`PYTHON=` 指向它）：07:03–08:31 全部通过，其中 web-test 端到端 55 passed。

## 待人确认
- 无。时钟替身只在一次性测试库里，不属于计量规则变更；如认为它也属“计量逻辑”必停项，请在 PR 描述首行标 [NEEDS-HUMAN]。
