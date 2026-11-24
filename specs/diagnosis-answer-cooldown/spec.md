# 诊断作答提交被拒时：按 retry-after 冷却提交按钮 · 规格草稿

- 状态：**已确认（2026-11-24，用户“按建议，默认值都同意，开始实现”）**：第五节三条都按默认。实现记录与差异见 lessons.md。
- 来源：`specs/retry-after-cooldown/spec.md` 第三节“诊断……等诊断上线前一起看”；`specs/diagnosis-probe-retry/` 只做了探针，作答那一半没做。
  再练、复测的作答被拒已经冷却（`specs/retry-after-cooldown`），诊断作答是同一类请求（过 BFF 分学科闸门），口径应一致。
- 不涉及必停项：不改契约、迁移、计量与扣次、判分与掌握计算；不新增文案（沿用已有的“这次没有提交上，请再点一次。”）；BFF、学习服务不改。

## 一、现状（2026-11-24 核对）
- 诊断组件 `apps/web/src/diagnosis/Diagnosis.tsx` 的 `submit`：作答提交失败（`r.ok === false`，非宵禁、非届别变化）只显示 `notSaved`，
  **提交按钮马上就能再点**。BFF 的 `answer` 过分学科闸门（`services/bff/src/diagnosis/service.ts` 第 411 行 `withSubjectSlot`），
  被拒时回 `503 SUBJECT_BUSY` + `retry-after`；学生连点会在闸门满时继续加压，这正是再练、复测那边加冷却要防的情况。
- `app/dev/diagnosis/actions.ts` 的 `answerAction` 不返回 `retryable` / `retryAfterSec`（探针那边已经返回，`src/lib/diagnosis.ts` 的失败结果里本来就带 `retryAfterSec`）。
- 重试是安全的：前端每次提交生成新的幂等键，但学习服务对“同一步、键不同而内容（作答与过程）相同”回原结果（`services/learning/learning/diagnosis/service.ts`
  第 12、413–417 行，安全审查 S5），用时 `elapsed_sec` 不参与比较；断连时第一次其实已写入的，再点一次也只算一次。
- 诊断组件目前只挂在开发演示路由 `app/dev/diagnosis/`，学生端还没有入口；这次是为上线前把作答这一半也定下来。

## 二、默认做法
1. **失败分类与探针相同**：可重试 = `503 SUBJECT_BUSY`、`429`、断连 / 超时（`503` 无错误码）；把 `src/diagnosis/probe-retry.ts` 的 `probeRetryable`
   改名为通用的 `retryableFailure` 挪到 `src/lib/retry-after.ts`（探针、作答共用；`probe-retry.ts` 改为从那里引用，行为不变）。
2. **可重试失败时**：作答、过程原样留着，状态行显示 `notSaved`，**提交按钮**按 `retry-after` 冷却（`cooldownMs`：有头用头、封顶 30 秒，没有 2 秒），
   冷却中按钮禁用、带 `data-cooldown="on"`，结束恢复；**不自动重发**，**不限次数**（作答不能跳过，与探针“只多给一次”不同；学生随时可以点“暂停”）。
   冷却期间计时照常走（诊断计时只算看题作答，被拒等待也是在看题；时长上限仍由服务端 7200 秒兜底）。
3. **不可重试的失败**（400、403、404、409 等）：维持现状，只显示 `notSaved`、不冷却。宵禁、届别变化维持原分支。
4. **前端改动**：
   - `answerAction` 只在可重试失败时多返回 `retryable: true` 与 `retryAfterSec`（同 `probeAction`）；`DiagnosisApi.answer` 的返回类型随之扩展；
   - `Diagnosis.tsx` 的 `submit` 在可重试失败时 `startCooldown(cooldownMs(r.retryAfterSec))`；换题（`show`）时清冷却。
5. **测试**：
   - 单测：`retryableFailure` 分类（沿用探针的用例，改引用）；`answerAction` 只在可重试时带 `retryAfterSec`；组件接线（可重试才冷却、按钮带 `data-cooldown`、
     不可重试不冷却、`show` 清冷却）。
   - 端到端：诊断那一套 BFF 没开闸门，造不出被拒（`specs/diagnosis-probe-retry/lessons.md`“与 spec 的差异”）；本次不加，诊断上学生端入口时在那一套上补
     探针与作答两条被拒用例。

## 三、不做
- 开始诊断、读当前题被闸门拒（`start` / `current`）：开始失败进“现在还不能开始”，读题失败显示 `notSaved`，都要学生重新点，现状不加压；
  “再试一次”按钮要新文案，不在本次。
- 作答成功后读下一题被拒时提示说成“没有提交上”的措辞问题：再点一次会按“内容相同回原结果”重放后读题，结果正确；改措辞要新文案，记进 lessons 待人确认。
- 自动重发、倒计时显示、新文案；学生端诊断入口；BFF、学习服务、契约。

## 四、验收
- 可重试失败后提交按钮按时长禁用再恢复，作答与过程不丢；冷却中点按钮不发请求；不可重试失败行为不变；探针行为不变。
- `make web-test`（含端到端，原诊断、探针用例照常通过）、`make check-fast` 通过；BFF、契约、迁移无改动。

## 五、待人确认（括号里是默认）
1. **次数**：作答被拒不限次，每次都冷却（默认）；还是像探针一样限次？（作答不能跳过，限次后只能让学生暂停或离开。）
2. **冷却时长**：与再练、复测、探针相同（有 `retry-after` 用它、封顶 30 秒，没有 2 秒）（默认）；还是另定？
3. **冷却期间计时**：照常计（默认）；还是冷却期间停表、恢复后接着算？（停表要在组件里扣掉冷却时长，算法更复杂，收益是被拒不占学生的诊断时长。）
