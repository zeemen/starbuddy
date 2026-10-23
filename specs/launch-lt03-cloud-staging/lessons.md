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
2. **许可证**：Terraform BSL 1.1 是否接受（否则 OpenTofu）。
3. **RDS**：高权限账号能否建 `BYPASSRLS` 角色、能否在 `starbuddy_ext` 装 pgcrypto（不能则属必停）；`connection_string_prefix`、`ssl_connection_string`、`pg_hba_conf` 参数名与内网域名后缀；自定义 pg_hba 后系统条目是否仍含 host 类型（演练时 `sslmode=disable` 必须失败）。
4. **网络**：企业级安全组是否确实不支持拒绝规则与优先级（改普通安全组的理由）；OSS 网关终端节点在 cn-beijing 的可用性与写法；NAT 边界防火墙能否由 Terraform 开启；VPN 若保留源地址需补办公网网段；DNS 隧道外泄（CoreDNS 转发白名单）低优先级。
5. **签名**：ACR 加签所需规格（缺省 Advanced）；cosign 私钥以文件放凭据管家是否接受；本地公钥验签时是否仍联网初始化 TUF 根（需抓包确认）。
6. **取值**：日志留存 180 天、OSS 生命周期天数、RDS 备份保留 14 天。
7. **其他**：knowledge-snapshot 用 RWO 云盘，快照写入方式另议；健康端点是否另登其余服务的攻击面条目由安全负责人定；探针不受 NetworkPolicy 拦截须在 staging 演练核对。
