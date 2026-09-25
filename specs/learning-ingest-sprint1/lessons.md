# 学习服务 · 冲刺 1-C：实现记录与待人确认

按 spec“待人确认”八项的默认方案实现（用户 2026-09-25 指示“按默认方案开始实现”）：
1. asyncpg，不用 psycopg 3；
2. 内部接口放在 `packages/contracts/openapi/learning-internal.v0.yaml`（方案 a，必停项）；
3. 两张不分区的表做幂等（迁移 `0007`，必停项）；
4. 不写 `review_task`；
5. 租约 60 秒；
6. 摘要只给 ID、verdict、semantics、human_review_required、跨科大类；
7. 保留期暂不实现（见下）；
8. 只检查内容已登记，不自动登记。

## 待人确认
1. **迁移 `0007` 由人执行**：新增 `platform.ingest_request`、`platform.evidence_registry` 两张表，以及它们的 RLS 和授权。已完成的测试：
   - `db/tests/50_ingest_idempotency.sql` 有 15 条正反向断言；
   - `db/tests/mutations.sh` 新增 5 个变异，全部能被发现。
2. **内部接口 OpenAPI 放进了 `packages/contracts`**（必停项）。它的请求体直接引用 `plugin-api.v0.9.yaml` 的 `GradeRequest` 和 `DiagnoseRequest`，没有另起一份。
3. **certifi 是 MPL-2.0**：它是 httpx 的传递依赖，只提供 CA 证书包。MPL 是文件级弱著佐权，不修改就没有义务。是否接受？
4. **幂等记录的保留期**（spec #7）：默认 30 天，但本冲刺没有实现清理任务。它和证据表的保留期一起，等 ADR 09 与合规顾问意见定了再做。
5. **attempt 必须能区分**：diagnose 里两道作答的题目和作答时间都相同时，直接返回 422。原因是平台靠 item_ref + occurred_at 把作答和作答证据对上（契约守护 N6）。如果以后确有同一时刻的重复作答，需要在契约里给证据加 `attempt_id` 引用，属于契约变更。
6. **数据库约束拦下的结果**一律返回 502 `ADAPTER_RESULT_REJECTED`，detail 只写 SQLSTATE 和约束名。数据库报错原文会带出整行数据，所以不转发。例如“总分超过满分”，契约把它交给平台侧校验，这里就是由数据库约束拦下的。
7. **重放的摘要键顺序**会和第一次不同：jsonb 不保留键顺序。内容相同，BFF 不应按字节比较。
8. **适配器在租约内没有返回时**，新请求会接管并再调一次适配器，所以适配器可能被调用两次。
   - 结果相同：适配器按 key 和请求体确定性派生证据 ID，平台也沿用同一个 attempt_id。
   - 不会重复扣次：计量不在这里。
   - 迟到的那一次落库时会发现租约已不归自己，于是放弃写入。

## 实现中的发现
- **asyncpg 按 SQL 里的类型转换推断参数类型**：写 `$1::timestamptz` 时要求传 datetime 对象。现在一律写成 `$1::text::timestamptz` 或 `$1::text::numeric`，由数据库解析文本。
- **数据库报错会泄露数据**：Postgres 的约束报错带 “Failing row contains (...)”，里面可能有学生作答。所以落库异常只取 SQLSTATE 和约束名，日志里只记异常类名。有测试断言。
- **阿鸦适配器的 `persist.ts` 已删除**：端到端测试改为“学习服务 → 阿鸦适配器（真实 HTTP 进程）→ 平台库”，放在 `services/learning/tests/test_e2e_aya.py`。
  原阿鸦 e2e 里有一条断言：内容登记 1 / 959 / 721 / 721，重复执行也不重复登记。它迁到了同一个文件。
- **校验内部接口 OpenAPI**：用 openapi-spec-validator 校验过，合法，但没有接入 `make check`。`contract-test` 目前只检查 `plugin-api.v0.9.yaml`，接入需要改 `check_contract.py`，留给契约评审时一起做。
