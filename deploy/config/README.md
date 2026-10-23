# 部署配置与密钥注入规范（LT-01 范围第 2 条）

适用于 `deploy/` 下的镜像、编排与清单。总原则：**仓库与镜像里没有任何真实值**，只有名字、口径与合成示例（CLAUDE.md 规则 8、9）。

## 1. 三类配置
| 类别 | 例子 | 怎么给 | 进不进镜像 |
|---|---|---|---|
| 运行开关与地址 | `STARBUDDY_ENV`、`LEARNING_HOST`、`STARBUDDY_TRUST_UI` | 编排文件里的明文环境变量（取值见 `deploy/flags/staging.yaml`） | 否 |
| 密钥 | 服务间令牌、`BFF_*_SECRET`、`*_HMAC_KEY`、连接串 | 运行环境的密钥管理注入（compose 用 `${VAR:?}` 从运行者环境取；k8s 用 `secretKeyRef`，Secret 由人创建） | 否 |
| 签字配置与名单 | 判断策略、掌握 / 再练 / 复测 / 今日配置、调度时间表、白名单 OTP 条目 | 只读挂载（`/run/config`、`/run/otp`），变量指向挂载路径 | 否（合成占位随代码进镜像，但 staging 拒绝加载） |

每个服务的变量清单（名字、用途、是否密钥、缺省、可用环境、staging 是否必填）在 `env-inventory.yaml`；
`deploy/tests/test_env_inventory.py` 核对名字在源码里真实存在、编排只用登记的名字、密钥只经注入、staging 必填项齐全。

## 2. 运行环境（LT-01 决定记录第 1、5 条）
- 五个服务（学习、洞察、知识、调度器、BFF）与审核台后端、前端认 `staging`；`pilot` / 生产仍拒绝（BFF 认这两个值，但没有短信供应商、拒绝启动）。
- staging **拒绝**：合成配置（`synthetic`、`config/*.synthetic.json`、未签字时间表）、短信捕获桩、开发开关（`BFF_AUTH_CONFIG`、`BFF_SHELL_CONFIG`、
  `BFF_CONCURRENCY_CONFIG`、`BFF_SMS_CAPTURE_FILE`、`STARBUDDY_ALLOW_CANDIDATE_CONTRACT`、`*_PRESENTER=synthetic`、`REVIEW_CONSOLE_FEATURES`、
  `STARBUDDY_DEV_PAGES` 等）；未签字的掌握配置仍拒绝。
- 监听地址：缺省 127.0.0.1；只有 staging 能显式配置为内网 IPv4（RFC 1918 字面量；`LEARNING_HOST`、`INSIGHT_HOST`、`KNOWLEDGE_HOST`、
  `REVIEW_CONSOLE_HOST`、`BFF_HOST`，前端 `WEB_HOST`）。服务间地址：https 或回环；staging 另收内网 IPv4 字面量的 http。不收主机名。
- 三科适配器不改代码：只监听回环，与学习服务共用网络命名空间（compose `network_mode: service:learning`，k8s 同一个 Pod）。
- 调度器与其子命令（`insight.scheduled`、`insight.jobs`、`learning.jobs`、`review_console expire-affairs`）在 staging 的连接串必须带
  `sslmode=verify-full&sslrootcert=/run/rds-ca/ca.pem`（固定 CA 路径、文件存在），主机只收回环、内网 IPv4 字面量，或连接前解析出的全部地址
  都是 RFC 1918 的主机名（不收 Unix socket）；连上之后再核对服务端地址（LT-03，见第 8 节）；子命令的运行环境必须也是 staging。
- 学习、洞察、知识服务、BFF 与审核台后端在 staging 同样拒绝不带 `sslmode=verify-full` 与固定 CA `/run/rds-ca/ca.pem`（文件存在）的连库串，
  在建连接池之前拒绝启动（决定记录（续）第 6 条；消息不带连接串）。

## 3. staging 短信：白名单固定码（决定记录第 2、6 条）
- `BFF_STAGING_OTP_FILE`（只读挂载，**属于密钥**：含号码与码）：`{"entries":[{"id","phone","code","issued_at","not_after"}]}`；
  管理员按人下发，窗口 ≤ 24 小时；id 不可复用。文件可热更新（每次签发前重读，坏了保留上一份并记审计）。
- 条目文件不能对属组或其他用户可写，否则拒绝启动（安全审查 S2）。
- `BFF_STAGING_OTP_AUDIT_FILE`（可写卷）：每个事件一行 JSON（`at`、`event`、`entry`、`phone_hmac`、`prev`、`mac`），不记码与号码原文；
  每行 fsync；`mac` 是本行正文与上一行 `mac` 的带密钥摘要（`BFF_LOG_HMAC_KEY` 派生），启动时逐行重算，改行、删中间行、换密钥伪造都拒绝启动
  （S1）。文件必须属于 BFF 进程用户、权限不宽于 0600（S2）。启动时据此恢复签发次数、作废、锁定与待核验状态。
- **首次部署**：条目文件里已有生效条目而审计文件不存在时拒绝启动（S1，防止删审计文件解除作废与锁定）；由人先建空文件
  （属主为 BFF 运行用户、权限 0600），或在第一条条目生效前启动 BFF（没有生效条目时自动新建）。审计文件要随环境保留；换
  `BFF_LOG_HMAC_KEY` 会使旧审计文件校验不过——轮换密钥前须由人归档旧审计文件并确认没有未作废的条目。
- 单实例（H2）：审计文件旁的 `<审计文件>.lock` 记录持有者（主机名、进程号、心跳时间），每 20 秒刷新；别的持有者心跳在 60 秒内就拒绝
  启动。滚动发布须先停旧实例（k8s 清单用 Recreate）；旧实例异常退出后最多等 60 秒才能起新实例。
- 规则：成功即作废（先在内存作废再写审计，审计写失败只告警，S3）；每条目签发 ≤ 3 次（库写成功后才记账）；失败 ≤ 5 次锁定；不在白名单的号码
  与现状完全相同（随机码、不投递）。码的有效期与尝试次数仍按 `services/bff/config/auth.json`（库函数核对）。
- **已知局限（S4）**：
  1. 知道某个白名单号码的人可以替他请求验证码，把该条目的 3 次签发名额用光（之后由管理员补发新条目）；每号每分钟、每天的限流仍有效。
  2. 白名单与非白名单号码的处理路径不同，时序上可能有可测差异；已让所有号码在请求与核验时都写一行审计（同等文件操作），但不保证时序完全一致。
  3. 截断审计文件末尾若干行发现不了（只能发现改行与删中间行），靠卷的访问控制与 S1 兜底。
  4. 审计里非白名单号码的 HMAC 也会留下（防探测用的同等文件操作带来的副作用）：**保留期待人定**（建议与访问日志同一保留期，到期归档删除）。
- 同意文本（S6、S7）：staging 只收由人登记的真实版本 `BFF_CONSENT_VERSIONS=privacy_policy=privacy-policy@<版本>,guardian_consent_under14=guardian-consent@<版本>`；
  前端两份文书分别配置 `STARBUDDY_CONSENT_VERSION_PRIVACY`、`STARBUDDY_CONSENT_VERSION_GUARDIAN`（与 BFF 登记的一致），staging 没配齐或是合成版本
  启动即拒绝，写入前两条都确认可用才写；合成版本（`synthetic-*`，不区分大小写）staging 拒绝（决定记录第 10 条）。
- `BFF_TRUST_CLIENT_IP`：编排里缺省 `0`；只有网关覆盖 `X-Forwarded-For`、BFF 只对前端开放时才能改为 `1`——**发布检查项**（S5）。

## 4. 镜像与基础镜像
- Dockerfile：`services/learning/Dockerfile`、`deploy/docker/*.Dockerfile`；每份有同名 `.dockerignore`（白名单，排除 `.env*`、测试、夹具、`legacy/`）。
- 基础镜像只经无缺省值的 `ARG *_IMAGE` 给出，`deploy/scripts/build-images.sh` 拒绝不带 `@sha256:` 的值（本机冒烟可 `ALLOW_TAG=1`）。
  正式来源是选定的境内云厂商镜像仓库（决定记录第 11 条，随 LT-03）。
- 镜像固化环境标记 `STARBUDDY_IMAGE_ENV`（构建参数，staging 或 test；S10）：staging 镜像拒绝以 dev / test 运行（学习、洞察、知识、调度器、
  审核台、BFF、前端核对；适配器不改代码、不核对）。已知局限：运行时同名环境变量可以覆盖，只防误用。本机冒烟用 test 标记另行构建。
- 前端由 `scripts/serve.ts` 启动包装：先核对运行环境（只 dev / test / staging）、`WEB_HOST`（dev / test 只 127.0.0.1，staging 另收内网 IPv4）、
  端口、镜像标记与 staging 的两份同意版本，通过才起 `next start`（A1）。
- 全部镜像：非 root（uid 10001）、只读根文件系统可运行（需要写的只有 tmpfs：`/tmp`、知识服务分词缓存、前端 `.next/cache`；BFF 审计卷）、
  健康检查：服务 HTTP `GET /readyz`（LT-03，第 9 节）；三科适配器不改代码，仍是 TCP 连接回环端口。

## 5. 迁移
`deploy/migrate/migrate.py`：不内置连接串（`MIGRATE_DSN` 或 `--dsn-env` 指定的变量；带口令的连接串拒绝，口令走 `PGPASSFILE`）；
先 `plan` 打印清单与确认串，`up` / `down` / `rehearse` 必须以 `--confirm` 逐字给出；版本表 `deploy_meta.schema_migrations`、目标标签
`deploy_meta.target`（防连错库）；副本演练只在 `--replica` 且标签以 `-replica` 结尾时可用，且与 `--with-bootstrap` 一样只连 Unix socket 或回环
（别的副本主机须 `--replica-host-allow` 显式放行，S9）；目标库已有 `platform` schema 却没有 `deploy_meta`（不是本执行器管理的库）时 up / down /
rehearse 拒绝、plan 只提示。**真实环境执行迁移须人批准**（CLAUDE.md 必停项）。
- 副本标签（决定记录（续）第 5 条）：从快照恢复出的副本带着主库的标签 `staging`，改成 `staging-replica` 只能用子命令
  `mark-replica --label staging-replica --replica --replica-host-allow <副本内网主机名> --confirm staging:mark-replica:staging-replica:<服务端地址>`：
  连接串的每个主机都必须在放行清单里，连上后核对服务端地址（回环或 RFC 1918，且是放行主机名解析出的地址之一），库里的标签必须正是
  `staging`，只做 `staging → staging-replica` 这一种改动，确认串逐字（含服务端地址，先不带 `--confirm` 运行一次拿到）；不手写 `UPDATE`。
- 标签以 `-replica` 结尾时，`up` / `down` 也必须带 `--replica --replica-host-allow`（不带就在连库之前拒绝）；手册里主库与副本用不同的变量名
  （`PRIMARY_DSN` / `REPLICA_DSN`，经 `--dsn-env` 指定），不再共用 `MIGRATE_DSN`。

## 5.1 学习服务的 learning-internal v1（契约审查 A2；LT-04）
learning-internal v1 已于 2026-10-20 签字为 1.1.0（specs/launch-lt04-learning-internal-v1），原开发开关 `LEARNING_INTERNAL_V1` 随之删除
（清单与开关矩阵都已移除，学习服务不再读它）。`/internal/v1` 的 grade、diagnose 写入只认 BFF 服务令牌（`LEARNING_SERVICE_TOKEN`），
写入前经迁移 0058 的 `platform.learning_meter_check` 核对计量（**0058 须由人在副本演练后先于本版本执行**，学习服务启动时核对它存在），
用途与证据等级由学习服务推导。staging 上诊断、再练、复测与今日编排只待教研签字内容（开关矩阵只标 `signed_content`）。
`LEARNING_DIAGNOSIS_SOURCE` 在 staging 缺省为 knowledge（合成夹具只在 test）。

## 5.2 环境变量的反向核对（契约审查 A3）
`deploy/tests/test_env_reads.py` 扫描各服务源码里读取环境变量的调用，被读取的名字都必须在 `env-inventory.yaml` 登记；白名单例外（运行时自带、
只读检测的代理变量、不在服务启动路径上的构建工具）在测试里逐个写明理由。适配器的候选契约开关（`STARBUDDY_ALLOW_CANDIDATE_CONTRACT`、
阿鸦的 `AYA_CONTENT_PACK` / `AYA_STEM_OVERLAY`）只在 dev / test；适配器不改代码、自身不区分环境，staging 编排一律不设，由编排测试把关。

## 6. 校验
`make deploy-check`：静态校验（Dockerfile、.dockerignore、编排、清单）+ 开关矩阵 + 环境变量清单 + 迁移执行器单测与一次性库上的副本演练
+ IaC 草案的静态规则检查（LT-03，`deploy/iac/check_iac.py`）+ 流水线模板静态校验（`deploy/tests/test_ci.py`）；
`DEPLOY_BUILD=1` 时另加镜像构建（需要能拉到基础镜像）与 `deploy/scripts/compose-smoke.sh` 本机合成全栈冒烟。
`make iac-validate`：terraform validate、tflint、checkov、tfsec（工具在时才跑，不联网下载；缺失时跳过，严格模式失败；不接进 check）。

## 7. 模型供应商（LT-02，`specs/launch-lt02-model-provider` 决定记录 2026-10-21）
仓库里只有变量名与占位：路由表 `services/learning/config/model_routes.yaml` 的 staging 节登记阿里云百炼（`bailian`，主）与火山方舟
（`ark`，备，含方舟托管的 DeepSeek），`whitelist: pending`；`docs/compliance/vendors.md` 两家状态“待开通 / 待登记”。
**主机名不写进仓库**：路由表 `allowed_hosts`、登记表“允许的主机名”、k8s 学习服务 Pod 注解 `starbuddy/model-egress-hosts` 都是
`<由人填写…>` 占位——真实接入主机名属于开通结果，由人在开通后三处同时填写（同一个 PR，`[NEEDS-HUMAN]`）。

由人完成（顺序）：
1. 开通两家、签数据处理协议、登记备案或准入依据（合规负责人），在 `vendors.md` 填写两行（主机名、地点、协议、依据、保留期限）。
2. 在路由表 staging 节填写：`allowed_hosts`（与登记表一致）、每个目标的 `version`（快照日期或版本号）与 `api_name`（控制台里的带日期快照名；
   思考 / 非思考按快照区分）；批准后把 `whitelist` 改为 `approved`、登记表状态改为“已批准”。按需调整 `budgets`（成本护栏，缺省小额度）
   与 `pricing`（单价，元 / 百万 token；空则不估算）。占位未填、未批准、缺 `allowed_hosts`、`api_name` 不带日期或版本号时 staging 拒绝启动；
   `services/learning/tests/test_model_router_staging.py` 核对路由表与登记表状态、主机名一致。
3. 在密钥管理里建 Secret（地址与 Key 都按密钥处理，变量名见 `env-inventory.yaml`：`MODEL_BAILIAN_URL`、`MODEL_BAILIAN_API_KEY`、
   `MODEL_ARK_URL`、`MODEL_ARK_API_KEY`；两科路由客户端令牌 `LEARNING_ROUTE_CLIENT_ZHIXING_TOKEN` / `_WENLI_TOKEN` 与适配器的
   `ZHIXING_ROUTE_TOKEN` / `WENLI_ROUTE_TOKEN` 同值）；按 `deploy/k8s/staging.yaml`（或 `deploy/compose/staging.yaml`）里学习服务与两科适配器
   注释掉的几行接线：适配器的 `ZHIXING_ROUTE_URL` / `WENLI_ROUTE_URL` = `http://127.0.0.1:8785`（学习服务的回环路由监听，见下）。
   **路由客户端令牌一旦设置，学习服务就按 `LEARNING_ENV` 加载路由表**：上面第 2 步没做完就设令牌，学习服务拒绝启动。

   **回环路由监听**（决定记录（续）第 5 条）：staging 的主监听是 Pod 内网地址（`LEARNING_HOST`），而适配器只认 https 或回环 http（适配器不改代码），
   所以 staging 下学习服务另在 `127.0.0.1:LEARNING_ROUTE_PORT`（缺省 8785，编排里显式写 8785，不能与 `PORT` 相同）起一个监听，**只服务**
   `/internal/v1/model/route`，其他路径一律 404；与主监听共用同一个应用与状态（同一份成本护栏计数），主监听不变；dev、test 不起
   （设了 `LEARNING_ROUTE_PORT` 拒绝启动）。改端口时 `LEARNING_ROUTE_PORT` 与两科 `*_ROUTE_URL` 要一起改（`deploy/tests/test_model_provider.py` 核对一致）。
4. 出网白名单：只给学习服务 Pod 放行两家接入主机名的 443（NetworkPolicy 只认 IP 段，按主机名放行在云防火墙 NAT 边界：
   LT-03 已转写为 `deploy/iac/alicloud/egress.tf`，主机名填进 IaC 变量 `model_egress_fqdns`，与上面三处一致）；
   路由建连前还会核对主机名（`allowed_hosts`，只收 https）。
5. 人执行冒烟（不进 `make check`、不进 CI，限次）：在有凭据的 staging 执行环境里 `make model-smoke SMOKE_ENV=staging MAX_CALLS=5`——
   每个不同的路由目标（主、备分别算；去重键是 供应商 + 模型 + 版本 + 快照名，现为 5 个）各发一次合成 L0 请求、不重试，
   只打印状态、耗时、token 与快照名是否一致，不打印回复原文；
   pilot、prod、test 一律拒绝。全部通过后把开关矩阵里 `model_provider` 的 pending 去掉（另起 PR）。
6. 人设回归：点点对照评测在 staging 的执行步骤见 `evals/persona/README.md`“staging 执行步骤（LT-02）”。

成本护栏（路由层、进程内，多实例按实例计；staging 每个已配路由的任务都必须配，否则拒绝启动）：按**每次真实的供应商调用**计次
（含重试与切到备选，成败都算）；token 按供应商报告的用量累加——失败的调用只有报告了用量（200 但内容不合格式）才累加，
HTTP 错误、超时、连接失败没有用量可读，只计次数。任务当日（北京时间 0 点重置）次数或 token 到上限后，内部接口 `/internal/v1/model/route`
返回 503 `ROUTE_UNAVAILABLE`、`detail: ROUTE_BUDGET_EXCEEDED`、`retryable: false`、`retry-after`（距重置的秒数）；适配器按 503 处理（与没有可用供应商时一致）。
不改计量与扣次规则；调用记录（日志 `starbuddy.model_router`）带 `estimated_cost_cny`（有单价时）。
部署侧核对：`deploy/tests/test_model_provider.py`（供应商变量登记为 staging 密钥；`MODEL_*`、路由客户端令牌与 `*_ROUTE_TOKEN` 不论何时都只经
secretKeyRef / `${…}` 注入；`model_provider` 仍 pending 时编排不设令牌与供应商变量；出网注解只在学习服务 Pod，且是占位或与路由表 `allowed_hosts` 一致；
回环路由端口在 env-inventory、k8s、compose、本节与编排注释里的 `*_ROUTE_URL` 一致）。

## 8. 阿里云 staging（LT-03，`specs/launch-lt03-cloud-staging` 决定记录 2026-10-21）
华北 2（北京）、ACK Pro（Terway）、只内网 + VPN / 办公网白名单。基础设施草案 `deploy/iac/alicloud`（Terraform，仅 validate 与静态检查，
plan / apply 由人执行，步骤见该目录 README）；镜像流水线 `deploy/ci`（构建、SBOM、扫描、签名、推送到 ACR 企业版）；演练手册 `deploy/runbooks`。

**托管 Postgres（RDS）的连接串**（存凭据管家，经 ack-secret-manager 同步成 k8s Secret，只经 `secretKeyRef` 注入）：
```
postgresql://<登录角色>@<RDS 内网主机名>:5432/<库名>?sslmode=verify-full&sslrootcert=/run/rds-ca/ca.pem
```
- 主机名用 IaC 输出 `rds_private_host`（VPC 内网地址；不建外网连接地址）；连接串整体按密钥处理（含登录角色口令时也只在凭据管家与
  k8s Secret 里，不进编排文件、镜像与日志；迁移执行器另按第 5 节不收带口令的连接串，口令走 `PGPASSFILE`）；
- **TLS 与主机名核对**：`sslmode=verify-full`（校验证书链与主机名）+ `sslrootcert`（RDS 的 CA 证书，由人放进 ConfigMap `starbuddy-rds-ca` 的
  `ca.pem`，连库的容器只读挂载到 `/run/rds-ca`，`deploy/k8s/staging.yaml`）。学习、洞察、知识服务（asyncpg）、BFF（node-postgres）、
  审核台与调度器都按这个连接串连库；
- **客户端强制 TLS**（决定记录（续）第 6 条）：staging 下学习、洞察、知识服务、BFF、审核台后端、调度器与其子命令一律拒绝不带
  `sslmode=verify-full`、或 `sslrootcert` 不是固定路径 `/run/rds-ca/ca.pem`、或该文件不存在的连接串（`sslmode` / `sslrootcert` 各只能出现一次；
  `require`、`verify-ca`、别的 CA 路径都不收）。服务在建连接池之前拒绝启动，消息只说缺什么、不带连接串。
- **服务端强制 TLS**：RDS 开 SSL、证书只签内网连接地址、pg_hba 只有 `hostssl` 条目（`deploy/iac/alicloud/rds.tf`，静态规则 R06）——
  不走 TLS 的连接在服务端就被拒（RDS 系统条目的行为待人核实，演练时用 `sslmode=disable` 从 VPC 内连接必须失败）。
- **调度器与子命令的主机核对**（补 LT-01 待人确认 6；`review_console/__main__.py` 的 `local_only` / `tls_verified`，调度器与四个子命令共用）：
  staging 下主机只收回环、RFC 1918 内网 IPv4 字面量，或主机名——主机名在**连接之前由客户端解析一次**，解析结果必须非空且全部是
  RFC 1918 地址（混有公网地址、解析失败都拒绝）；`hostaddr`、`service`、公网 IP 字面量、Unix socket 一律拒绝；dev / test 照旧只收本机。
  连接前的这次解析与驱动建连时的解析是两次查询（中间 DNS 变了发现不了），所以**连上之后**还核对服务端地址
  （`check_server_local`：服务端地址为空（Unix socket；staging 在连接前已拒绝 socket）、回环，或 staging 下的 RFC 1918 内网 IPv4 才收；
  端口转发到别处、连到公网地址的服务端都会被拒），两道一起才算数。
  学习、洞察、知识服务与 BFF 的主服务不做主机解析核对（行为不变），只靠上面的 TLS 规则（verify-full 校验证书与主机名）。
- 迁移执行器在 RDS 副本上演练时用 `--replica-host-allow <副本内网主机名>` 显式放行，副本改标签只用 `mark-replica`（第 5 节，
  `deploy/runbooks/rds-restore-and-migrate-rehearsal.md`）。

**入口与出网**：前端 Service 是私网负载均衡（`address-type: intranet`、访问控制白名单 = IaC 的 `slb_acl_id`、HTTPS 用私有 CA 签发的内网证书——
`BFF_SHELL_ORIGIN` 在 staging 必须是 https），不建 Ingress、不公开域名；出网三层：NetworkPolicy（缺省拒绝，连库的服务只放 RDS 5432；
学习服务的模型出网策略 except 排除 RFC 1918、100.64.0.0/10、169.254.0.0/16）、安全组（出方向白名单：公网只开 TCP 443，阿里云内部网段只开
内网 DNS、元数据与服务端点）、云防火墙 NAT 边界（只放行 `model_egress_fqdns`；防火墙没开时 SNAT 不建）。

**服务间的 Host 校验**（契约审查阻断 2）：洞察、知识服务与审核台后端有 Host 校验（防 DNS 重绑定）。k8s 里调用方经 Service 的 clusterIP 访问，
Host 头是 clusterIP，所以三者各有一个 staging 专用的额外允许 Host：`INSIGHT_EXTRA_HOST`、`KNOWLEDGE_EXTRA_HOST`（HTTP 与 MCP 共用）、
`REVIEW_CONSOLE_EXTRA_HOST`——恰好一个 RFC 1918 IPv4 字面量，k8s 里填本服务 Service 的 clusterIP；dev / test 设了拒绝启动。
compose 里调用方直接用被调方的固定内网地址（= 监听地址），不需要它。`deploy/tests/test_service_hosts.py` 核对每个调用方 `*_URL` 的主机
都在被调方的允许列表里（学习服务与 BFF 没有 Host 校验，只核对地址是被调方的 clusterIP 与端口）。

**镜像**：`deploy/k8s/staging.yaml` 里的 `ACR_REGISTRY/starbuddy/starbuddy-<服务>@sha256:DIGEST` 由人按流水线输出的
`dist/image-digests.txt` 替换（`ACR_REGISTRY` = `<ACR 实例名>-registry-vpc.cn-beijing.cr.aliyuncs.com`，只按摘要部署）。

## 9. 健康端点（LT-03 范围第 4 条）
| 服务 | 存活 | 就绪（503 = 未就绪） | 接口定义 |
|---|---|---|---|
| 学习服务、审核台后端 | `GET /healthz` | `GET /readyz`：lifespan 的启动前置（契约、迁移 0016 / 0037 / 0058 与执行权；审核台两个登录角色）已通过 + 连接池 2 秒内 `SELECT 1`；学习服务每次另核对 0058 的计量函数仍在、本角色仍有执行权（`has_function_privilege`） | `services/learning/openapi/health.v0.yaml` |
| 洞察服务 | 同上 | 启动前置（策略、词表、SIE-03、月度计划签字配置、登录角色）+ `SELECT 1` | `services/insight/openapi/health.v0.yaml` |
| 知识服务 | 同上 | 启动前置（契约、只读角色与时区、页面快照核对）+ `SELECT 1` | `services/knowledge/openapi/health.v0.yaml` |
| 调度器 | 同上（设了 `SCHEDULER_HEALTH_PORT` 才起监听；`--once`、`--dry-run` 不起） | 启动核对通过、循环在跑（收到 SIGTERM 即未就绪）+ 用 `SCHEDULER_DATABASE_URL` 3 秒内连库、核对服务端、`SELECT 1`（每次新建连接、用完即断，并发 1） | `services/scheduler/openapi/health.v0.yaml` |
| BFF | 同上 | 启动核对通过才起监听 + 2 秒内从连接池借一条连接 `SELECT 1`（超时就销毁这条连接，查询随之作废） | `services/bff/openapi/health.v0.yaml` |
| 前端 | 同上 | 启动包装同一套环境核对（`src/lib/serve-env.ts`）仍通过；不调 BFF | `apps/web/src/app/{healthz,readyz}/route.ts` |
| 三科适配器 | 不加端点（不改代码，决定记录（续）第 4 条）：exec TCP 连接 `127.0.0.1:PORT` | 同左 | （插件契约不变） |

口径：无需鉴权；响应只有 `{"status": …}`，不带版本、主机、库名、错误类别；`no-store`；只认 GET、路径逐字相等；探测不逐次记日志；
并发的探测共用同一次检查（single-flight），结果复用 3 秒（学习、洞察、知识服务三份 `health.py` 代码逐字相同，`deploy/tests/test_health_shared.py`
核对；BFF 同一口径，调度器并发 1 + 3 秒复用）。洞察、知识服务与审核台仍过 Host 校验：staging 另收显式配置的内网监听地址（k8s 探针经 Pod
地址访问）与第 8 节的额外允许 Host（调用方经 Service clusterIP 访问）。
k8s：就绪探针 `/readyz`、存活探针 `/healthz`（存活不依赖库，库抖动只摘流不重启）；适配器只监听回环，用 exec 在容器里做 TCP 连接
（启动探针兜底启动时间，存活探针 30 秒后开始、30 秒一次）。

## 10. 可观测性：指标、日志、告警（LT-05，`specs/launch-lt05-observability` 决定记录 2026-10-23：阿里云 ARMS 托管 Prometheus + SLS）
**指标**（决定记录（续）第 5 条）：学习服务、审核台后端、洞察服务、知识服务、BFF、调度器各另起一个**只服务 `GET /metrics` 的指标监听**
（与业务监听同一地址；端口变量与 staging 缺省：`LEARNING_METRICS_PORT` 9788、`REVIEW_CONSOLE_METRICS_PORT` 9792、`INSIGHT_METRICS_PORT` 9791、
`KNOWLEDGE_METRICS_PORT` 9790、`BFF_METRICS_PORT` 9787、`SCHEDULER_METRICS_PORT` 9793，各不相同、不能与业务端口相同；dev / test 只在设了时起）。
指标端口只认 GET、路径逐字相等（别的路径 404、别的方法 405）、`no-store`、不记访问日志、免鉴权、只在 Pod 内网；业务端口、学习服务的回环路由
监听与调度器的健康监听上的 `/metrics` 一律 404。k8s：容器端口名 `metrics`，PodMonitor 只采它；NetworkPolicy `metrics-from-collector` 在
命名空间外只给 ARMS 采集组件放行指标端口、不放行业务端口（命名空间与 Pod 标签由人填写）。前端与三科适配器不暴露指标。
- 实现：Python 三份 `obs.py`（`services/learning/learning/obs.py`、`services/insight/insight/service/obs.py`、
  `services/knowledge/knowledge/service/obs.py`，代码逐字相同，`deploy/tests/test_obs_shared.py` 核对；审核台与调度器复用学习服务那份），
  依赖 `prometheus-client 0.26.0`（Apache-2.0 AND BSD-2-Clause）；BFF `services/bff/src/observability/metrics.ts` 自写文本格式，**不引入指标依赖**
  （决定记录（续）第 4 条：不用 `prom-client`）。不出进程 / GC 等缺省指标。写指标出任何错都只丢这一条，不影响请求；计量计数在事务提交之后，
  计数出错不改变计量结果（第 6 条）。
- 标签白名单（集中定义、有测试与变异）：`service`、`route`（路由模板，匹配不到记 `unmatched`，不记原始路径）、`method`、`status_class`、
  `subject`、`task`、`provider`、`outcome`；学科、任务类型、供应商、结果只收枚举集合（枚举外一律记 `other`，Python 与 BFF 同一份），
  路由只收模板形状（像 UUID、手机号、邮箱、长数字、原始路径的值写入前就拒绝）。**任何学生 / 家长 / 账号标识、手机号、内容都不进指标。**
- 指标清单：`deploy/observability/metrics-inventory.yaml`（各服务测试把进程实际注册的指标与它逐项比对）。核心指标：请求数与时延直方图、
  连接池、就绪；模型路由调用 / 供应商尝试（失败）/ 熔断 / 护栏剩余 / 估算成本；BFF 计量预留 / 结算 / 释放（回滚）/ 冲突（409）/ 计量事件，
  学习服务写入前核对的放行与 409；复核队列积压（按学科计数与最早等待，采集时查库、缓存 30 秒）；调度任务成败、耗时、最近一次成功时刻。

**日志**：各服务一行一个 JSON：`ts`、`level`、`service`、`logger`（BFF 无此项），请求内另带 `route`（路由模板）与 `request_id`，
再并入原记录里**登记过的元数据字段**（安全审查 B：字段白名单 `LOG_FIELDS`，键名先驼峰归一为下划线小写，白名单外的键整项丢掉；Python 三份
`obs.py` 与 BFF `observability/log.ts` 同一份清单，IaC 日志索引字段只能从中选，`deploy/tests/test_obs_shared.py` 核对；BFF 另有 `student`、
`account` 两个哈希字段，只收带密钥 HMAC 截断的 16 位十六进制）。脱敏集中实现（两边同口径、有测试）：嵌套结构里的作答原文与内容、手机号、
邮箱、身份证、令牌 / 口令 / 密钥 / 会话、姓名、文本、地址、标识等字段整项丢掉；字符串里的手机号（允许空格、连字符分隔）、邮箱、身份证号、
Bearer / JWT / 密钥样式串、连接串替换为 `[redacted]`，UUID 替换为 `[uuid]`（`request_id` 字段除外）；超长截断；异常只记类名。
uvicorn 以 `log_config=None` 启动，它的日志也经同一个处理器。SLS 采集见 `deploy/k8s/observability.yaml`（安全审查 A：只采六个后端主容器——
Pod 标签 `starbuddy/metrics=true` 且容器名在白名单里；标准输出与标准错误，JSON 展开一层，解析失败的行丢弃；进 IaC 预建的 `app` 日志库，
留存 180–365 天由 IaC 设、采集配置不写；`sls.tf` 只给白名单字段建字段索引，不建全文索引）。

**告警与仪表盘**：`deploy/observability/alerts.yaml`（Prometheus 规则：服务不可用、就绪失败、5xx 率、p95 时延、连接池、模型熔断与失败率、
护栏将满 / 已满、估算成本、计量回滚 / 409 / 结果未知、复核积压、调度失败 / 长时间未成功、证书、磁盘、容器重启）与
`deploy/observability/dashboards/*.json`（总览、模型与成本、计量、学习链路）；`check_observability.py`（O01–O06）核对只引用登记的指标、
标签只用白名单，随 `make deploy-check`；有 promtool 的环境另跑 `make obs-promtool`。**阈值都是占位缺省，待人签字（SLO 通过线）**。

**由人完成**：开通 ARMS 托管 Prometheus 与 SLS（新增云服务，必停）；按 ARMS 实际情况填 NetworkPolicy 的采集组件命名空间与 Pod 标签、
`AliyunLogConfig` 的 project；在 ARMS 控制台导入告警规则与仪表盘，**配置告警接收人、通知渠道与值守排班**（仓库不含联系方式）；
签字阈值；核对 `external` 里采集侧指标名（kube-state-metrics、kubelet、拨测）在 ARMS 里可用；证书拨测只从 VPC 内网发起（集群内探针或
内网拨测点），不开公网拨测。ARMS 与 SLS 已作为数据处理方登记在 `docs/compliance/vendors.md`（状态待开通）。每周恢复演练见 `deploy/runbooks/weekly-restore-verify.md`。
