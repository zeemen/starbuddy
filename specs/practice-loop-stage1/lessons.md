# 阶段 1 · 1 月 错题再练（闭环）lessons

## PR 1 + PR 2：纯函数与配置、学习服务读取层与内部接口（2026-10-05）

### 做了什么
- `services/learning/learning/practice/`：
  - `config.py`：spec 第 7 节配置位与合成占位；配置不合法拒绝启动，未签字配置在 test / dev 以外接口回 501。
  - `queue.py`：错题判定、条目（学生 × 学科 × 节点 × 认知层次，读时计算，不新增表）、闭环、轮次、hint_followup 条目、`retest_due`、`queue_cap`。
  - `select.py`：候选按同节点同层次 → 非原题 → 版权不受限 → 已准入 → 不在排除窗口 → 客观题过滤；不同题目族优先，只有同族时标 `same_family_only`；组装 `GenerateVariationRequest` 并核对适配器返回；错因只传分类表中非敏感编码，分类表读不到时不传并计数。
  - `ticket.py`：HMAC-SHA256 票据（常量时间验签、过期 410、跨学生 / 跨学科 403）。
  - `store.py`、`sources.py`（知识服务 item.similar / item.search，请求与响应都按契约校验，不带学生标识）、`presenter.py`（只在 test 的合成题面）、`service.py`、`answer.py`、`api.py`（三个内部 POST，只认 BFF 令牌）。
- 作答只经 `IngestService.ingest("grade", …)`，服务端写死 `purpose = practice`、`channel = keypad`、`hint_level_used = 0`；返回只有 verdict、条目状态与 `pending_review`，不含掌握、置信度、证据 ID、错因。
- 内部接口定义 `services/learning/openapi/practice-internal.v0.yaml`（沿 SIE-02a 先例，不进 packages/contracts）；重放复用既有 `GET /internal/v1/replay/grade/{key}`。
- `make practice-mutation`（纯函数组、库上组）并入 `make check`。

### 测试
`make learning-test` 2933 passed；`practice-mutation` 纯函数 35/35、库上 33/33；新增并发用例连跑 3 次稳定。

### 审查意见处理（按用户“按默认建议处理审查意见”）
- 阻断 1（安全）：判分待复核或证据等级非 A/B 时条目状态不变，同一张票据可换新键重复作答（重复写入、重复计量、先错后对刷闭环）。改为锁内检查票据签发后这道题是否已有作答，有则 409 `TICKET_USED`。
- 阻断 2（契约）：会话级咨询锁的连接取自业务池，锁内又向同池取连接，并发到池上限即互相等死。改为独立锁池（大小 8），再练模块取连接一律 5 秒上限，超时 503 BUSY。
- 需人确认 H1（用户按默认确认）：已提交的幂等键只有 succeeded，或 pending 且租约未过期，才放过过期票据并跳过条目确认；failed 与租约过期的照常验票。
- 其余：拿锁后再读键状态；令牌与密钥字段不进 repr；无原题的 hint_followup 不调适配器（`no_source_item`）；待复核返回 `pending_review`；只出客观题（`no_objective_candidate`）；知识服务请求发送前自检；排除列表原题放第一位；计数键 `ticket_invalid`；hint_followup 闭环与 KGE-04 关闭口径对拍。

### 经验
- 会话级咨询锁的连接要握到判分结束，不能取自业务池。
- 只看条目状态拦不住“待复核”这类不改状态的判分结果，要另按“票据签发后已作答”拦。
- 幂等表里 failed 与租约过期的 pending 不能当作“已提交”放过票据检查。
- 再练的闭环口径与 KGE-04 的计入口径（题目族占用）不是一回事，凡“闭环”类展示都要对拍。
- 本仓不能装 hypothesis，性质测试沿用固定种子随机序列。
- 库上让 retest_task 到期：用 KGE 自然排出复测轮再拨快练习时钟，不要手工插 scheduled 行。
- `call_adapter` 把适配器 501 归成 502，要用 `retryable` 区分“不可用”与“可重试”。

### 与 spec 的差异
1. 列表页的可用性直接读库（kge.item_tag + 平台准入，口径同 item.search），不逐条调知识服务；next 仍走知识服务与适配器。
2. item.search 在“过滤后不同族不够”时才补（原写法在相似题全未准入时不会补搜）。
3. 不新建重放接口，复用既有 grade 重放；服务目录实际 3 条路由。
4. answer / next 请求多带 `student_ref`、`subject`，用于核对跨学生、跨学科。
5. 闭环额外要求证据等级 A / B 且不在申诉中。
6. `occurred_at` 取票据签发时间，重试指纹不变；BFF 换新票据用同键重试会 409。
7. 同键已成功或仍在处理中时放行过期票据，拿回原结果。
8. 取连接上限只覆盖再练模块；`IngestService` 与掌握重算内部仍是不带上限的 `learning_tx`（共用代码，未动），锁池独立后不再与锁连接互相等死。

### 待人确认
1. **闭环口径与 KGE-04 不一致**（库上实测）：提示后做对 fam-a 的一道题，再独立做对同族另一道，再练判闭环（只要求“不是原题”），KGE-04 记 `repeat_family` 不计入、hint_followup 任务一直开着；列表显示“已做对”而任务不关。选项：再练改为要求“另一族”才闭环（只改再练），或接受差异。多出现在同族兜底时。
2. 题目判分方式的真实来源：平台题目表与契约都没有该字段，目前只在合成题面标注，随 `present_item` 契约一起定。
3. 真实题面接入前 `rubric_version` 由题面提供者给出，来源随 present_item 契约定。
4. 错因编码以 error-taxonomy v0.9 草稿为准；契约示例中的 `zhixing:locate` 不在映射表。
5. 轮次口径：开着的条目里同单元任何错题都算“一轮做错”，读时计算拿不到作答来源。
6. 再练依赖 `STARBUDDY_ALLOW_CANDIDATE_CONTRACT=1`，未开时拒绝启动（同诊断）。
7. 锁池大小 8、取连接上限 5 秒为占位值，按部署并发定。
8. 记给 PR 3：BFF 先计量再调学习服务，票据过期、409 等被拒作答也会扣次（留存态每天 5 次）；票据是否只留在 BFF（载荷含学生内部 ID，无密钥轮换 kid）；同一作答占 2 个以上连接，连接池上限与限流一起评估。
9. 记给 PR 4：列表文案（如“这道题上次没做对”）是否算规则 11 所指的关于学生的陈述。
