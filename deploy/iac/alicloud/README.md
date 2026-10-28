# 阿里云 staging 基础设施草案（LT-03，Terraform）

依据：`specs/launch-lt03-cloud-staging` 决定记录（2026-10-21）——阿里云华北 2（北京）、托管 K8s（ACK）沿用 `deploy/k8s/staging.yaml`、
只内网 + VPN / 办公网白名单、不公开域名；Terraform 仅 validate 与静态检查，**不执行 plan / apply**。
本目录**没有状态文件、没有凭据、没有真实账号 ID、没有真实地址**；开通、账号、计费、网络与资源创建、执行 IaC 一律由人完成。

| 文件 | 内容 |
|---|---|
| `versions.tf` | Terraform 与 aliyun/alicloud provider 的版本约束；OSS 远端状态（部分配置） |
| `providers.tf` | 地域（只引用 `var.region`）、账号数据源（账号 ID 只在 plan 时取得）、统一标签 |
| `variables.tf` | 全部变量；没有缺省值的由人填写（`terraform.tfvars.example`） |
| `network.tf` | VPC、节点 / Pod / 数据库交换机（两个可用区）、普通安全组（入方向只收 VPC；出方向白名单：VPC、内网 DNS 53、元数据 80、服务内网端点 443、公网 443，100.64.0.0/10 与 169.254.0.0/16 在公网 443 之前拒绝，最后全拒绝）、NAT（只 SNAT，排在云防火墙访问控制之后、须人确认防火墙已开）+ EIP（只绑 NAT） |
| `ack.tf` | ACK Pro 托管集群（API Server 不开公网、Terway + NetworkPolicy、Secret 落盘 KMS 加密、控制面日志、RRSA；LT-05 加 ARMS 托管 Prometheus 采集组件 arms-prometheus，R20）与节点池（系统盘 KMS 加密、无公网地址） |
| `rds.tf` | RDS PostgreSQL 16 高可用（只内网、白名单只收 VPC、KMS 云盘加密、删除保护；强制 TLS：证书只签内网连接地址、pg_hba 只有 hostssl）与备份策略（每日 + 日志备份 = PITR） |
| `oss.tf` | 私有桶（私有 ACL、阻止公共访问、KMS 服务端加密、版本控制、生命周期）；OSS 网关终端节点（策略只放行本桶，待人确认） |
| `acr.tf` | ACR 企业版（缺省 Advanced，待人核实）：私有命名空间与仓库（10 个服务镜像 + 2 个基础镜像）、VPC 访问端点 |
| `egress.tf` | 出网白名单：云防火墙 NAT 边界只放行 `var.model_egress_fqdns` 的 TCP 443，最后一条拒绝其余；`nat_firewall_confirmed` 由人确认 |
| `sls.tf` | 日志服务：留存 ≥ 180 天、服务端加密、不建全文索引；app 日志库只给白名单字段建字段索引（LT-05，R21）；不记个人信息的口径在应用侧（各服务 obs 模块集中脱敏） |
| `kms.tf` | KMS 主密钥与凭据管家专用密钥（都自动轮转、删除保护）；ack-secret-manager 的最小权限 RAM 角色（逐个列名的凭据、只用专用密钥 Decrypt，不用通配符） |
| `slb.tf` | 内网负载均衡的访问控制（办公网 / VPN 网段白名单）；负载均衡本身由 ACK 按 web Service 创建 |
| `outputs.tf` | 只给执行 apply 的人填部署配置用的标识与内网地址（不提交） |

## 1. 工具与许可证核对（离线核对，未联网下载任何工具）

| 工具 | 许可证 | 结论 |
|---|---|---|
| Terraform ≥ 1.6 | BSL 1.1（Business Source License） | 只作开发 / 运维工具在内部本机与 CI 使用、**不分发、不对外提供托管或竞争性服务**，在 BSL 附加使用授权范围内，可用。**已决定改用 OpenTofu**（MPL-2.0，LT-03 决定记录第 10 条，2026-10-27；本目录写法两者通用，`make iac-validate` 自动识别 `tofu`） |
| aliyun/alicloud provider | MPL-2.0 | 可用（文件级弱 copyleft；只调用、不修改、不分发） |
| tflint | MPL-2.0 | 可用（开发工具，不分发） |
| checkov | Apache-2.0 | 可用 |
| tfsec | MIT（已并入 Trivy，Trivy 为 Apache-2.0） | 可用 |

本机（Claude 的工作环境）没有 terraform / tofu / tflint / checkov / tfsec，也不联网下载（不重装依赖原则），所以：
- **总是运行**：`deploy/iac/check_iac.py`（只用标准库的 HCL 小解析器 + 22 条规则 R01–R22，随 `make deploy-check`；规则逐条有变异测试 `deploy/tests/test_iac.py`；
  R20、R21 为 LT-05 新增：采集组件 arms-prometheus / logtail-ds 必须装、告警联系人类资源不进 IaC；日志库不建全文索引、字段索引只收白名单字段；
  R22 为 specs/plt-db-log-redaction 新增：RDS 慢查询与报错日志不写绑定参数、报错不写 DETAIL、不记全部语句、不开 SQL 洞察——
  学生 ID、usage_id、幂等键原文不进数据库服务端日志；须人在控制台核实这七个参数可改、是否需重启）；
- **工具在时运行**：`make iac-validate`（`deploy/scripts/iac-validate.sh`：`terraform init -backend=false` + `terraform validate`、tflint、checkov、tfsec；
  缺工具打印“跳过”，非严格模式算通过、`CONTRACT_STRICT=1` 时失败）。provider 不联网下载：须由人准备本地镜像目录 `IAC_PLUGIN_DIR`。

## 2. 由人执行：validate 与静态检查（不连云、不建资源）
1. 在有工具的机器上准备 provider 离线包（`terraform providers mirror <目录>`，在可联网的构建机上做一次），记下 `IAC_PLUGIN_DIR`；
2. `make iac-validate IAC_PLUGIN_DIR=<目录>`（等价于在临时副本里 `terraform init -backend=false -plugin-dir=<目录>` 与 `terraform validate`，
   再跑 tflint、checkov、tfsec）；
3. 把 provider 锁到当时的确切版本，提交 `.terraform.lock.hcl`（只含 provider 摘要）。`validate` 报的属性名问题（provider 版本差异，例如
   云防火墙 NAT 边界策略、节点池加密参数、`tags` 支持情况）按报错改正后重跑 `make deploy-check`。

## 3. 由人执行：plan / apply（须批准；`[NEEDS-HUMAN]`）
前置：
- 账号与计费：开通 ACK Pro、RDS、OSS、ACR 企业版（包年包月）、云防火墙（包年包月，并为 NAT 网关开启 NAT 边界防火墙）、KMS、SLS；
- 凭据：执行人用 RAM 用户扮演专用 RAM 角色取 STS 临时凭据（最小权限，按本目录资源类型授权），经运行环境提供给 provider；
  **不写进任何文件**、不进仓库、不进 CI 日志；
- 远端状态：另建一个私有、KMS 加密、版本化的 OSS 状态桶与表格存储锁表；`backend.hcl`（不提交）写 bucket、prefix、key、region、
  tablestore_endpoint、tablestore_table、encrypt = true；
- 变量：复制 `terraform.tfvars.example` 为 `terraform.tfvars`（不提交）并填写；其中 `cloud_service_endpoint_cidrs` 按 cn-beijing 的 ACK 管控、
  SLS、KMS、STS、OSS 内网端点解析出的地址填写（只在 100.64.0.0/10 里、掩码不宽于 /24），`nat_firewall_confirmed` 在为 NAT 网关开启
  NAT 边界防火墙并经第二人核实之后才设 true；之前必须显式设 false（变量无缺省值），此时任何 SNAT 都过不了前置条件，出网不会被静默放开；
  分阶段 apply 见 deploy/runbooks/staging-first-deploy.md 第 1 节。

步骤：
1. `terraform init -backend-config=backend.hcl`；
2. `terraform plan -out=staging.tfplan`，由第二人审阅（重点：没有公网入口、出网白名单只有模型供应商主机名、RDS 白名单只有 VPC 网段、
   地域全部 cn-beijing、无 DNAT、OSS 私有）；计划文件不提交；
3. `terraform apply staging.tfplan`；
4. 按输出填写部署配置（不提交真实值）：`deploy/k8s/staging.yaml` 里的 `<由人填写…>` 占位（内网负载均衡交换机与 `slb_acl_id`、存储类 `kmsKeyId`、
   镜像仓库 `ACR_REGISTRY`、RDS CA 证书 ConfigMap）、凭据管家里的各服务凭据（连接串用 `rds_private_host`，见 `deploy/config/README.md` 第 8 节）；
5. 在凭据管家创建应用凭据时选凭据专用密钥（`alias/<名前缀>-secrets`）作加密密钥，名字是 `starbuddy-staging/<Secret 名>`（`kms.tf` 的
   `app_secrets` 名单，与 `secretKeyRef` 一致）；在 ACK 里建命名空间与 `ack-secret-manager` 的同步规则，并用 RBAC 限定只有部署者能在
   `starbuddy-staging` 命名空间创建 / 修改同步规则（ExternalSecret 这一类 CR）；
6. 数据库：在 RDS 控制台建高权限账号（口令只在凭据管家），用 `deploy/migrate` 先在快照副本上演练再执行 `db/bootstrap` 与迁移
   （`deploy/runbooks/rds-restore-and-migrate-rehearsal.md`）。

## 4. 网段与清单的对应（`deploy/tests/test_iac.py` 核对）
- VPC `10.20.0.0/16`；节点、Pod（Terway）、数据库交换机都在 VPC 内；Service 网段 `10.96.0.0/16`（不与 VPC 重叠），
  `deploy/k8s/staging.yaml` 里显式写的 clusterIP（10.96.100.x）都在这个网段里；
- Pod IP 来自 Pod 交换机（RFC 1918），各服务的 `*_HOST`（downward API `status.podIP`）与服务间地址的内网 IPv4 口径不变。

## 4b. 监控与日志采集（LT-05，specs/launch-lt05-observability；由人开通与配置）
- 开通 ARMS 托管 Prometheus（随 ACK 组件 `arms-prometheus`，新增云服务属必停）；apply 后在集群里应用 `deploy/k8s/observability.yaml`
  （PodMonitor 采各服务 `/metrics`、AliyunLogConfig 把容器标准输出 / 标准错误采进 SLS 的 `app` 日志库）；
- 核对 ARMS 采集组件所在命名空间与 Pod 标签，填进 `deploy/k8s/staging.yaml` 的 NetworkPolicy `metrics-from-collector`（占位 `<由人填写…>`）；
- 告警规则 `deploy/observability/alerts.yaml` 在 ARMS 控制台导入（阈值是占位缺省，待人签字）；仪表盘 `deploy/observability/dashboards/*.json`
  导入 ARMS 的 Grafana；**告警联系人、通知渠道、值守排班由人在控制台配置**，不进 IaC 与仓库（R20 拒绝告警联系人类资源）。

## 5. 已知与待人确认
- 迁移用到的扩展 `pgcrypto`（`WITH SCHEMA starbuddy_ext`）与 `db/bootstrap/roles.sql` 里带 `BYPASSRLS` 的属主角色：RDS 的高权限账号能否创建
  `BYPASSRLS` 角色、能否在自定义 schema 里装扩展，须由人在副本演练时确认；不行时另议（可能需要 RDS 的特权操作或调整属主设计，属必停项）。
- 云防火墙 NAT 边界、ack-secret-manager 组件与 RRSA 信任策略的参数名随 provider / 组件版本变化，以 `terraform validate` 与控制台为准。
- 出网白名单在三层：云防火墙（主机名）、安全组（出方向白名单，公网只开 TCP 443；阿里云内部网段只开具体地址与端口）、NetworkPolicy
  （只学习服务 Pod，except 排除内网、100.64.0.0/10 与 169.254.0.0/16）；路由建连前还核对 `allowed_hosts`。
- 待人核实 / 确认（LT-03 修订）：
  - 企业级安全组是否支持拒绝规则与优先级——本草案改用普通安全组（`security_group_type = "normal"`，组内互通 Drop、只按规则）；
  - RDS：`connection_string_prefix`、`ssl_connection_string`、`pg_hba_conf` 的参数名与取值规则；设置自定义 pg_hba 后系统条目里是否仍有 `host`
    类型（演练核对：从 VPC 内用 `sslmode=disable` 连接必须失败）；内网域名后缀 `.pg.rds.aliyuncs.com`；
  - OSS 网关终端节点（`alicloud_vpc_gateway_endpoint` 与路由表挂载）在 cn-beijing 是否可用、`service_name` 写法与策略语法；
  - NAT 边界防火墙用 Terraform 开启（`alicloud_cloud_firewall_nat_firewall`）的参数与计费——本草案只用“确认变量 + SNAT depends_on”兜底；
  - ACR 企业版加签要求的规格（缺省 Advanced）。
