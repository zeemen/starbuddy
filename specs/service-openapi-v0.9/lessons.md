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

## 待人确认
1. **公共层自由文本的允许清单**（偏差 1）：节点名称、边界文字、原句、页面正文由教研审核发布，契约只限长。是否还要求这些字段在发布前过禁用推断词表？默认由 KGE 的程序体检负责（KGE-03），不在契约层做。
2. **`p_mastery` 与 `star_lit` 放在 `mastery.get`**：它们是推断，调用方是编排层与学习服务。按规则 11，前端展示仍要经洞察服务的判断，不能直接用 `mastery.get` 的结果对学生说话。是否在描述里再加一句强制说明，或者把 `mastery.*` 限定为只给 `orchestrator` / `learning` 调用（现在上下文的 `caller` 已经只允许这两类）？默认维持现状。
3. **`insight.context` 的字段白名单**：契约只约束字段 ID 的格式，并在示例里核对字段字典。每种任务能拿哪些字段、`audience = agent` 时排除家庭圈详情，由洞察服务实现（SIE-01）。是否要在契约里按任务列出白名单？默认不列，白名单放在字段字典里维护。
4. **预测区间的分数上限**：`low` / `high` / `actual_score` 按 150 封顶，`full_score` 只允许 100、120、150。其他学科或考试类型的满分由考情政策库定（对照文档第七节 #7）；政策库建成后这里改为引用政策库。
5. **错误体**：两份契约复用插件 API 的 `Problem`。服务特有的错误码（例如 `CLAIM_NOT_FOUND`、`AUDIENCE_NOT_ALLOWED`）没有加进枚举，要加就是改插件 API 的共享枚举，留到 W4 冻结时一起定。
