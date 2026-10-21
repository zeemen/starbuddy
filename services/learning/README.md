# 学习服务（services/learning）

平台里**唯一能写学习者模型**的服务（ADR 01 第 1 条）。冲刺 1-C 实现写入接口，规格见
`specs/learning-ingest-sprint1/spec.md`，实现记录与待人确认见同目录的 `lessons.md`。

| 接口 | 说明 |
|---|---|
| `POST /internal/v0/grade`、`/internal/v0/diagnose` | **已下线**（LT-04 决定记录（再续）第 1 条）：一律 410（问题码 `INVALID_REQUEST`、`retryable: false`），不读请求体、不验令牌、不调适配器、不落库。v0 写下的结果仍可经下面的重放接口查回（只读保留） |
| `POST /internal/v1/grade`、`/internal/v1/diagnose` | 请求体是信封 `{request, ingest}`：`request` 为插件契约 1.0.0 请求（插件 v1.0 已于 2026-10-09 签字；声明的 `contract_version` 必须与路径一致）。learning-internal v1 于 2026-10-20 签字为 1.1.0（LT-04）：只认 BFF 服务令牌；写入前经迁移 0058 的 `platform.learning_meter_check` 核对 `usage_id` + 幂等键 + 学生 + 方法，只放行预留中（reserved）的预留，否则 409 `IDEMPOTENCY_KEY_SEALED`（同键已成功的重放不受影响）；`ingest` 只能是键盘通道、用途 practice（自报其他用途或证据等级、非键盘通道 400），学习服务据此补齐证据的作答情境、等级（键盘 A、申诉 C）、通道、地图版本（迁移 0017，specs/evidence-attributes-sprint1；`learning/meter.py`、`learning/attributes.py`） |
| `POST /internal/v1/plan` | 今日计划（冲刺 4-H，随 learning-internal v1 于 2026-10-20 签字；`learning/plan.py`）：按 BFF 给的在读学科读当前掌握快照、调各科适配器 `/plan`（插件契约 v1.0），能力点经已发布地图的 `kge.skill_link` 换成知识节点；只回任务类型、目标节点、优先级、预计分钟、`rationale_code`。非消耗类，不写任何表；某科不可用时 `unavailable` |
| `GET /internal/v1/replay/{method}/{key}?student_ref=&usage_id=` | 只查不执行：200 原摘要；404 `REPLAY_NOT_FOUND` 并封存 key（`platform.replay_seal`，迁移 0016）；409 `BUSY` |

接口定义：`packages/contracts/openapi/learning-internal.v1.yaml`；`learning-internal.v0.yaml` 只剩下线说明（两个写入接口 410）。只供 BFF 调用，服务间令牌鉴权。

**learning-internal v1 已签字（LT-04，2026-10-20）**：原开关 `LEARNING_INTERNAL_V1` 已删除，`/internal/v1` 的写入、`plan`、诊断与再练（含复测）
模块不再需要任何开关。写入防护是“服务身份限定 + 计量核对”：只有 BFF 服务令牌能写；写入前按计量账本核对（迁移 0058；启动时核对函数存在、
且当前登录角色有执行权，任一不满足拒绝启动）——直接写入只放行预留中的预留，再练、复测作答只放行题库自练计次（counted），诊断步骤作答按会话自己的
`usage_id` 与预留键、方法 diagnose 放行预留中、已结算，以及“会话在预留有效期内开始、之后预留才过期”（`started_in_time`，库里比较会话创建时间
与预留过期时间，学习服务拿不到时间；决定记录（再续）第 2 条，计量规则变更待人确认）；会话创建于预留过期之后的仍 409。
不通过 409 `IDEMPOTENCY_KEY_SEALED`（该幂等键无有效计量，不可写入）。用途与证据等级由学习服务推导。
v0 写入已下线（410）；插件契约 0.9.0 的写入核心只剩进程内调用（测试经 `tests/conftest.py` 的 `v0` 夹具）。插件契约 1.1.0（PLT-12 `present_item`）仍是候选版，
另需 `STARBUDDY_ALLOW_CANDIDATE_CONTRACT=1`。
测试里计量记录由 `tests/conftest.py` 的 `meter_provisioned` 经测试库的 `learningtest.provision_meter`（`tests/fixtures/meter_provision.sql`）代建：
只经超级用户（admin）连接池调用，夹具函数不授给 `starbuddy_learning`；夹具只能在 `starbuddy_dev*` / `starbuddy_test*` 库执行，迁移后的库里不应有
`learningtest`（`db/tests/79zf` 断言）。核对本身是真实函数；测核对的用例标 `real_meter`。变异：`make internal-v1-mutation`。

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
7. **掌握重算**（KGE-04 第二步，`learning/mastery/`；只在配了 `LEARNING_MASTERY_CONFIG_<SUBJECT>` 的学科）：落库事务提交后另起事务，
   同步重算这次作答涉及的“节点 × 认知层次”单元，写节点键的 `mastery_estimate` 与 `retest_task`（迁移 0037）。失败只记计数与单元 ID，
   接口照常返回；幂等重放不触发。对账入口：`python -m learning.jobs mastery-sweep [--subject aya] [--now <ISO>]`（重算、衰减、任务缺补多撤、输出计数）。

**掌握读取与验证任务**（learning-internal 1.1.0，KGE-04 第二步 PR 3，`learning/mastery_routes.py`、`learning/mastery/api.py`）：
- `POST /internal/v1/mastery/get`、`/mastery/explain`：只认编排层令牌 `LEARNING_ORCHESTRATOR_CLIENT_TOKEN`（BFF、洞察令牌 401），`context.caller` 必须是 `orchestrator`；形状照搬知识服务 v0.9（响应按知识服务契约校验，
  不合格回 500）；只认当前已发布地图里的节点与层次；`star_lit` 按配置的 `star_level_rule`；证据链 `weight` 是重放时的更新强度，`hint_level` 封顶 5；
  门槛条件编码不外露。
- `POST /internal/v1/verification`：只接受 `LEARNING_INSIGHT_CLIENT_TOKEN`（洞察服务）；学生由洞察服务从异议记录取出后附带（学习服务读不到
  `insight.dispute`）；只建 `verify_self_report` 任务、不改掌握，按异议幂等，不返回题目；学科没有配置或单元不在当前地图里回 409 `MASTERY_UNAVAILABLE`。
- `POST /internal/v1/verification/cancel`：只认洞察服务令牌；洞察服务事务回滚后的补偿，按任务 ID + 学生撤销 open 的自述验证任务，幂等、不改掌握；不存在或不属于该学生 404 `TASK_NOT_FOUND`。

## 不做（冲刺 1）
计量（BFF 做，ADR 06）、复核任务入队（冲刺 2-G）、掌握估计（冲刺 4-C）、异步任务、查询接口。

## 常用命令
```bash
python3 -m venv .venv && .venv/bin/pip install --require-hashes -r requirements-dev.lock   # 在本目录执行；带哈希的锁文件
.venv/bin/pip install --no-deps -e ../../packages/contracts/python   # 契约共享库（本地包，不在锁里；它的依赖已在锁里）
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
| rfc3339-validator、six（契约共享库的依赖，见 `packages/contracts/python/pyproject.toml`） | 0.1.4、1.17.0 | MIT、MIT |

传递依赖都是 MIT、BSD、Apache-2.0 或 PSF-2.0，只有一个例外：**certifi 是 MPL-2.0**。它随 httpx 引入，只提供 CA 证书包。MPL 是文件级弱著佐权，不修改它就没有义务；已列入待人确认。

**锁文件**（W13 技术债 T06，specs/tech-debt-batch1）：`requirements.lock`（运行）与 `requirements-dev.lock`（运行 + pytest）由 `requirements*.txt`
用 pip-compile（pip-tools 7.6.1，BSD-3-Clause，只在临时环境里用来生成，不是依赖）生成，逐包带 sha256，版本与生成时 `.venv` 的已装版本逐个相同，
没有新增依赖；共有包的版本与哈希与 services/insight、services/knowledge 的锁文件一致。契约共享库是仓库内的本地包，`--require-hashes` 不接受，
所以不在锁里、单独 `--no-deps` 安装。重新生成的命令写在锁文件头部。
锁里共 31 个包：新登记核对的只有 rfc3339-validator 与 six（上表；原先随契约共享库装入、未在本表列出），pytest-asyncio 原表已有，
其余 28 个与 insight、knowledge 锁文件里的同名包版本、哈希一致，许可证见上段。
已知未锁：安装契约共享库时 pip 在构建隔离环境里现拉 setuptools（pyproject 的 build-system 要求）；要锁需把 setuptools 加进锁文件并改用
`--no-build-isolation`，属于新增锁定项，待人定。

## 镜像（dev / staging）
`Dockerfile`（W13 技术债 T07）：只用于 dev / staging，不部署生产。在仓库根目录构建：`docker build -f services/learning/Dockerfile -t starbuddy-learning:dev .`
（`Dockerfile.dockerignore` 白名单只放进要用的路径）。第三方依赖从 `requirements.lock` 带哈希安装；契约共享库**非可编辑安装**，
它按自己文件的位置找契约 YAML（site-packages 往上两级的 `openapi/`），所以装完由 `scripts/place_contract_yaml.py --source packages/contracts/openapi`
原样放置并逐份核对（不改共享库；让共享库随包携带 YAML 或读显式路径要改 packages/contracts，属于必停项）。
启动测试 `tests/test_noneditable_install.py`（随 learning-test）：摆出非可编辑安装的目录布局，断言不放契约时启动即失败、放置后启动通过。
基础镜像未固定摘要、服务只监听 127.0.0.1 两点见 Dockerfile 头部注释。

数据库驱动没有用 psycopg 3，因为它是 LGPL-3.0（spec 待人确认 #1）。

## 审核台 v0 后端（`review_console/`，specs/release-gate-sprint4 冲刺 1 · PR 2）
与学习服务同目录、**不同入口、不同连接角色**（spec 待人确认 #10，KGE 代码位置 ADR 定了再迁）。只读写公共知识层 `kge.*`。

| 模块 | 说明 |
|---|---|
| `snapshot.py` / `diff.py` | 地图版本快照与差异（纯函数，逐字节确定）；基准为同学科已发布版本，没有时全部按新增 |
| `risk.py` | 风险分类（KGE 第四章）；判不出的一律高风险；结构性口径与库里 `kge.structural_diff`、`map_change_required_signers` 一致 |
| `store.py` | 两个连接池：`REVIEW_CONSOLE_KGE_DATABASE_URL`（只继承 `starbuddy_kge`：读、改草稿）、`REVIEW_CONSOLE_REVIEW_DATABASE_URL`（只继承 `starbuddy_review`：签字、发布、撤下）；启动时核对 |
| `app.py` | `/review/v0/*` 接口；`/review/v0/auth/login`（唯一不需要会话的接口）、`/auth/logout`、`/auth/me`；其余接口的 staff 身份由请求头 `x-review-session` 的会话令牌经 `platform.staff_session_resolve` 解析（迁移 0020，PR 4） |
| `__main__.py` | `python -m review_console record --subject aya [--format csv\|json]` 导出版本记录（冲刺 3 起含页面行：`line = page`、`page_id`、`page_commit`；冲刺 4 起含考情行：`line = policy`、`policy_object`、`policy_object_id`、`city`、`cohort`、`policy_version`，来自 `platform.policy_signoff_record()`（迁移 0040）；考情行的 `version` 为空（只给地图版本号），方案版本号在 `policy_version`；`risk_level` 固定为 `high`；考情行不分学科，每个学科的导出都带同一批，跨学科汇总须按 `line = policy` 去重；新列只追加在末尾）；`reconcile-pages` 编译页面对账（见下） |
| `pages.py` | 编译页面对账（纯函数）：托管平台合并记录 × `kge.content_review` 页面审核记录（迁移 0038）× staff 对应表；`GET /review/v0/pages` 与命令行共用 |

签字（版本与变更单）、发布、改节点都要带上“看到的摘要”（`expected_digest`），与当前内容不同就拒绝（409）。

### 编译页面对账（specs/release-gate-sprint4 冲刺 3 段，第 6 节）
页面在页面仓库托管平台的合并请求里审核（ADR 12）；本仓不连托管平台。审核台这一侧做三件事：
1. **页面审核记录**：每名审核人对“页面 × 合并请求的头提交”签一次（`kge.sign_page_review`，签字身份，迁移 0038），记页面 ID、40 位提交号、风险与签字人；
   `page_commit` 的口径统一为**合并请求的头提交**（审核人批准时看到的提交），合并进主分支的合并提交由合并记录给出，对账报告逐页对照列出两者；
   同一账号对同一页面同一提交只能签一次；同一页面同一提交的后续签字风险级别必须与第一条一致。签字入口见下文“冲刺 4 补全”；
2. **对账**：`make page-reconcile-test` 用合成文件跑验收；实际执行：
   ```bash
   .venv/bin/python -m review_console reconcile-pages --export <合并记录.json> --staff-map <对应表.json> --db --out <报告.json>
   # 或 --records <审核记录.json>（离线）；退出码 0 通过、1 有失败（报告照常写出）、2 输入不对
   ```
   规则（每个页面只按它在主分支上**最近一次合并**判定，更早的合并列在 `superseded`、不再判定）：
   - 需要批准（中、高风险两人；与编译器 `window.REVIEWERS` 相等，测试核对）的页面在审核记录里没有 → `missing_review`；
   - 批准人（按对应表映射到 staff）与审核记录里批准的 staff 不是同一组 → `approver_mismatch`；批准人不在对应表 → `approver_unmapped`；
     人数不够（风险取两边较高的）→ `insufficient_approvals`；审核记录有驳回 → `rejected_in_review`；
   - **低风险**不需要事前签字：没有审核记录、或托管平台有批准人而审核台没有记录，都不算失败；审核台有记录时才比对批准人；
   - 孤儿审核记录（页面提交不是导出里任何合并请求的头提交）→ `no_merge`，只有该页面仍在本次导出范围内、且它最新的合并请求
     （有待审的取待审的，否则取最近一次合并）的头提交上还没有批准记录时才扣页（`blocking: true`）；对最新头提交重新签字即解除。
     导出范围外的旧孤儿只列出，不扣页。
   报告只含页面 ID、合并请求编号、头提交与合并提交、计数，不含用户名与账号。`held_pages` 交给编译器与快照导出
   （`python -m knowledge.compiler ... --hold-report <报告.json>`、`python -m knowledge.compiler.snapshot ... --hold-report <报告.json> --previous <上一份快照>`；
   两者都必填，报告有效期缺省 24 小时，见 services/knowledge/README.md“页面对账”）：合并窗口跳过这些页面，快照导出沿用上一份快照里的旧版本，直到人处理、对账重新通过；
3. **“页面”入口**：`GET /review/v0/pages`（只读）列待审合并请求与已合并页面的对账状态，链到托管平台网页；配置见 `review_console/config.py`
   （`REVIEW_CONSOLE_PAGES_EXPORT`、`REVIEW_CONSOLE_PAGES_STAFF_MAP`、`REVIEW_CONSOLE_PAGES_MR_URL`，三项都设或都不设）。
   地址模板必须是 https，`{mr_id}` 只能出现一次且只能在路径里（前面是“/”，不能拼进主机名、顶级域或端口），不带查询串与片段。
   所有输入文件最大 10 MB，拒绝重复键与 NaN。

合并记录导出（`kge-pages-merge-export/1`，由人或同步任务从托管平台导出成这个格式；多出的字段一律拒收，不收标题、描述等自由文本）：
```json
{"format": "kge-pages-merge-export/1", "repository": "org/kge-pages", "branch": "main", "exported_at": "2026-10-03T08:00:00+08:00",
 "merge_requests": [
   {"mr_id": "101", "state": "merged", "risk": "high", "author": "托管平台用户名", "approvers": ["托管平台用户名"],
    "pages": ["MATH.FUNC.QUAD.VERTEX_FORM"], "head_commit": "<40 位小写十六进制>", "merge_commit": "<40 位>", "merged_at": "2026-10-02T10:00:00+08:00"},
   {"mr_id": "104", "state": "open", "risk": "medium", "author": "托管平台用户名", "approvers": [], "pages": ["…"], "head_commit": "<40 位>"}]}
```
- `author`（可选，冲刺 4 起）是合并请求的作者（提交方）的托管平台用户名，经下面的 staff 对应表映射到 staff 账号。**提交方不能自签**：
  审核台页面签字（`POST /review/v0/pages/sign`）与抽检记录（`POST /review/v0/spot-checks/sign`）在签字人等于作者时返回 409 `SELF_SIGN`；
  **判不出从严**：导出里没有 `author`、或作者不在对应表里，都判不出签字人是不是提交方，一律 409 `AUTHOR_UNKNOWN`、不让签
  （所以要在线签字，导出必须带作者，且作者要在对应表里；编译器机器人账号当作者时如何登记，待人确认）。
  对账里作者本人在托管平台批准、或作者映射到的 staff 在审核记录里批准 → `self_approval`、扣页；作者缺失时对账判不出，不因此扣页（旧导出照常对账）；
- `risk` 取合并请求上的风险标注（编译器的合并请求说明、CI 体检给出）；`pages` 是这次合并改到的页面 ID（节点 ID 写法）；
- `head_commit` 是审核人批准时看到的提交（审核记录按它签），`merge_commit` 是进主分支的提交；托管平台应设为“合并前必须基于最新主分支”，
  使合并后的页面内容与头提交相同（ADR 12 待人确认 #1 开通时一并核对）。

staff 对应表（`kge-pages-staff-map/1`，审核台配置，由人维护，不含学生数据；两个用户名不能对应同一 staff）：
`{"format": "kge-pages-staff-map/1", "users": {"托管平台用户名": "<staff 账号 UUID，小写>"}}`。
审核记录文件（离线对账用；`signer` 只收小写规范 UUID；`--db` 直接读库时多出的列忽略）。两个格式号都接受：
- `kge-page-review-records/1`：每条**只允许**这 5 个字段，不许出现 `basis_code`；
- `kge-page-review-records/1.1`（冲刺 4 起）：5 个字段外加可选的 `basis_code`，只收 `mr_review`（审核台在线签字，即事前签字）与 `spot_check`（低风险事后抽检）；省略即事前签字。

`{"format": "kge-page-review-records/1.1", "records": [{"page_id", "page_commit", "risk_level", "decision", "signer", "basis_code"?}]}`。
合成样例：`tests/fixtures/pages-reconcile/`（对账通过的一组；测试在其上删记录、改提交号、减批准人）。

### KGE-06 封测周修订支持（specs/kge06-weekly-revision 范围 1、2；零迁移）
开关 `REVIEW_CONSOLE_FEATURES`（逗号分隔，缺省全关、只在 dev / test，名字不认识拒绝启动；关着时接口不注册）；前端同名开关 `STARBUDDY_REVIEW_FEATURES`。
- **低置信工作单**（`tag_worklist`）：`GET /review/v0/tag-candidates?subject=&map_version_id=&limit=&after_confidence=&after_id=` 经知识服务身份读待处理的
  `kge.tag_candidate`（0024 授权），置信度升序、（置信度, id）游标；每条带改标可选项（同一地图版本、同一题、同一角色、不同节点的其他待处理候选）。
  `POST /review/v0/tag-candidates/{id}/decide`（`accept` / `reject` / `relabel` + `target_id`）只经签字身份调 `kge.decide_tag_candidate`（库里核对 active staff，
  处理人记进 `platform.tag_candidate_signoff`）；改标 = 接受目标候选并驳回原候选，一个事务（不新造候选：来源与置信度要有出处）。
- **修订发布预检**（`revision_preflight`）：`GET /review/v0/map-versions/{id}/preflight` 复用差异视图（diff、`kge.structural_diff`、影响分析、签字进度），
  给出结构性 / 非结构性、应升的版本号段（结构性 → 次版本，其余 → 修订号）、要几人签、版本号是否合发布规则（`revision.py`，纯函数，与 `kge.publish_map_version` 同口径）。
  只给建议，签字发布仍走现有接口。测试 `tests/test_review_revision.py`；变异 `make review-console-mutation`。

### 冲刺 4 补全（specs/release-gate-sprint4 第 2、7、8、9、11 节；G 流第二部分 PR D）
- **页面签字入口**（PR A 待人确认 1 的默认）：`POST /review/v0/pages/sign`，只能签合并记录导出里某个合并请求的头提交，风险取合并请求标注，依据 `mr_review`；
  签字人等于合并请求作者 → 409 `SELF_SIGN`，作者缺失或不在对应表 → 409 `AUTHOR_UNKNOWN`（见上文 `author`）。
- **低风险事后抽检**（`spot_check.py`）：`GET /review/v0/spot-checks?day=YYYY-MM-DD` 按固定种子（SHA-256 排序，不依赖随机数实现）抽当天（北京时间）低风险合并的页面；
  哈希输入是种子、日期、页面与**合并提交**（合并后才确定；不用头提交，免得提交方反复改头提交挑一个抽不中的）。
  `POST /review/v0/spot-checks/sign` 只对已合并的低风险页面记结果（`kge.sign_page_review`，风险 low、依据 `spot_check`），提交方不能抽检自己的合并（`SELF_SIGN`）。
  对账把抽检记录与事前签字分开，抽检驳回 → `spot_check_rejected`、扣页。配置位：`REVIEW_CONSOLE_SPOT_CHECK_SEED`（**必须由人配置，仓库里没有缺省种子**；
  不设则抽检接口回 503 `SPOT_CHECK_UNCONFIGURED`）、`REVIEW_CONSOLE_SPOT_CHECK_RATIO`（默认 0.1；为 0 时照常返回、一页不抽，响应带告警 `warnings: ["ratio_zero"]`）、
  `REVIEW_CONSOLE_MERGE_WINDOW`（默认 02:00，只用于界面说明），**比例与窗口的默认值待教研负责人定**（ADR 12 待人确认 #3）。
- **风险分类器补全**（`risk.py`，纯函数，判不出一律高）：`classify_page`（锁定段、前置表述、教材投影、全量重编高；叙述段中；地区权重与其他渲染段、只更新原子哈希低；模型草稿至少中）、
  `classify_atom`（采分点高、题目族引用低、时政中、题目标签高）、`classify_policy`（一律高、两人）、`classify_content`（一律高）。中风险在阶段 0 按高审（spec 待人确认 #7）。
- **内容版本**（`content.py` 差异；迁移 0042 的只读函数读登记与发布记录）：`GET /review/v0/content-versions?subject=`、`GET …/{id}/diff`（与同前五段上一已发布版本比：能力视图、题目登记、准入；
  发布前提：所指地图版本已发布、（地区, 届别）有已发布考情方案——后者只警告）、`POST …/{id}/sign|publish|retire`、`POST /review/v0/admission/sign`、`POST /review/v0/admission/apply`。
  执行准入后返回 `recompute_since`：掌握重算由学习服务身份执行 `python -m learning.jobs admission-recompute --since <recompute_since>`（读 `platform.admission_change_log`，逐题调 KGE-04 的 `recompute_item`）；
  审核台的两个库身份都不能写个人层，所以不在审核台进程里重算。
- **考情**：`GET /review/v0/policy?city=`（方案与待签的来源文件、招录行、学校）、`GET /review/v0/policy/schemes/{id}`（与同城同届上一版本、与上一届已发布方案的差异；`validate_scheme` 逐条）、
  `POST …/schemes/{id}/sign|publish`、`POST /review/v0/policy/records/{id}/sign`（`object_type`：来源文件、招录行、学校，迁移 0047 起加控制线 `control_line`；
  0047 从招录行旧列回填的控制线是草稿，要两名 staff 在这里重新签字发布）。第二名批准时发布被表上的保护触发器拒绝，整笔回滚、签字不留痕：返回 409 `PUBLISH_FAILED_UNSIGNED` 并写明“签字没有保存”。
- **官方来源清单**（迁移 0048，specs/policy-decisions-v0.9.1 第 4 节）：`GET /review/v0/policy/sources`（主机名、发布机关、状态、登记时间）、
  `POST /review/v0/policy/sources`（`{host, note}`：新增或重新启用，经 `policy.add_source_domain`，库里规范化主机名、拒 IP / 通配符 / 端口 / 路径 / 只有公共后缀）、
  `POST /review/v0/policy/sources/retire`（`{host}`：下线，经 `policy.retire_source_domain`；清单行只下线不删除）。
  只给有 staff 权限 `policy_source_admin`（DBA 授予，`platform.staff_permission`）的账号，没有权限的三个接口都是 403 `SOURCE_ADMIN_REQUIRED`；
  主机名不合格 422、已在清单 409 `CONFLICT`、已下线再下线 409、不在清单 404；等清单行锁超过 5 秒时 409 `BUSY`（迁移 0052，固定说明，已整笔回滚，可稍后重试；与内部 API 的 `BUSY`“结果未知”含义不同）。操作记录只在 `platform.policy_source_domain_change`（操作者是 staff 账号，不进 policy）。
  说明写进公共层，不得填个人信息，含 Unicode 类别 Cc、Cf、Zl、Zp 的字符（C1 控制字符、U+202E 方向覆盖、零宽字符、行段分隔符）一律 422；前端 `/review/policy/sources` 把 punycode 主机名同时显示成 Unicode 形式，以防同形字。
- **版本记录**新增 `line = content_version`（审核、发布、撤下）与 `line = admission`（审核、执行）行，末尾追加列 `content_version_id, six_segment, content_map_version_id, item_id, from_admission, to_admission, admission_review_level`；
  `admission_review_level` 是准入的学科审核等级（Q0–Q5，只在准入行有值），列名带前缀，免得与风险分级的 `review_level`（low/medium/high）混读。
  `action` 列新增取值 `apply`（准入执行行；原有取值 review、publish、retire 不变）。
  这两类行的 `map_version_id`、`version` 为空（只给地图行），所指地图版本在 `content_map_version_id`；`rubric_versions` 对内容版本行取所指地图版本的评分版本，对准入执行行取降级时受影响的清单；`risk_level` 固定 `high`。
- **时政过期下架**：`python -m review_console expire-affairs [--today YYYY-MM-DD]` / `make current-affair-expire [TODAY=…]`，调迁移 0043 的 `kge.expire_current_affairs`：
  `valid_until` 早于当天且已上线的改为 retired，每条追加系统动作审核记录（`basis_code = expired_auto`、无签字人）。**expired_auto 记录不是人工批准**：
  `decision = approved` 只为满足审核记录的结论约束，不能当作任何人审核过该时政的证据。下架不可逆（0043 触发器）：已发布、已撤下版本上 retired 的时政不能改回
  published 或 pending，要重新使用须新建一条走审核。
  只连本机数据库：连接串里全部主机（多主机写法逐个、每个 `host` 查询参数、asyncpg 实际解析出的地址）都必须是 localhost、回环地址或 Unix socket，
  不接受 `hostaddr`、`service`、`servicefile` 参数；连上之后再核对 `inet_server_addr()` 为空（Unix socket）或回环地址，否则中止。只用 kge 连接，不接生产调度。

### 阿鸦准入决定导出（`review_console/admission_export.py`，G 流第二部分 PR E）
- `python -m review_console.admission_export export --pack <阿鸦内容包> --out <决定文件> [--report <报告>] [--replace]` 从平台导出
  `aya-admission-decisions/1`（规则与格式见模块说明与 adapters/aya/README.md“内容包格式 3”）；`… verify --pack <重建后的内容包>` 逐题核对不比平台宽。
- **发布门禁：重建内容包后未跑 verify 不得发布**（verify 退出码 0 才能提交、合入或部署重建后的内容包）。平台内容版本不是已发布时，
  内容包里 pending_review、admitted 的题一律不合格（`reason = version_not_published`）。
- 与 `expire-affairs` 同样只连本机数据库（`local_only` + `check_server_local`），只用审核台读身份（只继承 `starbuddy_kge`）。
- 读库之前先校验：内容包格式、六段标识、题号与准入枚举，输出路径（已存在且没加 `--replace` 即拒，`--replace` 时决定文件与报告各自原子替换，失败不留临时文件）。
- 退出码：0 成功；1 平台数据不能安全导出或 verify 不合格；2 输入不对、不是本机库、角色不对或库拒绝（只给简短说明，不打堆栈）。
- 合成走查：`make release-walkthrough [RELEASE_WALK_OUT=<新目录或空目录>]`（已纳入 `make check`）。

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
- 走 https；前端设 `STARBUDDY_ENV=staging`（会话 cookie 用 `__Host-sb_staff`、Secure）；不设 `STARBUDDY_DEV_PAGES`（开发专用页面只在开发与测试构建里）。
  `STARBUDDY_ENV` 误设成 dev / test 会让会话 cookie 失去 Secure 与 `__Host-` 前缀，上线检查要核对。
- 上线前验证：同一来源连续提交超过上限得到 429；向其他 /review 路径提交登录 action 同样 429；已登录用户的正常浏览不受影响（建议 GET 不限或放宽）。

## 模型路由骨架（`learning/model_router/`，specs/model-router-sprint1，冲刺 1-F）
- 所有模型调用只经 `ModelRouter.route(RouteRequest)`：调用方声明任务类型（T1–T9）与数据级别（L0–L3），不指定模型；返回 `model_version`（`model:<供应商>/<模型>@<快照>`）与 `source_version`（`prompt:<模板>@<版本>`）。
- 路由表 `config/model_routes.yaml`：仓库里只有 dev、test 两节与本地假供应商；staging、pilot、prod 由人按 `docs/compliance/vendors.md` 填写（必停项）。
  pilot、prod 启动校验：不出境、只用已批准的供应商、登记主机名并只接受 https、显式写带版本的快照名、主备不同厂商或平台；staging 可达 L2 的路由也只能用已批准的供应商；运行时再按数据级别过滤一次（境外只在 dev、test 接 L0）。部署入口 `load_for_deployment()` 从 `LEARNING_ENV` 读环境。
- 供应商地址与 Key 只写环境变量名（例如 `MODEL_FAKE_A_URL`），值由部署环境提供。调用记录只含元数据，默认写结构化日志（logger `starbuddy.model_router`）。
- `make model-router-mutation`：改坏出境过滤、启动校验、地址核对、熔断令牌、契约格式等 31 处关键检查，断言测试能发现（只有断言失败算发现）。
- 部署要求：错误上报（例如 Sentry）关闭局部变量采集（`with_locals` 之类），避免把请求内容、Key 带进异常上报。

## 内部模型路由接口（`POST /internal/v1/model/route`，specs/adapter-zhixing-sprint2 设计要点 5）
- 各科适配器调用模型的唯一入口：适配器只配本接口的地址与令牌（`LEARNING_ROUTE_CLIENT_<SUBJECT>_TOKEN`，按学科发放、绑定学科，与服务间令牌、适配器令牌互不相同），
  不持有任何供应商信息；请求交给 `model_router`（环境从 `LEARNING_ENV` 读，配置不合规即拒绝启动）。
- 400 `ROUTE_REJECTED`（任务、数据级别、Prompt 版本不合要求）、403 `SUBJECT_MISMATCH`、503 `ROUTE_UNAVAILABLE`（可重试）；日志只记学科、任务、数据级别、状态与耗时。
- 本接口不预留、不结算、不按幂等键去重：调用方（适配器）的 usage_id / idempotency_key 由学习服务上游的计量预留覆盖。
- 端到端：`tests/test_e2e_zhixing.py`（学习服务 → 知行适配器进程 → 本接口 → 假供应商 → 平台库）。

## 数学自适应诊断（`learning/diagnosis/`，specs/sie-02a-math-diagnosis，冲刺 3-L）
- 内部接口 `/internal/v1/diagnosis/sessions[...]`（开始 / 续做、当前题、探针、作答、暂停、放弃），文档 `openapi/diagnosis-internal.v0.yaml`；只供 BFF 调用，服务间令牌鉴权。
- 显式状态机与选题规则 `aya-diag-trace@0.1.0` 在 `engine.py`（纯函数，按步骤历史复算）；判分只经既有 v1 grade 写入流程（信封 `{request, ingest}`，键盘通道）调阿鸦验证器；只写迁移 0027 两张表、证据与复核队列。
- 用时按墙钟封顶（`resumed_at`），作答幂等键记在步骤上（同键或同内容重放回原结果，否则 409 `ANSWER_CONFLICT`）；过程原文证据 ID 记在 `work_evidence_id`。
- 题面：契约 `present_item` 签字前只有测试环境的合成题面（`LEARNING_DIAGNOSIS_PRESENTER=synthetic`，只允许 `LEARNING_ENV=test`、库名 `starbuddy_dev*` / `starbuddy_test*`，
  作答走 learning-internal v1 写入路径，插件契约 1.0.0 与 learning-internal v1 都已签字，不需开关；作答按会话的 diagnosis_full 预留核对计量）；不配时诊断接口 501。
  选题信息来自合成夹具（缺省）或知识服务（`LEARNING_DIAGNOSIS_SOURCE=knowledge`，见 `config.py`）。
  PLT-12（`specs/present-item-contract`）：`LEARNING_PRESENT_ITEM_SOURCE=adapter` 时诊断与再练的题面改经适配器 `present_item`（插件契约
  1.1.0 候选版，`learning/present_item.py`；只在 `STARBUDDY_ALLOW_CANDIDATE_CONTRACT=1` 时允许，否则拒绝启动），题面过两道白名单后多带
  `rich`（受限富文本）；缺省 `synthetic`，与之前逐字节一致。判分版本与判分方式仍取合成登记（真实来源随契约另定）。
  请求用契约的 `PresentItemContext`：不带学生 ID、幂等键与计量 ID，数据级别 L1。只展示版权状态 `original`、`licensed`、`public`
  的题面；`unverified`、`restricted`、withheld、简答，以及停在 1.0 的学科（`CONTRACT_VERSION_UNSUPPORTED`、404 `INVALID_REQUEST`、
  任何 501）都按没有题面（501 `PRESENTER_UNAVAILABLE`）。题面按（学科, 题号, 内容版本）进程内缓存 10 分钟。取题面不在数据库事务与
  advisory 锁里：事务内只查缓存，没命中则回滚、在事务外取题面后重做（`ViewNotCached`，`learning/diagnosis/service.py` 的 `views_prefetched`）。
- 年级（迁移 0045、0049，specs/policy-decisions-v0.9.1）：选题年级按会话建立时刻（服务时钟）与届别推算，建立时记在 `start_grade`；
  续做时按建立时刻、当前届别重算，不在 7–9 或与 `start_grade` 不同（会话中途届别被改）回 409 `SESSION_INCONSISTENT`，问题体带
  `session_id` 与 `reason = grade_changed`，会话不动；放弃这样的会话时记下 `abandon_reason = grade_changed`（BFF 据此退回额度）。
- `make diagnosis-mutation`：38 个变异，断言诊断测试能发现。

## 模考限时整卷（`learning/mock_exam/`，specs/mock-exam-plt18，PLT-18 第一步）
第一步只有纯函数与格式：不注册路由、不读写数据库、不计量、不写证据、零迁移、不改契约。
- `manifest.py`：卷清单 `starbuddy-mock-paper/1`（结构草案 `config/mock_exam/paper-manifest.schema.json`；合成样例
  `paper.math-fun-syn.json` 数学九年级函数、`paper.daofa-law-syn.json` 道法法律领域）。校验题目 ID 唯一、卷面分值（`paper_score`）和 = 满分、
  满分与限时与考情配置快照（`policy.subject_config` 投影）一致、题目在准入清单快照内且题型码与准入快照一致、题型码属于该科词表；
  快照由调用方传入（测试用 `tests/fixtures/mock_exam/` 的合成快照）。题型码（`single_choice`、`material_analysis` 等）是**工程占位词表**，
  考情库里没有这个字段。分值最多两位小数（对齐 `kge.scoring_point.score` 的 numeric(5,2)），按百分之一分整数运算；`policy.subject_config.full_score`
  是 numeric(5,1)、契约 `ExamConfig.full_score` 是 integer，三处精度不一致，这里不改（待人确认）。
  学科码映射在 `config/mock_exam/subjects.json`（待人确认，学科目录建成后改外键；`platform.subject_catalog` 把道法写作 `politics`，迁移 0013 写作 `morality_law`）。
- `session.py`：`created → in_progress → submitted | expired`。时间一律由调用方注入；截止 = 开始 + 限时 + `grace_seconds`，只在开始时算；
  `now < 截止` 才收作答，同题取最后一次，交卷、过期后拒绝，时钟回拨拒绝；截止及之后交卷或 `advance` 记为 expired（结束时刻 = 截止）。
  会话快照可签成 `me1.<kid>.<载荷>.<签名>`（HMAC-SHA256，标准库）；验签按 kid 在“当前 + 上一把”两把密钥里选，用于轮换，只用当前密钥签发；
  另核对载荷形状与时序一致性（建立 ≤ 开始 ≤ 截止，作答在 [开始, 截止)，交卷在截止前、过期结束 = 截止，最后处理时刻不早于以上任何时刻）。
  篡改、未知 kid、换卷、换学生、时序不一致一律 `TICKET_INVALID`，超过 `ticket_ttl_minutes` 为 `TICKET_EXPIRED`。密钥不合规（短于 32 字节、
  kid 不合规、两把相同）抛 `SigningKeyError`（配置错误，不是票据错误），路由层应映射为 500。
- `summary.py`：只收带 `evidence_ids`、`confidence`、`source_version`、`model_version`、`human_review_required` 的验证器 / Rubric 结果
  （缺任一字段 `MISSING_PROVENANCE`）；判分方式取再练登记的词表 `practice.presenter.GRADING`（对应契约 `DimensionScore.judged_by`，
  第二步改从 GradeResult 维度推出）。卷面得分 = 判分得分 / 判分满分 × 卷面分值，四舍五入到 0.01（ROUND_HALF_UP）；`score.total` 为 null
  的题计 0 分、列为待复核；`calibrated = false` 的题让输出带“未校准”标记；给了题目登记的判分版本时核对 `source_version` 里的 rubric / verifier 段。
  输出得分、各节得分、未作答题数、超时、待复核（人工复核、置信度 < `review_confidence_below`、得分为 null）与“暂定”标记、按节点的得失分聚合
  （判分带采分点且采分点快照完整覆盖时按采分点归到节点，否则整题计入并列入 `whole_item_ids`）。`nodes` 只是证据汇总，**不得落库，也不得当作
  掌握度使用**。只有数字、ID 与编码，不生成任何对学生的陈述（规则 11）。
- 配置位 `config/mock_exam/config.synthetic.json`（宽限 30 秒、低置信阈值 0.6、票据有效期 240 分钟）全部是合成占位，待教研签字；
  未签字只允许 `LEARNING_ENV=test / dev`。签字沿用复测配置的做法：签字人与时间由人填进配置文件（`signed_by`、`signed_at`，自填、不是密码学签名）。
  第二步接入时，`env` 只能取自 `learning/config.py` 已校验的 `LEARNING_ENV`，不得由请求或其他来源传入。
- `make mock-exam-test`（也随 `learning-test`）、`make mock-exam-mutation`（把关键检查逐条改坏，断言测试能发现；不连库）。

### 票据安全（第二步上线前必须满足）
- **只签名不加密**：载荷里有学生内部 ID、会话 ID、各题 `answer_ref` 与作答时刻。第二步之前票据**不得交给客户端**，也不得进 URL、日志与分析事件；
  第二步改为 AEAD 封装（沿用 BFF `sbp1` 的做法）或只给客户端服务端会话 ID。
- **密钥**：模考票据密钥单独配置，不与 BFF（`BFF_AUTH_SECRET` 派生的各用途密钥）、再练 / 复测票据密钥复用；轮换时把旧密钥放进“上一把”，
  旧票据过了 `ticket_ttl_minutes` 后再撤掉上一把。
- **重放（第一步不防，第二步上线前置条件）**：票据是自包含快照，纯函数无法知道它是否已被用过。已知两种攻击：
  1. 重复开始：保存 created 状态的票据，开考后再拿它开始一次，得到更晚的截止（重置截止）；
  2. 回滚重答：交卷后拿交卷前 in_progress 状态的旧票据继续作答、再次交卷（覆盖已交的答卷、重复交卷）。
  第二步建会话表解决：会话表唯一约束（每个会话只能开始一次、只能结束一次）+ 状态版本号（票据带版本，只认最新版本，只能单调前进）。
  `tests/test_mock_exam_session.py` 里两条 `xfail(strict=True)` 用例记录这两种攻击，修好后应改为普通用例。

### 计量与证据口径（只是方案，第一步不实现，接入前须人确认）
- **计量**（规则 4）：照迁移 0008 第 557 行的设计——“完整诊断、模考、讲评课在‘提交时’预留（不是开始时）”，资源 `mock_exam` 按次、
  `reservation_ttl` 150 分钟（按处理时长定）。方案：**交卷（或到截止由 `advance` 定格为 expired）时**，BFF 带 `usage_id` 与
  `idempotency_key`、以 `source_method = grade` 预留一次 `mock_exam`；**整卷判分全部完成时**结算；判分失败或预留过期前没能完成时回滚。
  迁移 0009 第 78 行 `platform.meter_method_allowed` 只允许 `mock_exam` 配 `grade`（不能配 `diagnose` 或其他方法），所以预留与结算的
  `source_method` 必须是 `grade`。一次模考只预留一次，以会话 ID 为幂等依据（第二步会话表的唯一约束保证）；整卷内逐题判分属于这一次模考，
  **不再另计再练或复测次数**，也不各自预留。
- **票据有效期与预留时长**：两者计时起点不同、互不替代。票据有效期（合成占位 240 分钟）从每次签发起算，要覆盖“限时 + 宽限”内学生
  作答的全程，交卷那一次请求带的票据必须仍有效；预留时长（150 分钟）从交卷预留起算，只覆盖交卷后的判分处理，判分（含需要模型的题）
  必须在这段时间内结算，否则预留过期、按回滚处理。
- **备选（待人确认，必停）**：开始时预留、并把 `mock_exam` 的预留时长上调到覆盖“最长限时 + 宽限 + 判分处理”。这要改迁移 0008 种子与
  计量逻辑，与 0008 第 557 行的“提交时预留”不同，未经人确认不采用。
- **证据**：逐题作答仍经现有 grade 写入路径（learning-internal v1 信封 `{request, ingest}`）由学习服务落库。`evidence_kind` 不由调用方给，
  由学习服务按 learning-internal v1 `IngestContext` 的规则推导：用过提示 → `hinted`；没报 `hint_level_used` → 空（掌握计算按保守口径）；
  否则 `purpose = retest` → `delayed_retest`、`transfer_check` → `transfer`、其余 `independent`。所以模考逐题 grade **必须报
  `hint_level_used = 0`、`purpose = practice`**，推导结果才是 `independent`。按 practice 口径的影响：这些证据照常进入 KGE-04 的同步掌握重算，
  可能让该单元已排的复测任务按既有规则提前满足或改排，但不会被当作延迟复测（`delayed_retest`）计数，也不会计入转移检验。
  是否新增考试情境的 `evidence_kind / purpose` 值（要改迁移 0017 的 CHECK、learning-internal 契约与 KGE-04 证据权重键，必停）待人确认
  （spec 待人确认 2）。整卷汇总本身不是证据、不写任何表，模考成绩是否进入预测与对账另定（spec 待人确认 3）。
- **判分时机**：限时内只记作答引用；判分在交卷后进行（与提交时预留一致），汇总只认会话里每题最后一次作答对应的判分；
  `human_review_required` 为真、低置信或得分为 null 的题让总分保持“暂定”，复核结论回来后重算汇总（重算口径随第二步）。
