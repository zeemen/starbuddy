# 知行（zhixing）适配器设计初稿

状态：草稿，等人确认。**不开始实现。**
依据：本目录 inventory.md；完整方案 v1.0 第 5.4、5.5 节；开发计划冲刺 2（知行先接 grade、diagnose、generate_variation）和冲刺 4（覆盖 ≥5/6）；点点-规格书-V1.2 第七章；考官型基线 V1.1 附录 A。
路径缩写同 inventory.md：Z = legacy/zhixing，S = legacy/wenli/初中语文_AI_class/wenli。

> 本设计依赖 `packages/contracts` 中的学科插件 API。其中新增字段、适配器私有 schema（`zhixing.*`）以及任何 RLS 变更，都属于 CLAUDE.md 规定的必停项，落地时 PR 首行必须写 [NEEDS-HUMAN]。

---

## 1. 六方法映射

所有方法都要返回 `result / evidence_ids / confidence / source_version / model_version / human_review_required`。

| 插件方法 | 冲刺 | 复用的现有代码 | 做法 | 主要差距 |
|---|---|---|---|---|
| **grade** | 2 | `daofa-m3-schema.ts`、`build-m3-grade.ts`（去掉 PERSONA_TONE）、`daofa-m3-rubric.ts:6-25,49-52`、m3/grade 的 clamp、重算、满分二评逻辑；M2 的 `scoreLocate` 作为确定性验证器 | 包装：引擎只输出结构化的逐点判定，不产出任何人设文本 | 缺溯源字段（见第 2 节）；判分和人设要拆成两步：引擎判分，然后方圆模板宣判、点点复盘；题目和 Rubric 需要版本化 |
| **diagnose** | 2 | `m3ToDimensionRates`、`scoreLocate`、`locateSignalRate`、M1 和 M4 的 `score_rate` | 包装纯函数，输出 evidence_event，由平台掌握估计负责聚合 | 没有入口诊断卷；证据事件目前被 running average 覆盖掉了；五维和总纲的"过程四步"不一致（待确认）；定位力混用 0/1 和比率，量纲不一致 |
| **generate_variation** | 2 | `build-item-generate.ts`、`daofa-item-map.ts`、M4 的 `variant_question` | 包装，加上错因和节点条件，产出物一律进入平台 `content_review` | 没有按错因生成；没有管理员门禁；M4 变式不落库；AI 题的 `common_errors` 是空的 |
| **report** | 4 | `buildM5Diagnosis`（只保留计算，去掉文案） | 知行只做字段提供者，由平台报告引擎合成 | 家长摘要目前放在学生端 |
| **teach** | 4 | M1（`build-m1-split.ts`）、M2（定位题库和判定）、M4（`build-m4-anchor.ts`）的引擎 | 包装引擎，输出结构化步骤，由点点主持渲染 | F4、F6、F11 仅方案；`comment`、`trap`、`coaching` 里的学长口吻要剥离 |
| **plan** | 4 之后 | 只有 `DIM_COACHING` 静态文案 | 重写：知行提供候选任务和前置关系，由平台编排器排序 | 推荐文案引用了不存在的 F4 |

冲刺 4 能覆盖 5/6（grade、diagnose、generate_variation、report、teach 的部分功能），plan 放到之后。

---

## 2. grade 溯源字段差距

| 契约字段 | 现状 | 适配器做法 |
|---|---|---|
| result | `per_point_results` + `total_score` | 保留，把 `overall_comment` 从结果里移除 |
| evidence_ids | 只有 `evidence_span`，没有 ID | 每个采分点的证据片段都写成 evidence_event，返回 ID |
| confidence | 只有布尔值和 `uncertainty_note` | 由校验规则产出置信度，例如采分点证据是否齐全、多次采样是否一致（具体算法待定） |
| source_version | 没有 | 题目、Rubric、知识图谱引入 `content_version`，并补齐六段标识 |
| model_version | 写死 `"routed"` | 由平台模型路由返回实际的供应商/模型/版本 |
| human_review_required | 由模型自报，满分时强制置真 | 保留规则，另外加入低置信度和申诉触发 |

---

## 3. 数据归属

**迁到 platform.*（唯一真相源）**：
- `daofa_ability` → `skill_node`（subject=zhixing）+ `mastery_estimate`。由事件推导，记录算法版本、置信度和证据 ID。FiveDimRadar 改由平台星图渲染。
- `daofa_submission` → `attempt`（原始作答）+ `rubric_result`（推断）+ `evidence_event`。判分行对学生只读。
- 四类归因 → `error_attribution`（大类映射待教研签字）。
- M1 自拆、M2 定位、M4 挂接的过程信号 → `evidence_event`（这些目前都不落库）。
- `daofa_review_queue`、申诉 → 平台统一复核队列 `appeal`。
- M6 → `exam_run`；家长摘要 → 报告引擎；注册同意 → `consent_record`。
- `admin_user`、`model_option`、`app_settings` → 平台管理和模型路由，不属于知行。

**留在 zhixing.*（学科私有，版本化内容）**：
- 知识图谱节点（通过 skill_node 的 `subject + local_id` 索引）
- 题目和采分点 Rubric（**道法和历史分库**）
- 定位题库、M1 题型案例
- AI 命题草稿（审核状态走平台 `content_review`）
- 时政挂接素材（带生效和失效日期）
- 错误剧本清单（点点 V1.2 第七章要求学科包提供）

---

## 4. 包装 vs 重写

**可以包装**（纯函数、Schema、Prompt，改动小）：
- `daofa-m3-schema.ts`（扩展溯源字段）
- `m3ToDimensionRates`、`scoreLocate`、`locateSignalRate`
- `buildMockReport`、`buildM5Diagnosis`（只保留计算部分）
- `mapItemRowToQuestion`
- M3 的 clamp、重算、二评逻辑
- `build-m1-split`、`build-m3-grade`、`build-m4-anchor`、`build-item-generate`（去掉人设口吻）
- 知识图谱、题库、定位题、案例数据（迁移成版本化内容）

**需要重写**：
- LLM 调用：改走平台模型路由，带上 usage_id、idempotency_key、预留、结算、回滚和 model_version。不能再用 `callText("grade")` 承载对话。
- 持久化：由事件推导，不再吞异常。
- 鉴权：接平台家庭账号；所有 API 必须鉴权并计量。
- RLS：判分和能力对学生只读；题目状态只允许审核角色改；申诉校验作答归属。（涉及 migrations 和 RLS，[NEEDS-HUMAN]）
- 方圆出场模板：新建。
- 家长报告、申诉复核：迁到平台。
- M4 时政流水线：白名单、审核、发布全部新建。

---

## 5. 与点点、方圆的边界

| 现有能力 | 去向 |
|---|---|
| `/chat`、`build-chat.ts`、`daofa-persona.ts` 三风格 | 点点（语料只作候选，需要教研审核并跑人设回归） |
| `daofa-persona-guard.ts` 禁语正则和探针 | 平台人设守护和 `evals/persona` 种子 |
| PACE_LIMIT 节律提醒 | 平台壳 / 点点 4.8 |
| M6 合场复盘文案 | 点点（只报数字和建议，不评价快慢） |
| M2 "我当年……"教练文案 | 删除 |
| M3 `overall_comment` | 拆开：方圆宣判（中性，受 G 类禁语约束），点点复盘 |
| 首页问候、诊断推荐、家长摘要 | 点点和报告引擎 |

知行作为学科包只交付：结构化引擎输出、错误剧本清单、出场条件映射、方圆的交还话术、课堂形态声明（主持型）。

---

## 6. 接入前阻断项（建议）

1. 危机信号的确定性转介路径（输入端检测 + 固定话术），属于平台层。
2. 禁语 D 类检测。
3. 模型路由固定为境内供应商，或者先完成跨境评估。
4. 所有 API 都鉴权并计量。
5. 修复 RLS 漏洞（如果线上仍在运行，需要单独处置）。

---

## 需要人确认的疑点（适配器设计相关）

1. 学科 skill_node 的坐标：用代码里的"五维"还是总纲的"过程四步"？
2. `confidence` 的算法口径：多次采样一致性、证据覆盖率，还是由校验器给出？
3. 知行四类归因映射到七大类（尤其是"转化错"），需要教研签字。
4. 冲刺 2 的 grade 金标准：先拿 2 道内置题加 3 份样卷做种子，还是等教研标注的新集合？样卷来源需要先确认。
5. 历史学科（春秋 / 知行·史）是否同属 zhixing 适配器、分库存放，还是单独建适配器？
6. 以上第 6 节的阻断项，是否全部作为冲刺 2 的门禁？
