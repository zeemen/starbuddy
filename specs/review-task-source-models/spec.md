# 复核任务来源快照按种类建模与核对（审核台响应核对的后续）· 规格草稿

- 状态：**草稿，待人确认**（第六节）。
- 依据：`specs/review-console-responses/lessons.md`“与 spec 的偏差”第 4 条（复核任务的来源快照写成 Any、不往下比，要按来源种类拆模型另起）；
  `db/migrations/0036_insight__weekly_report.up.sql` 的 `platform.review_task_detail`（最新定义）。
- 不涉及必停项：不改迁移、库函数、RLS、判分与掌握；只给已有返回加类型与核对，返回内容不变。周报抽检的页面显示见待人确认第 2 条。

## 一、现状
- `GET /review/v0/queue/{task_id}` 的 `source` 是库函数 `platform.review_task_detail` 按 `source_kind` 拼的 JSON：
  判分结果（grade_result：结论、总分、置信度、评分 / 来源 / 模型版本、复核原因、证据 ID、逐维分）、诊断步骤（diagnosis_step：任务种类、节点、题目、
  判分与过程原文证据的引用、最终答案的结论与证据、作答时间）、判断（claim：模板与版本、对象类型、状态、置信度、来源、模型版本、证据、建立时间）、
  周报（weekly_report：受众、周起止、状态、渲染句、算法与策略版本、生成时间）；能力观察与错因归因没有分支，`source` 为空。
- 后端响应模型把它写成 `dict[str, Any] | None`，前端保留手写的 `GradeSource | ClaimSource | DiagnosisStepSource`，两边都不核对。
  测试样本已能看出前端可能写错的地方：判断的 `template_version` 库里是整数、前端写成字符串；判分的 `model_version` 样本里出现过空值、前端写成不可空；
  逐维分的 `score`、`rule_ref` 前端写成可空（要以库为准核对）。前端没有周报来源的类型，页面对周报抽检任务不展开来源。
- 审核台测试只造过判分与判断两种来源的任务；诊断步骤、周报抽检两种来源没有经过审核台接口的测试。

## 二、默认做法
1. **后端模型**：`responses.py` 新增 `GradeSource`、`DiagnosisStepSource`、`ClaimSource`、`WeeklyReportSource`（`extra="forbid"`，可空按库列的 NOT NULL
   与库函数的 LEFT JOIN 判断）；`QueueTask` 加模型校验：`source` 按 `source_kind` 选模型，能力观察与错因归因必须为空。
   周报的渲染句（`sentences`，insight 写入的 JSON 数组）只核对外层（数组、每句带判断 ID 与模板），句内其余键保持 Any。
2. **导出**：`source` 导出成按 `source_kind` 分支的结构（`{"type": "tagged", "tag": "source_kind", "cases": {种类: 节点 | null}}`），
   `export_review_responses.py` 与前端比较测试同步支持。
3. **前端**：`QueueTask` 改成按 `source_kind` 区分的联合类型（每个种类一个分支，`source` 的类型随种类定）；按核对结果改正 `GradeSource` 等手写类型；
   新增 `WeeklyReportSource`。页面用类型收窄替代现在的 `src as GradeSource` 强制转换。
4. **测试**：审核台测试补造诊断步骤与周报抽检两种来源的任务（合成数据），各打一次详情接口；覆盖核对扩为“每个来源种类至少一次详情 2xx”。
5. **变异**：后端 2 条（来源模型删字段不导出、来源种类与模型对错）、前端 2 条（某来源少字段、某来源可空写错），接进已有两套变异。

## 三、不做
- 改库函数给出的来源内容（例如给周报来源加客观栏、给诊断步骤加过程原文）：库函数注释里本来就记着“是否要给待人确认”，不在本任务。
- 周报抽检任务在页面上的展开显示（见待人确认第 2 条，默认不做）。

## 四、交付顺序
1. 后端来源模型、导出与审核台测试补造两种来源；2. 前端联合类型、类型改正与页面收窄；3. 两侧变异。

## 五、验收
- 四种来源各有模型，详情接口对每个来源种类至少一次 2xx；导出与前端核对一致；新增变异全部被发现。
- `make learning-test`、`make web-test`、`review-console-mutation` 通过；审核台页面行为不变（判分、判断、诊断步骤三种来源的显示不变）。

## 六、待人确认（括号里是默认）
1. **周报渲染句**：只核对外层（每句的判断 ID、模板键与版本），句内其余键保持自由结构（默认）；还是逐键建模（要先和 insight 的周报写入格式对齐，牵涉 SIE 侧）？
2. **周报抽检的页面显示**：本任务只补类型，不在复核详情页展开周报来源（默认，显示什么、给不给渲染句原文由产品与 SIE 负责人定）；还是一起做最小显示（受众、周、句数、算法版本）？
3. **能力观察与错因归因**：库函数对这两种不给来源，模型里定为必须为空（默认）；还是放宽为“可空、任意”以防以后库函数补分支（补分支时要同步改模型）？
