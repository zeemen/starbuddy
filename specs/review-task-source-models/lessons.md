# 复核任务来源快照按种类建模与核对 · 实现记录

2026-11-11 用户确认（“按建议，默认值都同意，开始实现”）：spec 第六节待人确认 1–3 全部按默认——周报渲染句只核对外层（实现比默认更严，见偏差第 1 条）；
周报抽检不在页面展开；能力观察与错因归因的来源必须为空。不涉及必停项。

## 交付
1. **后端模型**（`services/learning/review_console/responses.py`）：`GradeSource`（含 `GradeDimensionSource`）、`DiagnosisStepSource`、`ClaimSource`、
   `WeeklyReportSource`（含 `WeeklySentence`），一律 `extra="forbid"`；`SOURCE_BY_KIND` 登记来源种类 → 模型（能力观察、错因归因为 `None`）。
   `QueueTask.source` 仍声明为字典（输出不变），由模型校验器按 `source_kind` 选模型核对，不该有来源的种类给了来源、该有的缺了都报错（响应校验 500）。
   可空按基表 NOT NULL 与库函数里的 LEFT JOIN：判分的 `model_version` 不可空（判断的才可空）；诊断步骤的结论与证据来自 LEFT JOIN 的判分，可空。
2. **导出**：`source` 展开成按 `source_kind` 分支的结构（`{"type": "tagged", "tag": "source_kind", "cases": {种类: 节点 | null}}`），
   分支集合须与 `source_kind` 的枚举相同（`_tagged`）。
3. **前端**（`apps/web/src/lib/review.ts`）：`QueueTask` 改为按 `source_kind` 区分的联合类型（`QueueTaskOf<种类, 来源>`）；新增 `WeeklyReportSource`、
   `WeeklySentence`。复核详情页用类型收窄替代 `src as GradeSource` 等三处强制转换。`tests/review-responses.test.ts` 支持分支比较：
   前端联合的每一支 `source_kind` 是字面量、各支合起来恰好是后端的分支集合，每一支按“其余字段 + 这一支的来源”逐字段比。
4. **测试**：
   - 复核队列补周报抽检用例（`tests/test_review_queue.py::test_weekly_report_spot_check_detail`）：合成判断与家长版周报由超级用户写入，
     洞察服务角色调 `platform.review_sample_weekly_reports` 进队，再走完领取、放回、结案（结案码 `weekly_report_ok`）；
   - 覆盖核对（`tests/test_responses_coverage_review.py`）加跑 `tests/test_diagnosis_session.py`（带出诊断步骤来源），收集插件对任务详情另按来源种类计，
     断言四种有来源的种类各至少一次详情 2xx；
   - 枚举对库约束（`tests/test_review_responses_enums.py`）扩到 22 列：判分结论（两处）、判定方式、诊断步骤种类、判断的对象类型 / 状态 / 来源（库里是枚举类型）、
     周报受众与状态。
5. **变异**：后端 2 条（“响应导出”组：来源模型删字段不导出；“响应模型库上”组：来源种类与模型对错，这一组加跑复核队列测试）；
   前端 4 条（某支少字段、某支可空写错、少一个分支、恒为空的分支写成有来源）。

## 核对发现的前端类型问题（已改正）
- 判断来源的 `template_version` 写成字符串，库里是整数。页面拼成 `模板@版本` 显示，结果不变。
- 诊断步骤的 `kind` 只写了 process / explain，库约束有第三种 adaptive（进复核队列的只有前两种，类型与库约束对齐）。
- 诊断步骤的 `work_text` 库函数从来不给：页面原来读它的分支走不到，删去，过程原文一律显示“待授权”（与原来实际显示的一样）。

## 与 spec 的偏差
1. **周报渲染句逐键建模**（默认是只核对外层、句内其余保持自由结构）：读 0036 才看到库里的 `insight.valid_weekly_sentences` 已经把每句的键白名单
   （8 个键）、类型与取值都定死了，`claim_revision` 由写入守卫统一盖上。逐键建模不需要再和洞察服务对齐格式，库的约束就是格式；运行时也不会因此多出 500
   （库里存不进不合格的句子）。所以按库的约束全建了。若要退回只核对外层，把 `WeeklySentence` 换成三个键 + 其余 Any 即可。
2. **能力观察、错因归因不在覆盖核对里**：库函数对这两种不给来源，现有流程也造不出这两种复核任务；模型里“必须为空”由前端变异“恒为空的分支写成有来源”
   与后端的模型校验器守着，但没有真实返回经过。库函数以后补分支时，要同时改 `SOURCE_BY_KIND`（枚举对库的核对不覆盖这一点）。
3. **周报抽检任务的详情页**：原来来源非空但不认识的种类什么都不显示，现在显示“来源对象暂无可显示的结构化内容”（与来源为空时同一句）。不展开周报内容（待人确认 2 的默认）。

## 测试结果（本机）
- 复核队列、诊断会话与三科端到端 52 项 + 周报抽检 1 项通过；覆盖核对通过（四种来源各至少 3 次详情 2xx）；枚举对库 22 项、导出 7 项通过。
- 前端单测 250 项通过（含响应核对 2 项、响应变异对照组 + 11 条）；tsc 通过。
- `review-console-mutation` 的“响应导出”3 条、“响应模型库上”4 条全部被发现。
- 学习服务全量、`make web-test`：见下方补记。

## 经验
- 库里已有严格约束的 JSON（周报句、客观栏）可以直接照约束建模：约束就是格式说明，比读写入方代码可靠。
- 按另一字段分支的结构，模型上保持原类型、用校验器核对，输出就不会被 pydantic 的联合类型改写（智能联合可能挑错分支或改变键序）。

## 待人确认
- 无新增。周报抽检在复核详情页显示什么（是否给渲染句原文）、诊断步骤过程原文的授权方式，仍是原来的待人确认项（spec 第三节）。
