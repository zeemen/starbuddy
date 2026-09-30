# 冲刺 4-D（知行、文砺部分）· lessons

spec：`specs/adapter-zhixing-wenli-methods-sprint4/spec.md`（已确认，待人确认 #1–#4 全部按默认）。PR 首行 `[NEEDS-HUMAN]`（契约行为按 v1.0 候选版）。
本任务只改 `adapters/zhixing/`、`adapters/wenli/` 与本目录；**不改** `packages/contracts`、`db/`、`docs/personas`、`evals/**`、Makefile、
`adapters/zhixing/content/pack.json`（采分点须与原子层一致）。四个新方法都是确定性算法：不调模型、不联网、不产分、不写关于学生的陈述。

## 交付

| 科目 | 方法 | 算法（model_version） | 代码 | 单测 |
|---|---|---|---|---|
| 知行 | plan | `algo:zhixing-plan@0.1.0` | `src/core/plan.ts`、`src/plan-weights.ts`、`content/plan-weights.json`、`src/adapter.ts` | `tests/plan.test.ts`（9 条） |
| 知行 | report | `algo:zhixing-report@0.1.0` | `src/core/report.ts`、`src/adapter.ts` | `tests/report.test.ts`（6 条） |
| 文砺 | generate_variation | `algo:wenli-item-select@0.1.0` | `src/adapter.ts`、`src/content.ts`（`questionTypeOf`） | `tests/variation.test.ts`（6 条） |
| 文砺 | report | `algo:wenli-report@0.1.0` | `src/core/report.ts`、`src/taxonomy.ts`、`src/adapter.ts` | `tests/report.test.ts`（6 条） |

- 能力声明：两科都是 5/6（只有 teach 未实现，501）；知行 plan `notes_code: domain_weights_uncalibrated`，两科 report `counts_only`，
  文砺 generate_variation `admitted_items_only`。`tests/contract.test.ts` 随之改为断言 5/6。
- **变异**：两科各一个 `tests/method-mutations.test.ts`，把新方法的关键逻辑逐条改坏（知行 plan 9 条、report 8 条；文砺 generate_variation 9 条、
  report 9 条），复制到临时目录（按仓库结构镜像，`packages`、`docs` 符号链接）跑对应单测，先确认模块能加载、再要求单测失败（退出码 1）。
  覆盖排序（不乘领域权重、不乘可提升、不排序、失分忽略 p_mastery）、准入过滤、同题型、截断、硬凑前置、成环、窗口边界、去重、
  夹带亮点编码、错因口径混算、trend 方向、效力、置信度。随 `make adapter-*-test` 运行（每科约 10–20 秒）。
- 一致性夹具：知行加 plan 2 条、report 2 条；文砺加 generate_variation 2 条、report 2 条（conformance 对这三个方法只做 schema、坏请求、幂等）。
- README 两份更新了接口表与新方法说明。

## 实现要点
1. **知行 plan 的规划单元是能力视图节点**，不是（节点，领域）：契约 `PlanTask.skill_nodes` 只能写能力点，按（节点，领域）拆会出现
   看起来完全相同的任务。节点的领域权重 = 该节点全部道法采分点的领域权重按分值加权平均。`syn-dim-shenti` 没有挂采分点，不排任务。
2. **领域权重单独成文件** `content/plan-weights.json`（合成值，`calibration: pending_research_calibration`，notice 必须含“合成”“待教研校准”，
   否则拒绝加载；`source_version` 里写 `algo:zhixing-plan-weights@0.1.0-uncalibrated`）。没有往 `pack.json` 加字段，所以
   `zhixing-rubric-check` 与 4-K 的导入器都不受影响。采分点按第一个 `node_ids` 的前缀归领域，加载时要求每个道法采分点恰好落在一个领域。
3. **前置只来自 `node_path`**：知识服务 `node.path` 只走强前置边（契约摘要“只走强前置边”），所以观点之间只有 related 边时 `node_path` 为空，
   plan 不产生任何 `prerequisite_task_ids`；适配器不从 related 边、内容包或排名推前置。有强前置边时按 depth、from、to 顺序连边，成环的跳过。
4. **report 的 trend**：契约 `ReportFields.skill_summary[].trend` 必填，只能输出编码；做法与阿鸦 spec 一致，只比较平台给的
   `mastery_history` 状态序（窗前最后一条为基线），不重算掌握度。没有 `highlight_codes`（默认 #3 只给计数与证据 ID）。
5. **错因口径分开**：知行 report 只收 `shenti_cuo / dingwei_cuo / zhuanhua_cuo / guifan_cuo`，文砺只收语文映射表里的编码（含阅读错因标签），
   跨科大类还必须与 `docs/mapping/error-taxonomy.v0.9.yaml` 一致，否则 422。语文 `dingwei`（定位性，找原文）与道法 `dingwei_cuo`（定位错，找教材）
   互相不收（KGE 第八章“定位”同名问题），单测与变异都覆盖。
6. **文砺同题型**：客观题取内容包 `type`（choice / fill），作文取 `essay_<genre>`；只给 `skill_node` 时没有参照题型，只按能力格选。
   `skill_node` 与源题能力格不同 → 422（请求自相矛盾）。源题本身不算候选，所以“只有源题已准入”得到 `no_admitted_candidate` 而不是 `all_excluded`。
7. 一致性夹具在 v0.9 与 v1.0 下共用：v1.0 要求 `response_*` 证据带 `attempt_id`，v0.9 不允许该字段，所以 report 夹具的作答证据用 `ocr_transcription`。
8. 变异子进程两路并行、调到最低优先级：知行 `idempotency.test.ts` 的“缓存满全是进行中”一条对时序敏感（30ms 等待、150ms 桩延迟），
   变异子进程四路满速并行时它偶发失败；降优先级后连续 5 轮全绿。这条时序测试本身在机器繁忙时也偶发失败（不跑变异时 5 轮里见过 1 次），另起任务加固。

## 与 spec / 任务说明的偏差
1. **错因编码没有写成 `MORAL.*` / `CHN.*`**：契约 `ErrorAttribution.subject_category` 与 `ReportFields.error_distribution[].subject_category`
   的 pattern 是 `^[a-z0-9_\-]{1,48}$`，写不下 `MORAL.LOCATE_BOOK` 这种形式。本版沿用映射表里的学科编码（知行 `*_cuo`、语文 `shenti` 等），
   两科编码本来不重名，report 按学科严格核对、不混算。要输出 KGE 形式的编码需要改契约（必停），见待人确认 A。
2. **文砺“五维量表逐维统计、主观维带不确定度”做不了**：`ReportFields` 没有量表维度字段（`additionalProperties: false`），
   `ReportRequest` 也不带判分维度得分，信封的 `uncertainty` 是整体区间、没有逐维位置。本版只按能力格给作答计数与证据 ID。见待人确认 B。
3. **知行“逐维采分统计”只到作答次数**：`skill_summary` 只有 `attempts`，没有逐维得分或命中次数字段；采分点片段证据只进 `evidence_ids`。
4. `exam_config`（两科）、`learner_context`、`exam_date` 不参与计算，只参与请求体指纹：内容包没有与考情题型编码对应的字段。
5. 一致性夹具由实现会话起草（与 sprint2 相同），需要独立会话复核。

## 待人确认
A. **学科错因编码的形式**：是否要在契约里放宽 `subject_category` 的 pattern（或加 `kge_error_code` 字段）以输出 `MORAL.*` / `CHN.*`；默认不改契约。
B. **文砺 report 的量表逐维统计**：需要契约 `ReportRequest` 带判分维度记录、`ReportFields` 加逐维字段（含主观维不确定度），属契约变更（必停）；
   或者由平台报告引擎直接读 `platform.grade_dimension` 聚合，适配器不管。默认后者、本版不做。
C. **知行 plan 常数**（全部合成、待教研校准）：六个合成领域的权重、按状态的默认失分（unseen 0.5、understand 0.7、execute 0.5、stable 0.25）、
   可提升系数（unseen / understand 1、execute 0.8、stable 0.5、transfer / retain 0）、`high_exam_weight` 阈值 0.2、每题 8 分钟。
   4-K 道法教研给出真实领域权重后替换 `plan-weights.json` 并把 `calibration` 改为 `calibrated`。
D. **未测节点的处理**：知行 plan 对 unseen / 没有记录的节点排 `kind: diagnose` 任务（失分取中性 0.5）；阿鸦 spec 的做法是 unseen 不排任务。
   两种口径待 4-H / 编排层统一。
E. **trend 是否输出**：契约必填，本版按平台状态序计算（与阿鸦一致）；若要求“趋势一律经判断账本”，需要契约把 trend 改为可选或加 `unknown`。
F. **文砺作文的“同题型”**：按体裁区分（`essay_<genre>`）；若教研认为不同体裁也可互为变式，改 `questionTypeOf` 一处。
G. 知行 `idempotency.test.ts` 的时序敏感测试建议另起任务加固（改为等待桩收到两次调用再发第三个请求）。
