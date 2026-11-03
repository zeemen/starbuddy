# 插件 API v1.0 契约评审会 · 表决表

- 用途：供评审会逐项表决。本表只整理仓库里已写下的问题、默认建议与出处，**不代替评审下结论**；“默认建议”都是起草人或来源文件里已写的默认做法，评审可以推翻。
- 截至：2026-10-08，仓库 HEAD `5e10fe7`。行号是各文件在该提交时的行号（`文件:行号`）。
- 引用缩写：
  - 【清单】= `specs/contract-conformance-sprint1/v1.0-review.md`（61 项原始清单）
  - 【spec】= `specs/plugin-api-v1.0/spec.md`；【记录】= `specs/plugin-api-v1.0/lessons.md`
  - 【二步】= `specs/plugin-api-v1.0-step2/lessons.md`
  - 【v1.0】= `packages/contracts/openapi/plugin-api.v1.0.yaml`（候选版）
  - 【LI】= `packages/contracts/openapi/learning-internal.v1.yaml`（1.1.0 草稿）
  - 【IS】= `packages/contracts/openapi/insight-service.v0.9.yaml`
  - 【W13】= `docs/gates/w13-debt-and-decisions.md`
- 表决栏填“同意 / 修改 / 否决”之一；选“修改”时请在表后“修改意见”处写明改成什么。

---

## 一、会议说明

### 1.1 会议目的
插件 API v1.0 目前是**候选版**：文件已写好、测试已按它跑通，但清单 61 项没有人签字（【v1.0】:7-8；【记录】:3,48；【W13】:32 的 B01）。本会对三份待签契约逐项表决：
1. 插件 API v1.0：61 项（第二节）；
2. 学习服务内部 API `learning-internal` 1.1.0 与洞察服务 API `insight-service` v0.9：各自的签字要点（第三节）；
3. 【W13】第 2.2 节“契约”33 条中不在 61 项里的事项（第四节），可本次表决，也可只定处理人与时间。

### 1.2 签字后会发生什么（仓库已写明的后续动作）
| 动作 | 现状 | 出处 |
|---|---|---|
| 关掉候选开关 `STARBUDDY_ALLOW_CANDIDATE_CONTRACT` | 1.0.0 目前列为候选版，不设该开关就拒用：共享库 `CANDIDATE_VERSIONS = {"1.0.0"}`；三科适配器 `CANDIDATE_CONTRACT_VERSIONS = ["1.0.0"]`，作默认版本时不开开关拒绝启动；学习服务今日计划在开关关闭时每科都返回 `unavailable` | `packages/contracts/python/starbuddy_contract/contract.py:26-40`；`adapters/aya/src/contract.ts:17`、`adapters/wenli/src/contract.ts:18`、`adapters/zhixing/src/contract.ts:18`；`adapters/aya/src/main.ts:33-35`；【LI】:161-162 |
| 默认版本切到 1.0.0 | 共享库缺省 `0.9.0`；三科 `DEFAULT_CONTRACT_VERSION = "0.9.0"`，能力声明报 0.9.0；阿鸦“v1.0 签字后改报 1.0.0” | `contract.py:23`；`adapters/aya/src/contract.ts:15`、`adapters/wenli/src/contract.ts:16`、`adapters/zhixing/src/contract.ts:16`；`specs/plugin-api-v1.0-step2/spec.md:50`；`specs/e2e-integration-sprint5/lessons.md:88-90` |
| 重生 Gate 0 样例 | 签字后“默认版本与候选开关一并收口，需重生样例”；做法：干净工作区跑 `make gate0-evidence GATE0_OUT=tools/e2e-cohort/sample`，PR 说明差异 | `specs/e2e-integration-sprint5/lessons.md:90`；【W13】:402（4.8 节） |
| BFF 写入切到 `/internal/v1` | 默认“v1.0 签字后另开小任务” | 【二步】:33 |
| 按结论改契约与测试 | 改契约、反例、变异、一致性测试，同步改阿鸦与学习服务；另开 PR，首行 `[NEEDS-HUMAN]` | 【清单】:120 |

> 行号差异说明：用户任务与 【W13】:32 写的是“按 e2e lessons 第 66 行”。该行号对应 W13 文件写作时的提交 `d0ba0f7`；此后该文件前面插入了内容，同一句话现在在第 90 行（第 88-90 行是“契约缺口 6”）。现第 66 行讲的是截图脚本的文案定位，与本事无关。

### 1.3 建议时长与参会人
- **参会人（仓库写明的）**：技术负责人、四科负责人、教研代表（【清单】:118；【spec】:101）。仓库现状：英语已搁置，Gate 0 口径已改为三科（【W13】:33 的 B02）；“四科负责人”是否改为三科由会议定。
- 洞察服务契约的签字人仓库没有写明；SIE 相关数值签字提到“SIE 负责人”（`specs/sie-02b-claims-first-report/lessons.md:66`）。是否请 SIE 负责人到会，由组织者定。
- **建议时长（起草人估计，仓库无规定）**：本表共 118 行（61 + 12 + 14 + 31）。若“维持 / 写进描述”类合并表决、破坏性变更逐条讨论，约 3 小时；也可拆两场：第一场插件 API 61 项（约 2 小时），第二场其余两份契约与 2.2 节（约 1.5 小时）。
- 会前一周发出本表与【清单】（【清单】:118）。

### 1.4 关于“61 项”的核对
- 【清单】第一至七节合计正好 **61 项**：一、18 项（N1–N18，行 21-38）；二、10 项（v09-#1–#10，行 43-52）；三、12 项（无编号，行 57-68，本表记作“三-1”至“三-12”）；四、5 项（sie-#1–#5，行 75-79）；五、5 项（kge-#1–#5，行 86-90）；六、9 项（svc-#1–#9，行 99-107）；七、2 项（pol-#1–#2，行 114-115）。与【spec】:6、:97 的“61 项”一致。
- 其中 N17、N18、pol-#2 改的不是插件 API 本身（前两项改 `learning-internal`，pol-#2 改 `insight-service`），但都在 61 项里，本表照列。
- 【记录】里明写“61 项”的是第 12 行与第 48 行；第 3 行只写“评审会逐项签字”，第 23 行是候选开关的说明，二者没有写数字（【W13】:32 引用的是 3、23、48 行）。
- 较早的 `specs/contract-conformance-sprint1/lessons.md:50` 写的是“16 + 10 + 12 项”，那是清单后来扩充之前的状态。

---

## 二、插件 API v1.0 表决表（61 项）

列说明：“默认建议 / 当前实现”中标【破坏】的，是相对 v0.9 会让原来合法的请求或响应被拒的变更。“影响”先写清单原文，再写仓库里依赖它的代码位置（举例，不保证穷尽）。

### A. 破坏性收紧（9 项）
| 编号 | 问题 | 默认建议 / 当前实现 | 影响 | 出处 | 表决 |
|---|---|---|---|---|---|
| N3 | 证据没有字段指明属于哪道作答，同题同刻的两道作答只能拒绝 | 作答类证据必带 `attempt_id`【破坏】；候选版已实现 | 四科适配器、学习服务核对；`adapters/{aya,zhixing,wenli}/src/adapter.ts`、共享库 `verify()` | 【清单】:23；【spec】:27；【v1.0】:13-14 | |
| N4 | `AttemptInput.attempt_id` 可选，但平台落库要重试不变的 ID | 写明由平台生成，并随 N3 改为必填【破坏】（偏离清单原结论“否”） | 学习服务早已这样传；实际影响只有异步英语示例 | 【清单】:24；【spec】:28；【记录】:16 | |
| N7 | 请求学科与 `item_ref` / `skill_node` 前缀一致，原来只在平台侧校验 | schema 按学科 if/then 约束【破坏】；道法只约束 `item_ref`（知行坐标未定，见 v09-#9） | 四科；【v1.0】:587-593 起 | 【清单】:27；【spec】:31；【记录】:19 | |
| N10 | 时间字段只靠 `format`，缺格式插件时不检查 | `Timestamp` 加带时区的 RFC 3339 pattern【破坏】 | 四科；【v1.0】:381 | 【清单】:30；【spec】:34 | |
| N13 | 一致性测试里的约定契约没写：未实现方法 501、`request_id` 不比较、`student_ref` 是否强制 uuid、错误体类型 | 全部写进契约；`student_ref` 加小写 UUID pattern【破坏】 | 四科、一致性测试；【v1.0】:32-33、约 :508 | 【清单】:32；【spec】:37；【记录】:18 | |
| svc-#9 | `signal_code` 任意编码都能写，包括声纹类前缀（规则 12） | 拦截声纹、面部、眼动、情绪等词根【破坏】；按下划线分段匹配，`facet_` 不误伤 | 文砺输入通道 `adapters/wenli/src/core/channel.ts`；学习服务 `learning/store.py`；【v1.0】:644 | 【清单】:107；【spec】:85；【记录】:28 | |
| svc-#7 | 插件 `ScoringPoint.point_id` 允许大写，知识服务与迁移 0011 只允许小写 | 只允许小写【破坏】 | 知行、文砺采分点；【v1.0】:1249 | 【清单】:105；【spec】:65 | |
| sie-#2 | `report` 的 `forecast` 可返回给学生与家长，与字段字典“只给 Agent”冲突 | 移除 `forecast`，预测只由 `insight.predict` 给编排层【破坏】（偏离 spec 的“改 audience”） | 阿鸦 `src/methods/report.ts` | 【清单】:76；【spec】:57；【记录】:15；【v1.0】:19 | |
| sie-#4 | `teach` 的 `verified_claims` 与判断账本的 claim 同名不同义 | 改名 `verified_statements`【破坏】 | 阿鸦 `src/methods/teach.ts`（阿鸦 teach 原未实现，影响为零） | 【清单】:78；【spec】:59；【v1.0】:1127 | |

### B. 错误码、重试与幂等（6 项）
| 编号 | 问题 | 默认建议 / 当前实现 | 影响 | 出处 | 表决 |
|---|---|---|---|---|---|
| N17 | 计量预留过期后用原 key 重试，学习服务会重新执行，出现“没有预留的执行” | `learning-internal` 新增只查询的重放接口；没有记录时封存该 key，晚到的原请求返回 `IDEMPOTENCY_KEY_SEALED`；按 `student_ref`、`usage_id` 绑定。已实现（迁移 0016） | 学习服务 `learning/app.py`、BFF `src/metering/learning-client.ts`；改的是【LI】:113-141 | 【清单】:36；【spec】:41；【二步】:31-32；【W13】:7 | |
| N18 | `learning-internal.v0` 对 5xx 语义有歧义 | 契约写明 5xx 一律结果未知；学习服务 `INTERNAL` 改 `retryable: true` 已在第二步完成 | 学习服务、BFF；【LI】:52-54、:1227 | 【清单】:37；【spec】:42 | |
| N8 | 可重试错误即“结果未知”，须同 key 重试后再结算或回滚 | 写进 `Problem.retryable` 描述 | BFF、学习服务；【v1.0】:27-29 | 【清单】:28；【spec】:32 | |
| N14 | 同 key 不同请求体的冲突由谁检测 | 平台（学习服务）负责，适配器尽力而为；一致性测试保留 409 断言 | 四科、学习服务 | 【清单】:33；【spec】:38；【v1.0】:33 | |
| N1 | 鉴权失败没有 Problem 编码，401 借用 `INVALID_REQUEST` | 新增 `UNAUTHENTICATED`（不破坏）；阿鸦暂未启用 | 阿鸦 server、参考适配器；【v1.0】:1693；【二步】:40 | 【清单】:21；【spec】:25 | |
| N9 | 不支持的模式或输入形态（拍照、`paper` / `process`）用哪个错误码 | 新增 `MODE_NOT_SUPPORTED`（不破坏）；阿鸦 v1.0 下已用 | 阿鸦、知行、文砺 `src/adapter.ts`；【v1.0】:1692 | 【清单】:29；【spec】:33 | |

### C. 判分、采分点与掌握门槛（12 项）
| 编号 | 问题 | 默认建议 / 当前实现 | 影响 | 出处 | 表决 |
|---|---|---|---|---|---|
| 三-5 | 主观维度待复核时，是否暂缓计入正式测评与掌握门槛 | 采纳：待复核结果不推动星亮门槛（ADR 11） | 掌握计算（属必停类“星亮门槛”） | 【清单】:61 | |
| N5 | `rule_ref` 要对照注册表，但注册表不存在，任意值都被接受 | 写明注册表来源：采分点型以 `kge.scoring_point` 为准，验证器型以 `capabilities.verifiers` 弱校验；平台尚未落实核对（【二步】:42-43） | 学习服务、迁移（必停） | 【清单】:25；【spec】:29；【v1.0】:38-39 | |
| N6 | 总分与维度分之和是否要一致 | 写明加权和、上限取采分点分值之和，平台校验 | 文砺五维、知行采分点 | 【清单】:26；【spec】:30；【v1.0】:40 | |
| N2 | `needs_review` 暂不给分，借用“维度分 null + `dimension_unavailable`” | 描述写明“null 表示待复核”，v1.1 再议新增 `pending_review_score` | 阿鸦、知行、文砺 `src/adapter.ts`；复核台 `services/learning/review_console/app.py` | 【清单】:22；【spec】:26；【v1.0】:43 | |
| kge-#2 | N5 的注册表与 `kge.scoring_point` 的关系 | 写进描述（同 N5） | 同 N5 | 【清单】:87；【spec】:63 | |
| kge-#3 | N6 各维之和的上限从哪里取 | 写进描述（同 N6） | 同 N6 | 【清单】:88；【spec】:63 | |
| kge-#1 | 判分维度 `dimension_id` 与采分点 `point_id` 的对应 | 写进描述：维度 ID = 采分点 ID | 知行、文砺；【v1.0】:41 | 【清单】:86；【spec】:63 | |
| kge-#4 | `source_version` 里地图版本段的写法 | 写进描述：`graph:<学科>-map@x.y.z` | 四科；【v1.0】:42 | 【清单】:89；【spec】:63 | |
| 三-3 | 分数不超过满分；总分与各维之和一致 | 同 N6（满分已由数据库保证） | 同 N6 | 【清单】:59 | |
| 三-6 | 平台重新判定的语义是否在 schema 里表达 | 不表达（学习服务已实现，一致性测试也检查） | 学习服务 | 【清单】:62 | |
| v09-#1 | 低置信与高置信的数值 | 维持（验证器 1.0；主观维 ≤ 0.5 并复核；可选 `uncertainty`），文砺、知行接入后用金标准校准 | 判分评测 | 【清单】:43；【spec】:45 | |
| v09-#6 | 验证器原因码是否进契约 | 维持（作为枚举进契约，阿鸦已按此实现） | 阿鸦 | 【清单】:48；【spec】:45 | |

### D. 证据与输入通道（6 项）
| 编号 | 问题 | 默认建议 / 当前实现 | 影响 | 出处 | 表决 |
|---|---|---|---|---|---|
| svc-#4 | grade 的输入改为作答证据 | grade 新增可选 `answer_evidence_ref`、`evidence_level` 与归一化形态（`latex`、`mhchem`、`semantic`）；`image_ref`、`transcript_ref`、`paper_image_refs` 标废弃、迁移期仍接受（spec 待人确认 #4 默认先废弃，ADR 15 原写 v1.0 删除） | 学习服务 `learning/attributes.py`；阿鸦 v1.0 只认 `text` 与 `choice`，`latex` 等返回 422（【二步】:37-38）；【v1.0】:24-25、:1172-1176、:797-824 | 【清单】:102；【spec】:80-82；【记录】:22 | |
| svc-#2 | 证据草稿是否直接带等级、通道、情境等字段 | 不带，由平台补齐（spec 待人确认 #3 默认） | 学习服务；文砺另有私有约定（见第四节 4-12） | 【清单】:100；【spec】:64；【v1.0】:48-49 | |
| kge-#5 | `EvidenceDraft` 的 `evidence_kind`、`map_version`、`cognitive_level` | 不加，由平台补齐（同 svc-#2） | 同 svc-#2 | 【清单】:90；【spec】:64 | |
| N15 | 同一证据被不同请求引用 | 证据只记一次，推断可以多次；写进描述 | 文档 | 【清单】:34；【spec】:39；【v1.0】:34 | |
| N11 | 含照片或语音的证据要求请求为 L3 | 维持平台侧校验（schema 看不到请求级别） | 学习服务 | 【清单】:31；【spec】:35 | |
| v09-#7 | `data_class` 由谁定 | 维持：调用方声明，平台校验 | 学习服务 | 【清单】:49；【spec】:45 | |

### E. 与两个核心、考情库的衔接（7 项）
请求里新增的都是**可选**字段，平台上线对应服务后开始填；适配器不直接调用两个核心、不读 policy 表（【spec】:68）。

| 编号 | 问题 | 默认建议 / 当前实现 | 影响 | 出处 | 表决 |
|---|---|---|---|---|---|
| svc-#1 | 插件请求里的画像上下文 | diagnose / plan / report 请求新增可选 `learner_context`（`insight.context` 裁剪结果，推断条目带 `claim_id`）；按方法限定字段未定（第四节 4-4） | 学习服务 `learning/plan.py`、阿鸦 `methods/plan.ts`、`methods/report.ts`；【v1.0】:835 | 【清单】:99；【spec】:71；【记录】:49 | |
| pol-#1 | 各科满分、题型、时长的来源 | plan、generate_variation、grade 请求新增可选 `exam_config` | 阿鸦 `methods/plan.ts`、`variation.ts`；【v1.0】:957 | 【清单】:114；【spec】:74 | |
| svc-#6 | 知识服务怎么进插件请求 | 新增可选 `knowledge_page`（teach）、`node_path`（diagnose、plan）、`item_family`（generate_variation） | 阿鸦、知行、文砺；【v1.0】:842、:1070、:1398 | 【清单】:104；【spec】:73 | |
| svc-#5 | `rubric.get` 与 grade 的 `rubric_version` | grade 新增可选 `scoring_points` | 知行、文砺 `src/adapter.ts`；【v1.0】:1167 | 【清单】:103；【spec】:72 | |
| pol-#2 | 洞察服务 `ExamTarget.full_score` 写死 100/120/150 | 放宽为 1–300 整数，服务端对照政策库校验（改的是【IS】）；但预测区间上限仍是 150（见第三节 I-6） | 洞察服务；【IS】:611 | 【清单】:115；【spec】:77；【记录】:38 | |
| svc-#3 | report 的结论句 | 同 sie-#1 | 同 sie-#1 | 【清单】:101；【spec】:75 | |
| svc-#8 | `insight.predict` 的可见范围 | 不涉及插件 API（对账卡上线时由人确认） | 无 | 【清单】:106；【spec】:76 | |

### F. 判断账本与对学生的表述（5 项）
| 编号 | 问题 | 默认建议 / 当前实现 | 影响 | 出处 | 表决 |
|---|---|---|---|---|---|
| sie-#1 | `report` 的 `trend`、`highlight_codes` 由适配器产出、平台合成给学生家长，不引用判断 | 保留字段，描述写明“不直接展示给学生家长，结论句由洞察服务生成”；`trend` 是否改可选见第四节 4-11 | 三科 report；【v1.0】:53-54、:1546、:1558 | 【清单】:75；【spec】:56 | |
| sie-#3 | `teach` 讲解文本可能含关于学生的陈述 | 描述写明讲解只讲内容，不写关于学生的判断 | teach；【v1.0】:54 | 【清单】:77；【spec】:58 | |
| N16 | teach 讲解、OCR、ASR 等自由文本里的泄露查不到 | 维持：靠人设与评测抽检 | teach、文砺、英语 | 【清单】:35；【spec】:40 | |
| 三-10 | 姓名、学校类 PII 的检测 | 人设与评测抽检，不进 schema | 同上 | 【清单】:66 | |
| sie-#5 | `diagnose` 输出 | 维持（只落推断层，没有面向学生的文本） | 无 | 【清单】:79；【spec】:60 | |

### G. 计量、超时与异步（6 项）
| 编号 | 问题 | 默认建议 / 当前实现 | 影响 | 出处 | 表决 |
|---|---|---|---|---|---|
| v09-#2 | `teach` 是否计量、是否拆成两个操作 | 按计量 v2 资源类型（`ai_active_time`、`lesson_whiteboard`、`voice_realtime`）写进描述；`hint` 计入学时 | BFF 计量（必停类）；【v1.0】:1039 | 【清单】:44；【spec】:46 | |
| 三-1 | 普通提示是否计量 | 同 v09-#2 | 同上 | 【清单】:57 | |
| 三-2 | 结算单位、内容指纹字段 | 结算单位进资源类型配置；v1.0 不加内容指纹，去重由 BFF 按平台侧摘要做 | BFF | 【清单】:58；【spec】:50 | |
| 三-9 | 各方法的超时约定 | 写进描述：grade 10 秒、diagnose 30 秒、其余 30 秒 | 学习服务、四科；【v1.0】:35 | 【清单】:65；【spec】:48 | |
| 三-12 | 非消耗类方法不计量的理由 | 写进描述（“计量”一节） | 文档；【v1.0】:71-75 | 【清单】:68 | |
| v09-#3 | 异步回调方式 | 维持只提供轮询（202 加轮询已有一致性测试） | 英语（已搁置） | 【清单】:45；【spec】:45 | |

### H. 写法约定与暂缓事项（10 项）
| 编号 | 问题 | 默认建议 / 当前实现 | 影响 | 出处 | 表决 |
|---|---|---|---|---|---|
| v09-#5 | `model_version` 写法 | 维持 `model:供应商/模型@版本`，快照版本写在 `@` 后（ADR 04 增补） | 四科 | 【清单】:47；【spec】:47 | |
| 三-11 | 一次结果用两个模型时 `model_version` 怎么写 | 写主模型，其余写进 `source_version` 的 `algo:` / `prompt:` 段 | 四科 | 【清单】:67；【spec】:49 | |
| 三-4 | 请求学科与内容版本、前缀一致 | 已由 N7 在 schema 表达 | 同 N7 | 【清单】:60 | |
| v09-#9 | 知行 skill_node 坐标 | 等教研；N7 对道法先只约束 `item_ref` | 知行 | 【清单】:51；【spec】:45、:105 | |
| N12 | `content_version` 只有一个 `grade`，阿鸦题库覆盖初一到初三 | 不进 v1.0，随课程版本迁移另议（必停） | 阿鸦、平台 curriculum_version | 【清单】:38；【spec】:36 | |
| v09-#4 | 学科枚举是否一次列全七科 | 维持只列四科（新增非破坏） | 无 | 【清单】:46；【spec】:45 | |
| v09-#10 | `teach` 是否要流式变体 | v1.1 再议 | teach | 【清单】:52；【spec】:45 | |
| v09-#8 | 文砺“作文二改幅度” | 维持不设字段，等数据来源 | 文砺 | 【清单】:50；【spec】:45 | |
| 三-7 | 文砺“维度 × 难度”的变式参数 | 冲刺 2 文砺接入时定（清单未写是否已定） | 文砺 | 【清单】:63 | |
| 三-8 | 英语安全审核 flag 与复核分开 | 随调整后的英语方案定（英语已搁置） | 英语 | 【清单】:64 | |

**61 项之外、已写进候选版的变更（随本表一起确认）**：`idempotency_key` 限定为小写 UUIDv4【破坏】（【记录】:21；【v1.0】:17、:1586）；选择题选项、`latex`、`mhchem` 的字符与命令约束（【记录】:28；【v1.0】:23）；适配器路径仍用 `/plugin/v0`、靠 `contract_version` 协商（【记录】:17）；`semantic` 只约束“操作名 + 最多 8 个参数”（【记录】:20）；没有 `mhchem` 示例（【记录】:24）。

**修改意见**（填编号与改法）：

---

## 三、另两份待签契约的签字要点

### 3.1 学习服务内部 API `learning-internal` 1.1.0（12 项）
文件头写“v1 候选版，待评审签字”（【LI】:4-7）。1.1.0 只新增接口与错误码，写明“非破坏性”（【LI】:13-14）；但档案页两接口、星图接口是在“未发布的 1.1.0 草稿内追加”，没有升版本号，签字范围因此扩大（【LI】:26-33）。

| 编号 | 要点 | 当前实现 / 默认建议 | 影响 | 出处 | 表决 |
|---|---|---|---|---|---|
| L-1 | 写入接口 `/internal/v1/grade`、`/diagnose`，请求体改为信封 `{request, ingest}`，`request` 原样是插件 v1.0 请求 | 已实现；BFF 写入仍走 v0，签字后另开小任务切换 | 学习服务、BFF 计量 | 【LI】:35-40、:69-111；【二步】:33 | |
| L-2 | 只查询的重放接口与 key 封存（N17）；新错误码 `REPLAY_NOT_FOUND`、`IDEMPOTENCY_KEY_SEALED` | 已实现；迁移 0016 必须先于该版本学习服务上线 | BFF `src/metering/learning-client.ts`、学习服务；迁移 0016 | 【LI】:55-58、:113-141、:1211-1212；【二步】:31-32 | |
| L-3 | 重放仍是 GET，key、`student_ref`、`usage_id` 在查询串里；封存记录保留期 | 现做法：关访问日志、不经记 URL 的代理；“下次改版考虑改 POST”；保留期随档案级联删除，未定是否按时间清理 | 部署、学习服务 | 【二步】:34-36 | |
| L-4 | 5xx 一律结果未知，`retryable: false` 只对 4xx 有意义（N18） | 已实现；迁移期 v0 的 BFF 可能“已落库却没扣次”（只少计） | BFF 计量（必停类） | 【LI】:52-54、:1227；【记录】:50 | |
| L-5 | 今日计划 `POST /internal/v1/plan`（非消耗类、不写表；响应不含 `p_mastery` 等） | 已实现；只按插件 v1.0 实现，候选开关关闭时每科 `unavailable`；请求响应形状待确认 | BFF 统一壳 `src/shell/plan-client.ts`、学习服务 `learning/plan.py` | 【LI】:143-174；`specs/unified-shell-sprint4/lessons.md:231` | |
| L-6 | 掌握查询 `mastery/get`、`mastery/explain`：直接引用知识服务 v0.9 的 schema，只给编排层令牌 | 已实现；编排层（调用方）尚未实现；`caller` 枚举只有编排层与学习服务；explain 的 `weight` 可反推未签字配置数值 | 编排层、知识服务契约 | 【LI】:15-20、:207-266；`specs/kge-04-mastery-step2/lessons.md:207-208,211` | |
| L-7 | 验证任务 `verification`、撤销 `verification/cancel`（SIE-03“我其实会”），只接受洞察服务令牌 | 已实现；学生由洞察服务附带而非学习服务反查；`verification_item_refs` 实为任务引用不是题目；孤儿任务兜底未定 | 洞察服务、迁移授权 | 【LI】:21-25、:267-325；`specs/kge-04-mastery-step2/lessons.md:202-204` | |
| L-8 | 学情档案页 `profile-page/ensure`、`profile-page/read`；新错误码 `PROFILE_UNAVAILABLE` | 已实现；在 1.1.0 草稿内追加未升版，“签字人需重审”；会话开始由谁调 ensure 未定 | 编排层、洞察服务（周报） | 【LI】:26-29、:328-384；`specs/kge-04-mastery-step2/lessons.md:227,247,250` | |
| L-9 | 星图节点星态 `star-map/nodes`，只给 BFF 令牌，只含星态不含数值 | 已实现；BFF 令牌第一次能读到掌握派生的星态；纵深防御（再带账号 ID）要改契约，留给 ADR | BFF 星图 `src/star-map/nodes-client.ts` | 【LI】:30-33、:389 起；`specs/star-map-v1-h5/lessons.md:6,45,49` | |
| L-10 | 模型路由 `POST /internal/v1/model/route`：适配器调模型的唯一入口，令牌按学科绑定 | 已在文件内，未单独标“待签字”，随文件整体签 | 三科适配器 | 【LI】:179-201 | |
| L-11 | 四种服务令牌（BFF、路由客户端、洞察服务、编排层）的划分与分发轮换 | 契约已定方案；分发与轮换由人定 | 部署 | 【LI】:431-444；`specs/kge-04-mastery-step2/lessons.md:208,247` | |
| L-12 | 版本号：文件已是 1.1.0，代码里的键 `internal_contract("1.0.0")` 代表 `/internal/v1` 一组路径 | 签字时定 1.1.0 是否一次签全（含后追加的三个接口） | 共享库、检查脚本 | 【LI】:4；`specs/kge-04-mastery-step2/lessons.md:191,227` | |

### 3.2 洞察服务 API `insight-service` v0.9（10 个工具，14 项）
文件头写“草稿 v0.9，待评审；W4 与插件 API 一起冻结 v1.0”（【IS】:7）。九个工具对应 SIE 第九章，第十个 `insight.report` 是后来在本草稿内追加的，“签字范围随之扩大”（【IS】:9-11）。10 个工具：`insight.context`（:61）、`claims`（:79）、`explain`（:97）、`dispute`（:115）、`predict`（:133）、`reconcile`（:151）、`record`（:169）、`export`（:187）、`erase`（:206）、`report`（:225）。

| 编号 | 要点 | 当前实现 / 默认建议 | 影响 | 出处 | 表决 |
|---|---|---|---|---|---|
| I-1 | 版本号：pol-#2 放宽 `full_score` 时没有升版本号，后来又追加 `insight.report` | 默认“签字时一并定版本” | 编排层、BFF | 【记录】:38；【IS】:4,10-11 | |
| I-2 | 调用方与受众：`context`、`claims` 只给编排层；`export`、`erase` 只给家长；`report` 只给 BFF；`predict`、`reconcile` 只给编排层 | 契约已写；BFF 代表学生或家长时洞察服务再核对授权（迁移 0044 草稿） | BFF、编排层；迁移 0044 | 【IS】:30-39 | |
| I-3 | 面向学生家长的文字只以带 `claim_id` 的 `RenderedStatement` 出现；自由文本允许清单正好 5 项 | 契约已写，检查脚本断言 | 洞察服务、BFF 信任界面 | 【IS】:15-22 | |
| I-4 | `insight.context` 的字段白名单是否按任务列进契约；`learner_context` 按方法限定字段 | 默认不列，放字段字典；后者随 SIE-01 定 | 编排层、插件 svc-#1 | `specs/service-openapi-v0.9/lessons.md:140`；【记录】:49 | |
| I-5 | `insight.dispute`：请求没有事实与作答引用；`fact_ref` 是内部引用（建议注明“BFF 不得转发”或改不透明句柄）；契约允许编排层但库要求提出人；没有撤回工具 | 只登记，未改契约 | BFF 信任界面、迁移 0032 | `specs/sie-03-dispute-reconcile/lessons.md:157,171-173` | |
| I-6 | `insight.predict`：请求满分 1–300，而响应 `low` / `high` 与 `actual_score` 上限 150，满分超过 150 时回 400；`scope_node_ids` 契约 400、库里 64 | 只登记，未改契约 | 洞察服务、考情库 | 【IS】:611,632-633,661；`specs/sie-03-dispute-reconcile/lessons.md:159` | |
| I-7 | `insight.reconcile`：`exam_record_id` 接受两种 ID；学生版 / 家长版对账卡没有读取工具；预测与对账卡没有 BFF 只读工具（如 `insight.exam_view`） | 待人定：契约新增工具，或给 BFF 读授权（迁移） | BFF（两个 GET 现回 503） | `specs/sie-03-dispute-reconcile/lessons.md:162,225` | |
| I-8 | `insight.record` 的写入路径：给编排层开写入函数，还是从 v1.0 去掉或改只读转发；`BehaviorEvent` 加 payload 与探针事件 | 待人定（迁移、契约） | 编排层、BFF 行为事件 | `specs/sie-02b-claims-first-report/lessons.md:59-60`；`specs/behavior-events-sprint2/lessons.md:47` | |
| I-9 | `insight.export` / `insight.erase`：契约的一次性确认码、异步导出与库里的“发起 → 24 小时确认 → 冷静期 7 天”不一致；洞察服务没有执行权限，缺省回 501 | 待人定：BFF 直接调库函数并把两个工具移出洞察服务，或给洞察服务授权 | BFF、迁移 0035 | `specs/sie-03-dispute-reconcile/lessons.md:167-170`；【IS】:191,210 | |
| I-10 | `insight.report`（第十个工具）：只给 BFF；信封外层溯源字段不得转发前端；首份报告的取用接口 | 已在草稿内；取用接口待定 | BFF 首份报告 | 【IS】:225-237；`specs/sie-02b-claims-first-report/lessons.md:70` | |
| I-11 | 周报工具：新增 `insight.weekly` 或扩展 `insight.claims`（现在 BFF 直读迁移 0036） | 待人定 | BFF 周报 | `specs/trust-ui-sprint5/lessons.md:163` | |
| I-12 | 错误码：服务特有错误码未进枚举；冲突 / 证据不足暂用 409 + `INVALID_REQUEST` | 原定 W4 冻结时一起定 | 插件 API 共享 Problem 枚举 | `specs/service-openapi-v0.9/lessons.md:142`；`specs/sie-03-dispute-reconcile/lessons.md:164` | |
| I-13 | 不计次的前提（父级 usage 引用）；模型供应商是否在契约里限定为境内已备案 | 默认 BFF 按会话限频、不带父级引用；供应商不进契约，由 ADR 04 与路由配置保证 | BFF、规则 13 | `specs/service-openapi-v0.9/lessons.md:143-147` | |
| I-14 | 幂等：契约要求同 key 异体 409，实现是进程内缓存，持久幂等表需迁移 | 只登记 | 洞察服务、迁移 | `specs/sie-03-dispute-reconcile/lessons.md:165-166`；【IS】:45-48 | |

---

## 四、【W13】2.2 节 33 条中不在 61 项里的（31 条）

【W13】:110 标题写“（33）”，第 111-143 行正好 33 条。逐条比对后：
- **完全在 61 项内**（1 条）：第 118 行“待复核、暂不给分的单列表示”= N2。
- **部分在内**（2 条，下表只列不在内的部分）：第 111 行（“61 项签字”本身在内，迁移期与引用链不在）；第 119 行（`UNAUTHENTICATED` = N1、拍照模式错误码 = N9；`FORBIDDEN` 不在）。
- 其余 30 条不在 61 项内。其中涉及 `learning-internal`、`insight-service` 的，已在第三节逐项列出，下表只写“见 L-x / I-x”。

| 编号 | 事项 | 来源里的默认 / 现状 | 出处（【W13】行 → 原始出处） | 表决 |
|---|---|---|---|---|
| 4-1 | 迁移期 W6 是否合适；v1.0 → 服务契约 v0.9 → 插件 v0.9 的引用链（删 v0.9 前服务契约要先改指 v1.0） | 默认 v0.9 保留到冲刺 2 结束（W6）；README 仍写 W6，v0.9 文件仍在 | :111 → 【记录】:51,53-54；`packages/contracts/README.md:42` | |
| 4-2 | `FORBIDDEN` 错误码（403 现借用 `INVALID_REQUEST`） | 未定 | :119 → `specs/kge-03-compile-layer/lessons.md:263` | |
| 4-3 | v0 契约补 `IDEMPOTENCY_KEY_SEALED`；重放接口改 POST | v0 路径封存复用 409 `IDEMPOTENCY_CONFLICT`；见 L-3 | :112 → 【二步】:35-36,41 | |
| 4-4 | 迁移期 5xx 语义 | 见 L-4 | :113 → 【记录】:50 | |
| 4-5 | `learner_context` 按方法限定字段 | 见 I-4 | :114 → 【记录】:49 | |
| 4-6 | 作图语义（`semantic`）的操作表由谁定 | 未定 | :115 → 【记录】:55 | |
| 4-7 | `TeachRequest` 加“作答已提交”信号以放开 4–5 级提示；“第 5 级”内容与规格书不符 | 现一律不给 4 级及以上 | :116 → `specs/adapter-aya-methods-sprint4/lessons.md:84-89` | |
| 4-8 | 提示被扣下的原因码放在哪（`TeachResult.withheld_reason_code` 或 `ReviewReason` 新值） | 未定 | :117 → `specs/adapter-aya-methods-sprint4/lessons.md:111-113` | |
| 4-9 | 模型判分的专门复核原因码；专门的“忙”码；L3 用 `DATA_CLASS_VIOLATION` 还是 `INVALID_REQUEST` | 现借用 `verifier_boundary_case`、`RATE_LIMITED`、`INVALID_REQUEST` | :120 → `specs/adapter-zhixing-sprint2/lessons.md:32-33,64-65` | |
| 4-10 | `DiagnoseRequest` 携带既往逐点得分 | 未加，知行诊断只能给 partial / inconclusive | :121 → `specs/adapter-zhixing-sprint2/lessons.md:30-31` | |
| 4-11 | `skill_summary.trend` 改可选或加 `unknown` | 默认不改，按平台状态序计算 | :125 → `specs/adapter-zhixing-wenli-methods-sprint4/lessons.md:63` | |
| 4-12 | `AttemptInput.evidence_level`（或学习服务转发作答等级）；svc-#4 只给 grade 加了等级，diagnose 的作答没有 | 文砺暂用私有约定，“真实链路上走不通” | :122 → `specs/adapter-wenli-sprint2/lessons.md:39-41,53-55` | |
| 4-13 | `subject_category` pattern 放宽或加 `kge_error_code` | 默认不改契约 | :123 → `specs/adapter-zhixing-wenli-methods-sprint4/lessons.md:55` | |
| 4-14 | 文砺 report 量表逐维统计 | 默认由平台报告引擎直读 `grade_dimension`，本版不做 | :124 → `specs/adapter-zhixing-wenli-methods-sprint4/lessons.md:56-57` | |
| 4-15 | 插件 API 新增只读 `present_item`；诊断内部接口并入 `learning-internal.v1.yaml`；`IngestContext.purpose` 加 `diagnosis`；知识服务 `item.search` 翻页 | 签字前诊断只能用合成题面；诊断作答现按 practice 写 | :126 → `specs/sie-02a-math-diagnosis/lessons.md:84-85,104-106,112-113,126` | |
| 4-16 | grade 摘要加只读溯源字段；道法 `process` 诊断入口；学生年级与内容版本不一致是否拒绝 | 交评审 | :127 → `specs/e2e-integration-sprint5/lessons.md:78-80,85-87`（W13 写的 54-56、61-63 是旧行号） | |
| 4-17 | 按学生查节点的读接口；复测调度契约（现为桩） | 节点查询靠只读 SQL；复测用桩 | :134 → `specs/e2e-integration-sprint5/lessons.md:81-84`（W13 写的 57-60 是旧行号） | |
| 4-18 | `POST /internal/v1/plan` 形状 | 见 L-5 | :128 → `specs/unified-shell-sprint4/lessons.md:231` | |
| 4-19 | 知识服务：`PageGetResponse.atom_refs` 补类型；`SourceVersion` 新增 `pages`；`page_id` 映射写入描述；旧版页面的 `source_version` | 现借用 `graph:kge-pages@…` | :129 → `specs/kge-03-compile-layer/lessons.md:262-265,272` | |
| 4-20 | 知识服务 MCP outputSchema（成功与错误合成 oneOf） | 现未声明 | :130 → `specs/kge-03-compile-layer/lessons.md:104` | |
| 4-21 | 服务契约：采分点在契约与迁移之间的对应（`subject_dimension`、`keyword_family`、`partial_credit_rule`） | 默认以契约为准，导入时收紧迁移 | :135 → `specs/service-openapi-v0.9/lessons.md:149-152`；`specs/kge-03-compile-layer/lessons.md:268` | |
| 4-22 | 服务契约：`mastery.*` 只给编排层 | 未定是否写成强制说明 | :139 → `specs/service-openapi-v0.9/lessons.md:139` | |
| 4-23 | 洞察服务：`insight.record` 写入路径；首份报告取用接口 | 见 I-8、I-10 | :131 → `specs/sie-02b-claims-first-report/lessons.md:59-60,70` | |
| 4-24 | 洞察服务：预测与对账卡给 BFF 的只读工具 | 见 I-7 | :132 → `specs/sie-03-dispute-reconcile/lessons.md:225-228` | |
| 4-25 | 洞察服务：预测区间上限 150 与 `full_score` 1–300 不一致 | 见 I-6（W13 引的【IS】566、587-588、616 现为 611、632-633、661） | :133 | |
| 4-26 | 洞察服务：dispute、export、erase、reconcile 的一组差异 | 见 I-5、I-7、I-9 | :136 → `specs/sie-03-dispute-reconcile/lessons.md:157-173` | |
| 4-27 | 洞察服务：周报工具 | 见 I-11 | :137 → `specs/trust-ui-sprint5/lessons.md:163` | |
| 4-28 | 洞察服务：“为什么”的 BFF 读取路由与响应形状（写进 BFF 的 `trust.v0.yaml`，不是洞察服务契约） | 前端已提议形状，待 BFF 定稿 | :138 → `specs/trust-ui-sprint5/lessons.md:40-50` | |
| 4-29 | 服务契约：服务特有错误码；不计次与父级 usage；供应商枚举 | 见 I-12、I-13 | :139 → `specs/service-openapi-v0.9/lessons.md:142-147` | |
| 4-30 | `insight-service` 放宽 `full_score` 未升版本号；`BehaviorEvent` 加 payload | 见 I-1、I-8 | :140-141 | |
| 4-31 | 审核台接口 `review-console.v0.yaml` 是否进契约；点点结构字段 `referral` / `handoff` / `root_causes` 的正式字段名；英语 3-D 登记事项（L3 示例、`transcript.v0`、开放题 verdict）是否仍评审 | 未补进清单，“由契约负责人决定” | :142-143 → `specs/review-queue-sprint2/lessons.md:52`；`specs/release-gate-sprint4/lessons.md:72`；`specs/diandian-cases-v0-sprint1/lessons.md:38`；`specs/adapter-echo-sprint3/lessons.md:33-39` | |

说明：表内 31 行覆盖 32 条（33 条减去完全在内的第 118 行）：【W13】:140、:141 合为 4-30，:142、:143 合为 4-31；:139 拆为 4-22 与 4-29；:135 的 `insight.context` 白名单部分见 I-4。

---

## 五、会后行动清单

| # | 动作 | 依据 | 负责人（会上填） | 期限 |
|---|---|---|---|---|
| 1 | 把每项结论、签字人、日期记到【清单】每项之后 | 【清单】:119 | | |
| 2 | 按结论改契约、反例、变异测试、一致性测试，同步改阿鸦与学习服务；另开 PR，首行 `[NEEDS-HUMAN]` | 【清单】:120 | | |
| 3 | 收口候选开关：从共享库与三科适配器的候选版本列表里移除 1.0.0 | `contract.py:28`；三科 `src/contract.ts` | | |
| 4 | 默认版本切到 1.0.0：共享库缺省值、三科 `DEFAULT_CONTRACT_VERSION`、阿鸦能力声明 | `contract.py:23`；`specs/plugin-api-v1.0-step2/spec.md:50` | | |
| 5 | 在干净工作区重生 Gate 0 样例，PR 里说明差异 | `specs/e2e-integration-sprint5/lessons.md:90`；【W13】:402 | | |
| 6 | 另开小任务：BFF 写入切到 `/internal/v1` | 【二步】:33 | | |
| 7 | 定迁移期终点；删除 v0.9 前先把两份服务契约改指 v1.0 | 【记录】:51,53-54 | | |
| 8 | 定 `insight-service` 与 `learning-internal` 的签字版本号 | 【记录】:38；`specs/kge-04-mastery-step2/lessons.md:227` | | |
| 9 | 第四节未表决的条目：定处理人与时间 | 本表第四节 | | |
| 10 | 部署前提（不属契约，但与签字后上线相关）：迁移 0016 先于学习服务上线 | 【二步】:31-32；【W13】:36 的 B24 | | |

---

## 六、签字栏

| 契约 / 范围 | 角色 | 姓名 | 结论（全部同意 / 按修改意见 / 否决项编号） | 日期 | 签字 |
|---|---|---|---|---|---|
| 插件 API v1.0（61 项） | 技术负责人 | | | | |
| 插件 API v1.0（61 项） | 数学（阿鸦）负责人 | | | | |
| 插件 API v1.0（61 项） | 道法（知行）负责人 | | | | |
| 插件 API v1.0（61 项） | 语文（文砺）负责人 | | | | |
| 插件 API v1.0（61 项） | 英语负责人（英语已搁置，是否到会由组织者定） | | | | |
| 插件 API v1.0（61 项） | 教研代表 | | | | |
| learning-internal 1.1.0 | 技术负责人 | | | | |
| insight-service v0.9 | 技术负责人 | | | | |
| insight-service v0.9 | （仓库未写明，会上定） | | | | |

记录人：　　　　　　会议日期：　　　　　　
