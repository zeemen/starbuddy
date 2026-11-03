# KGE-05 复测与验证题交付 · 实现记录

2026-10-09 起由 Claude 按用户授权自主推进；计量口径属必停项，提交首行 `[NEEDS-HUMAN]`，开关默认关。

## 交付
- 学习服务 `learning/retest/`：只读 `retest_task` 的 scheduled / early / decay（作答用途写 `retest`）与 verify_in_platform / verify_self_report（写 `transfer_check`），到期才列出；选题复用再练，参照题取该知识点最近一次作答，排除原题与近期做过的题，只出没做过的题目族，没有则 `no_new_family`；作答只经现有 grade 写入路径，落库后同步重算，任务由 KGE-04 关闭；用途由服务端写入票据锚点，请求带 purpose 一律 400。内部接口 `openapi/retest-internal.v0.yaml`。
- 8 周复测点：配置项默认关、天数为空；打开后只在列表上读时算出条目，不建任务。
- 票据：学习服务前缀 `rt1`、BFF 封装 `sbr1`（附加数据不同），复测与再练票据互用不了。
- BFF 三条路由，开关 `STARBUDDY_RETEST_UI=retest`；本人核对、宵禁、`meter_practice` 计次、重试复用再练实现（计量 SQL 仍只在 practice/service.ts 一处）；共用限流桶 `practice.session.minute`；前端只拿到条目 ID、节点、层次、类别、状态、题面、判分结论与 due / done。
- 前端复测入口、列表页、作答页（开关默认关），文案全为 `{{TODO_COPY:retest.*}}`。
- 对拍：同一组合成作答，经复测接口与直接经 grade 写 `purpose=retest` 的两名学生，掌握记录、复测任务、作答记录逐字段一致。

## 与 spec 的偏差
1. hint_followup 不在复测列表（已由错题再练提供）。
2. verify_self_report 任务 KGE-04 本来不关；本模块读时判断到期后同知识点做满 `verify_items_per_task`（合成值 2）道 transfer 情境作答就不再出题，任务留给洞察服务结案。
3. 复测不出同族题（同族重做 KGE-04 记 repeat_family，不计入复测轮）。
4. 学科码沿用前端码放在 BFF 路径；列表不转空原因编码；作答结果状态是 due / done。
5. 复测票据与再练共用同一密钥（前缀与附加数据区分），没有新增密钥；`LEARNING_RETEST_CONFIG` 要求同时设 `LEARNING_PRACTICE_CONFIG`。
6. 为复用动了已有代码（行为不变）：`PracticeService.next` 拆出 `pick` 并加 `require_new_family`；BFF 几个再练函数导出、`curfewBlocked` 参数类型放宽；学生壳回跳白名单加 retest；`tests/practice.test.ts` 豁免 retest-api.ts。

## 审查意见的处理（按默认）
- 两份审查共同阻断：本模块是全系统第一个 transfer 证据来源，但选题只保证“没做过的题目族”，不保证是变式或迁移情境题；`transfer_check` 直接计入星亮门槛的 `transfer_evidence_min`（证据权重，必停）。→ 人签字前用保守口径：verify 类、scheduled、early 与 8 周复测点作答用途写 `practice`，只有 decay 写 `retest`。
- 计量：计次前加只读预检（条目仍到期、票据未用），减少计次后被拒；已计次的键在宵禁时只放行找回原结果。
- 对拍补强：出题与列表前后 platform.* 全表行数不变；按任务类参数化，比较 evidence_event、grade_result；新增“作答后直接改任务表”变异。原先的“对拍”只证明“本模块写入等价于一次同用途的 grade”，不能证明“有无本模块一致”。
- 自述验证道数只统计本任务票据下的作答；`EMPTY_REASONS` 补 `NO_NEW_FAMILY`；内部 OpenAPI 的判分结论加枚举、错误统一引用 Problem；BFF 补三条鉴权与票据核对变异。
- 记录不改：再练与复测共用票据密钥（前缀与附加数据区分），正式上线前改为按用途派生子密钥；限流共用桶。

## 待人确认
0. **作答用途映射（必停，证据权重）**：现为保守口径（只有 decay 写 retest，其余写 practice）；何时让验证题写 `transfer_check`、scheduled / early 写 `retest`，需教研确认“同单元新族题算不算变式”并签字。8 周复测点属复测调度，一并签。
1. **计量口径（必停）**：默认按题库自练计次，只计次不扣额度，与错题再练合计每天 5 次（留存态）；学习服务拒绝的作答已计一次不释放。复测与验证要不要扣额度，与 mastery-estimate-sprint4 #21 一起定。
2. 作答用途映射：复测轮、衰减、8 周复测点写 retest；平台内验证、自述验证写 transfer_check。
3. 做错一题后 KGE-04 常立即排出衰减任务（置信度低于下限即到期），复测与错题再练入口会大量重叠。
4. 自述验证“做完”是读时判断，道数 2 是合成值。
5. 8 周复测点天数待教研签字，可能与衰减任务重复。
6. 复测配置全部数值是合成占位。
7. 限流与再练共用 `practice.session.minute`（每分钟 60 次，两类合计）。
8. 复测文案全为占位，需人写人审（必停）；“复测 / 验证”列表标签算不算关于学生的陈述（规则 11）。
9. 列表直接显示知识点编号。
10. 要求“没做过的题目族”而真实内容少时，复测可能经常不可用。
11. 预检与作答之间条目仍可能变化，这种情况下被拒的作答仍计一次。
12. 新增的两个只读接口（`/internal/v1/retest/check`、`/retest/replay`）是否并入 learning-internal 契约。

## 修复后的实现要点
- 用途映射只在 `learning/retest/tasks.py` 的 `PURPOSE` 一处；教研签字后改映射只动这里。
- 复测作答的作答 ID 由幂等键与锚点派生（`attempt_id_for`），自述验证道数读时用学生已成功的判分键反推，不新增表；直接经 grade 的作答不算。
- 宵禁时，已计次的票据只调 replay 找回原结果（不执行、不封存），否则一律 402 curfew。
