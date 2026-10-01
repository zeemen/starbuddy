# 洞察服务（services/insight）

SIE-02b 第一步（`specs/sie-02b-claims-first-report/spec.md`，待人确认 #1–#7 按默认）：判断账本的唯一写入方（ADR 13 草稿）。
本步只做服务本体；BFF 读接口与开发演示页是第二步（见 lessons.md“第二步计划”）。实现记录、偏差与待人确认见同 spec 目录的 `lessons.md`。
SIE-03 PR②（`specs/sie-03-dispute-reconcile/spec.md`）：异议三路、考前预测与对账卡、触发规则 v1、导出与删除的服务端编排；
库层依据是迁移草稿 0032–0035（待人执行），偏离与契约差异见 `specs/sie-03-dispute-reconcile/lessons.md`“PR② 洞察服务”。

- 数据库只以 `starbuddy_insight` 连接：只写 `insight.claim`（0010 的触发器校验状态机、原因白名单、模板、可见范围与有效期上限、依据归属，
  历史由属主为 authz 的触发器追加）与 0032–0034 授予的 `insight.dispute`（只改状态与验证任务引用）、`insight.intervention_log`、
  `insight.exam_prediction`、`insight.exam_reconciliation`；新建异议只经 `insight.raise_dispute`、进复核队列只经
  `platform.enqueue_claim_dispute`；读 platform 推断与事实只用 0028 授予的列（不读证据原文），不写任何 platform 表。
- 预测冻结（0034）与到期删除（0035）的库函数只授 `starbuddy_scheduler`：由 `insight/scheduled.py` 以调度器自己的连接调用
  （登录角色只继承 scheduler，断言同上），不走洞察服务的连接池。
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
| `insight/jobs.py` | 服务内接口（不在契约里、不对外开放）：`sync_mastery`、`recompute_metrics`、`sweep`、`first_report`（给第二步的 BFF）；SIE-03 的 `settle_fact_corrections`、`confirm_data`、`sweep_disputes`、`dispute_metrics`、`reconcile_exam`、`evaluate_triggers`、`dispatch_triggers`，以及调度器入口 `freeze_due_predictions`、`execute_due_erasures`；冲刺 5-H 的每周任务 `weekly_reports` |
| `insight/weekly.py`、`insight/store_weekly.py` | 冲刺 5-H 跨科周报 v0（迁移 0036）：学生版成长手记与家长版周报 = 固定段落 + 衔接短语占位 + 判断渲染句（每句带 claim_id），客观栏来源 objective；复用首份报告的一致性检查与禁用推断词表，另查按受众的排除词（家长版：同伴比较、排名、层级标签；学生版：家长问卷与评价），任一不过记 withheld；写 `insight.weekly_report`（只追加），生成后按 `config/weekly.synthetic.json` 抽检进统一复核队列（不阻断） |
| `insight/sie03_policy.py` | SIE-03 配置（`config/sie03.synthetic.json`）：验证题道数与时限、预测区间参数、对账可见范围、触发规则参数（`params_version`）、删除回执范围与全部占位文案；合成占位只允许 test，未签字不下发触发动作；占位文案与编码加载时过禁用推断词表（命中即拒绝加载） |
| `insight/dispute.py` | 异议三路的纯函数：流转表（与 0032 逐行一致）、下一步编码、事实引用、“我其实会”的验证结论（supported / not_supported / conflict / pending）、验证任务接口与接口桩 `StubVerificationScheduler`（KGE-04 第二步前，题目引用带 `stub-verify` 前缀，不是真实题目） |
| `insight/predict.py` | 预测区间（范围节点 × 掌握 × 权重 × 题型，向先验收缩，证据少则宽，80% 区间）与对账（区间内外、偏差节点、契约四方向；高于区间不出卡）、对账句子检查（与 0034 守卫同一条件）；`MasterySource` 接口（缺省没有掌握行 → 证据不足） |
| `insight/triggers.py` | 触发规则 v1 的纯函数：提醒、证据化鼓励、降负（连续中断、连错后退出增多）；只引用成立 / 假设、未过期的判断，去重 |
| `insight/store_sie03.py` | SIE-03 数据库层：提出异议（库函数再核提出人 + 洞察服务核对身份一致，i_know 同事务转待验证并登记验证任务）、事实更正后撤回相关判断并重算、异议清理与指标、预测、对账卡生成与读取、触发建议与下发 |
| `insight/guardian_requests.py` | `insight.export` / `insight.erase` 的接口与形状映射；0035 的库函数只授 BFF 会话，缺省实现不接线（501） |
| `insight/scheduled.py` | 调度器入口：`freeze_due_predictions`、`execute_due_erasures`（`starbuddy_scheduler` 连接，角色断言） |
| `insight/shared.py` | 只读引用学习服务的 `learning.mastery.consistency`（一致性检查规则引擎）与认知层次；禁用推断词表经同一模块取 `docs/design/sie/sie_lexicon.py` |
| `insight/service/` | FastAPI（`POST /v0/<工具名>`）：`config`（环境变量）、`contract`（契约加载与严格校验）、`tools`（唯一实现层）、`app`（鉴权、Host 校验、请求体限制） |

## 契约工具（`packages/contracts/openapi/insight-service.v0.9.yaml`）
| 工具 | 调用方 | 行为 |
|---|---|---|
| `insight.claims` | 编排层 | agent 可见的非终态、未到期、模板已审核的判断摘要；按学科、对象类型、状态、条数过滤 |
| `insight.context` | 编排层 | 上下文包：节点判断 → `inner.g3.node_mastery`（门槛编码），中圈指标 → `middle.g5/g7.*`（数值；自评校准另给偏差方向）；条目按字段字典核对来源与可见范围；`token_budget` 按每条约 25 token 截断；`statements` 暂为空 |
| `insight.explain` | BFF（学生本人 / 家长）、编排层 | 结论（模板渲染）、置信度分档、证据链（证据 ID、时间、来源、等级、节点）、到期时间；依据的算法版本进 `source_version`。前端受众看不到的判断与“不存在”同一个 404；BFF 调用先经 `ActorVerifier` 核对授权（缺省一律不通过，见 lessons 待人确认） |
| `insight.record` | BFF、编排层 | 契约严格校验后回 501：洞察服务没有可用的写入路径（lessons） |
| `insight.dispute` | BFF（学生本人 / 家长） | 先经 `ActorVerifier` 核对 actor（家长含 link_id；缺省不通过，与 explain 同口径），再按与库函数同条件预检判断可见性；三路异议：`i_know` → 判断转待验证、登记 2 道验证任务（接口桩）、`verify_by` = 提出后 24 小时；`one_off_slip`（只学生本人）→ 只记自述、不改判断；`data_error` → 由判断推出原始事实（考试记录，或证据唯一对上的作答）、进复核队列、返回 `fact_ref`（原始事实的内部引用，BFF 不得转发给前端）。响应的 `evidence_ids` 是被异议判断的证据。提出人由库函数按关系核对，身份与 actor 不一致、判断不属于或不可见都按同一个 404 |
| `insight.predict` | 编排层 | 找学生 / 家长录入的考试安排（`mock` 对应一模、二模），按合成掌握行估 80% 区间，写考试类判断（只给 agent）与预测；考试当天零点起返回原结果不再重算；`frozen_at` 未冻结时是预定冻结时间；满分超过 150（契约响应上限）回 400；没有证据回 409 |
| `insight.reconcile` | 编排层 | 只读对账卡（成绩或考试安排的 ID）；陈述只由判断渲染；卡片由 `jobs.reconcile_exam` 在成绩上传后生成 |
| `insight.export` / `insight.erase` | BFF（家长） | 经 `GuardianRequests`；缺省不接线回 501（0035 的库函数只授 BFF 会话，lessons 待人确认） |

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
| `INSIGHT_SIE03` | SIE-03 配置；缺省 `config/sie03.synthetic.json`（合成占位，只允许 test）。**非 test 环境不设它（或指向合成占位）会拒绝启动**；验证任务接口桩也只在 test 环境缺省启用，其他环境没注入真实实现时“我其实会”回 501 |
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
- SIE-03（`tests/test_sie03_units.py`、`test_sie03_db.py`，夹具 `tests/sie03_seed.py`）：流转表与编码集合与迁移、契约一致；
  禁用推断阻断（占位文案、编码、对账判断渲染）；异议三路端到端与身份核对；事实更正后相关判断 100% 撤回并重算；预测区间随证据变窄、
  冻结后库层拒绝修改；对账卡四方向与每句判断 ID；触发只写建议、未签字不下发；导出与删除经 0035 库函数（测试里以 BFF 角色调用）
  映射成契约形状，删除执行后该学生零行、别的学生不受影响；
- 变异（`tests/mutations.sh`，94 条，含 SIE-03 的 44 条）：全部须被发现。
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
