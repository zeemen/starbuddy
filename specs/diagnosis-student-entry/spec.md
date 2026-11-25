# 学生端数学诊断入口：科目页入口与正式做题页（前端开关 STARBUDDY_DIAGNOSIS_UI） · 规格草稿

- 状态：**草稿，待人确认第五节**（2026-11-25）。
- 来源：`specs/launch-test-2026/spec.md` 第 19 行把“诊断”列进 12 月内部成员测试的学生端功能；`deploy/flags/staging.yaml` 的 `diagnosis` 项
  `state: "on"`（只等 0058 与教研签字）。但学生端至今**没有诊断页**：诊断组件 `apps/web/src/diagnosis/Diagnosis.tsx` 只挂在开发演示路由
  `app/dev/diagnosis/[studentId]`（文件名 `page.dev.tsx`，生产与 staging 构建里不存在），科目页也没有入口。`specs/diagnosis-probe-retry`、
  `specs/diagnosis-answer-cooldown` 都把“学生端诊断入口”列为另有任务。
- 必停项：**入口文案是面向学生的新话术（人设，必停）**。本任务只放明显占位 `{{TODO_COPY:diagnosis.*}}`，与再练、复测、PWA 同一做法，定稿由人写人审；
  不改契约、迁移、计量、判分与掌握计算，不改 BFF 与学习服务。

## 一、现状（2026-11-25 核对）
- BFF：诊断路由不设开关，配了 `BFF_LEARNING_URL` 就注册（`services/bff/src/diagnosis/service.ts`），只认学生本人、过分学科闸门、按
  `diagnosis_full` 预留计量。学习服务的题面在 `present_item` 签字前只有合成占位（只允许 test），staging 回 501。
- 前端：演示页的 server action（`app/dev/diagnosis/actions.ts`）带运行时环境与 Host 检查（`devRuntimeAllowed`），只能在开发构建里用；
  组件本身（计时、暂停续做、探针、宵禁、届别变化、冷却）已完整，文案在 `src/diagnosis/copy.ts`，过禁语扫描。
- 再练、复测的模式：前端开关（`practice/features.ts`、`retest/features.ts`，`launchTier` 为 local / staging 才读）关时入口返回 null、页面 404
  （在读会话之前判断）；开时科目页挂入口，页面只认学生本人（`requireOwnStudent`），家长端没有入口。

## 二、默认做法
1. **前端开关** `STARBUDDY_DIAGNOSIS_UI=diagnosis`（新文件 `src/diagnosis/features.ts`，同 `practice/features.ts`：dev / test / staging 可开，pilot、生产一律关，
   只在服务端读）。默认关：关时不渲染入口、诊断页 404，与现在零差异。登记进 `deploy/flags/staging.yaml` 的 `diagnosis` 项（`service: web`，staging 值 `diagnosis`），
   `deploy/tests/flags_check.ts` 加上解析函数。
2. **入口**：数学科目页（`/s/{studentId}/learn/math`）在再练、复测入口旁加 `DiagnosisLink`（只数学；开关关返回 null；家长端科目页不引用）。
3. **正式做题页** `/s/{studentId}/learn/math/diagnosis`：先过开关（关时 404，不读会话），再 `requireOwnStudent`；页面标题沿用 `DIAGNOSIS_COPY.heading`，
   有“返回科目”链接；正文是现成的 `Diagnosis` 组件。
4. **server action 正式化**：把 `app/dev/diagnosis/actions.ts` 的六个 action 挪到 `src/diagnosis/actions.ts`，去掉 `devRuntimeAllowed`，改为
   “开关开 + 会话是学生本人”才放行（其余照旧：CSRF 核对、参数校验、只回 ok 与题面视图）；演示页改为引用它，另保留自己的开发环境与 Host 检查。
   客户端包装（每个动作一个新的幂等键）同样挪到 `src/diagnosis/` 共用。
5. **文案**：新增占位 `DIAGNOSIS_ENTRY_COPY = { entry: '{{TODO_COPY:diagnosis.entry}} 诊断', back: '{{TODO_COPY:diagnosis.back}} 返回科目' }`，
   并入 `allDiagnosisCopy()` 走禁语扫描；`todo-by-owner.md` 产品一节“人写人审全部占位文案”那一行补上这两条。
6. **测试**：
   - 单测：开关解析（各环境）；入口在开关关、非数学、家长端时不渲染；页面在开关关时 404 先于读会话（静态检查，同再练）；
     action 在开关关或非学生本人时一律 `{ ok: false }` 且不调 BFF；生产构建里仍没有 `/dev/diagnosis`（`tests/build-output.test.ts`）。
   - 端到端：诊断那一套（`e2e/diagnosis.spec.ts`，后面是真实学习服务的合成题面）给前端开 `STARBUDDY_DIAGNOSIS_UI=diagnosis`，新增一条
     “学生从数学科目页点入口 → 做完一步 → 暂停 → 续做”；家长会话打开该页 404；原有演示页用例照常。axe 不退化。

## 三、不做
- 今日计划里“诊断”任务的跳转（今日计划的任务项目前都没有链接，另议）。
- 诊断结束后的结果展示（规则 11：结束页不给结论，现状不变）。
- 其他学科的诊断；BFF、学习服务、契约（`present_item` 签字另有流程）；入口文案定稿（人写人审）。

## 四、验收
- 开关关：与现在零差异（无入口、诊断页 404、不发请求）。开关开：学生本人能从数学科目页进入并完成诊断流程；家长与非本人 404。
- `make web-test`（含端到端）、`make check-fast`、`deploy/tests` 通过；BFF、契约、迁移无改动。

## 五、待人确认（括号里是默认）
1. **入口位置**：数学科目页，与再练、复测并列（默认）；还是放今日计划、或由点点引导（spec 设计要点“诊断入口由 4-H 与点点决定”）？
2. **开关**：新设前端开关 `STARBUDDY_DIAGNOSIS_UI`，staging 可开（默认）；还是不设开关、跟着 BFF 是否配了学习服务地址走？
3. **重复进入**：入口每次都能点；有未完成的会话时组件按现有逻辑续做（BFF 返回当前会话），做完后再点是否允许开新会话（扣一次 `diagnosis_full`）——
   默认按 BFF 现有规则（额度与宵禁由 BFF 判断），前端不另加限制；还是做完后入口隐藏？
