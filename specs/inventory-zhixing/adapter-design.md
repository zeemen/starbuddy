# 知行（zhixing）适配器设计

状态：草稿 v0.2，已按 [decisions.md](./decisions.md)（2026-09-25）修订；标"待输入"的条目等教研或负责人的材料。**不开始实现。**
依据：本目录 inventory.md、decisions.md；完整方案 v1.0 第 5.4、5.5 节；开发计划冲刺 2（知行先接 grade、diagnose、generate_variation）和冲刺 4（覆盖 ≥5/6）；点点-规格书-V1.2 第七章；考官型基线 V1.1 附录 A。
路径缩写同 inventory.md：Z = legacy/zhixing，S = legacy/wenli/初中语文_AI_class/wenli。

> 契约字段、`zhixing.*` schema、RLS 变更、计量、方圆和点点的人设 Prompt 都属于必停项，落地时 PR 首行必须写 [NEEDS-HUMAN]。

### v0.2 修订摘要（对应 decisions.md 编号）

| 决定 | 本文改动 |
|---|---|
| A2 置信度口径 | 第 1 节 grade、第 2 节：M3 低置信并复核，M2 高置信 |
| 对齐文砺 #6 | 第 1 节 generate_variation 定为只从已审题库选题 |
| #13/A5 历史分库、契约 v1 不含历史判分 | 第 1 节、第 3 节 |
| #7/A1 平台是唯一真相源 | 第 1 节 diagnose、第 3 节；坐标待教研 |
| #3、#5、#6 来源待核、白名单 | 新增第 3.1 节内容准入 |
| #4、A4 样卷不作为金标准 | 第 2 节 |
| #14 三风格仅作候选 | 第 5 节 |
| #15、#16、#2 | 第 6 节阻断项 |
| #9、#10 legacy 不处置 | 第 6 节平台侧要求 |
| #1 无真实用户 | 第 3 节不做数据迁移 |
| A6 阻断项不作为冲刺 2 门禁 | 第 6 节 |

---

## 1. 六方法映射

所有方法都要返回 `result / evidence_ids / confidence / source_version / model_version / human_review_required`。

| 插件方法 | 冲刺 | 复用的现有代码 | 做法 | 主要差距 |
|---|---|---|---|---|
| **grade** | 2 | `daofa-m3-schema.ts`、`build-m3-grade.ts`（去掉 PERSONA_TONE）、`daofa-m3-rubric.ts:6-25,49-52`、m3/grade 的 clamp、重算、满分二评逻辑；M2 的 `scoreLocate` 作为确定性验证器 | 引擎只输出结构化的逐点判定，不产出人设文本。**M3 主观题：一律低置信，并置 `human_review_required = true`。M2 定位：确定性验证器，高置信**。契约 v1 只接**道法**判分，历史判分不在范围内 | 判分与人设拆成两步：引擎判分，然后方圆模板宣判、点点复盘；题目和 Rubric 需要版本化 |
| **diagnose** | 2 | `m3ToDimensionRates`、`scoreLocate`、`locateSignalRate`、M1 和 M4 的 `score_rate` | 包装纯函数，输出 evidence_event，交给平台 `mastery_estimate`（唯一真相源）聚合；五维只作为算法参考。skill_node 坐标**待教研定**（五维还是过程四步） | 没有入口诊断卷；定位力混用 0/1 和比率，量纲不一致 |
| **generate_variation** | 2 | `daofa-item-map.ts`、已审通过的 `daofa_item` | **契约 v1：只从已审题库选题**。候选限定为同节点、同错因，并且已准入（见 3.1）；没有合格候选时返回空结果并说明原因。`build-item-generate.ts` 和 M4 `variant_question` 的产出只能进入 content_review，过审后才进入候选池 | AI 题的 `common_errors` 是空的；时政相关变式必须来自白名单素材 |
| **report** | 4 | `buildM5Diagnosis`（只保留计算，去掉文案） | 知行只做字段提供者，由平台报告引擎合成 | 家长摘要目前放在学生端 |
| **teach** | 4 | M1（`build-m1-split.ts`）、M2（定位题库和判定）、M4（`build-m4-anchor.ts`）的引擎 | 包装引擎，输出结构化步骤，由点点主持渲染。M4 只能挂接白名单时政素材（3.1） | F4、F6、F11 仅方案；`comment`、`trap`、`coaching` 里的学长口吻要剥离 |
| **plan** | 4 之后 | 只有 `DIM_COACHING` 静态文案 | 重写：知行提供候选任务和前置关系，由平台编排器排序 | 推荐文案引用了不存在的 F4 |

冲刺 4 能覆盖 5/6（grade、diagnose、generate_variation、report、teach 的部分功能），plan 放到之后。

---

## 2. grade 溯源字段差距

| 契约字段 | 现状 | 适配器做法 |
|---|---|---|
| result | `per_point_results` + `total_score` | 保留，把 `overall_comment` 从结果里移除 |
| evidence_ids | 只有 `evidence_span`，没有 ID | 每个采分点的证据片段都写成 evidence_event，返回 ID |
| confidence | 只有布尔值和 `uncertainty_note` | **口径已定（A2）**：M3 主观题一律低置信（数值随契约定）；M2 定位给出高置信 |
| source_version | 没有 | 题目、Rubric、知识图谱引入 `content_version` 和六段标识 |
| model_version | 写死 `"routed"`（ZHIXING-KD-07） | 由平台模型路由回传；M2 验证器记算法版本 |
| human_review_required | 模型自报，满分时强制置真 | M3 一律置真；题目未准入时置真；申诉触发时置真 |

**金标准（A4，待输入）**：2 道内置题和 3 份样卷都"来源待核"，**不作为金标准**。在教研提供合成标注集之前，M3 保持"全量复核"。

---

## 3. 数据归属

**迁到 platform.*（唯一真相源）**：
- `daofa_ability` → `skill_node`（subject=zhixing）+ `mastery_estimate`。由事件推导，记录算法版本、置信度和证据 ID。坐标**待教研定**；FiveDimRadar 改由平台星图渲染
- `daofa_submission` → `attempt`（原始作答）+ `rubric_result`（推断）+ `evidence_event`。判分和推断**对学生只读**
- 四类归因 → `error_attribution`（大类映射待教研签字，#8）
- M1 自拆、M2 定位、M4 挂接的过程信号 → `evidence_event`（这些目前都不落库）
- `daofa_review_queue`、申诉 → 平台统一复核队列 `appeal`，写入时校验 attempt 归属
- M6 → `exam_run`；家长摘要 → 报告引擎；注册同意 → **`consent_record`（监护人核验）**
- `admin_user`、`model_option`、`app_settings` → 平台管理和模型路由

**留在 zhixing.*（学科私有，版本化内容，受 3.1 准入约束）**：
- 知识图谱节点（通过 skill_node 的 `subject + local_id` 索引），**只保留自写的要点概括和页码索引**
- 题目和采分点 Rubric，**道法和历史分库**
- 定位题库、M1 题型案例
- AI 命题草稿（审核状态走平台 content_review）
- 时政挂接素材（只来自白名单源，带生效和失效日期）
- 错误剧本清单（点点 V1.2 第七章要求学科包提供）

**数据迁移**：不做（#1：没有真实用户）。

### 3.1 内容准入（#3、#4、#5、#6）

| 内容 | 状态 | 能否入平台 |
|---|---|---|
| 2 道内置主观题（标注为南京真题、改编） | **来源待核** | 核实前不作为已准入内容 |
| 3 份样卷 | **来源待核** | 不作为金标准；如果确认来自真实学生，一律排除 |
| 知识图谱 `phrases`（统编教材逐句摘录） | **来源待核** | 法务确认前不入库；节点只保留自写概括和页码索引 |
| 读本节点原文 | **来源待核** | 同上 |
| 定位题 10 道、M1 案例 24 个 | 未审（自称教研雏形） | 过审后入库 |
| 时政素材 | 只来自白名单源 | 走 content_review 审核发布；硬编码的 2 条种子不迁移 |
| AI 命题、M4 变式 | 非准入 | 只进 content_review；不得自由生成核心政治判断内容 |

"已准入" = 教研审核通过，并且来源已核实。只有已准入内容能进入：正式测评、掌握门槛、report、generate_variation 的候选池。

---

## 4. 包装 vs 重写

**可以包装**（纯函数、Schema、Prompt，改动小）：
- `daofa-m3-schema.ts`（扩展溯源字段）
- `m3ToDimensionRates`、`scoreLocate`、`locateSignalRate`
- `buildMockReport`、`buildM5Diagnosis`（只保留计算部分）
- `mapItemRowToQuestion`
- M3 的 clamp、重算、二评逻辑
- `build-m1-split`、`build-m3-grade`、`build-m4-anchor`（去掉人设口吻）
- 知识图谱、题库、定位题、案例数据（按 3.1 准入后迁移）

**需要重写**：
- LLM 调用：改走平台模型路由，带上 usage_id、idempotency_key、预留、结算、回滚和 model_version
- 持久化：由事件推导，不再吞异常
- 鉴权：接平台家庭账号；所有 API 必须鉴权并计量
- RLS：判分和能力对学生只读；题目状态只允许审核角色改；申诉校验作答归属（[NEEDS-HUMAN]）
- 方圆出场模板：新建
- 家长报告、申诉复核：迁到平台
- M4 时政流水线：白名单、审核、发布全部新建
- 错误封装：不向前端透传上游错误

测试基线在平台仓库里重新建立，不沿用 legacy 的 smoke 自述（#11）。

---

## 5. 与点点、方圆的边界

| 现有能力 | 去向 |
|---|---|
| `/chat`、`build-chat.ts`、`daofa-persona.ts` 三风格 | 点点。**语料只作候选**，需要教研审核并跑 `evals/persona` 回归；"去年道法满分""我当年……"不得沿用（#14） |
| `daofa-persona-guard.ts` 禁语正则和探针 | 平台人设守护和 `evals/persona` 种子；补上禁语 D 类 |
| PACE_LIMIT 节律提醒 | 平台壳 / 点点 4.8 |
| M6 合场复盘文案 | 点点（只报数字和建议，不评价快慢） |
| M2 "我当年……"教练文案 | 删除 |
| M3 `overall_comment` | 拆开：方圆宣判（中性，受 G 类禁语约束），点点复盘 |
| 首页问候、诊断推荐、家长摘要 | 点点和报告引擎 |
| 危机信号处理 | **平台层确定性转介，由点点执行**（#16） |

知行作为学科包只交付：结构化引擎输出、错误剧本清单、出场条件映射、方圆的交还话术、课堂形态声明（主持型）。

---

## 6. 上线前阻断项

与 decisions.md 的"上线前必须解决"对齐。这些是**上线门禁，不是冲刺 2 门禁**（A6）：冲刺 2 可以在测试环境里接入。

1. **危机转介**（#16）：平台层确定性转介，由点点执行；禁语 D 类检测。
2. **未成年人同意**（#15）：平台 `consent_record` 加监护人核验。
3. **数据驻留**（#2）：模型路由和数据存储按 ADR 09 执行。
4. **内容准入**（#3、#5、#6）：来源待核内容不入库；时政只来自白名单并经审核。
5. **鉴权与计量**（#10）：所有 API 鉴权并计量；命题只进 content_review。
6. **RLS**（#9）：判分和能力对学生只读；题目状态只允许审核角色改；申诉校验归属；附测试（[NEEDS-HUMAN]）。

legacy 里的对应问题登记为已知缺陷 ZHIXING-KD-01~10（见 decisions.md），不在 legacy 里修。

---

## 待输入

| 编号 | 事项 | 影响 |
|---|---|---|
| #7/A1 | skill_node 坐标：五维还是过程四步（教研定） | diagnose 的输出映射 |
| #8/A3 | 四类错因映射到跨科大类，"转化错"归哪一类（教研签字） | error_attribution 的写入 |
| A4 | 合成金标准标注集 | M3 能否从全量复核降为抽样复核 |
| #4 | 3 份样卷是否来自真实学生 | 是否需要从评测和仓库中排除 |
| #17 | `.env.example` 核查 | 无（只作记录） |
