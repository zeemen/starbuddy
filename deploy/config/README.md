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
- 调度器与其子命令（`insight.scheduled`、`insight.jobs`、`learning.jobs`、`review_console expire-affairs`）在 staging 的连接串与服务端地址
  只收回环、Unix socket 或内网 IPv4；子命令的运行环境必须也是 staging。

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
  TCP 健康检查（HTTP 健康端点另起）。

## 5. 迁移
`deploy/migrate/migrate.py`：不内置连接串（`MIGRATE_DSN` 或 `--dsn-env` 指定的变量；带口令的连接串拒绝，口令走 `PGPASSFILE`）；
先 `plan` 打印清单与确认串，`up` / `down` / `rehearse` 必须以 `--confirm` 逐字给出；版本表 `deploy_meta.schema_migrations`、目标标签
`deploy_meta.target`（防连错库）；副本演练只在 `--replica` 且标签以 `-replica` 结尾时可用，且与 `--with-bootstrap` 一样只连 Unix socket 或回环
（别的副本主机须 `--replica-host-allow` 显式放行，S9）；目标库已有 `platform` schema 却没有 `deploy_meta`（不是本执行器管理的库）时 up / down /
rehearse 拒绝、plan 只提示。**真实环境执行迁移须人批准**（CLAUDE.md 必停项）。

## 5.1 学习服务的 learning-internal v1（契约审查 A2）
`LEARNING_INTERNAL_V1` 是开发开关（v1 未签字，LT-04），staging 设了拒绝启动。因此 staging 上判分、计划调适配器、诊断、再练、复测与今日编排
在 LT-04 之前都不可用（开关矩阵标 `internal_v1`）。`LEARNING_DIAGNOSIS_SOURCE` 在 staging 缺省为 knowledge（合成夹具只在 test）。

## 5.2 环境变量的反向核对（契约审查 A3）
`deploy/tests/test_env_reads.py` 扫描各服务源码里读取环境变量的调用，被读取的名字都必须在 `env-inventory.yaml` 登记；白名单例外（运行时自带、
只读检测的代理变量、不在服务启动路径上的构建工具）在测试里逐个写明理由。适配器的候选契约开关（`STARBUDDY_ALLOW_CANDIDATE_CONTRACT`、
阿鸦的 `AYA_CONTENT_PACK` / `AYA_STEM_OVERLAY`）只在 dev / test；适配器不改代码、自身不区分环境，staging 编排一律不设，由编排测试把关。

## 6. 校验
`make deploy-check`：静态校验（Dockerfile、.dockerignore、编排、清单）+ 开关矩阵 + 环境变量清单 + 迁移执行器单测与一次性库上的副本演练；
`DEPLOY_BUILD=1` 时另加镜像构建（需要能拉到基础镜像）与 `deploy/scripts/compose-smoke.sh` 本机合成全栈冒烟。
