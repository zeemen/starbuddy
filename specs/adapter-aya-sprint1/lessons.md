# 阿鸦适配器 · 冲刺 1：实现记录与待人确认

实现按 spec“待人确认”六项的默认方案进行（用户 2026-09-25 指示“按默认方案开始实现”）：
1. 运行时 `node:http` + `ajv`（MIT）+ `ajv-formats`（MIT），测试 `node:test`；
2. 服务端内容包过渡，不写 `aya.*` 迁移；
3. 适配器照常置 `human_review_required = true`、原因 `content_not_admitted`，是否进人工复核队列由平台定；
4. 命中预标注陷阱给 0.8，没命中不产出错因；
5. 登记脚本只输出 SQL，开发库由 Claude Code 执行，pilot 与生产由人执行；
6. TypeScript。

## 待人确认

1. **课程版本的年级与教材版本**。阿鸦题库覆盖初一到初三，但 `platform.curriculum_version` 只有一个 `grade` 字段。
   暂登记为 `nanjing / 2027 / 9 / suke-2024 / aya / 0.1.0`。
   - 教材版本：legacy 里有 140 处提到“苏科”，所以写 `suke-2024`；年份是猜的。
   - 年级按“中考总复习”写成 9。
   是否要按年级拆成三个课程版本，或者给 `curriculum_version` 增加“适用年级范围”？后者要写迁移，属于必停项。
2. **needs_review 时的维度分**。验证器遇到边界情况时不下对错结论，维度分写成 `null`。
   契约因此要求同时带 `dimension_unavailable` 原因和 `excluded_dimensions`。`dimension_unavailable` 原本是为英语发音维设计的，这里借用了。
   是否在契约 v1.0 里为“待复核、暂不给分”单列一种表示？改契约属于必停项。
3. **鉴权失败没有对应的 Problem 编码**。服务间令牌缺失或错误时返回 401，`code` 暂用 `INVALID_REQUEST`。
   建议契约 v1.0 增加 `UNAUTHENTICATED`（必停项，本 PR 不改契约）。
4. **内容包（含答案）进了代码仓库**：`adapters/aya/content/pack.json`，约 400 KB。
   - 仓库是私有的，legacy 仓库里本来就有这些答案。
   - 答案只在适配器进程内使用，接口与日志都不返回，有测试断言。
   是否接受放在仓库里？还是改为部署时从受控存储拉取？
5. **`calibrated` 一律写 `false`**：判分还没有经过金标准校准（`evals/grade` 尚未建立）。平台会据此标注“未校准”。
6. **重复请求的冲突检测只在进程内**。
   - evidence_id 由 `idempotency_key` 与请求体指纹确定性派生，进程重启后仍然一致。
   - “同 key 不同请求体 → `IDEMPOTENCY_CONFLICT`”只在同一进程的缓存里能发现（只存指纹，上限 1 万条，24 小时过期）。
     进程重启或多副本时，同 key 换请求体会静默拿到一批新 ID。
   - **这是冲刺 1-C 学习服务的硬性要求**：学习服务必须按 `idempotency_key` 记录请求指纹并拒绝冲突，写进 1-C 的 spec。
7. **attempt_id 由谁生成**：契约里 `AttemptInput.attempt_id` 是可选的，适配器不使用它。
   学习服务落库 `platform.attempt` 时需要一个重试不变的 attempt_id，建议由平台在调用前生成并传入。端到端测试就是这样做的。

8. **needs_review 之后的重判由谁触发**（契约守护建议）：学习服务应按 `verdict = needs_review` 触发复核后的重判，
   不要按 `dimension_unavailable`，否则可能把这一维当成永久排除。
9. **准入降级不同步**：登记脚本只增不改，题目从 admitted 改为 rejected 这类降级不会同步到平台，要走发布流程。
   登记时内容包里的 admitted 也只登记为 pending_review，准入升级只能由发布闸门做。
10. **拍照作答、`paper` / `process` 模式**返回 422 `INVALID_REQUEST`。契约里 `DISCIPLINE_NOT_SUPPORTED` 的定义是“学科内分支不在本版本范围”，
    也可以用；选哪个请契约评审时一并定。
11. **CI 必须设 `CONTRACT_STRICT=1`**：本机没装 Python 依赖时 `contract-test` 会打印“跳过”并返回 0。

## 合并前审查（契约守护、安全审查）已修复
- 请求目标畸形、请求体传到一半断开，都会让进程因 unhandled rejection 退出。
  现在整个处理函数都包在 try/catch 里，并设置了 `requestTimeout` 和 `headersTimeout`。有回归测试。
- Makefile 被写坏：`adapter-aya-test` 被粘进了 `eval-persona` 的 echo 引号里。已恢复。
- `content_version` 原来只比两段，现在六段都比。
- 错因的 `algorithm_version` 现在带上映射表版本，写成 `aya-trap-attr@0.1.0-tax0.9.0`（规则 6）。
- 幂等缓存原来存整份响应（里面有学生原文），现在只存指纹。命中时重新计算，结果完全一致。
- 登记 SQL 不再把新题直接登记为 admitted。
- 请求里写 `appeal: false` 和不写 `appeal`，现在算作同一请求体。
- 单选、多选里重复的选项 key 会被拒绝。
- 日志只记已知的方法名。500 错误额外记异常类名，但不记异常消息。
- 令牌比较改为先对两边做 SHA-256 摘要，不再泄露令牌长度。
- 泄露测试补了两处：数字字段按路径白名单检查；每道填空题都用错误作答逐题断言，响应里没有字段等于标准答案。
- 新增测试：`src/` 不引用测试代码，也不连数据库。

## 实现中的发现

### spec 数字更正
能力点是 **959** 个，不是 spec 写的 939 个。按 legacy `allNodeById` 去重计数，覆盖 32 个专题。题目 721 道，陷阱码 429 个，数字无误。

### legacy 题库问题（待教研修题，适配器不改题）
有两处“典型错误答案”其实与标准答案等价，legacy 和本验证器都判对，因此不会产出错因：
- `Q-ALG-U02-D-001` 的错误答案 `2·x+x+3`，化简后等于标准答案 `3x+3`；
- `Q-DCE-U03-D-001` 的错误答案 `0.17人`，归一化时去掉“人”，结果等于 `0.17`。

`tests/contract.test.ts` 固定了这份清单；以后新增这类冲突会让测试失败。

### legacy 符号验证会误判含函数的式子
legacy 的解析器把 `sin` 当成 `s·i·n` 三个变量，所以 `sin(2x)` 与 `2sin(x)` 会被判为等价。
验证器 v0 在走 legacy 符号路径之前先检查函数名，命中就返回 `needs_review`，原因码是 `trig_or_multiletter_function`。
这是唯一一处“legacy 判对、本验证器不判对”的情况，有测试覆盖。

### 验证器 v0 比 legacy 多判对的情况
- **不含变量的常量式**：例如 `2√2` 与 `√8`。legacy 只对含变量的式子做符号比对，所以判错。现在按数值判为等价。
- **多答案都是常量时**：按数值多重集比对，例如 `√8,-1` 与 `-1，2√2`。

这两类都是确定性比较，不属于边界情况。

### 其他与 spec 措辞的差异
- `ERROR_TRAPS`（陷阱码 → 根因）放进了内容包 `content/pack.json`，没有复制成 `src/core/` 下的源码：它是内容，和题目用同一个版本号追溯。
- 除 `ajv`、`ajv-formats` 外，还新增了运行时依赖 `yaml`（ISC 许可）。它用来直接读取契约 YAML 和错因映射表 YAML，避免手抄一份结构。
- 选择题同样产出一条 `verifier_trace`（采样 0 点、一致 0 点、无原因码），与 spec“外加一条 verifier_trace”一致。
- 敏感类“心理与习惯”（`xinli_xiguan`）不自动归因（完整方案第四章红线）。目前 legacy 没有任何陷阱码归到这一类。
- diagnose 里答错且自评“猜的”，不降低“没掌握”这条观测的置信度。spec 只规定了“答对且猜的”要降权。

### 工程
- Node 22.18 起可以直接运行 `.ts`（类型擦除）。它不支持构造函数参数属性，所以 legacy `symbolic.ts` 里有一处改成了显式字段。改动已在文件头注明。
- 生成内容包需要读取 legacy 的 `@/` 路径别名，用 `scripts/legacy-hooks.mjs` 做模块解析，不改 legacy 源码。
- `.claude/hooks/guard.py` 的“读取密钥”规则会误拦同一条命令里同时出现 `cat` 与 `TOKEN` 的 heredoc。写含这类变量名的文件时改用编辑工具。
