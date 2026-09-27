# SIE-00：画像字段字典、判断账本 schema 与状态机、禁用推断词表

状态：**已确认**（2026-09-27，用户：“按默认建议，开始实现 SIE-00”；待人确认 #1–#8 按默认做法）。实现记录见 `lessons.md`。
任务包：SIE-00（开发计划 v2.0 冲刺 0 · W2；SIE V1.1 第十一章 S0）

关联：
- **SIE V1.1**：
  - 第十一章 S0（W1–2）：“SIE-00：字段字典（三圈九组、来源类型、可见范围）；判断账本与状态机 Schema；禁用推断词表”，验收“Schema 与字段字典冻结”；
  - 第二章七条设计原则：判断先于展示、来源标注、证据分级、会过期、只写学习事实、写入与读取分离、最少必要；
  - 第三章：三圈九组、六维画像的位置、判断账本字段；
  - 第四章：初中采集清单（必填 / 建议 / 可选 / 自动）与三阶段建档；
  - 第五章：证据权重、中圈指标的定义、最低样本与效度检验、判断生命周期（图 2）；
  - 第八章：学生、家长、Agent、教研各自看到什么；
  - 第九章：insight schema 数据模型增量与接口；
  - 第十章：不推断清单、授权与用途；
  - 风险表：“画像字段膨胀 → 字段字典准入：新增字段须说明它改变哪个教学决策”，“Claude Code 实现偏离规则 → 禁用推断与无引用陈述做成自动化测试；账本写入只开放给洞察服务”。
- **开发计划 v2.0**：
  - 冲刺 0 W2：“Schema v0.9：platform.*、kge.*、insight.* 三个 schema 的 ERD 与迁移草稿；SIE 字段字典（三圈九组、来源类型、可见范围）；判断状态机”；
  - ADR 11（原 v2.0 编号，本仓库定为 13）“判断账本唯一写入方为洞察服务”；
  - Gate 0 硬条件“画像底线”：可见陈述 100% 引用判断；禁用推断 0；C 级识别内容不生成结论；删除覆盖全表。
- **完整方案 v1.1**：4.3 三圈模型、4.4 证据与判断规则（判断的五种状态）、4.5 信任的产品化、5.5（各科结论性表述不迁移，只存档）、第十一章不推断清单与用途分项授权。
- **仓库现状与决定**：
  - `specs/plan-v2-alignment/spec.md` 已确认的默认做法：#3 个人层已有表留在 `platform.*`，`insight.*` 只放新增对象；#6 用途分项授权表等合规方案；#7 核对 `platform.guardian_report` 与 SIE 同名表；#2 新增 ADR 编号为 11–14。
  - 已有表：`platform.student_profile`、`consent_record`（0002）；`attempt.self_report`（0004，作答把握度列）；`mastery_estimate`、`skill_observation`、`error_attribution`（0005）；`guardian_report`（0006，按主题的家长自由文本）。
  - KGE-00 结论字段清单（`specs/kge-00-asset-inventory/report/conclusion-fields.md`）：三科旧结论共 27 个结构；其中 8 个字段或取值触及不推断清单或同伴比较（文砺 `student_seg_t` 四类分型与 `profiles.segment`、诊断的 `student_label` 与 `rank_range`、阿鸦 `abilityBand`）。
- **CLAUDE.md**：规则 2、5、6、8、11、12、13；必停项“db/migrations、RLS 与权限”“判断模板、禁用推断词表、画像可见范围”。

## 目标
把“平台可以知道学生什么、从哪来、谁能看、凭什么下结论”写成一份可校验的字段字典和一个能强制执行规则的判断账本，让 W2 评审能冻结它们。之后的 SIE-01（建档与事实层）、SIE-02（首份画像）、SIE-03（异议与对账）都按这两份东西实现，不再各自发明字段和状态。

## 设计要点

### 1. 字段字典：一份机器可校验的 YAML
- 文件：`docs/design/sie/field-dictionary.v0.9.yaml`，人读版 `docs/design/sie/field-dictionary.md`（由脚本生成，不手改）。
- 覆盖 SIE 第三章三圈九组、第四章采集清单、第五章中圈指标、第九章 insight 数据模型里出现的**每一个**画像字段。每个字段写：

| 键 | 含义 | 取值 / 规则 |
|---|---|---|
| `id` | 字段编号 | `圈.组.名`，如 `outer.g1.weekly_hours` |
| `circle` / `group` | 三圈与九组 | inner / middle / outer / through；g1–g9（对应原九层 ①–⑨） |
| `layer` | 三层数据 | fact / evidence / inference（SIE 第二章“事实、证据、推断分存”） |
| `source_type` | 来源 | objective / self / guardian / inferred（可多选；多来源时各自单独存） |
| `collect` | 采集级别与时机 | required / suggested / optional / automatic；onboarding / periodic / continuous |
| `visibility` | 默认可见范围 | agent / student / guardian 的子集；外圈不含 student；中圈指标在效度检验通过前只有 agent |
| `storage` | 落库位置 | 已有表写 `platform.表.列`；新增写 `insight.表.列` 并注明由哪个任务包建（SIE-01/02/03） |
| `teaching_decision` | 它改变哪个教学决策 | 必填，非空；写不出来的字段不准入（SIE 风险表） |
| `guardian_deletable` | 家长能否删除 | 外圈与自述字段必须为真 |
| `metric` | 中圈指标才有 | 可复算定义、最低样本、效度检验方法、`validated: false` |
| `status` | 首期状态 | live / silent（静默采集）/ planned |
| `legacy_sources` | 旧产品对应字段 | 来自 KGE-00 结论字段清单；只作追溯，不迁移数值 |

- **首发对外维度**：只有数学“知识掌握”“知识迁移”两维可以对学生和家长可见（SIE 需要拍板的第 2 项，默认采纳）；其余维度 `status: silent`，`visibility: [agent]`。
- **最少必要**：真实姓名、精确学校与班级、住址不进字典（SIE 第二章）。`student_profile` 已有的 `birth_year_month` 标为“合规用途（未满 14 周岁判断），不参与任何推断”。
- **不再采集的旧字段**：KGE-00 列出的 8 个触及规则的字段或取值单独列在 `retired:` 段，写明原因。检查脚本保证它们不会以任何名字回到字典。

### 2. 字段字典的检查脚本
- `docs/design/sie/check_field_dictionary.py`，接入 `make sie-dictionary-check` 并加入 `check-fast` 与 `check`（仿照 `taxonomy-check`）。检查：
  1. 结构与取值合法；`id` 唯一；每个组至少一个字段，九组都有；
  2. `teaching_decision` 非空；
  3. 外圈 `visibility` 不含 student；外圈与自述字段 `guardian_deletable: true`；
  4. 中圈指标 `validated: false` 时 `visibility` 只能是 `[agent]`；`metric` 的定义、最低样本、效度检验三项齐全；
  5. `source_type: inferred` 的字段必须落在推断层，且只能通过判断账本对外（`storage` 指向 `insight.claim` 或 KGE 的掌握表）；
  6. `storage` 指向的已有表和列，必须在 `db/migrations` 里真实存在（文本解析，不连库）；
  7. 字段的 `id`、名称、说明与 `retired:` 段对照禁用推断词表（设计要点 5）：命中即失败；
  8. 生成的人读版与 YAML 一致。
- 反例测试：逐项改坏一份夹具，每一项检查都必须报错。

### 3. 判断账本 schema（迁移草稿，必停项）
- 新迁移 `db/migrations/0010_insight__claim_ledger.{up,down}.sql`。编号 0010 原先是冲刺 4 spec 预留的，那几份 spec 已标为“按 v2.0 待重审、迁移编号作废待重排”。
- 新 schema `insight`，按对照文档 #3 只放新增对象。本迁移只建判断账本，SIE 第九章的其他表（exam_record、behavior_event、self_report、confidence_probe、learner_metric、exam_prediction、teaching_response、dispute、intervention_log、profile_snapshot、purpose_grant）留给 SIE-01/02/03，字段先写进字典。
- **`insight.claim_template`**：判断模板登记表，属于必停项，由人审核后才能登记。
  - 字段：`template_key`、版本、`target_type`、允许的可见范围上限、结论文本模板、审核人、审核时间、状态。
  - 判断只能引用已登记、已审核的模板，这样“你不擅长数学”这类句子没有模板可用，在账本层就写不进去。
- **`insight.claim`**：字段按 SIE 第九章：

  ```
  id, student_id, subject, target_type (node | skill | metric | exam | preference), target_id,
  template_key + template_version, value jsonb, confidence, evidence_ids uuid[],
  source_type (inferred | self | guardian | objective), model_version, status, visibility text[],
  created_at, expires_at, updated_at
  ```

  约束：
  - `source_type = inferred` 时 `evidence_ids` 非空、`model_version` 非空（规则 6）；
  - `expires_at` 必填（“会过期”），并且晚于 `created_at`；
  - `visibility` 不能超出模板允许的上限；
  - `target_type = node` 时，在 KGE-01 之前 `target_id` 指向 `platform.skill_node`（`subject + local_id`），KGE-01 之后按 ADR“星图统一定义”改指 knowledge_node。
- **`insight.claim_history`**：
  - 只能追加：禁止 UPDATE 与 DELETE，家长删除请求的级联删除除外；
  - 每次状态变化由触发器自动写一行：原状态、新状态、原因、证据 ID、时间。
- **状态机**（SIE 图 2 与完整方案 4.4），由触发器强制，非法流转直接报错。
  - 状态：hypothesis 假设 / established 成立 / to_verify 待验证 / refuted 推翻 / withdrawn 撤回。
  - 允许的流转：

    | 从 | 到 | 条件 |
    |---|---|---|
    | 新建 | hypothesis | 证据不足 |
    | 新建 | established | 证据量与置信度达标 |
    | hypothesis | established | 证据量与置信度达标 |
    | hypothesis | to_verify | 到期、异议或考试偏差 |
    | established | to_verify | 到期、异议或考试偏差 |
    | to_verify | established | 验证题支持原判断 |
    | to_verify | refuted | 验证不支持 |
    | 非终态 | withdrawn | 依据的事实被更正 |

  - refuted 与 withdrawn 是终态。推翻与撤回的判断留在账本里用于评估与审计，不对学生展示（SIE 图 2 注）。
  - “阈值达标”“到期”这些条件由洞察服务判断，数据库只管流转是否合法和留痕。
- **写入与读取分离**（ADR 13 草稿）：
  - 新数据库角色 `starbuddy_insight`，是唯一能写 `claim` 与 `claim_history` 的角色；
  - `starbuddy_learning`（学科服务）与 `starbuddy_bff` 都只能读，并且只能经下面的视图读；
  - 角色写进 `db/bootstrap/roles.sql`，属于必停项。
- **RLS 与展示视图**：
  - `claim` 启用 RLS，学生与家长的读取沿用 `can_read_student`（家长必须有有效的 guardian_student_link，规则 5）；
  - 学生视图只返回 `visibility` 含 student、状态为 hypothesis / established / to_verify 的判断；to_verify 在星图上画虚线，不作为陈述；
  - 家长视图同理，按 guardian 过滤；
  - Agent 视图可以看全部非终态判断，但仍按学生隔离；
  - 推翻、撤回的判断任何前端视图都看不到。
- **删除**：学生档案删除时级联删除 `claim`、`claim_history`（Gate 0“删除覆盖全表”的一部分）。
- 迁移测试沿用 `db/tests/`：
  - up → down → up；
  - 每条合法流转和每条非法流转；
  - 历史只能追加；
  - 非洞察服务角色不能写；
  - 学生、家长、跨档案的可见性；
  - 未登记或未审核的模板不能用；
  - 可见范围超出模板上限时拒绝；
  - 推断类判断缺证据时拒绝；
  - 级联删除；
  - 快照测试（`db/tests/snapshot/privileges.sql`）同步更新；
  - 变异测试加上本迁移的关键约束。

### 4. 判断模板：只登记“形状”，不写文案
- SIE-00 只定模板登记表的结构和审核流程，**不登记任何对学生可见的模板文案**（文案属于必停项，由产品与教研写、人审核）。
- 测试夹具里用合成模板（`test.*` 前缀，仅 agent 可见），验证机制可用。
- 首批模板（数学“知识掌握”“知识迁移”两维）放到 SIE-02 由人定稿。

### 5. 禁用推断词表草案（必停项，人定稿）
- 文件：`docs/design/sie/forbidden-inference.v0.yaml`，状态写“草稿，待产品、教研与合规签字”。
- 分类按 SIE 第十章与 CLAUDE.md 规则 12：
  - 智力或天赋；
  - 人格与性格；
  - 心理健康状态或诊断；
  - 持续的情绪状态；
  - 家庭经济与关系；
  - 基于摄像头、面部、眼动、声纹的推断；
  - 群体推给个人；
  - 层级标签；
  - 固定学习风格；
  - 定性否定（如“你不擅长数学”）。
- 每类写三样东西：
  - 中文与英文的词或模式；
  - 禁止的示例句；
  - 容易误伤、应当允许的示例句，如“这道题审题时漏看了限定词”“你说最近有点累”（学生本人说出的感受只记录、不复述、不推断）。
- 用在三个地方：
  1. 字段字典检查（设计要点 2 第 7 项）；
  2. 判断模板登记前的检查：`claim_template` 登记脚本调用它；
  3. 之后 SIE-03 的“禁用推断”自动化测试与档案页的“陈述—判断一致性检查”，本任务只提供词表和一个可复用的匹配函数。
- 匹配函数与它的正反例测试放在 `docs/design/sie/`，与字典检查同一个脚本包。

### 6. 与已有表、旧结论的对照
写进 `docs/design/sie/field-dictionary.md` 的“对照”一节：
- `platform.guardian_report`：
  - 现状：按 5 个主题的家长自由文本；
  - SIE：`guardian_report(field, value jsonb, superseded_at)`，结构化字段、可更替；
  - 默认：保留现表作为“家长观察原文”，结构化字段在 SIE-01 新建，两者都标 `source_type: guardian`。改不改现表由 SIE-01 决定（对照文档 #7）。
- `platform.attempt.self_report`：
  - 现状：作答时的把握度（confident / unsure / guessed）；
  - SIE：`confidence_probe` 是做题**前**的“能做对吗”。时机不同，不合并；
  - 字典里分两个字段，前者标 `status: live`，后者 `planned`（SIE-01）。
- `platform.mastery_estimate` / `skill_observation` / `error_attribution`：KGE 计算的推断，字典里标 `layer: inference`。对学生可见的陈述不能直接读这些表，只能读引用了它们的判断（规则 11；过渡期已合并的星图按原样运行）。
- `platform.consent_record`：同意记录；用途分项授权 `purpose_grant` 等合规方案（对照文档 #6），字典里标 `planned`。
- KGE-00 的 27 个旧结论结构：全部进 `legacy_sources` 追溯，数值不迁移；触及规则的 8 个进 `retired:`。

### 7. ERD 与 ADR
- `docs/adr/erd.md` 增加 insight 一节，画出 `claim_template`、`claim`、`claim_history` 与 `platform.student_profile`、`skill_node` 的关系。
- 起草 `docs/adr/13-claim-ledger-single-writer.md`，状态为“草稿，待签字”：判断账本唯一写入方为洞察服务，内容包括数据库角色、写入与读取分离、模型版本可回放。ADR 索引同步。

## 范围
### 做
- 字段字典 YAML 与生成的人读版；检查脚本与反例测试；`make sie-dictionary-check`（加入 `check-fast` 与 `check`）。
- 禁用推断词表草案与匹配函数（带正反例测试）。
- 迁移 `0010_insight__claim_ledger`：`insight` schema、`claim_template`、`claim`、`claim_history`、状态机触发器、RLS、展示视图、`starbuddy_insight` 角色、授权；迁移测试、快照与变异测试。
- ERD 增补；ADR 13 草稿与索引。
- `specs/sie-00-field-dictionary/lessons.md`。

### 不做
- SIE 第九章其余表（exam_record、behavior_event、self_report、confidence_probe、learner_metric、exam_prediction、teaching_response、dispute、intervention_log、profile_snapshot、purpose_grant）：只进字典，建表在 SIE-01/02/03。
- 洞察服务本身的代码与 `insight.*` OpenAPI（对照文档第五节第 4 项，另起任务）；判断的计算逻辑、阈值与到期时长。
- 任何对学生或家长可见的模板文案；词表的最终版本。
- 改 `platform.*` 已有表；迁移已有数据；导入任何旧结论。
- 不在任何共享环境执行迁移（人执行）。

## 验收标准（可测试）
- [ ] 字段字典覆盖 SIE 第三章九组、第四章采集清单每一项、第五章八个中圈指标、第九章 insight 模型的每个画像字段；覆盖对照表附在人读版末尾，逐项写字典 `id`。
- [ ] `make sie-dictionary-check` 通过，并已加入 `check-fast`、`check` 与 CI；设计要点 2 的 8 项检查各有至少一个反例被拦下。
- [ ] 字典里对学生或家长可见的字段只有数学“知识掌握”“知识迁移”两维相关字段，以及学生或家长自己填写的外圈与自述字段；中圈指标全部 `visibility: [agent]`、`validated: false`。
- [ ] KGE-00 的 8 个触及规则的旧字段或取值全部在 `retired:` 段；检查脚本能拦住它们以原名或同义名重新进入字典。
- [ ] 禁用推断词表覆盖 CLAUDE.md 规则 12 与 SIE 第十章的每一类；匹配函数对每类的禁止示例全部命中、对允许示例全部不命中。
- [ ] 迁移在本地临时库 up → down → up 通过；只用标准 Postgres 16 能力。
- [ ] 状态机：表中每条合法流转成功并自动写一行历史；其余流转（含从终态出发）全部报错；历史不能 UPDATE 或 DELETE。
- [ ] 只有 `starbuddy_insight` 能写 `claim`、`claim_history`；学科服务、BFF、学生、家长写入全部被拒。
- [ ] 可见性：
  - 学生只能看到自己的、`visibility` 含 student 的、非终态判断；
  - 家长只能经有效 link 看到含 guardian 的判断，同手机号不能跨档案；
  - 推翻与撤回的判断任何前端视图都看不到。
- [ ] 未登记或未审核的模板、超出模板可见范围上限、推断类判断缺证据或模型版本、缺 `expires_at`，都被约束拒绝。
- [ ] 删除学生档案时，其 claim 与 claim_history 全部删除。
- [ ] 权限快照与变异测试更新，`make check` 严格模式通过。

## 涉及的必停项（见 CLAUDE.md）
- **db/migrations、RLS 与权限策略、数据库角色**：是（迁移 0010、`db/bootstrap/roles.sql`、快照）。
- **判断模板、禁用推断词表、画像可见范围**：是（模板登记表结构、词表草案、字典的默认可见范围）。三者都以草稿形式提交，由人签字定稿。
- 契约 / 计量 / 人设 / 第三方与出境：否。
- PR 首行写 `[NEEDS-HUMAN]`。

## 依赖与输入
- SIE V1.1、完整方案 v1.1、开发计划 v2.0（`docs/plan/`）。
- KGE-00 结论字段清单（已提交）。
- 对照文档已确认的默认做法 #2、#3、#6、#7。
- 不依赖 KGE-01：节点类判断在 KGE-01 前先指向 `platform.skill_node`。

## 待人确认
1. **状态流转细节**：
   - SIE 图 2 没有画“假设 → 推翻”。默认不允许：假设遇到反证先转待验证，再由验证题决定。要不要允许直接推翻？
   - 考试偏差时，“成立 → 待验证”，不直接推翻（SIE 第六章冲突处理）。默认按此。
2. **到期时长**：
   - schema 只要求 `expires_at` 必填；每类判断的默认有效期（例如节点判断多少天）由产品与教研定，写进洞察服务配置，不写死在迁移里。
   - 需要在 SIE-02 前给出首批数值。
3. **`insight` schema 与新角色**：
   - 按对照文档 #3 的默认做法，判断账本放 `insight.*`，新增角色 `starbuddy_insight`；
   - 洞察服务的代码放哪（`services/learning` 内的独立模块，还是新服务）待 ADR 决定；
   - 本迁移只建角色，不决定服务位置。
4. **禁用推断词表的定稿人**：产品负责人、教研与外部合规顾问谁签？英文词是否一并管？
5. **首发可见维度**：默认只开数学“知识掌握”“知识迁移”（SIE 拍板第 2 项）。是否确认？
6. **`platform.guardian_report` 的去留**：默认保留作家长观察原文，结构化字段在 SIE-01 另建；也可以在 SIE-01 改造现表。
7. **学校信息**：字典默认只收“地区”，不收学校与班级。外部考试的“排名区间”只作为考试记录的一部分存（SIE 第三章），不对学生展示同伴比较。是否确认？
8. **迁移编号**：本任务用 0010。冲刺 4 三份 spec 原先预留的 0010–0012 在它们按 v2.0 改写时重排。
