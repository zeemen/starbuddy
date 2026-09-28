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
