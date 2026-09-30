# 冲刺 4-D（阿鸦部分）· 实现记录与待人确认

2026-09-30。按 spec（已确认，待人确认 #1–#9 全部按默认）实现：阿鸦在 `contract_version = 1.0.0` 下声明六个方法都已实现（6/6）。
不改契约、迁移、人设与 evals；不调模型、不联网；全部合成数据。

## 交付
- `adapters/aya/src/methods/`：四个方法的纯函数（不读时钟、不做 IO）。
  - `plan.ts`：算法 `aya-plan@0.1.0`。
  - `teach.ts`：`aya-hint-ladder@0.1.0` 与 `aya-page-lesson@0.1.0`。
  - `variation.ts`：`aya-variant-select@0.1.0`。
  - `report.ts`：`aya-report@0.1.0`。
  - `shared.ts`：题型编码、前缀、按时刻比较。
- `src/adapter.ts`：四个方法共用一个入口：版本、schema、学科与内容版本、幂等，出门前按契约自检（fail closed）。
  - 能力声明 `adapter_version = 0.2.0`。1.0.0 下六个方法都已实现，teach 带 `notes_code: deterministic_no_model`。
  - 原 `ProblemError` 挪到 `src/problem.ts`，行为不变。
- 内容包格式 2（`aya-content-pack/2`，`pack_version 0.2.0-0ed2fe4`，从同一 legacy 提交重新生成）。
  - 题目加 `usage`、`estMinutes`、`hintPath`；节点加 `board`。
  - 另加 `board_exam_share`，标 `q1_derived_uncalibrated`。
  - 另加 `skill_links`（空，`map_ref: null`）。
  - 仍然没有题干、解析、`prerequisites`。
  - 六段标识的 `content_version` 仍是 0.1.0，因为题目与答案都没变。
- 一致性测试夹具：`conformance/fixtures.yaml` 按 spec 第 5 节的表加了 10 条用例，每条都带 `conflict_body`。
  另有 `conformance/fixtures.v0.9.yaml`，是去掉这四个方法后的副本，有漂移测试。
- 单测新增 5 个文件：`plan`、`variation`、`report`、`teach`、`methods-v1`；`pack.test.ts` 改为按格式 2 断言。合计 115 条。
- 变异测试：`scripts/mutations.ts`，44 条，四个新方法各 ≥ 8 条，另有共同入口与内容包。
- Makefile 只改了阿鸦几行（见偏差 1），新增 `adapter-aya-mutation` 并加入 `check`。
- 更新了 `adapters/aya/README.md`。

## 与 spec 的偏差
1. **一致性测试的阿鸦两遍改了 Makefile**（`packages/contracts` 与共享核对库没有改）。
   - 原因：同一份夹具同时给 0.9.0、1.0.0 两遍用。四个新方法的用例带 `node_path`、`item_family`、`knowledge_page`，过不了 0.9.0 的 schema。
   - 0.9.0 下四个方法声明未实现，但夹具里有它们的用例，通用测试会直接判失败。
   - 改法：
     - 0.9.0 那一遍改用 `fixtures.v0.9.yaml`，不再写报告；
     - 1.0.0 那一遍写 `conformance-aya.json`（spec 验收标准要求 1.0.0 下 6/6）。
   - 请确认可以这样改 Makefile。`/gate-check` 读到的阿鸦报告因此从 0.9.0 变为 1.0.0。
2. **四个新方法只按 1.0.0 实现**。
   - 0.9.0 下能力声明为未实现，0.9.0 请求返回 501。
   - spec 没写 0.9.0 下怎么办；v0.9 的迁移期已过（W6）。
3. **夹具由实现会话编写**。spec 要求由独立会话编写、不参考实现，本任务没有独立会话可用。
   - 做法：先写夹具，再写实现；只依据 spec 与契约。
   - 通用测试对这四个方法只查 schema、坏请求与幂等，夹具里没有内容期望。
   - 仍需独立会话或该科负责人复核。
4. **generate_variation 的原题不算“被排除的候选”**。原题本身不进候选池。`all_excluded` 只在匹配的已准入题全在 `exclude_item_refs` 里时出现。
   否则“只有原题准入”也会报 `all_excluded`，含义不对。
5. **几处 spec 没定的细节**，都按下面做，并有单测覆盖：
   - plan：复测任务同样最多取 3 道题。节点在过滤后没有可用的题时不排任务，也包括复测。
   - plan：`mastery` 里同一能力点出现两次，返回 422。
   - plan：练习优先级按 `0.1 + 0.7 × raw / (1 + raw)` 折算，只依赖本能力点。
     复测按时刻先后，在 1.0–0.85 之间线性分配。
   - generate_variation：同时给原题与 `subject_error_category` 时，错因按指定的算；同时给原题与 `skill_node` 时，节点取两者之并。
     `item_family` 里别的学科的题返回 400 `SUBJECT_MISMATCH`，因为 schema 不约束这一字段的前缀。
   - report：`skill_summary` 只列窗内有作答或窗内有掌握记录的能力点。
     错因记录引用的证据全在窗外时不计入。`evidence_ids` 是窗内的作答证据，加上计入的错因引用的窗内证据。
   - teach(hint)：连 0 级都含答案时不给提示文字，只返回一条 `item_ref` 段，`hint_level_used = 0`。
     目前有 3 道题这样，见下面“提示含答案”。
   - teach(lesson)：`skill_node` 不在内容包里返回 422；页面正文为空返回 422；超过 4000 字的段按代码点再切。
   - 答案检查：去掉空白与 `$` 后比较。两个字符以上的答案按子串查；单个字符的答案按独立记号查，避免把 `x^2` 里的 2 当成答案。
     只检查填空题，选择题的答案是选项 key。

## 提示含答案（交教研改题，适配器不改题）
0–3 级提示含标准答案原文的填空题有 **74 道**，括号里是含答案的等级。
spec 说的 45 道只统计了两个字符以上的答案；这里把单字符答案也算进去了（按独立记号）。

Q-ALG-U02-D-001(3)、Q-APP-U03-D-001(3)、Q-APP-U05-D-001(3)、Q-APP-U06-D-001(3)、Q-AXS-U01-D-003(1)、Q-AXS-U03-D-001(1)、Q-AXS-U03-D-003(1)、Q-AXS-U03-D-005(1)、Q-AXS-U03-R-001(1)、Q-AXS-U03-T-001(1)、Q-CGT-U01-D-003(1)、Q-CGT-U01-T-001(23)、Q-CGT-U03-T-001(3)、Q-CIR-U02-D-001(2)、Q-CIR-U05-D-003(3)、Q-CRD-U03-D-003(12)、Q-CRD-U03-R-001(2)、Q-DCE-U01-D-004(3)、Q-EQU-U10-L-002(123)、Q-FIG-U06-D-002(3)、Q-FRAC-U01-D-002(3)、Q-FUN-U10-D-001(3)、Q-FUN-U13-R-001(3)、Q-INEQ-U02-R-001(3)、Q-INEQ-U03-D-003(3)、Q-INV-U02-D-001(3)、Q-INV-U02-R-001(3)、Q-INV-U02-T-001(3)、Q-INV-U05-D-001(3)、Q-LIN-U02-R-001(3)、Q-PLN-U06-D-001(1)、Q-PLN-U06-D-004(1)、Q-PLN-U06-R-002(1)、Q-POW-U04-D-001(23)、Q-PRC-U02-D-003(03)、Q-PRC-U02-R-001(03)、Q-PRF-U01-T-001(013)、Q-PRF-U02-T-001(3)、Q-PYT-U03-D-001(3)、Q-PYT-U03-D-002(3)、Q-PYT-U03-D-003(3)、Q-PYT-U03-R-001(3)、Q-PYT-U03-T-001(3)、Q-RAD-U01-D-003(3)、Q-RAT-U03-D-001(13)、Q-RAT-U07-D-002(3)、Q-REAL-U02-D-002(23)、Q-REAL-U02-R-001(3)、Q-REAL-U02-T-001(2)、Q-REAL-U03-D-003(3)、Q-SIM-U01-D-001(3)、Q-SLE-U04-D-002(1)、Q-SPR-DP-C1(3)、Q-SPR-DP-S4(3)、Q-SPR-DP-V1(3)、Q-SPR-IL-C1(3)、Q-SPR-IL-S2(3)、Q-SPR-IL-S4(23)、Q-SPR-IL-V1(3)、Q-SPR-ND-C1(2)、Q-SPR-ND-V1(13)、Q-SPR-QF-V1(13)、Q-STA-U02-R-001(2)、Q-STA-U04-D-001(23)、Q-STA-U04-D-002(2)、Q-STA-U04-D-004(2)、Q-STA-U04-P-001(3)、Q-TRF-U01-D-003(1)、Q-TRF-U01-D-004(1)、Q-TRF-U01-R-001(1)、Q-TRF-U02-D-002(3)、Q-TRF-U02-D-005(1)、Q-TRF-U03-D-003(3)、Q-TRF-U03-D-005(1)

连 0 级都含答案的 3 道：Q-PRC-U02-D-003、Q-PRC-U02-R-001（答案“蓝球”），Q-PRF-U01-T-001（答案“线段”）。
这 3 道的答案词本来就出现在题目里，是否算泄露请教研判断。

## 待人确认
1. **4–5 级提示几乎都含答案**。338 道填空题里有 273 道，4 或 5 级提示里有标准答案原文。legacy 的 4–5 级就是“参考结论”或完整示范。
   - spec 只要求 0–3 级不露答案。
   - 这与 decisions #4“答案永不下发到客户端”有张力。
   - 建议平台只在作答提交之后才放开 ≥ 4 级；或者由人决定 teach 最高给到 3 级。
2. **“第 5 级”的内容**。600 道只有 0–5 级的题里，529 道的第 5 级以“变式：”开头。
   规格书 §3.1 把“提供变式题”定为第 6 级，legacy 的等级与规格书差一级。按 spec 仍输出 `hintPath[5]`。
3. **Makefile 的改动**（偏差 1），以及 Gate 0 覆盖率报告从 0.9.0 那一遍改取 1.0.0 那一遍。
4. **一致性测试对新方法的深度**（spec 待人确认 #4，按默认）：只靠通用测试加适配器单测，不改 `packages/contracts` 的共享核对库。
   平台侧核对与泄露检查仍只覆盖 grade、diagnose。
5. **内容包格式与 4-G 的合并顺序**（spec 待人确认 #8，默认 4-G 先合并）。4-G 发布闸门还没实现，本任务先把格式升到 2。
   - 4-G 合并时需要：
     - 在格式 2 之上加逐题来源与准入决定文件，格式号顺延为 3；
     - 4-G 的 C9 词表检查覆盖 `hintPath`（提示原文已进包）。
   - 若 4-G 要求两边合成一次格式变更，请在 4-G 合并前告知，本任务可改回。
6. **夹具复核**：10 条新用例由实现会话编写（偏差 3），需独立会话或该科负责人复核。
7. **`skill_links` 为空**：KGE-01 导入阿鸦图谱之前，plan 用不上 `node_path`，不会出现 `prerequisite_task_ids` 与 `prerequisite_of_many`。
   - 对应表来自平台导出的 `kge.skill_link`。
   - 生成脚本已留入口 `AYA_SKILL_LINKS=<导出文件>`，由人执行。
8. **plan 与 report 的编码白名单**需要教研与 5-H 确认：
   - plan 的理由编码：`retest_scheduled`、`prerequisite_of_many`、`high_exam_weight`、`weak_mastery`；
   - report 的亮点编码：`skills_improved`、`skills_declined`、`no_activity`。
   - plan 的参数（薄弱度默认值、板块占比、S/M 类系数、成本）照搬 legacy，是 Q1 推导值（spec 待人确认 #6）。
9. **题型编码**（spec 待人确认 #7）：暂写 `single_choice`、`fill_blank`、`multi_choice`，考情库词表定下来后对齐。
10. **teach 的使用边界**（spec 待人确认 #1 附带项）：v0 不做身份披露与转介，`ai_disclosure_shown`、`referral_triggered` 恒为 false。
    平台不能把学生自由输入送进 teach，接入对话前由平台人设层先做披露与转介。
    页面版本写不进 `source_version`，由编排层记录。

## 测试
- `make adapter-aya-test CONTRACT_STRICT=1`：类型检查通过，115 条单测全过（新增 5 个文件）。
- `make adapter-aya-mutation`：44 条变异全部被发现，约 3.5 分钟（4 核）。
- `make conformance CONTRACT_STRICT=1`：全过。
  - 阿鸦 1.0.0 那一遍：46 passed、6 skipped，报告为 `contract_version 1.0.0`、`implemented 6`、`passed 6`，`global_failures` 为空；
  - 阿鸦 0.9.0 那一遍（`fixtures.v0.9.yaml`）：32 passed、2 skipped。
- `make learning-test CONTRACT_STRICT=1`：2428 passed（它会启动阿鸦适配器，也会读 `pack.json`）。
- 没有跑全量 `make check`：同一工作区里其他会话正在改 adapters/zhixing、adapters/wenli、evals/persona。
