# 冲刺 3-D：开口成章适配器（异步 grade、diagnose、report）

状态：**搁置（2026-09-26）**。用户指示“英语课程需要做重大的调整，原先的产品开发先搁置”：本 spec 不实现，开口成章适配器、学习服务的异步配套都不做。搁置记录与影响见同目录 `lessons.md`。英语方案调整后另起 spec；本文件保留作历史参考。原状态：草稿，等人确认后才开始实现。

关联：
- **开发计划 冲刺 3-D**（W7–W8）：“开口成章适配器：grade 先走异步（录音 → 评测 → Rubric）、diagnose、report；实时语音链暂留原产品内”。负责人是英语开发 + 语音工程，验收是“一次口语作答的评测结果写入平台，语音原文件不入学生模型”。
- **Gate 0 软条件**：契约覆盖率开口成章 ≥ 4/6（冲刺 4 补 `plan`）。本冲刺交付其中 3 个方法。
- **盘点**：`specs/inventory-echo/`（inventory.md §4 模型调用、§5 语音链、§6 评测资产、§7 Rubric 对照；adapter-design.md v0.2；decisions.md A1、A3、A5、#7、#8、#12）。
- **模板**：阿鸦适配器 `specs/adapter-aya-sprint1/spec.md` 与 `lessons.md`（#2 needs_review 借用 null、#3 复核队列会被刷满、#6 冲突检测只在进程内、#7 attempt_id 由平台生成）。
- **契约**：`packages/contracts/openapi/plugin-api.v0.9.yaml`（202 + `GET /jobs/{job_id}`、`GradeRequest`、`DiagnoseRequest`、`ReportRequest`、`EvidenceDraft.media_ref`、`data_class`、`usage_id`）；一致性测试 `packages/contracts/conformance/`。
- **契约评审**：`specs/contract-conformance-sprint1/v1.0-review.md` N11（语音 L3）、N14（冲突检测归平台）、N16（转写文本无结构约束）、N17、N18、v09-#3（异步只提供轮询，英语接入时复核）。
- **学习服务**：`specs/learning-ingest-sprint1/spec.md` 范围第 3 条（202 返回“不支持”）与 `lessons.md` #8（租约）、#11（可重试 = 结果未知）。
- **计量**：`specs/metering-impl-sprint2/spec.md`（语音与 teach 类资源的 BFF 流程未接）。
- **ADR**：04（按数据级别路由）、06（计量；`voice_realtime` 按分钟，本冲刺不接实时语音）、08（英语模型与语音链，建议选项 A）、09（数据驻留）。四份都是草稿，未签字。
- **人设（只读引用）**：`docs/personas/考官型基线与角色卡-方圆春秋雅典娜-V1.1.md` §2.3、§3.3、附录 C（雅典娜）；`docs/personas/七科AI老师角色规范-v1.1.html`（场景角色不判分）。
- **错因映射**：`docs/mapping/error-taxonomy.v0.9.yaml` 的 `echo` 段。

## 目标
一次话题简述的口语作答，走完这条异步链路，结果写进平台学习者模型：

> 录音（合成）→ 平台语音层（本冲刺是假引擎）→ 转写稿 `transcript_ref` → 学习服务 → echo 适配器 `grade`（202 + 轮询）→ Rubric A/B/C → 学习服务核对并落库

落库后能查到 `evidence_event`（转写、语音特征）、`attempt`、`grade_result` 与 `grade_dimension`（D 维为 null）、`error_attribution`。**原始音频的引用和内容都不出现在 `platform.*` 里。**

同时交付 echo 的 `diagnose`（同步，只读证据）和 `report`（字段提供者），三个方法都通过一致性测试。

## 现状（起草时核实）
- **适配器与 legacy 代码**：
  - 仓库里没有 `adapters/echo`。
  - 本检出的 `legacy/` 下只有 `aya`、`wenli`，**没有 `legacy/echo`**（`legacy/` 被 `.gitignore` 忽略）。盘点是在别处的只读副本上做的（inventory.md 盘点日期 2026-09-24）。
  - 要包装的 `server/rubric.mjs`、`scriptedEngine.mjs`、`shared/data/*` 目前本会话读不到，见“依赖与输入”。
- **契约 v0.9**（v1.0 未冻结，`openapi/` 下只有 `plugin-api.v0.9.yaml`）：
  - `grade`、`diagnose` 可以返回 202 `JobAccepted`。同一个 `idempotency_key` 重试必须返回同一个 `job_id`；任务结果里的 evidence_id 也必须不变。`GET /jobs/{job_id}` 返回 `JobStatus`，`succeeded` 时带 `response`，`failed` 时带 `error`。只有轮询，没有回调（v09-#3）。
  - `AttemptInput.response` 四选一：`text`、`choice`、`image_ref`、`transcript_ref`。**没有音频引用字段**。
  - 证据 `kind` 有 `asr_transcription`（payload 是 `{text}`）和 `speech_features`（payload 是 `{signal_code, value}`，一条证据一个数）。`media_ref` 必须是 `objstore://`。
  - `DimensionScore.score` 可以为 null，此时 `score.excluded_dimensions` 必填，并且必须复核、原因含 `dimension_unavailable`。`MethodCapability.notes_code` 的示例就是 `pronunciation_dimension_null`。
  - `SourceVersion` 的类别限定为 rubric / verifier / prompt / items / algo / graph，没有语音引擎这一类。
  - **echo 示例 `GradeRequestEchoTranscript` 声明 `data_class: L2`**，`content_version` 写的是 `grade: 9`、`textbook_version: bubian-2024`。
- **一致性测试**：
  - 异步路径已覆盖：`cli.py --reference-async` 让参考假适配器返回 202；`support.py` 校验 `JobAccepted`、轮询 `/jobs`、校验 `JobStatus`；违约 `async_new_job_id` 验证“同 key 必须同 job_id”。
  - 平台侧核对和泄露检查只覆盖 grade、diagnose；`report` 只做 schema、坏请求、幂等。
  - ASR 转写文本只检查位置，不逐字比对（README“检查边界”，即 N16）。
- **共享核对库** `packages/contracts/python/starbuddy_contract/checks.py`：
  - `L3_KINDS` 含 `asr_transcription`、`speech_features`。响应里有这两类证据，而请求不是 L3 时整体拒绝（4c）。**所以按契约示例用 L2 发 echo 请求，一定会被拒绝。**
  - `media_ref` 只能取本次请求里出现过的引用（4b），`transcript_ref` 在其中。
  - 第 8 条只核对 `response_text`、`response_choice` 与学生作答一致；`asr_transcription` 的文本**不核对**，多出来的 `asr_transcription` 证据也不拒绝。
- **学习服务** `services/learning`：
  - `METHODS = ("grade", "diagnose")`；`adapter_client.py` 收到 202 时返回 501 `ASYNC_NOT_SUPPORTED`。
  - diagnose 只接受 `intake`、`gap_test` 且带 `attempts`。
  - 单次调用超时 grade 10 秒、diagnose 30 秒；同 key 的租约 90 秒（`config.py`）。租约过期或状态为 `failed` 时，同 key 的新请求会接管，沿用原 attempt_id（`store.claim`）。
  - `learning-internal.v0.yaml` 只有 grade、diagnose 两个接口，**没有 report**。错误码里已有 `BUSY`（503，可重试，带 `retry-after`）和 `ASYNC_NOT_SUPPORTED`。
  - 不写 `review_task`（1-C 待人确认 #4）。
- **计量**：
  - `platform.resource_type` 有 7 种（迁移 `0008`），其中 `voice_realtime` 按分钟、单独 `voice` 池、预留 30 分钟。**没有适合“一次异步口语批改”的资源类型。**
  - BFF 只接通了 `diagnosis_screen`、`diagnosis_full`、`mistake_analysis` 三种资源（`services/bff/src/metering/meter.ts` 的 `METHOD_OF`）。BFF 调学习服务的超时是 grade 15 秒（`learning-client.ts`）。
- **其他平台能力**：
  - 仓库里没有模型路由（冲刺 1-F）的代码，没有对象存储实现，没有 `evals/` 目录，也没有语音层。
  - 适配器按 ADR 04 第 1 条不能自己持有模型 Key。所以本冲刺 **LLM 判 Rubric 这条路没有合规的出口**。
- **错因映射**：echo 段只有 `grammar → computation_or_grammar` 是 `clear`；`vocabulary`、`task_fulfilment`、`pronunciation` 都是 `disputed`，映射表整体待教研签字。
- **人设规范**：
  - 雅典娜的 Rubric 来源写的是“音素级发音评测 + 话题简述 Rubric（语法 / 词汇 / 达意三维）”（附录 C）。
  - legacy Rubric 细则 V1.0 是 A50 / B20 / C15 / D15 四维（inventory.md §7）。
  - 两者的维度划分不一致。
  - 考官型基线 §2.3 规定“引擎未返回不出场”，与异步判分一致。本冲刺不产出任何人设文本。

## 设计要点

### 1. 链路与职责边界（ADR 08 选项 A 的阶段 0 形态）
| 环节 | 本冲刺由谁做 | 说明 |
|---|---|---|
| 录音上传、存对象存储 | 测试夹具 | 合成音频（静音或正弦波，**不含人声**），写到本地对象存储替身的 `audio/` 前缀下 |
| ASR、语音特征 | 平台语音层**接口 + 假引擎** | 假引擎按合成音频的文件名返回预置的合成转写稿，写到 `transcripts/` 前缀下，得到 `transcript_ref`。不接任何供应商 |
| 音频的保留与删除 | 平台语音层 | 本冲刺只在测试里删除；生产策略见待人确认 #9 |
| grade 编排 | 学习服务 | 调适配器，处理 202 和轮询，核对，落库（第 4 节） |
| Rubric A/B/C、证据草稿 | echo 适配器 | 读 `transcript_ref`，判分，产出证据。**不接触音频** |
| 雅典娜宣判、点点复盘 | 人设层 | 不在本冲刺 |

- 契约里没有音频字段，所以音频不会经过适配器，也不会进入学习服务的请求。
- “语音原文件不入学生模型”靠三层保证：
  1. 契约里没有入口；
  2. 学习服务拒绝任何指向 `audio/` 前缀的引用（第 4 节第 5 条）；
  3. 测试直接查库断言（验收标准）。

### 2. 转写稿格式 `transcript.v0`（语音层 → 适配器、学习服务）
- 是一个 JSON 对象，内容包括：
  - 格式版本；
  - 语言 `en`；
  - 全文 `text`；
  - 分段 `segments[]`：每段有起止毫秒、文本、ASR 置信度、`uncertain` 标记；
  - 引擎标识 `engine`（本冲刺是 `asr-fake@0.1.0`）。
- **不包含**学生 ID、姓名、音频引用。
- JSON Schema 暂放 `adapters/echo/schemas/transcript.v0.json`，学习服务的测试读同一份文件。是否进 `packages/contracts` 见待人确认 #3。
- 有了分段时间戳，C 维（停顿）可以按 Rubric 细则计算，补上 Web Speech 没有时间戳的缺口（inventory.md §7）。

### 3. echo 适配器 `adapters/echo/`（TypeScript，Node 22，与阿鸦同一工具链）
- **`GET /capabilities`**：
  - `grade`：已实现、`async: true`、`notes_code: pronunciation_dimension_null`；
  - `diagnose`：已实现、`async: false`；
  - `report`：已实现；
  - `plan`、`teach`、`generate_variation`：未实现，返回 501。
- **`POST /grade`**：同步校验请求，然后返回 202 `JobAccepted`。
  - `job_id = uuidv5(echo 命名空间, idempotency_key + 请求体指纹)`：同 key 同请求体一定得到同一个 job_id，进程重启后也一样。
  - 任务表只放在进程内存里，只存指纹和状态，不存转写原文（阿鸦 lessons #6 的做法）。
  - 进程重启后任务丢失，`GET /jobs` 返回 404 `JOB_NOT_FOUND`，由学习服务用同一个 key 重新提交（第 4 节）。
  - 同 key、不同请求体只能在进程内发现，平台侧由学习服务负责（N14）。
- **`GET /jobs/{job_id}`**：`queued` / `running` / `succeeded` / `failed`，`poll_after_ms` 默认 1000。
- **判分**（包装 legacy `rubric.mjs` 的 `scriptedRate`，复制到 `src/core/`，文件头注明来源路径；legacy 只读）：

  | 契约字段 | 取值 |
  |---|---|
  | `result.dimensions` | A、B、C 三维由规则判分：`judged_by = rubric_rule`、`rule_ref = echo-scripted-rate@0.1.0`、单维 `confidence ≤ 0.4`；D 维（发音）`score = null`。维度 ID 见待人确认 #5 |
  | `result.score` | `total` 只计 A/B/C；`excluded_dimensions = [D 维 ID]`；`calibrated = false`（decisions #13）；`max_total` 见待人确认 #6 |
  | `result.verdict` | `partial` 或 `not_applicable`（话题简述没有对错），不用 `correct` / `incorrect` |
  | `evidence` | ① 一条 `asr_transcription`：`item_ref`、`occurred_at` 与作答相同，`payload.text` 等于转写全文，`media_ref = transcript_ref`；② 若干 `speech_features`（`asr_confidence_mean`、`speech_duration_sec`、`pause_count`、`uncertain_segment_count`），只来自转写稿，不存音频；③ A 维的有效句用 `scoring_point_span` 引用 ① 的偏移，不复制文本 |
  | `result.error_attributions` | 只产出 `grammar`（映射表中唯一 `clear` 的编码）。`algorithm_version = echo-err-attr@0.1.0-tax0.9.0`；B 维的错误若落在 `uncertain` 分段，排除，不计入（Rubric 细则的 uncertain 规则） |
  | `human_review_required` | 恒为 true。原因含 `dimension_unavailable`、`low_confidence`；内容未准入时平台再补 `content_not_admitted` |
  | `model_version` / `source_version` | `algo:echo-scripted-rate@0.1.0`；`rubric:echo-topic-brief@1.0.0;algo:asr-fake@0.1.0;items:<内容包版本>` |

- **LLM 判 Rubric（`rubric_model`）本冲刺不启用**：
  - 模型路由（1-F）还不存在，适配器不能自己持有 Key（ADR 04）；
  - 评分 Prompt 的改动（补 uncertain 规则）按 CLAUDE.md 第 7 条要跑回归，而 `evals/` 还没有建。
  - 代码里只留 `ModelRouter` 接口和一个测试用的假实现，默认关闭。见待人确认 #7。
- **`POST /diagnose`**（同步，只读证据）：
  - `mode = gap_test`，输入是带 `transcript_ref` 的作答，外加 `prior_evidence`（之前的转写与语音特征）。
  - 输出 `skill_observation`：能力点是语法点、句型族、词汇，按 completionConditions 与 wordPack 命中情况判定 `demonstrated` / `not_demonstrated` / `inconclusive`，`algorithm_version = echo-diag@0.1.0`。
  - 错因规则同 grade。
  - legacy 的五维 skills、XP、成就一律不输出（decisions #12）。
  - NPC 回合信号（decisions #8）要等实时对话接入平台才会有，本冲刺不处理。
- **`POST /report`**（非消耗类，同步）：
  - 输入是平台给的证据、错因和掌握度序列；输出 `skill_summary`、`error_distribution`、`highlight_codes`。
  - 不输出 `forecast`：没有已准入内容。
  - 不输出任何转写原文或对话；`highlight_codes` 不得出现 `xp_`、`achievement_` 类编码。
- **内容包与登记**：
  - 从 legacy `shared/data/*` 生成服务端只读内容包，覆盖 U1–U3、5 道话题简述、语法点、句型族、36 个词。
  - 登记脚本只输出 SQL（阿鸦 lessons 默认 5），把 echo 的课程版本、能力点、题目登记进平台。
  - **全部登记为 `source_unverified`**（decisions #14）。U1 外貌词完成条件先删除，不进内容包（decisions #10）。
- **禁用资产**：偶像风图片不进适配器（decisions #11）；legacy 的 Anthropic 调用、`/api/*` 代理不复制（ECHO-KD-01～04）。

### 4. 学习服务配套改动（`services/learning`，不改契约）
1. **支持 202**：
   - 按契约校验 `JobAccepted`：`method`、`usage_id`、`idempotency_key` 必须与请求一致。
   - 按 `poll_after_ms` 轮询 `GET /jobs/{job_id}`，取值限制在 200 毫秒到 5 秒之间，并校验 `JobStatus`。
   - `succeeded`：把 `response` 交给现有的核对和落库，流程与同步完全相同。
   - `failed`：按同步错误处理，只转发编码形状的 `code`，用 `retryable` 判断能否重试。
   - `404 JOB_NOT_FOUND`：适配器重启，用同一个 key 重新提交一次。
   - 删除“202 → 501 `ASYNC_NOT_SUPPORTED`”的分支。这个错误码保留在枚举里，契约评审时再删。
2. **异步总预算**：新增配置 `async_budget`，默认 60 秒，必须小于租约（90 秒）减去 10 秒，启动时校验。
   - 预算内任务没完成：标记本次为 `failed`，`error_code = JOB_PENDING`，返回 503 `BUSY`（`retryable: true`，带 `retry-after`）。
   - BFF 用同一个 key 重试时接管，重新提交拿到同一个 job_id，继续轮询。
   - 这沿用了 1-C 的接管机制和 N8 / N18 的“结果未知”语义，**不需要改 `ingest_request` 表**。是否新增专用错误码，见待人确认 #4。
3. **L3 前置校验**：请求里有 `transcript_ref` 或 `image_ref` 时，`data_class` 必须是 L3，否则在调用适配器之前返回 400。现在只在响应侧（4c）拦截，适配器已经白算了一次。
4. **转写一致性**（N16）：学习服务用自己的对象存储读取器读 `transcript_ref`，逐字核对：
   - `asr_transcription` 证据的 `payload.text` 必须等于转写稿的 `text`；
   - 不属于任何作答的 `asr_transcription` 证据，一律拒绝。

   本冲刺在 `services/learning` 里实现，不改共享库 `checks.py`（见待人确认 #3）。
5. **拒绝音频引用**：请求中的 `transcript_ref`、`prior_evidence[].media_ref`，以及响应证据的 `media_ref`，只要落在 `audio/` 前缀下，就整体拒绝。
6. **对象存储读取器**：`objstore://` → dev 和测试用本地目录（`LEARNING_OBJSTORE_LOCAL_ROOT`）。
   - 拒绝 `..` 路径段：契约的 pattern 允许 `.`，挡不住路径穿越。
   - 只读，单个文件上限 1 MB。
   - 生产的对象存储等 ADR 10 选型。
7. **超时**：diagnose 仍是同步调用，30 秒。report 学习服务不调用（平台报告引擎在后续冲刺）。

### 5. 数据分级与出境
- echo 口语作答的请求一律声明 L3，与契约示例不同（见“冲突”与待人确认 #2）。学生打字作答用 `response.text`，按 L2 处理。
- 适配器不调用任何模型，也没有网络出口。假 ASR 引擎在进程内运行，所以本冲刺没有新的出境路径。
- 测试全部使用合成数据（规则 8）：合成音频不含人声，转写稿由教研风格的合成句子组成，不使用 legacy Playwright 快照里的任何内容。

### 6. 测试
- **适配器单测**（`adapters/echo/tests/`）：
  - Rubric A/B/C 规则：正例、边界、uncertain 分段排除；
  - D 维为 null 时，`excluded_dimensions` 与复核原因齐全；
  - 错因只产出 `grammar`；
  - `job_id` 确定性（同 key 同体得到同一个 ID；换体得到新 ID）；
  - 进程重启后 `JOB_NOT_FOUND`；
  - report 不含原文、没有 `forecast`；
  - 泄露：响应和日志里不出现转写原文以外的学生内容、内容包答案要点，做法同阿鸦 `leak.test.ts`。
- **一致性测试**：echo 夹具放在 `adapters/echo/conformance/fixtures.yaml`，**由另一个会话或英语负责人编写**（conformance README“夹具由谁写”），`make conformance` 纳入 echo。
- **学习服务测试**：
  - 用假适配器覆盖 202 的各种情况：立即成功、多次 running 后成功、failed（可重试 / 不可重试）、`JOB_NOT_FOUND` 后重新提交、超过预算后 `BUSY` 再由同 key 接管、同 key 并发；
  - L3 前置校验；
  - 转写不一致或多出转写证据时拒绝；
  - `audio/` 引用拒绝；
  - 路径穿越拒绝。
  - 每个拒绝场景都断言一行不写。
- **端到端** `services/learning/tests/test_e2e_echo.py`：合成音频 → 假语音层 → echo 适配器（真实 HTTP 进程，异步）→ 学习服务 → 本地一次性 Postgres。断言：
  - 查得到 `evidence_event`（`asr_transcription`、`speech_features`）、`attempt`、`grade_result`（D 维 null、`calibrated = false`）、`error_attribution`；
  - evidence_id 与任务结果一致；同 key 重试不重复入库；
  - 对 `platform` schema 全部 text / jsonb 列扫描：不含 `audio/` 前缀的引用，也不含合成音频文件的 SHA-256。
- **变异**：至少覆盖以下几处：
  - 学习服务不校验 `JobAccepted.usage_id`；
  - 不核对转写一致性；
  - 放行 `audio/` 引用；
  - 预算不小于租约；
  - 适配器 `job_id` 改成随机数（复用一致性测试的 `async_new_job_id`）。

## 范围

### 做
1. `adapters/echo/`：capabilities、异步 grade 与 `/jobs`、同步 diagnose、report、内容包生成与登记脚本（只输出 SQL）、`transcript.v0` schema（第 2、3 节）。
2. `services/learning` 的配套改动（第 4 节），包括本地对象存储读取器，以及测试用的假语音层夹具。
3. 测试（第 6 节）。`Makefile` 新增 `adapter-echo-test` 并纳入 `make check`；`conformance` 目标加上 echo。
4. 把本冲刺发现的契约问题补进 `specs/contract-conformance-sprint1/v1.0-review.md`（只写评审项，不改契约），并写 `lessons.md`。

### 不做
- 实时语音链、NPC 对话（`teach`）、`plan`、`generate_variation`：实时链留在原产品（decisions A1），`plan` 在冲刺 4。
- 真实 ASR、TTS、发音评测供应商：冲刺 4-I 盲测后再选，D 维保持 null。
- LLM 判 Rubric 的上线：等模型路由（1-F）和评分回归集（第 3 节）。
- 平台语音层的生产实现、对象存储选型、音频保留与删除策略（待人确认 #9）。
- BFF 接入 echo 的计量流程与新资源类型（待人确认 #8）；语音分钟计量（冲刺 4-I）。
- 学习服务的 report 接口、平台报告引擎（冲刺 5-H）。
- `echo.*` 私有 schema 与迁移；成就、XP 的归宿（decisions #12，不进平台）。
- 雅典娜宣判模板、点点复盘话术，以及任何人设 Prompt 改写（decisions #9，必停项）。
- 复核队列入队（冲刺 2-G）。

## 验收标准（可测试）
- [ ] 端到端：一次合成口语作答（合成音频 → 假语音层 → 异步 grade）落库，能查到 `evidence_event`（`asr_transcription` + `speech_features`）、`attempt`、`grade_result` + `grade_dimension`（D 维 `score` 为 null，`excluded_dimensions` 非空，`calibrated = false`）、`error_attribution`；evidence_id 与适配器任务结果一致。这是开发计划的验收前半句。
- [ ] 端到端：扫描 `platform` schema 的全部 text / jsonb 列，没有 `audio/` 前缀的引用，也没有合成音频的 SHA-256。这是验收后半句“语音原文件不入学生模型”。
- [ ] 同一个 key 重试（包括超过异步预算后的接管、适配器重启后的重新提交）：`job_id` 相同、行数不变、返回同一摘要。
- [ ] `asr_transcription` 文本与转写稿不一致、多出不属于作答的转写证据、请求带转写却声明 L2、引用落在 `audio/` 下，四种情况都整体拒绝、一行不写。
- [ ] 所有 grade 结果带 `evidence_ids`、`confidence`、`source_version`、`model_version`、`human_review_required`（规则 3），并且 `human_review_required = true`、原因含 `dimension_unavailable`。
- [ ] echo 的 grade、diagnose、report 通过一致性测试；覆盖率报告为 3/6（Gate 0 目标 4/6 由冲刺 4 的 `plan` 补齐）。
- [ ] 适配器源码里没有模型供应商的 Key、BaseURL 或 SDK 依赖，没有出网调用。有测试断言：`src/` 不引用 `fetch` 到非回环地址。
- [ ] 报告字段里没有转写原文、逐句对话、`forecast`、XP 或成就编码。
- [ ] 测试数据全部合成（规则 8），合成音频不含人声。
- [ ] 严格模式 `make check` 通过；新增依赖的许可证已核（规则 10）。

## 涉及的必停项（见 CLAUDE.md）
按默认方案逐条判断：

| 必停项 | 是否涉及 | 说明 |
|---|---|---|
| `db/migrations` 下的任何文件 | 否 | 异步接管复用 `ingest_request` 现有状态；不建 `echo.*`。如果选待人确认 #8 (b) 新增资源类型，或 #9 要加数据库约束，就变成“是” |
| RLS 策略 | 否 | — |
| 计量与权益逻辑 | 否 | BFF 不接 echo（待人确认 #8）。测试里直接用合成 `usage_id` 调学习服务，与 1-C 相同 |
| `packages/contracts` 的任何变更 | 否 | 契约示例的 L2、`transcript.v0` 的归属、转写核对进共享库、`JOB_PENDING` 错误码，都只写进 v1.0 评审清单。如果评审选择进契约，另开 `[NEEDS-HUMAN]` PR |
| evals 金标准 | 否 | `evals/` 不存在；金标准由教研标注（decisions A5），本冲刺不创建，只读 |
| 人设 Prompt、禁语表、转介话术 | 否 | 不产出人设文本；LLM 评分 Prompt 不启用（待人确认 #7） |
| 新增第三方服务、模型供应商、出境路径 | 否 | 只有进程内的假 ASR 引擎，不接任何供应商 |

**登记 SQL 写的是平台内容表**：dev 库由 Claude Code 执行，pilot 与生产由人执行（阿鸦 lessons 默认 5）。实现 PR 如果越过上表任何一条，首行写 `[NEEDS-HUMAN]`。

## 依赖与输入
- **legacy/echo 只读副本**：本检出里没有，需要负责人放到 `legacy/echo/` 或告知路径。没有它就无法包装 `scriptedRate`、生成内容包。
- **话题简述 Rubric 细则 V1.0**：维度定义与档位描述，教研提供或确认（待人确认 #5）。
- 已合并：契约 v0.9、一致性测试框架（含异步路径）、学习服务 1-C、计量 2-E。
- 未签字：ADR 04、06、08、09，错因映射 v0.9。本 spec 按它们的建议选项写。
- 没有依赖、也不阻塞本冲刺的：模型路由 1-F、复核队列 2-G、语音盲测 4-I、境内托管 ADR 10。

## 待人确认
1. **“录音 → 评测”在本冲刺是假引擎**：开发计划写的是“录音 → 评测 → Rubric”，但境内 ASR 和发音评测供应商要到冲刺 4-I 盲测后才选（decisions #7、ADR 08），盲测还需要先上线监护人同意流程。
   - 默认：做平台语音层的接口和假引擎，用合成音频跑通链路，D 维为 null。验收里的“评测结果”指 A/B/C 的 Rubric 结果与转写特征。
   - 另一个选择：这次验收推迟到 4-I 之后。
2. **echo 口语作答的数据级别**：契约示例写 L2，但转写和语音特征属于 L3（ADR 09 §2），共享核对库也会拒绝 L2（4c）。默认按 L3 发送，契约示例在 v1.0 评审时改成 L3（新增评审项）。同一示例的 `grade: 9`、`textbook_version: bubian-2024` 也不对：英语是译林 2024 七上。一并改。
3. **`transcript.v0` 与转写核对放在哪里**：
   - 默认：schema 放 `adapters/echo/schemas/`，转写一致性核对放 `services/learning`，都不动 `packages/contracts`，本冲刺不必停。
   - 另一个选择：两者都进 `packages/contracts`（schema 与共享 `checks.py`），一致性测试也能用上。这是契约变更，必停。建议 v1.0 评审时一起定（N16）。
4. **异步任务超时的返回码**：默认复用 503 `BUSY`（可重试，带 `retry-after`），`ingest_request.error_code` 记 `JOB_PENDING`，不改 `learning-internal` 契约。另一个选择：新增错误码 `JOB_PENDING` 或让学习服务直接返回 202 给 BFF，这两种都是契约变更，必停。
5. **Rubric 维度 ID 与划分**：
   - legacy Rubric V1.0 是 A/B/C/D 四维，雅典娜角色卡写的是“语法 / 词汇 / 达意”三维加发音，错因映射按 `grammar` / `vocabulary` / `task_fulfilment` / `pronunciation` 编码，三处不一致。
   - 默认：本冲刺照 legacy V1.0 实现（它是唯一有代码的版本），维度 ID 暂定为 `content`（A）、`accuracy`（B）、`fluency_coherence`（C）、`pronunciation`（D），`rubric_version = echo-topic-brief@1.0.0`。
   - 由英语教研定最终划分，改名只需升 Rubric 版本。
6. **D 维为 null 时 `max_total` 怎么写**：默认不重新归一化，`max_total` 只计 A/B/C 的满分（5 分制下是 4.25），平台展示“不含发音维”。另一个选择：把 A/B/C 放大到 5 分，但这会让分数看起来和真实考试可比，而实际上并不可比。
7. **LLM 判 Rubric 何时启用**：默认本冲刺只用规则判分（`rubric_rule`，低置信），LLM 路径只留接口，默认关闭。启用需要三个条件：
   - 模型路由 1-F 可用，并按 L3 路由到境内模型；
   - 评分 Prompt 的回归集建好；
   - 确认“评分 Prompt 是否属于第 7 条的 Prompt 变更”：它不是人设 Prompt，但第 7 条写的是“任何 Prompt”。
8. **echo 异步批改怎么计量**：现有 7 种资源里没有合适的；`voice_realtime` 按分钟计实时语音，不适用；`mistake_analysis` 带 24 小时同题去重，同一话题再说一次会不扣次，也不合适。
   - 默认：本冲刺 BFF 不接 echo，测试直接调学习服务。
   - 后续新增资源类型（例如 `oral_grade`，按次、预留 10 分钟、不去重），这需要迁移，属于计量逻辑，必停。
   - 另外，BFF 的 grade 超时 15 秒短于异步预算 60 秒（2-E lessons #6），接入时要让 BFF 遵循 `retry-after`。
9. **音频的保留、删除与回放**：PRD 13.3 写“默认保留错题相关片段供回放，其余 N 天后删除”，B-5 待法务定。
   - 默认：音频只存在语音层的存储里，`platform.*` 不存任何音频引用。以后要回放，经语音层按转写稿 ID 查找，不在证据里挂音频。
   - 是否要在 `evidence_event.media_ref` 上加数据库约束，禁止 `audio/` 前缀？加的话需要迁移，必停。默认不加，只靠服务校验加测试。
10. **复核队列**：D 维为 null，所以每一条 echo grade 都要复核，而且内容全部未准入。和阿鸦 #3 一样，会把队列刷满。默认沿用 1-C 的建议：原因只有 `dimension_unavailable` 和 `content_not_admitted` 的结果不进人工队列，只标“练习、不含发音维”。由 2-G 定。
11. **错因范围**：默认只产出 `grammar`（映射表中唯一 `clear` 的编码）。`vocabulary`、`task_fulfilment` 等教研签字后再加；`pronunciation` 在 D 维为 null 期间不产生错因。
12. **verdict 取值**：话题简述没有对错。默认有分时用 `partial`，转写为空或过短（少于 1 个有效句）时用 `not_applicable` 并要求复核。是否需要在契约里为“开放题”单列一种 verdict，留给契约评审。
13. **diagnose 的能力点坐标**：默认用 legacy 的语法点（7 个）、句型族（9 个）、词汇（36 个），编号为 `echo:G-xx`、`echo:P-xx`、`echo:W-<词>`。具体编码规则由英语教研确认。
14. **对象存储的键名约定**：默认 `objstore://<env>/audio/<student>/<uuid>.<ext>` 与 `objstore://<env>/transcripts/<student>/<uuid>.json`。
    - 学习服务不校验路径里的学生段：`transcript_ref` 属于哪个学生，由 BFF 在调用前负责，与选科、同意检查一样。
    - 另一个选择：学习服务也校验学生段。但路径里放学生 ID 本身就是一种信息暴露，需要一起定。
