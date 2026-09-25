# packages/contracts · 学科插件 API 契约

学科插件 API 是四科适配器（以及后续三科）与平台之间**唯一的硬接口**（CLAUDE.md 规则 1）。
本目录的任何改动都属于必停项，PR 首行写 `[NEEDS-HUMAN]`。

| 文件 | 说明 |
|---|---|
| `openapi/plugin-api.v0.9.yaml` | OpenAPI 3.1 草稿 v0.9（待评审） |
| `scripts/check_contract.py` | 契约检查：OpenAPI 合法性、示例符合 schema 且证据引用闭合、反例集、验收标准的结构化检查 |
| `tests/counterexamples.py` | 反例集：每个反例都必须被 schema 拒绝。新增规则时同时新增反例 |
| `tests/test_mutations.py` | 变异测试：把契约故意改坏，断言检查能发现，证明每条规则都在起作用 |
| `requirements-dev.txt` | 检查脚本的依赖（许可证已核，见文件头注释） |

起草计划与验收标准：`specs/contracts-plugin-api-v0.9/spec.md`。

## 运行检查

```bash
python3 -m venv .venv && .venv/bin/pip install -r packages/contracts/requirements-dev.txt
make contract-test PYTHON=.venv/bin/python      # 约 1 秒，check-fast 也会跑
make contract-mutation PYTHON=.venv/bin/python  # 约 15 秒，make check 会跑
```

没装依赖时，两个目标都只打印“跳过”，不算失败。CI 里请设 `CONTRACT_STRICT=1`，这样缺依赖会直接失败。装了依赖后，任何一项不通过都会让 `make` 返回非零。

## 版本策略

- 语义化版本。`info.version`、`RequestContext.contract_version`、信封里的 `contract_version` 三处必须一致。
- **非破坏性变更**（升次版本号）：新增可选字段、新增枚举值（例如新学科、新错因编码）、新增方法。
- **破坏性变更**（升主版本号）：删除字段或枚举值、把可选改成必填、收紧取值范围、改变字段语义。
- v0.x 期间允许破坏性变更，但每次都要在 PR 描述里列出影响到的适配器。W4 冻结为 v1.0 之后，破坏性变更必须有迁移期。
- 契约测试**由契约生成**，不由写适配器的同一个会话手写（ClaudeCode 执行方案 第六章）。

## 设计要点（评审时请重点看）

1. **适配器不写平台表，也不读平台表**。证据以 `evidence` 草稿的形式放在响应里，带适配器生成的 UUID；学习服务按这个 ID 落库（ADR 01）。适配器需要的既有证据、错因、掌握度时间序列，都由平台放进请求（`prior_evidence`、`recent_evidence`、`ReportRequest.evidence / error_attributions / mastery_history`），适配器不自行查库，也不保存副本。
2. **判分没有自由文本**。检查脚本遍历六个响应的完整 schema 树，任何字符串字段都必须有 enum、const、pattern 或 format 约束；开放对象和 `patternProperties` 也不允许。只有两处例外：学生作答原文（`EvidencePayloadText.text`），以及 teach 的讲解文本。六个响应信封之外不允许任何额外字段（`unevaluatedProperties: false`）。评语由人设层单独生成。
2a. **复核规则写进结构**。模型判分一律算主观维度；主观维度的置信度 ≤ 0.5，并且必须复核；`verdict = needs_review` 必须复核；维度分为 null 时，必须列出被排除的维度并复核；由验证器判分时必须带验证器对象和版本。grade 至少要带一条新证据。
3. **计量字段**。`diagnose`、`grade`、`teach` 用 `MeteredContext`，`usage_id` 必填；六个方法都要求 `idempotency_key` 必填。
4. **内容准入**。证据带 `admission`，结果必须带 `semantics`（`formal` / `practice`）。只要有一条证据未准入，结果就只能是 `practice`，并且必须复核。
5. **可追溯**。`model_version` 必须是 `model:供应商/模型@版本` 或 `algo:名称@版本`，不允许 `routed` 这类值。
6. **数据级别**。每个请求都带 `data_class`，插件请求都能关联到学生，所以只允许 L2 或 L3（ADR 09 §2）。适配器如果要调用模型，必须经平台模型路由（ADR 04）。
7. **异步**。`diagnose`、`grade` 可以返回 `202 + job_id`，平台轮询 `GET /jobs/{job_id}`。受理时回传 `usage_id` 和 `idempotency_key`；同一个 key 重试返回同一个 job_id。任务结果的类型与 method 绑定，只有 succeeded 状态才带结果。

## 结构上表达不了、由平台侧校验的规则
- 分数不超过满分；总分与各维之和一致。
- `result.rubric_version` 等于请求里的 `rubric_version`。
- 证据引用闭合：`evidence_ids` 只能指向本响应的草稿，或者请求里的既有证据（示例已由检查脚本验证闭合）。
- 请求 `subject` 与 `item_ref`、`skill_node` 的前缀一致。
- 申诉（`appeal = true`）的结果必须复核。
- `EvidencePayloadText.text` 只能是学生原文，不能是评语（由人设与判分评测抽检）。

## 四科 × 六方法映射（完整方案 5.4 → 契约字段）

下表说明完整方案 5.4 映射表中的每一格，在契约里分别对应哪些请求字段。“阶段”一列取自各科适配器设计 v0.2。

| 方法 | 文砺 wenli | 阿鸦 aya | 知行 zhixing | 开口成章 echo |
|---|---|---|---|---|
| diagnose | M1 试卷 OCR：`mode=paper` + `paper_image_refs`；OCR 低置信 → `ocr_confirmation_required` | 三阶段动态诊断：`mode=intake/gap_test` + `attempts`；`self_report=guessed` 降权 | F1 + F3 反推过程四步：`mode=process` + `attempts` | 入口筛查与转写证据：`mode=intake` + `attempts[].response.transcript_ref`（阶段 0） |
| plan | M2 处方：`mastery` + `constraints`，只输出学科处方 | 规划 Agent 的优先级与复测清单：`PlanTask.kind=retest/practice` | 候选任务与前置关系：`prerequisite_task_ids`（阶段 1） | 五步单元闭环模板（阶段 1） |
| teach | M3 微课 + M6 三级提示：`mode=hint/lesson`，`persona_id=miaobi` | 教练 0–6 级提示：`allowed_hint_level`，`verified_claims`，`persona_id=aya` | 点点主持 F1/F2/F4：`persona_id=diandian`；方圆不在 teach 内 | NPC 场景 + 点点提示（阶段 1）；雅典娜不在 teach 内 |
| grade | M5 五维：`dimensions[].subjective=true` → 低置信 + 复核；quiz 规则判分：`judged_by=rubric_rule` | 验证器 v0：`judged_by=verifier`，边界情况 `verdict=needs_review` + `verifier.reason_codes`；过程题：`scoring_points` | F3 采分点：`scoring_points`；M2 定位：`judged_by=verifier`；**只接道法** | Rubric A/B/C；D 维 `score=null` + `excluded_dimensions`；异步 `202` |
| generate_variation | 同能力格、同错因，从已审题库选题 | 同节点、同陷阱码，`exclude_item_refs` 不重推原题 | 同节点、同错因；时政变式只能来自白名单素材 | 阶段 1 新建题库后接入 |
| report | 七域雷达、错因分布：`skill_summary`、`error_distribution`；位次和提分只在 `forecast` | 三视角字段；`forecast` 只基于已准入内容 | F9 学情字段 | 双语报告字段（阶段 0 作为字段提供者） |

目前找不到对应契约字段的内容，已列入 spec 的“待人确认”：
- 文砺“作文二改幅度”没有数据来源（wenli adapter-design §1）。
- 知行的 skill_node 坐标还没定：五维，还是过程四步（zhixing adapter-design §1，待教研）。
