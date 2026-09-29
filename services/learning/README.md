# 学习服务（services/learning）

平台里**唯一能写学习者模型**的服务（ADR 01 第 1 条）。冲刺 1-C 实现写入接口，规格见
`specs/learning-ingest-sprint1/spec.md`，实现记录与待人确认见同目录的 `lessons.md`。

| 接口 | 说明 |
|---|---|
| `POST /internal/v0/grade` | 请求体是契约 `GradeRequest`；调适配器判分，核对后写入 |
| `POST /internal/v0/diagnose` | 请求体是契约 `DiagnoseRequest`（冲刺 1 只支持带 attempts 的同步诊断） |
| `POST /internal/v1/grade`、`/internal/v1/diagnose` | 请求体是信封 `{request, ingest}`：`request` 为插件契约 1.0.0 候选版请求（需 `STARBUDDY_ALLOW_CANDIDATE_CONTRACT=1`，声明的 `contract_version` 必须与路径一致），`ingest` 为输入通道与用途；学习服务据此补齐证据的作答情境、等级、通道、地图版本（迁移 0017，specs/evidence-attributes-sprint1） |
| `GET /internal/v1/replay/{method}/{key}?student_ref=&usage_id=` | 只查不执行：200 原摘要；404 `REPLAY_NOT_FOUND` 并封存 key（`platform.replay_seal`，迁移 0016）；409 `BUSY` |

接口定义：`packages/contracts/openapi/learning-internal.v0.yaml`、`learning-internal.v1.yaml`（迁移期并存到 W6，specs/plugin-api-v1.0-step2）。只供 BFF 调用，服务间令牌鉴权。

部署注意（specs/plugin-api-v1.0-step2）：先执行迁移 0016 再上线本版本（启动时检查 `platform.replay_seal`，不存在就拒绝启动）；
重放接口的查询串带 `student_ref`、`usage_id`，运行 uvicorn 时加 `--no-access-log`，不经会记录 URL 的代理。

## 一次请求怎么走
1. 按契约校验请求；学科必须配置了适配器；作答的题目必须已在平台登记。
2. **幂等检查**（`platform.ingest_request`，不分区）：
   - 同 key、同请求体，已成功过：直接返回原摘要，响应头 `idempotent-replayed: true`。
   - 同 key、不同请求体：409。
   - 同 key 正在执行：等它完成。
   - 失败过，或持有者租约已过期：接管，沿用原 attempt_id。
3. 调适配器，把平台生成的 attempt_id 带过去。只在连接失败或上游 `retryable` 时重试一次。
4. **平台核对**（规则在共享库 `packages/contracts/python/starbuddy_contract/checks.py`，一致性测试也用它），任何一条不通过就整体拒绝、一行不写：
   - 契约 schema；
   - 证据 ID 闭合；
   - 学科一致；
   - 题目与能力点已登记；
   - 准入与效力以平台为准；
   - 作答与作答证据按 item_ref + occurred_at 一一对应。
5. **一个事务落库**，以 `starbuddy_learning` 身份写入：
   - 写 `evidence_event`、`attempt`、`grade_result`、`grade_dimension`、`error_attribution`、`skill_observation`。
   - 证据 ID 先登记到 `platform.evidence_registry`：同 ID 同内容就跳过，不同内容就拒绝。
   - 事务最后把幂等记录推进为 `succeeded`。
6. 返回平台摘要：只有 ID 与编码，不含作答、答案、维度分。

## 不做（冲刺 1）
计量（BFF 做，ADR 06）、复核任务入队（冲刺 2-G）、掌握估计（冲刺 4-C）、异步任务、查询接口。

## 常用命令
```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt   # 在本目录执行；含可编辑安装的契约共享库
make learning-test          # 在仓库根目录：单测 + 幂等与核对测试 + 端到端（需要本机 PostgreSQL 16 与 node）
LEARNING_ENV=dev LEARNING_DATABASE_URL=... LEARNING_SERVICE_TOKEN=... \
LEARNING_ADAPTER_AYA_URL=http://127.0.0.1:8787 LEARNING_ADAPTER_AYA_TOKEN=... .venv/bin/python -m learning
```
`LEARNING_ENV` 只能是 `dev` 或 `test`：冲刺 1 不连接 pilot 或生产（CLAUDE.md 规则 9）。数据库登录角色只能继承 `starbuddy_learning`。

## 依赖与许可证（规则 10）
| 包 | 版本 | 许可证 |
|---|---|---|
| fastapi | 0.141.1 | MIT |
| pydantic | 2.13.5 | MIT |
| uvicorn | 0.54.0 | BSD-3-Clause |
| asyncpg | 0.31.0 | Apache-2.0 |
| httpx | 0.28.1 | BSD-3-Clause |
| jsonschema | 4.26.0 | MIT |
| pyyaml | 6.0.3 | MIT |
| pytest、pytest-asyncio（开发） | 9.1.1、1.4.0 | MIT、Apache-2.0 |

传递依赖都是 MIT、BSD、Apache-2.0 或 PSF-2.0，只有一个例外：**certifi 是 MPL-2.0**。它随 httpx 引入，只提供 CA 证书包。MPL 是文件级弱著佐权，不修改它就没有义务；已列入待人确认。

数据库驱动没有用 psycopg 3，因为它是 LGPL-3.0（spec 待人确认 #1）。

## 审核台 v0 后端（`review_console/`，specs/release-gate-sprint4 冲刺 1 · PR 2）
与学习服务同目录、**不同入口、不同连接角色**（spec 待人确认 #10，KGE 代码位置 ADR 定了再迁）。只读写公共知识层 `kge.*`。

| 模块 | 说明 |
|---|---|
| `snapshot.py` / `diff.py` | 地图版本快照与差异（纯函数，逐字节确定）；基准为同学科已发布版本，没有时全部按新增 |
| `risk.py` | 风险分类（KGE 第四章）；判不出的一律高风险；结构性口径与库里 `kge.structural_diff`、`map_change_required_signers` 一致 |
| `store.py` | 两个连接池：`REVIEW_CONSOLE_KGE_DATABASE_URL`（只继承 `starbuddy_kge`：读、改草稿）、`REVIEW_CONSOLE_REVIEW_DATABASE_URL`（只继承 `starbuddy_review`：签字、发布、撤下）；启动时核对 |
| `app.py` | `/review/v0/*` 接口；`/review/v0/auth/login`（唯一不需要会话的接口）、`/auth/logout`、`/auth/me`；其余接口的 staff 身份由请求头 `x-review-session` 的会话令牌经 `platform.staff_session_resolve` 解析（迁移 0020，PR 4） |
| `__main__.py` | `python -m review_console record --subject aya [--format csv\|json]` 导出版本记录 |

签字（版本与变更单）、发布、改节点都要带上“看到的摘要”（`expected_digest`），与当前内容不同就拒绝（409）。

### 审核台部署要求（staging；由人配置，仓库里没有部署配置）
- 审核台后端只监听 127.0.0.1，只接受同机前端（apps/web 服务端）的请求；浏览器不直连。
- **登录限流放在部署层**（2026-09-29 用户决定）：库里只按账号锁定（连错 5 次锁 15 分钟），后端看不到用户来源，
  所以在 apps/web 前面的反向代理按来源 IP 限流。Next 的 server action 按请求头 `Next-Action` 分派、不看路径，
  登录 action 可以被提交到任意 `/review` 路径，所以**对 `/review` 下所有 POST（或所有带 `Next-Action` 头的请求）限流**，不能只限 `/review/login`。
  建议值：每个来源每分钟 10 次、突发 5 次，超出返回 429；另设全站登录总量上限，防止换来源轮流试。
  参考（nginx，只作示意，按实际网关改写）：

  ```nginx
  map $request_method $review_post_key { POST $binary_remote_addr; default ""; }  # 只限 POST（键为空不计数）
  limit_req_zone $review_post_key zone=review_login:10m rate=10r/m;
  location ^~ /review {
      limit_req zone=review_login burst=5 nodelay;
      limit_req_status 429;
      proxy_pass http://127.0.0.1:3000;
  }
  ```
- **数据库服务器日志不能记下口令、6 位码、令牌**（安全审查 PR 4 B-2）：登录函数的参数是明文。对审核台的签字登录角色（超级用户执行）：
  `ALTER ROLE <签字登录角色> SET log_statement = 'none';`、`SET log_min_duration_statement = -1;`、`SET log_parameter_max_length = 0;`、
  `SET log_parameter_max_length_on_error = 0;`。人工执行 `platform.staff_enroll` / `staff_reset` 时，先在本会话关掉语句日志，
  并用不写历史文件的 psql（`HISTFILE=/dev/null`），见迁移 0020 的用法注释。执行者须是超级用户或 BYPASSRLS 角色。
- **staging 库名不能以 `starbuddy_dev` 或 `starbuddy_test` 开头**：合成种子只认这两类库名，里面的合成凭据很好猜。
- 走 https；前端设 `STARBUDDY_ENV=staging`（会话 cookie 用 `__Host-sb_staff`、Secure）；不设 `STARBUDDY_DEV_AUTH`。
  `STARBUDDY_ENV` 误设成 dev / test 会让会话 cookie 失去 Secure 与 `__Host-` 前缀，上线检查要核对。
- 上线前验证：同一来源连续提交超过上限得到 429；向其他 /review 路径提交登录 action 同样 429；已登录用户的正常浏览不受影响（建议 GET 不限或放宽）。

## 模型路由骨架（`learning/model_router/`，specs/model-router-sprint1，冲刺 1-F）
- 所有模型调用只经 `ModelRouter.route(RouteRequest)`：调用方声明任务类型（T1–T9）与数据级别（L0–L3），不指定模型；返回 `model_version`（`model:<供应商>/<模型>@<快照>`）与 `source_version`（`prompt:<模板>@<版本>`）。
- 路由表 `config/model_routes.yaml`：仓库里只有 dev、test 两节与本地假供应商；staging、pilot、prod 由人按 `docs/compliance/vendors.md` 填写（必停项）。
  pilot、prod 启动校验：不出境、只用已批准的供应商、锁定快照、主备不同厂商或平台；运行时再按数据级别过滤一次（境外只在 dev、test、staging 接 L0）。
- 供应商地址与 Key 只写环境变量名（例如 `MODEL_FAKE_A_URL`），值由部署环境提供。调用记录只含元数据，默认写结构化日志（logger `starbuddy.model_router`）。
- `make model-router-mutation`：改坏出境过滤、启动校验、熔断等 16 处关键检查，断言测试能发现。
