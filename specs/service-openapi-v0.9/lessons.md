# 知识服务与洞察服务 OpenAPI v0.9 实现记录

2026-09-28。用户确认“按默认建议，开始实现”，spec 待人确认 #1–#11 全部按默认执行：
1. 只有平台编排层与学习服务调用 `evidence.write`；
2. 适配器不直接调用两个核心；
3. 服务的代码位置另行决定；
4. `page.get` 先列入，编译层建成前返回 501；
5. `to_verify` 以虚线返回；
6. 导出与删除只定接口；
7. `node.resolve` v0.9 只收文本；
8. 三份契约 W4 一起冻结；
9. 去向引擎与政策库的接口不在 v0.9；
10. `item.match` 不加；
11. 证据等级写进 `evidence.write`。

## 交付
- `packages/contracts/openapi/knowledge-service.v0.9.yaml`：
  - 10 个工具，各一对请求与响应示例；
  - 公共类型 `NodeId`、`MapVersion`、节点类型、层级、范围、认知层次、边类型；
  - 两种上下文：`PublicContext` 不带学生标识；`StudentContext` / `WriteContext` 带学生标识，写入要求幂等键；
  - 统一信封 `ServiceResultBase`。
- `packages/contracts/openapi/insight-service.v0.9.yaml`：
  - 9 个工具，各一对示例；
  - `RenderedStatement`：唯一的自由文本，必须带 `claim_id`；待验证只能画虚线，假设与成立画实线；
  - `ContextEntry`：推断类条目必须带 `claim_id`，取值只能是编码；
  - 事件来源由事件类型决定；
  - 预测只给 80% 区间，不给概率；对账卡用编码表达偏差方向与调整。
- `packages/contracts/scripts/check_services.py`：
  - 检查项：
    - 合法性，跨文件 `$ref`；
    - 工具清单逐一对应；
    - 信封与版本；
    - 写入类工具的幂等键；
    - 公共层不带个人层字段名；
    - 洞察服务没有名次、百分位、概率类字段与取值；
    - 两份契约都没有 `usage_id`；
    - 无约束文本扫描（带允许清单）；
    - 证据等级只有 A、B；
    - 示例校验与证据引用闭合、字段 ID 在字段字典里、示例无 PII；
    - 描述、枚举与示例过禁用推断词表。
  - 由 `check_contract.py` 在默认运行时一起调用，`make contract-test` 的输出多一段服务契约统计。
- `packages/contracts/tests/service_counterexamples.py`：31 个反例，全部被拒绝。
- `packages/contracts/tests/test_mutations.py`：新增 37 个服务契约变异，共 83 个，全部被发现。另外逐条核对过每个变异是被预期的那一项检查发现的，不是被 OpenAPI 合法性等别的检查顺带拦下（见下节第 5 条）。
- `docs/design/kge/check_node_id.py`：新增一处比对，知识服务契约 `NodeId` 的 pattern 与迁移 0011 逐字一致；自检反例增加一个。
- `packages/contracts/README.md`：文件表与“两份服务契约的要点”。
- `specs/contract-conformance-sprint1/v1.0-review.md`：第六节 svc-#1–#5，登记与插件 API 的衔接；插件 API 本身不改。

## 与 spec 的偏差（按实现取舍，需要时由人改回）
1. **无约束文本扫描的允许清单比 spec 宽**：
   - spec 写“允许的例外只有 `RenderedStatement.text` 与 `node.resolve` 请求里的学生输入文本”；
   - 但公共层工具返回的节点名称、包含与不包含、教材册与章、原句、题位、采分点关键词、页面正文，本来就是教研审核过的公共知识内容，只能是文字；
   - 实现把这些登记进知识服务的允许清单，都要求限长；
   - 洞察服务的允许清单只有 `RenderedStatement.text`，与 spec 一致；
   - 扫描范围与插件 API 一样只看响应，所以 `node.resolve` 的请求文本不需要登记。
2. **个人层字段名检查只看字段名，不看枚举取值**：共享的 `ReviewReason` 枚举里有 `student_appeal`，公共层响应的信封也引用它。它只是复核原因的编码，不是学生数据，所以公共层检查只扫字段名。名次与概率类检查同时扫字段名和枚举取值。
3. **异议类型改名**：
   - SIE 第八章的“那次是粗心”在契约里写成 `one_off_slip`，含义是学生本人说那次只是失误，平台记录并观察，不改判；
   - 用 `careless` 会命中禁用推断词表（英文 careless 被归为“把粗心当特质”），而契约的描述、枚举都要过词表检查；
   - SIE-02 建 `dispute` 表时沿用 `one_off_slip`。
4. **导出也算写入类**：`insight.export` 会生成一次导出任务，也要求幂等键，避免家长重复点击生成多份导出。spec 的写入清单里没有它。
5. **变异测试的一个陷阱**：
   - “删掉 allOf 里的一条”在只剩一条时会留下空 `allOf`；空 `allOf` 本身不合法，会被 OpenAPI 校验先拦下，看起来“被发现”，其实没有证明对应的反例有效；
   - 服务契约的这类变异改为删空时连 `allOf` 一起删；
   - 逐条核对后，补了一个知识服务的反例（需要复核却不给原因）。
6. **变异测试变慢**：从约 15 秒变为约 50 秒。每个服务变异都要重跑两份契约的 OpenAPI 校验与全部示例。`make check` 仍可接受，`check-fast` 不跑变异。

## 安全审查与处理（2026-09-28）
按默认建议修复，阻断项全部处理：
- **B1 受众与调用方没有绑定、洞察服务不知道代表谁**：
  - 洞察服务上下文加 `actor`：角色（学生 / 家长）、当前账号 ID、家长访问时的 `guardian_student_link` ID；
  - 绑定规则：`caller = bff` 时受众只能是学生或家长，`actor.role` 与受众一致，家长必须带 `link_id`；`caller = orchestrator` 时受众只能是 Agent，不带 `actor`；
  - `insight.context`、`insight.claims` 只给编排层；
  - 描述写明：`caller` 由服务从令牌取，与请求体不一致返回 403。
- **B2 按对象 ID 取数不核对归属**：`insight.explain`、`insight.dispute`、`insight.reconcile` 的对象 ID 描述写明必须属于 `student_ref`，不属于时与“对象不存在”响应完全相同。越权用例进 SIE-01 的一致性测试。
- **B3 导出与删除**：
  - 只给家长（`caller = bff`、`audience = guardian`）；
  - 确认码的规则写进描述：绑定家长、学生与动作三者，一次性，10 分钟内有效，连续 5 次错误锁定；
  - 删除回执的范围至少一项；
  - 导出完成时必须给下载凭据与失效时间；下载凭据改为不透明的 `dl_` 随机值，不含学生标识，下载经 BFF 重新鉴权；
  - 同一幂等键重放导出，返回当前状态。
- **B4 来源冒充**：学生自述与“那次只是失误”只能由学生本人提交，家长观察只能由家长提交，行为事件只能由编排层记录。
- **B5 `node.resolve`**：只给编排层，数据级别固定 L2；描述写明只用境内已备案模型、服务不记录输入文本。
- **建议项**：
  - S1：公共层 7 个响应的 `evidence_ids` 必须为空；
  - S2：取值编码收紧为小写编码，描述写明服务按字段字典校验、未登记返回 422；
  - S3：照片、语音或带原始素材引用的证据，数据级别必须是 L3；
  - S4、S5、S7：两份契约的描述写明错误不泄露存在性、幂等键作用域是“调用方 + 工具 + 学生”、日志不记请求体；
  - S6：检查脚本加上下文字段与调用方类别的快照比对、更宽的个人层字段词表、请求侧无约束文本扫描（只放行学生原文两处）、禁止内联二进制；幂等键加 pattern。
- **数量**：新增 23 个反例（共 54 个）与 23 个变异（服务契约共 60 个），逐条核对都是被对应规则发现的。

没有照做的：
- S3 另提到插件 API 的 `signal_code` 可以写 `voiceprint_*` 一类前缀。这是插件 API 的问题，登记到 v1.0 冻结时处理，本次不改插件 API。

## 契约审查与处理（2026-09-28）
契约审查针对修复安全问题之前的提交；其中“删除与导出可由编排层发起”（必须修 4）与大部分“来源可以伪造”（必须修 3）已随安全修复解决。其余按默认处理：
- **必须修 1，“唯一自由文本”可以换写法绕过**：检查脚本改为：
  - pattern 必须拒绝含空格与中文的探针文本；
  - format 只允许 uuid、date、date-time、uri-reference；
  - 没有类型的 `{}`、没有字段清单的对象、`unevaluatedProperties: true`、`prefixItems` 一律报错。
- **必须修 2，陈述的展示方式**：`display` 本已必填；检查脚本的必填核对扩大到展示方式、模板键与版本、置信度说明；补“待验证不带 display”“成立画虚线”两个反例。
- **必须修 3，来源**：判断摘要加 `source_type`（必填）与 `model_version`（推断类必填）。
- **必须修 5，考试预测的可见范围**：
  - `insight.predict`、`insight.reconcile` v0.9 只给编排层（Agent 受众），与字段字典 `inner.g2.exam_prediction` 只给 Agent、v1.0-review sie-#2 一致；
  - 对账卡上线（SIE-03）时由人放开；
  - 检查脚本新增一项：示例条目的来源与受众必须符合字段字典登记。
- **必须修 6，证据等级可以绕过**：
  - 加五条约束：OCR 转写只能来自拍照；ASR 转写只能来自语音；拍照必须匹配到题库题（外部题最高 C 级，不写入）；画布与语音的 A 级必须经学生确认（新增 `confirmed`）；未准入内容上的作答不能是 A 级；
  - 所有叫 `evidence_level` 的属性都要求只有 A、B，不只看共享定义。
- **必须修 7，词表检查有盲区**：属性名、`const`、`default`、schema 的 `examples` 一起过词表；编码里的 `_ . -` 换成空格再查。只改检查脚本，没有改词表。
- **必须修 8，名次与概率扫描覆盖不全**：
  - 正则加上 likelihood、chance、odds、admission_chance / prob / rate、peer、cohort、average、beat、exceed、top_pct、同伴、平均、超过；
  - 两份契约都扫。
- **必须修 9，衔接登记**：
  - svc-#5 改为“编排层取采分点放进 grade 请求”；
  - 新增 svc-#6（知识服务工具怎么进插件请求）、svc-#7（插件 `point_id` 大小写不一致）、svc-#8（预测与 sie-#2）、svc-#9（`signal_code` 能写声纹前缀）。
- **建议项**：
  - `node.resolve` 的描述补“不回流公共层或编译层、复核队列不带原文”；
  - `insight.context` 已只给编排层；
  - `rubric.get` 结果加 `map_version`，示例的 `source_version` 带地图版本段；
  - README 补“平台侧校验”两条：响应的 Rubric 版本等于请求；预测 `low ≤ high ≤ 满分`；
  - 陈述加 `confidence_band`；
  - `insight.reconcile` 定为只读，调整由录入考试记录的任务完成；
  - 变异测试要求至少有一条报错来自结构化检查，而不是只被 OpenAPI 合法性拦下；
  - 遍历按“节点、owner、属性”去重，不再漏掉同一锚点的第二个位置；
  - Makefile 的耗时说明改为约 1 分钟。
- **数量**：新增 13 个反例（共 67 个）与 26 个变异（服务契约共 86 个），逐条核对都是被对应规则发现的。

没有照做的：
- 建议 2，个人层字段名词表与迁移 0011 的 `kge.has_personal_key` 逐字比对并扩充：
  - 检查脚本的词表已比迁移宽（加了 user、child、parent、phone 等）；
  - 让迁移词表跟着扩充要改迁移，属于必停项，留给 0011 评审时一起定（待人确认 #8）。
- 建议 4 的两处：
  - `subject_dimension` 比迁移严；
  - `keyword_family`、`partial_credit` 与迁移的 jsonb 如何对应。
  这两处都登记为待人确认 #9，不改迁移。

## 待人确认
1. **公共层自由文本的允许清单**（偏差 1）：节点名称、边界文字、原句、页面正文由教研审核发布，契约只限长。是否还要求这些字段在发布前过禁用推断词表？默认由 KGE 的程序体检负责（KGE-03），不在契约层做。
2. **`p_mastery` 与 `star_lit` 放在 `mastery.get`**：它们是推断，调用方是编排层与学习服务。按规则 11，前端展示仍要经洞察服务的判断，不能直接用 `mastery.get` 的结果对学生说话。是否在描述里再加一句强制说明，或者把 `mastery.*` 限定为只给 `orchestrator` / `learning` 调用（现在上下文的 `caller` 已经只允许这两类）？默认维持现状。
3. **`insight.context` 的字段白名单**：契约只约束字段 ID 的格式，并在示例里核对字段字典。每种任务能拿哪些字段、`audience = agent` 时排除家庭圈详情，由洞察服务实现（SIE-01）。是否要在契约里按任务列出白名单？默认不列，白名单放在字段字典里维护。
4. **预测区间的分数上限**：`low` / `high` / `actual_score` 按 150 封顶，`full_score` 只允许 100、120、150。其他学科或考试类型的满分由考情政策库定（对照文档第七节 #7）；政策库建成后这里改为引用政策库。
5. **错误体**：两份契约复用插件 API 的 `Problem`。服务特有的错误码（例如 `CLAIM_NOT_FOUND`、`AUDIENCE_NOT_ALLOWED`）没有加进枚举，要加就是改插件 API 的共享枚举，留到 W4 冻结时一起定。
6. **不计次的前提**（安全审查需人确认 #2）：
   - `insight.explain`、`insight.context` 可能由模型写排序与衔接，`node.resolve`、`item.similar` 可能调用模型；
   - BFF 会话发起的调用（预测、记录、解释）也不逐次计量。
   - 默认由 BFF 按会话限频，不在契约里带父级 usage 引用；是否要带父级引用用于审计，需要人定。
7. **模型供应商写进契约**（安全审查需人确认 #4）：`ModelVersion` 允许任意供应商。个人层工具与 `node.resolve` 是否在契约里用枚举限定为境内已备案供应商？默认在 ADR 04 增补与路由配置里保证，契约不列供应商名单（换供应商不应改契约）。
8. **个人层字段名词表**：检查脚本（契约层）比迁移 0011 的 `kge.has_personal_key`（库层）宽。是否把库层词表扩到同样范围，并加一个逐字比对的检查？改迁移属必停项，随 0011 评审定。
9. **采分点在契约与迁移之间的对应**：
   - `subject_dimension` 在契约里是编码，在迁移里是任意 1–64 字；
   - `keyword_family`、`partial_credit_rule` 在迁移里是 jsonb，在契约里是字符串数组与枚举。
   默认以契约为准，KGE-02 导入采分点时把迁移收紧到与契约一致。
10. **契约审查需人确认 #1–#7**：
    - `mastery.get` 结果不得直接对学生说话：已写进知识服务描述；星图过渡期按原样运行（规则 11 过渡条款）。
    - 词表缺 anxiety、learning style 类英文词条：改词表属必停项，随词表签字一起补。
    - `FieldId` 是否改为按字典生成的枚举：默认不改，运行时按字典强制。
    - `inner.g2.exam_rank_band` 以取值形式流动：README 的“没有名次字段”指字段名与取值编码，不含录入值；该字段只记录录入值、不展示给学生。
    - `exit_after_errors` 行为事件的持久化与平台规划 v1.3“不持久化节奏信号”是否冲突：默认保留为事实事件，不据此生成情绪判断。
    - `node.resolve` 进复核队列不带原文：已写进描述。
    - `to_verify` 仍带文字发给前端：同 ADR 13 待人确认 #2。

