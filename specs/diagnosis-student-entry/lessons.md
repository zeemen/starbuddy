# 学生端数学诊断入口与正式做题页 · 实现记录

- 2026-11-25 用户“按建议，默认值都同意，开始实现”：入口放数学科目页；新设前端开关 `STARBUDDY_DIAGNOSIS_UI`，staging 可开；重复进入按 BFF 现有规则。

## 做了什么
- `apps/web/src/diagnosis/features.ts`（新）：`diagnosisEnabled`（dev / test / staging 读 `STARBUDDY_DIAGNOSIS_UI=diagnosis`，pilot、生产与没设一律关）、学科只有数学。
- `src/diagnosis/ops.ts`（新，不是 server action）：六个诊断操作的参数校验与 BFF 调用，原样从演示页的 action 挪出（逻辑不变）；身份由调用方传入。
- `src/diagnosis/actions.ts`（新，`'use server'`）：正式做题页的六个 action，先过开关再 `actionSession`，然后调 ops。
- `src/app/dev/diagnosis/actions.ts`：改为开发环境关卡 + 调 ops（不受前端开关影响，演示页行为不变）。
- `src/diagnosis/DiagnosisStudent.tsx`（新，客户端包装）、`src/diagnosis/DiagnosisPages.tsx`（新：入口 `DiagnosisLink` 与做题页 `DiagnosisStudentPage`：
  开关 → 学科 → `requireOwnStudent`，顺序在单测里钉住）；路由 `src/app/s/[studentId]/learn/[subject]/diagnosis/page.tsx`；数学科目页挂入口。
- 文案：`DIAGNOSIS_ENTRY_COPY`（`{{TODO_COPY:diagnosis.entry}} 诊断`、`{{TODO_COPY:diagnosis.back}} 返回科目`），并入 `allDiagnosisCopy()` 的禁语扫描；
  `todo-by-owner.md` 产品一节的“人写人审全部占位文案”补上这两条。
- 部署：`deploy/flags/staging.yaml` 的 `diagnosis` 项加 web 开关；`deploy/compose/staging.yaml`、`deploy/compose/local-test.yaml`、`deploy/k8s/staging.yaml` 的 web 设
  `STARBUDDY_DIAGNOSIS_UI=diagnosis`；`deploy/config/env-inventory.yaml` 登记；`deploy/tests/flags_check.ts` 与 `test_flags_matrix.py` 纳入。
- 测试：`tests/diagnosis-student-entry.test.ts`（开关、闸门顺序、只认学生本人、入口位置、action 先过开关、ops 只被两处引用、占位文案）；
  `tests/diagnosis.test.ts`、`diagnosis-probe-retry.test.ts`、`diagnosis-answer-cooldown.test.ts` 改为核对 ops；
  端到端 `e2e/diagnosis-entry.spec.ts`（practice-on 一套，前端另开开关）与 `e2e/diagnosis-entry-off.spec.ts`（trust-off 一套）；
  安全头爬取登记新页面（爬取的各套里开关关，404 页照样核对）。

## 与 spec 的差异
- **正式 action 不在前端判断“是不是学生本人”**：`actionSession` 只有令牌，没有 `/me`；多查一次 `/me` 会给每次作答加一个往返。授权照旧由 BFF 判断
  （诊断路由只认学生本人，家长会话 403、别人的学生 404），页面另用 `requireOwnStudent` 只给学生本人渲染。与再练、复测的 action 同一做法。
- **端到端的“做完一步 → 暂停 → 续做”没有放在正式页上跑**：开了开关的那一套（practice-on）后面是合成学习服务桩，诊断接口一律 501；
  后面是真实学习服务的那一套（trust-off）是“开关全关”的基线，打开开关会让安全头爬取、科目页等用例一起变化。所以正式页只核对入口、页面、
  授权、安全头与“开始不了”（合成学生 P3 没有权益：不建会话、不占额度）；全流程仍由 `e2e/diagnosis.spec.ts` 在演示页上跑——两页用同一个组件、
  同一份 ops，正式页与演示页的差别只在 action 的关卡（单测钉住）。
- 正式页在页面打开时就开始诊断（组件现有行为），所以没有放进通用的安全头爬取（会给爬取用的学生建会话、占额度），改为在 `diagnosis-entry.spec.ts` 里单独核对。

## 待人确认
1. 入口与返回的文案（`{{TODO_COPY:diagnosis.*}}`）由人写人审（已登记进 `todo-by-owner.md`）。
2. 今日计划里“诊断”类任务目前没有跳转（今日计划的任务项都没有链接），是否让它跳到诊断页，另议。
