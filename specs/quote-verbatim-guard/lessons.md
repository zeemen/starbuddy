# 引语逐字校验 · 实现记录（lessons）

2026-11-25，用户确认第五节五条都按默认后实现。

## 做了什么
- **规则引擎**（`services/learning/learning/mastery/consistency.py`，档案页与洞察服务共用）：
  - 新类型 `Quote { key, claim_id, evidence_id, text }`；`Section.quotes`、`ClaimView.evidence_ids` 都带缺省值，现有调用方不用改。
  - 衔接语里用 `{q:<key>}` 占位；key 只许小写字母与下划线，避免触发“衔接语无数字”。
  - `check_page` / `finalize` 新增两个关键字参数：
    - `quotes_allowed`：缺省 False，调用方确认受众只有学生本人才传 True；
    - `quote_verdicts`：原文核对结果，没有结果算不通过。
  - 新增七个阻断编码：`unmarked_quote`、`quote_placeholder`、`quote_too_long`、`too_many_quotes`、`quote_not_cited`、`quote_unverified`、`quote_audience`。
  - 现有第 0–6 条不放宽：
    - 第 0 条按含占位的整句比对，带占位的短语也要人审进表；
    - 第 2–6 条只查占位以外的文字；
    - 禁用推断查全文，引语一起查。
  - 固定衔接短语表加载时拒收带引号的短语。阻断记录的片段不含引语文字。
- **原文核对**（新文件 `services/learning/learning/mastery/quotes.py`）：
  - `matches` 实现逐字口径：NFKC 规范化、去空白，再做连续子串比对，引语长度 1–30 码点。
  - `verify(conn, 学生, 引语)` 以 `starbuddy_learning` 身份只读查库，一次最多 8 段。只有同时满足下面几条才通过：
    - 证据属于该生，种类是 `response_text`，通道是 `keypad`；
    - 判断属于该生，且学习服务读得到；
    - 证据在判断的 `evidence_ids` 里。
  - 所引判断有未结的复核任务 → `disputed`；文字对不上 → `no_match`；其余情形一律 `not_found`。
  - `page_verdicts` 把一页的核对结果整理成 `check_page` 要的形状；超出上限时整页按不通过，不查库。
- **洞察服务**：首份报告、周报的检查调用传 `quotes_allowed=audience == "student"`。档案页由学生、家长共用，保持缺省 False。
- **输出不变**：用 main 的副本和本分支各生成学生版、家长版的首份报告与周报，外加一份被阻断的报告，比对哈希相同。已有的一致性、报告、周报、月计划单测照常通过。

## 与 spec 的差异
1. **学习服务的内部接口没有加**（spec 第二节第 4 条）。起草时我写了“这个接口不在契约里（与验证任务接口同类）”，这是错的：
   `/internal/v1/verification` 定义在 `packages/contracts/openapi/learning-internal.v1.yaml` 里，加 `/internal/v1/quote/verify` 就是契约变更，属必停。
   所以本期：
   - 学习服务只做查库核对的函数，不开接口；
   - 洞察服务不实现 HTTP 核对源，也就没有核对结果，按“没有结果算不通过”处理。
   效果与 spec 的 fail closed 相同；本期也没有任何地方产生引语。接口随第一个生成引语的功能另起，见待人确认 Q1。
2. **“只认键盘输入”收得比 spec 严**：
   - spec 写的是只认 `response_text`，但库里 `response_text` 也可能来自拍照或手写板通道（`channel` 为 `photo` / `canvas`）。实现另加了 `channel = keypad`，通道为空的旧数据也不认。
   - 另外，库约束 `evidence_event_keypad_kind` 本来就不允许 OCR / ASR 证据标键盘通道，所以“种类”条件只是第二道防线，变异测试里没有单列这一条。
3. **“数据有误”异议**：学习服务没有 `insight.dispute` 的读权限，实现改为看所引判断有没有未结的复核任务（`platform.review_task`，`source_kind = claim`）。
   “数据有误”的异议正是经 `platform.enqueue_claim_dispute` 进这个队列。复核结案后引语可以再次通过。
4. **洞察服务的变异**：spec 写的是“HTTP 实现超时 → 不通过”，因第 1 条没有 HTTP 实现，改为两条接线变异：报告、周报的家长版也允许引语。

## 测试
- 新增 `services/learning/tests/test_mastery_quotes.py`（纯函数，66 条）：
  - 每个新编码的正例与反例；
  - spec 列出的引号逐个测，引号表写死在测试里，不从实现取；
  - 逐字口径的正反例；
  - 性质测试（固定种子随机 400 轮）：随机连续子串都通过，替换、插入、删除一个字符后都不通过；
  - 长度上限测试：原文里确有 31 字也不收。
- 新增 `services/learning/tests/test_mastery_quotes_db.py`（库上 6 条）：
  - 通过与 `no_match`；
  - `not_found` 统一口径：拍照、语音、手写板、无通道、不在依据里、过期判断、别的学生的判断、写进本生判断的别的学生的证据、非 UUID；
  - `disputed` 与结案后恢复；
  - 只读（核对前后表行数不变）与上限。
- 新增 `services/insight/tests/test_quote_guard.py`：报告、周报按受众传参；接线口径（学生版没有核对结果也被挡）。
- 变异测试：
  - `services/learning/scripts/mastery_mutations.py` 纯函数组加 12 条、库上组加 6 条；
  - `services/insight/tests/mutations.sh` 加 2 条。
  - 第一次跑漏检了一条（“逐字比较不限长度”），已补测试。

## 待人确认
- **Q1（必停：契约）**：洞察服务要核对原文，需要在 `learning-internal.v1.yaml` 追加 `POST /internal/v1/quote/verify`。拟定的接口：
  - 洞察服务令牌；
  - 请求：学生 + 最多 8 段 `{ key, claim_id, evidence_id, text }`；
  - 响应：每段一个编码，不回原文与位置；
  - 日志不记引语。
  建议随第一个生成引语的功能（模型衔接语或讲评课）一起做，PR 首行写 `[NEEDS-HUMAN]`。
- **Q2**：档案页现在由学生、家长共用一个视图，所以永远不能带引语。将来如果要让学生的档案页引用原话，得先把学生视图和家长视图分开编译。
- **Q3**：引语长度与条数（30 码点、每节 1 段、一次核对 8 段）仍是默认值，等 SIE 负责人与教研定数字。改数字只是改常量，不涉及格式版本。
- 已知局限（spec 第三节）：不加引号的转述（如“你说你学不会”）确定性规则查不出，现在由固定短语表兜底；以后要加模型辅助检查，只能追加阻断。
