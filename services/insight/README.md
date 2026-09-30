# 洞察服务（services/insight）

SIE-02b 第一步（`specs/sie-02b-claims-first-report/spec.md`，待人确认 #1–#7 按默认）：判断账本的唯一写入方（ADR 13 草稿）。
本步只做服务本体；BFF 读接口与开发演示页是第二步（见 lessons.md“第二步计划”）。实现记录、偏差与待人确认见同 spec 目录的 `lessons.md`。

- 数据库只以 `starbuddy_insight` 连接：只写 `insight.claim`（0010 的触发器校验状态机、原因白名单、模板、可见范围与有效期上限、依据归属，
  历史由属主为 authz 的触发器追加）；读 platform 推断与事实只用 0028 授予的列（不读证据原文），不写任何 platform 表。
- 不调模型、不联网；判断模板、报告标题、顶部提示语、衔接短语**全部是明显的占位**（必停项：人写人审后登记）；
  阈值与置信度分档是配置位，仓库里只有合成占位（**非签字值**），未签字配置下判断最高只到“假设”。

| 模块 | 说明 |
|---|---|
| `insight/claims.py` | 判断生成与生命周期（纯函数）：合成掌握行（KGE-04 spec 第 13 节的形状）→ 每“节点 × 层次”的“知识掌握”判断，门槛到迁移及以上再加“知识迁移”判断；假设 / 成立 / 待验证 / 推翻 / 撤回的规则见模块说明；白名单 `ALLOWED` 与 0010 `insight.change_allowed` 逐行一致（有测试） |
| `insight/metrics.py` | 中圈指标八项（SIE 第五章定义与最低样本）的纯函数计算；结果只写 `visibility = {agent}` 的 metric 判断，只到“假设” |
| `insight/render.py` | 判断 → 契约 `RenderedStatement`：模板渲染（`{变量}` 只收编码），渲染结果过禁用推断词表；待验证画虚线 |
| `insight/report.py` | 首份画像报告的结构化内容（学生版 / 家长版）：提示语 + 标题 + 分节（衔接短语 + 判断渲染句，每句带判断 ID）；渲染后跑陈述—判断一致性检查（复用 `learning.mastery.consistency`）与禁用推断检查，任一不过不出报告；`shadow_judgments` 导出影子评审比对的输入 |
| `insight/policy.py` | 判断策略配置（`config/claims.synthetic.json`）与报告检查词表（`config/report-check.synthetic.json`，格式同 KGE-04 档案页）的加载与校验 |
| `insight/store.py` | 数据库层：角色断言、按受众与可见范围读判断（与 0010 策略同一条件，洞察服务自己的 RLS 全放行所以这一道必需；中圈指标对前端一律不返回）、同一学生一个事务内“加锁 → 读现有判断与历史 → 生成操作 → 逐条写（每条带原因）”、中圈指标读取、到期扫描 |
| `insight/jobs.py` | 服务内接口（不在契约里、不对外开放）：`sync_mastery`、`recompute_metrics`、`sweep`、`first_report`（给第二步的 BFF） |
| `insight/shared.py` | 只读引用学习服务的 `learning.mastery.consistency`（一致性检查规则引擎）与认知层次；禁用推断词表经同一模块取 `docs/design/sie/sie_lexicon.py` |
| `insight/service/` | FastAPI（`POST /v0/<工具名>`）：`config`（环境变量）、`contract`（契约加载与严格校验）、`tools`（唯一实现层）、`app`（鉴权、Host 校验、请求体限制） |

## 契约工具（`packages/contracts/openapi/insight-service.v0.9.yaml`）
| 工具 | 调用方 | 行为 |
|---|---|---|
| `insight.claims` | 编排层 | agent 可见的非终态、未到期、模板已审核的判断摘要；按学科、对象类型、状态、条数过滤 |
| `insight.context` | 编排层 | 上下文包：节点判断 → `inner.g3.node_mastery`（门槛编码），中圈指标 → `middle.g5/g7.*`（数值；自评校准另给偏差方向）；条目按字段字典核对来源与可见范围；`token_budget` 按每条约 25 token 截断；`statements` 暂为空 |
| `insight.explain` | BFF（学生本人 / 家长）、编排层 | 结论（模板渲染）、置信度分档、证据链（证据 ID、时间、来源、等级、节点）、到期时间；依据的算法版本进 `source_version`。前端受众看不到的判断与“不存在”同一个 404；BFF 调用先经 `ActorVerifier` 核对授权（缺省一律不通过，见 lessons 待人确认） |
| `insight.record` | BFF、编排层 | 契约严格校验后回 501：洞察服务没有可用的写入路径（lessons） |
| `insight.dispute` / `predict` / `reconcile` / `export` / `erase` | 契约规定的调用方 | 501（冲刺 5 及以后） |

处理顺序与知识服务相同：令牌决定 caller（sha256 后常量时间比较）→ 调用方准入 → 501 → 契约版本 → 请求体 caller 与令牌一致 →
契约严格校验请求 → 执行 → 统一信封 → 契约校验响应（不合格回 500）。请求体限 64 KB，JSON 拒绝 NaN / Infinity 与重复键；
整个应用按 Host 允许列表校验。日志只记 request_id、caller、工具名、状态码、错误码、耗时。

## 配置与启动
| 环境变量 | 说明 |
|---|---|
| `INSIGHT_ENV` | `dev` 或 `test`；不连接 pilot 或生产（规则 9）。合成占位配置只允许 `test` |
| `INSIGHT_DATABASE_URL` | asyncpg 连接串。登录角色**只继承** `starbuddy_insight`（例：`CREATE ROLE insight_app LOGIN IN ROLE starbuddy_insight`，由人按 `db/bootstrap/roles.sql` 的约定创建）。连接后 `SET ROLE starbuddy_insight`、`TimeZone=Asia/Shanghai`；启动时断言不是超级用户、没有 BYPASSRLS / CREATEROLE / CREATEDB、不继承其他应用角色、不是 `pg_` 预置角色成员，否则拒绝启动（注入的连接池同样断言） |
| `INSIGHT_TOKEN_BFF` / `_ORCHESTRATOR` | 服务间令牌（≥ 32 字符、各不相同、至少一个）。学生与家长没有令牌（401） |
| `INSIGHT_POLICY` / `INSIGHT_REPORT_CHECK` | 判断策略与报告检查词表；缺省是 `config/` 下的合成占位（只允许 test） |
| `INSIGHT_HOST` / `INSIGHT_PORT` | 缺省 `127.0.0.1:8791`（契约 servers）；不接受 `0.0.0.0`、`::` 等通配地址（主机名与旧式写法先解析） |

```bash
cd services/insight && python3 -m venv .venv && .venv/bin/pip install --require-hashes -r requirements-dev.lock
make insight-test insight-mutation CONTRACT_STRICT=1      # 在仓库根目录：单测、性质测试、库上测试、契约严格校验、影子评审脚本、变异
INSIGHT_ENV=test INSIGHT_DATABASE_URL=... INSIGHT_TOKEN_ORCHESTRATOR=... .venv/bin/python -m insight.service
```

## 测试
- 单测与性质测试（`tests/test_claims.py`、`test_properties.py`、`test_metrics.py`、`test_report.py`、`test_policy.py`、`test_service_unit.py`）：
  生命周期白名单与 0010 逐行一致、不可逆流转、同输入两次逐字节相同、幂等、未签字只到假设、指标与手算一致、报告三类阻断各有用例；
- 库上测试（`tests/pginsight.py` 起一次性 Postgres，执行 bootstrap 角色与全部迁移、灌合成数据）：角色断言反例、洞察服务之外不能写判断、
  洞察服务不能写 platform / 历史 / 模板、0028 授权够用且读不到原文、可见范围过滤与 0010 的 BFF 行级策略一致、中圈指标库上手算、契约严格校验；
- 变异（`tests/mutations.sh`，50 条）：全部须被发现。
`make insight-test CONTRACT_STRICT=1` 时没有 Postgres 直接失败，非严格模式下库上测试跳过。

## 依赖与许可证（规则 10）
不引入新包：全部与知识服务、学习服务已登记的同版本（许可证已在 `services/knowledge/README.md` 核过）。

| 包 | 版本 | 许可证 | 用途 |
|---|---|---|---|
| fastapi / starlette / pydantic | 0.141.1 / 1.7.0 / 2.13.5 | MIT / BSD-3 / MIT | HTTP；传递依赖 pydantic-core、annotated-types、annotated-doc、typing-extensions、typing-inspection（MIT / PSF-2.0）、anyio（MIT）、idna（BSD-3） |
| uvicorn | 0.54.0 | BSD-3-Clause | 服务进程；传递依赖 click（BSD-3）、h11（MIT） |
| asyncpg | 0.31.0 | Apache-2.0 | 数据库连接（psycopg 3 为 LGPL-3.0，不引入） |
| jsonschema / referencing | 4.26.0 / 0.37.0 | MIT / MIT | 契约严格校验；传递依赖 jsonschema-specifications、rpds-py、attrs（MIT） |
| pyyaml | 6.0.3 | MIT | 契约、字段字典、禁用推断词表 |
| pytest（开发） | 9.1.1 | MIT | 测试；传递依赖 pluggy、iniconfig（MIT）、packaging（Apache-2.0 或 BSD-2）、Pygments（BSD-2） |
| httpx（开发） | 0.28.1 | BSD-3-Clause | 进程内 ASGI 测试客户端（不联网）；传递依赖 httpcore（BSD-3）、certifi（MPL-2.0，只作 CA 数据） |

供应链：`requirements.lock` / `requirements-dev.lock` 逐包带 sha256，安装一律 `pip install --require-hashes`。本步在离线环境里从
`services/knowledge` 的锁文件摘出同版本同哈希的条目（没有联网重新解析）；联网环境可用锁文件头部的 pip-compile 命令重新生成核对。
