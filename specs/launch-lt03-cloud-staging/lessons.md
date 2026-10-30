# LT-03 阿里云 staging 配置、镜像构建、健康端点 · 实现记录

2026-10-22/23 由 Claude 推进（specs/launch-test-2026）。提交首行 `[NEEDS-HUMAN]`：新增第三方云服务与工具（阿里云 ACK Pro、RDS、OSS、ACR 企业版、云防火墙、NAT/EIP、SLS、KMS 凭据管家、ack-secret-manager、云效 Flow；Terraform、tflint、checkov、tfsec、syft、trivy、cosign）、KMS / RAM 权限策略、各服务连库 TLS 与 Host 校验、迁移执行器新子命令。**不创建任何真实云资源，不含账号 ID、凭据、真实地址**；本机无 terraform 等工具，`terraform validate` 未实际执行。不改契约、迁移、适配器代码。

## 交付
- **IaC 草案** `deploy/iac/alicloud/`（Terraform）：VPC / 交换机 / 普通安全组（入方向只收 VPC；出方向只放内网 DNS、元数据、由人填写的云服务端点 /24 以内、公网 443；100.64.0.0/10 与 169.254.0.0/16 先拒、末条全拒）、NAT（只 SNAT，依赖必填确认变量 `nat_firewall_confirmed` 与云防火墙 NAT 边界放行 `model_egress_fqdns` 的 443）、ACK Pro 与节点池、RDS PostgreSQL 16（内网、KMS 加密、日志备份 PITR、删除保护、证书只签内网连接地址、pg_hba 只 hostssl + scram）、OSS 私有桶（经网关终端节点，策略只放本桶）、ACR 企业版（缺省 Advanced，待核实）、SLS（≥180 天、不建全文索引）、凭据管家专用 KMS 密钥 + RRSA 最小权限（逐个列名）。`.gitignore` 排除状态与 tfvars。许可证核对写在 README（Terraform BSL 1.1 仅内部工具，不合适改 OpenTofu）。
- **静态检查**：`deploy/iac/check_iac.py`（标准库，HCL 小解析器，R01–R19，每条有变异测试）接入 deploy-check；`make iac-validate` 在工具存在时调用 terraform / tflint / checkov / tfsec（缺失打印跳过、严格模式失败，`CHECKPOINT_DISABLE=1`，不联网下载 provider），**不接入 check**。
- **k8s 厂商化**：ESSD 存储类（KMS、保留）、PVC、镜像 `ACR_REGISTRY/...@sha256:DIGEST` 占位、前端私网 SLB（白名单、HTTPS）、RDS CA 只读挂载、RDS 出网与前端入网 NetworkPolicy、探针改 HTTP（适配器为 exec TCP + startupProbe）。
- **连库 TLS**：staging 下学习、洞察、知识、BFF、审核台、调度器拒绝不带 `sslmode=verify-full` 或 `sslrootcert` 非 `/run/rds-ca/ca.pem`（且文件存在）的连接串；连接前客户端解析须全为 RFC 1918，连上后再核对服务端地址（两次解析之间的变化发现不了，已如实写入文档）。
- **服务间 Host**：`INSIGHT_EXTRA_HOST`、`KNOWLEDGE_EXTRA_HOST`、`REVIEW_CONSOLE_EXTRA_HOST`（仅 staging，恰好一个 RFC 1918 IPv4，填各自 clusterIP）；一致性测试核对每个调用方 `*_URL` 主机都在被调方允许列表中（k8s 与 compose）。顺带修了 LT-01 遗留：审核台、洞察、知识在 k8s 经 clusterIP 调用会被 Host 校验拒绝。
- **HTTP 健康端点**：学习、洞察、知识、调度器（独立小监听 `SCHEDULER_HEALTH_PORT`）、BFF、审核台、前端补 `/healthz` 与 `/readyz`，只回 `{"status"}`、`no-store`、免鉴权、不记日志；readyz single-flight + 3 秒缓存，学习服务每次核对 0058 执行权；三份 Python health.py 逐字一致（有测试）。前端两端点登记攻击面清单。适配器不加（用户决定）。
- **镜像流水线** `deploy/ci/`：构建 → uid 与健康核对 → SBOM（syft）→ 扫描（trivy，高危失败，库从 ACR）→ 推送 → cosign 签名与 SBOM 证明 → 本地公钥 `cosign verify` / `verify-attestation`，验不过即失败；锚定正则核对仓库地址；trap 中 docker logout；关闭版本检查与遥测。云效 Flow 模板只手动触发。
- **迁移执行器** `mark-replica` 子命令（显式副本主机放行、核对服务端地址、只 staging → staging-replica、逐字确认串）；`-replica` 标签的 up / down 强制副本参数（连库前拒绝）。
- **演练手册** `deploy/runbooks/`：RDS 快照恢复到副本、mark-replica、迁移 up / down 演练、应用 / 迁移 / PITR 回滚；第 6 节列探针、NetworkPolicy、服务端 TLS、Host、出网的演练核对项；测试核对命令、变量名、确认串与执行器一致。

## 测试
子代理逐目标（严格模式）：deploy-check（207 测试 + R01–R19）、staging-mutation（50 + 9）、learning-test 3743、insight / knowledge / scheduler 测试与变异、review-console-mutation、bff-test、bff-mutation（相关三组）、web-test、三科适配器测试、adapter-aya-mutation、attack-surface-check、contract-test 通过。严格 `make check` 见提交说明。

## 审查意见的处理
- 契约审查阻断：适配器改代码违反 LT-01 → 用户决定撤回；k8s 经 clusterIP 调用被 Host 校验拒绝 → 额外允许 Host + 一致性测试。建议采纳（文档与代码对齐、readyz 核对 0058 执行权、health.py 一致、探针演练核对项）。
- 安全审查阻断：100.64.0.0/10 可绕过出网白名单 → 安全组收窄 + OSS 网关终端节点 + R16；演练手册副本限定只靠手工改标签 → 用户决定执行器子命令；“缺省签名”未核对 → 取消 acr 模式、只用 cosign 并实际验签。建议全部采纳（云防火墙确认变量、readyz 防放大、KMS 专用密钥与最小权限、R17–R19、工具遥测关闭、docker logout）；四个主服务强制 TLS → 用户决定 staging 强制 verify-full。

## 教训
- 部署清单的服务间地址（clusterIP）要和被调方的 Host / 地址校验一起测，静态结构校验与探针（Host 为 Pod IP）都发现不了“互调被拒”。
- “默认拒绝出网”要把云厂商内部网段（100.64.0.0/10）算进去，否则 NAT 防火墙与主机名白名单形同虚设。
- 流水线里“已开启 / 已核对”的说明必须对应真实的核对步骤；离线无法核实的签名模式宁可取消。
- 演练手册中的破坏性操作，安全前提必须由执行器强制，不能只靠手工 SQL 与变量命名。
- 子代理多次违反命令约束（首轮：heredoc 运行只读 python、两处 `2>/dev/null`、/tmp 遗留变异临时目录；修复轮：一处 `>/dev/null 2>&1`），均为只读检查、无外部影响；/tmp 下 `aya-mut.*` 共 20 个目录（约 9MB，仓库副本）未清理——删除被仓库守卫钩子拦下（递归删除须人确认）。今后指令中给出允许的替代写法（如检查目录用 Glob / Read）。
- 改公共函数或文本后，各变异脚本的锚点会失效（本轮 insight、bff 各 1 条），实现阶段就要同步。

## 待人确认
1. **开通与执行（由人）**：阿里云账号与计费（云防火墙、ACR 企业版为包年包月）；在有工具的环境执行 `terraform validate` / plan 并按报错修正属性名；填写 tfvars（`cloud_service_endpoint_cidrs`、`model_egress_fqdns`、办公网 / VPN 网段等）；镜像加签与验签配置；前端内网 HTTPS 的私有 CA 证书（与 spec“不做证书”冲突，staging 下 `BFF_SHELL_ORIGIN` 必须 https）。
2. **许可证**：已决定用 OpenTofu（决定记录第 10 条，2026-10-27）。
3. **RDS**：高权限账号能否建 `BYPASSRLS` 角色、能否在 `starbuddy_ext` 装 pgcrypto（不能则属必停）；`connection_string_prefix`、`ssl_connection_string`、`pg_hba_conf` 参数名与内网域名后缀；自定义 pg_hba 后系统条目是否仍含 host 类型（演练时 `sslmode=disable` 必须失败）。
4. **网络**：企业级安全组是否确实不支持拒绝规则与优先级（改普通安全组的理由）；OSS 网关终端节点在 cn-beijing 的可用性与写法；NAT 边界防火墙能否由 Terraform 开启；VPN 若保留源地址需补办公网网段；DNS 隧道外泄（CoreDNS 转发白名单）低优先级。
5. **签名**：ACR 加签所需规格（缺省 Advanced）；cosign 私钥以文件放凭据管家是否接受；本地公钥验签时是否仍联网初始化 TUF 根（需抓包确认）。
6. **取值**：日志留存 180 天、OSS 生命周期天数、RDS 备份保留 14 天。
7. **其他**：knowledge-snapshot 用 RWO 云盘，快照写入方式另议；健康端点是否另登其余服务的攻击面条目由安全负责人定；探针不受 NetworkPolicy 拦截须在 staging 演练核对。

## 补记（2026-10-28）：OpenTofu 与 Terraform 子账号策略草案
- 决定记录第 10 条：用 OpenTofu。`deploy/runbooks/ram/` 三条 RAM 策略草案（基础设施允许、数据服务允许、显式拒绝）与 `deploy/tests/test_ram_policies.py`；手册第 0.1、0.3、0.6 节、1.4 审阅清单、阶段 A / B / C 与新增阶段 R 同步。
- 安全审查两轮：B1（RAM 写动作可经新策略 / 新角色提权）→ 子账号不授任何 RAM 写动作，`secret_reader` 策略、角色、挂载改为阶段 R 由人用管理员身份 apply；B2 / N1 / N2（查询通配夹带日志正文、镜像登录凭据、kubeconfig、集群接入脚本与证书、数据库日志与备份下载链接）→ 日志、镜像仓库逐个列查询动作，其余敏感读取进拒绝；B3（关桶级阻止公共访问再改 ACL）→ 拒绝账号级阻止的修改，主账号开启账号级阻止列为开工阻断项。测试按产品登记查询通配、展开通配查重叠。
- 教训：查询类通配不等于只读元数据——各云产品的 Describe / Get 里常夹着数据下载链接与凭据，授通配前要逐产品排查，或干脆逐个列。

### 待人确认（续）
8. 草案里的动作名、资源 ARN 格式（尤其 OTS、日志、PassRole 的服务角色名）以 RAM 控制台校验为准；首次 plan / apply 报权限不足时按报错逐个补，补的动作同样过测试。
9. 锁定的 provider 版本读取 `alicloud_cs_managed_kubernetes` 时是否调用证书 / kubeconfig 接口并把客户端私钥写进状态；若会，子账号读状态桶即可绕过拒绝，须换版本或另想办法。
10. 残余风险：子账号仍可改节点池（自定义数据、扩容）、绑 EIP、放开安全组入方向，直接调 API 不经 plan 审阅。建议 D1 的 EIP 绑定由人执行，全部阶段完成后撤掉写类策略只留只读；是否接受由人定。
11. 阶段 R 由管理员 apply 的状态与代码，子账号可写状态桶：管理员只从已审阅的提交执行，plan 前核对状态桶版本。
12. 走 STS 时 `sts:AssumeRole` 的精确 ARN 授权未写成 JSON，由人另建。

## 补记（2026-10-29）：首次 tofu validate 与开工前只读核对
- 开工前只读核对：网络（`registry.opentofu.org`、provider 下载、阿里云各 OpenAPI / OSS / OTS 端点）通；子账号凭据有效；但状态桶 `GetBucketInfo` / `ListObjects`、`vpc:DescribeVpcs`、`kms:ListKeys` 全部隐式拒绝——三条 RAM 策略尚未生效（用户去挂载）。状态桶版本控制 / 加密与锁表主键待权限生效后再核。
- 第一次在有 provider 的环境执行 `tofu validate`（OpenTofu 1.9.0，aliyun/alicloud 1.294.0，`~> 1.230` 解析结果），按 provider 文档修正三处：
  `alicloud_rds_backup_policy` → `alicloud_db_backup_policy`（属性名不变）；`alicloud_cr_ee_vpc_endpoint_linked_vpc` → `alicloud_cr_vpc_endpoint_linked_vpc`（属性不变）；
  云防火墙 `domain_resolve_type` 由字符串改为数字 `2`（FQDN 与 DNS 动态解析都用，与原意一致）。手册阶段 B / B2 的 `-target` 与 `check_iac.py`、测试同步。
- `tofu fmt -check` 原先不过（只差空白），一并格式化；多行列表改为一项一行，变异锚点同步。
- `make iac-validate IAC_PLUGIN_DIR=<provider 缓存>` 的 validate 通过；tflint、checkov、tfsec 本机没有，仍为跳过。

### 待人确认（续）
13. provider 1.294.0 在 OpenTofu registry 没有 GPG 公钥，init 提示“跳过签名校验”，只靠锁文件里的哈希；是否接受由人定。`.terraform.lock.hcl` 按手册 1.1 由人另起 PR 提交（只含当前平台 linux_amd64 的哈希，跨平台需 `tofu providers lock -platform=...`）。
14. `log_backup_retention_period` 大于 `backup_retention_period` 时 provider 会静默改成后者，之后每次 plan 都有差异；当前两者缺省都是 14，tfvars 里改值时保持日志备份 ≤ 数据备份。
15. 不买 KMS 实例后的替代方案（决定记录第 14 条，阶段 B 之前定）：RDS 云盘加密改用服务密钥或不加密、OSS 业务桶改 OSS 完全托管加密（AES256）、ACK Secret 落盘加密与节点系统盘、ESSD 存储类的加密方式、凭据管家（ack-secret-manager）的替代（例如由人在跳板 kubectl 建 Secret）。staging 存放内部家庭（含未成年人）的真实数据，降级部分须写明残余风险，由用户确认。
16. 首次只读核对时 OSS 返回的拒绝类型是 `ResourceGroupLevelIdentityBasedPolicy`：策略可能挂在了资源组范围；OSS、表格存储等对资源组授权支持不全，子账号策略须按“账号级别”授权。

## 补记（2026-10-29）：阶段 A 已 apply
- 开工前核对排障：RAM 策略起初没挂到子账号（个人权限为空）；子账号 AK 上有一条“启用但没有白名单”的网络访问限制策略，表现为 OSS 报 AK disabled、其他产品报 `InvalidAccessKeyId.AccessPolicyDenied`，由人禁用该策略后恢复；表格存储实例网络 ACL 起初不放行公网（`OTSAuthFailed`），由人放开；状态桶版本控制由人开启。
- 用户决定（“按默认”）：`office_vpn_cidrs = []`，阶段 A 不建 `slb_acl` 两个资源（暂无办公网 / VPN；以后改白名单要删条目，子账号被拒绝，届时由人处理）；`cloud_service_endpoint_cidrs` 取会话从公网 DNS 解析出的 cn-beijing 内网端点所在 /24（OSS 内网、SLS 内网、STS 与 ACK 管控、ACK 匿名端点；不含 KMS），阶段 C 之后从 VPC 内复核；`k8s_version`、`node_key_pair_name` 为临时值，阶段 C 之前换真实值。
- 阶段 A plan：23 新增 / 0 修改 / 0 删除（VPC、6 交换机、普通安全组与 15 条规则；无计费资源），用户确认后只 apply 该计划文件，计划文件已删；验收：状态 23 个资源、同目标重新 plan 为 No changes、状态桶出现 `staging/terraform.tfstate`。
- 阶段 C 之前由主账号完成容器服务的云资源访问授权（核对时报 `EntityNotExist.Role: aliyuncsdefaultrole`）。

## 补记（2026-10-30）：按决定记录第 15 条改 IaC（不买 KMS 实例）
- `kms.tf` 移除两把自建密钥与别名，`secret_reader` 改为只有 `oos:GetSecretParameter`（11 个参数 ARN）；`rds.tf` 用变量 `rds_disk_encryption`（deferred → ServiceKey 两步）；
  `oss.tf` AES256；`ack.tf` `disable_encryption = true`、系统盘不指定 KMS 密钥；`outputs.tf` 去掉两个 KMS 输出；存储类去掉 `kmsKeyId`。
- `check_iac.py` R06 / R07 / R09 改为“必须加密，可用服务密钥或 AES256”，Secret 落盘要么给密钥要么显式 `disable_encryption = true`；有自建密钥时仍查轮转与删除保护（变异改为临时加一把不合格的密钥）。
- 手册：阶段 K 改为说明段；阶段 B 加 B-enc；3.1 先核对 RDS 云盘加密；3.3 真实数据门槛；4.1 改 OOS 加密参数；走查只用合成数据。

### 待人确认（续）
17. `kms-alternatives.md` 里的“需人核实”：RDS / ECS 服务密钥与 OOS 加密参数是否免费、是否需要 KMS 实例；RDS 改服务密钥是否重启、子账号需要的 RDS 动作名（B-enc 首次 plan / apply 按报错补）；
    读 OOS 加密参数是否还要 KMS 动作（首次同步按报错补，阶段 R 由人改策略）。帮助中心不在会话放行名单，未能核实。
18. OOS 加密参数的取值长度上限：签字配置文件（8 个）与 `otp.json` 能否放进单个参数；放不下时另定办法（例如拆分或由人 kubectl 手建这两个 Secret）。
19. cosign 签名私钥原定存凭据管家（`deploy/ci`），不买 KMS 实例期间要另定存放处（例如云效流水线自带的密钥变量）；本次未改 CI 文件。

## 补记（2026-10-30）：提交 35ac408 的安全审查与处理
- 阻断 B1（真实数据门槛能绕过：内测手册没写、措辞只说“导入”）→ `docs/ops/internal-test-runbook.md` 第 0 节加第 7 条硬前置；开关矩阵 `internal_test_ops` 加 pending `kms_instance`，测试核对 `ack.tf` 有 `disable_encryption = true` 时不能解除；手册第 5 节表补一行；`providers.tf` 注释改正。
- 阻断 B2（ACK 不加密 Secret 后的补偿控制不可执行）→ 手册 4.1 写明集群权限（只部署者是管理员、全集群只部署者能建同步规则）与 `kubectl auth can-i` 逐条核对；系统组件读 Secret 的绑定由人核对。
- 阻断 B3（恢复 / 回滚手册仍写 KMS，恢复出的新实例没有加密核对）→ `rollback.md`、`rds-restore-and-migrate-rehearsal.md`、`weekly-restore-verify.md` 改为服务密钥并加“核对云盘加密已开启，没开不切换 / 不演练”。
- 建议 S1 / S2（R09 能被 null、空串、缺省 deferred 绕过）→ R09 只收 `"ServiceKey"`、自建密钥引用或两步三元表达式；变量不得有缺省值；ACK 集群加 `precondition var.rds_disk_encryption == "ServiceKey"`；新增 6 个变异。
- 建议 S3（secret_reader 测试太松）→ 测试锁定动作集合恰为 `oos:GetSecretParameter`、一条 Statement、信任条件 aud / iss / sub / Federated。
- 建议 S4（子账号策略）→ 去掉 `kms:Describe*` 等查询（IaC 已无 KMS）；拒绝策略与 SENSITIVE_READS 加 OOS 三个读参数动作。**控制台里已挂的两条策略需由人按新文件更新**（拒绝策略多三项、数据策略少三项）。
- 建议 S6（encryption_key 是否 computed）→ 已用 provider 1.294.0 schema 核实：Optional + Computed。
- 未处理、待人确认：数据标签 `data-class` 是否在不买 KMS 期间改为 `synthetic-only` 并与 R09 联动（审查 S2 选项二；现已由 `kms_instance` 门槛测试绑定，标签改动会让阶段 A 的 23 个资源出现标签修改）；“合成数据”边界——员工自己的真实手机号能否用于白名单登录走查（手册第 6 节）；白名单条目导入方式（内测手册与 `tools/internal-test` 仍写凭据管家；内测要等 `kms_instance` 解除，届时随凭据去向一并定）。

## 补记（2026-10-30）：阶段 B 首次 apply 失败（没有建出任何资源）
- OSS：provider 建桶前调 `ListBuckets` 查重名，子账号没有该权限 → 数据策略单独一条 `oss:ListBuckets`（资源只能写账号级 `acs:oss:*:*:*`，只返回桶名），测试只给这一对开例外。
- RDS：`ServiceLinkedRole.NotExist`，由主账号建 RDS 的服务关联角色（子账号被拒绝 `ram:CreateServiceLinkedRole`）。
- 日志服务：`AccountNotExist`，账号未开通日志服务，由主账号开通。
- 计划文件已删；三项处理完后重新 plan、重新确认。教训：手册 0.1 的“一次性授权”清单要在首次 apply 前逐项对照控制台核实，不能只靠 plan——plan 不调用创建接口，发现不了未开通与缺角色。

## 补记（2026-10-30）：阶段 B 第二次 apply 部分成功
- 建成：OSS 业务桶、ACL、阻止公共访问、网关终端节点与路由表挂载；RDS 实例（按量计费开始）与日志 Project 建成但报错、被标记 tainted。未建：RDS 备份策略、两个日志库、日志索引。
- RDS 报错：`log_statement`、`log_error_verbosity` 在 RDS 不开放修改 → 用户决定从 IaC 去掉、迁移前人工核对（`specs/plt-db-log-redaction` 决定记录）。报错时实例的删除保护还没打开、`log_parameter_max_length` 为 -1，下一次 apply 纠正；库里没有数据。
- 日志 Project 报错：provider 建完读 `log:GetProjectPolicy` 被拒 → 按 provider 1.294.0 源码一次查全日志资源的读接口，数据策略补 `log:GetProjectPolicy`、`log:GetLogStoreMeteringMode`、`log:ListShards`（只读元数据）。
- 用户同意后对两个资源执行 `tofu untaint`（只改状态，不动云上资源）。
- 教训：子账号策略里逐个列出的查询动作，要按 provider 源码里各资源 Read 函数实际调用的接口核对，不能只按 API 文档猜；RDS 参数要先查参数模板确认可改。

## 补记（2026-10-30）：阶段 B 第三次 apply
- RDS 原地修改完成并只读核对：删除保护开、SSL 开（证书签内网前缀）、pg_hba 只剩 hostssl + scram（阿里云默认的 host replication 0.0.0.0/0 md5 已去掉）、5 个日志参数为目标值；白名单另有阿里云自动加的隐藏组 `hdm_security_ips`（100.104.x.x，数据库自治服务）。日志库 app / audit 与索引建成。
- 备份策略报 `IncorrectCompressType`（PostgreSQL 云盘不收 "4"）→ `rds.tf` 去掉 `compress_type`（Optional + Computed，用缺省）；用户同意后 untaint 备份策略。
