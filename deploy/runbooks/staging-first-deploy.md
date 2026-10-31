# staging 首次部署与走查手册（LT-01 至 LT-05 串联）

适用：阿里云华北 2（北京）境内 staging 的**第一次**全量部署与上线前走查（specs/launch-test-2026；LT-01 部署打包、LT-02 模型供应商、
LT-03 云与镜像、LT-04 learning-internal v1 与迁移 0058、LT-05 可观测性）。以后的日常发布、演练与回滚仍按本目录其他手册。

**谁来做**（LT-03 决定记录第 7 条，2026-10-24）：
- Claude 会话只做 Terraform：用户在云环境设置里放行域名、以环境变量配置专用 RAM 子账号凭据（Claude 不读取、不打印其值）。
  **每一次 apply 之前，Claude 贴出 plan 摘要（资源增删改数量、计费类资源与预估费用口径），经用户逐次确认后才执行**；不执行 destroy
  （须另行确认）；状态只在 OSS 远程后端（私有、版本控制、OSS 完全托管加密）并加锁，不落仓库、不落会话临时目录。
- 其余步骤（VPC 内的数据库、集群、凭据、控制台开通与计费）**由人执行**：一人操作、一人复核关键输出，变更记录留痕（时间、操作人、复核人、结果），
  记录里不写学生数据、不写凭据。
- 全部 `<…>` 是由人填写的占位；本手册与仓库里**没有任何真实值**（账号 ID、地址、实例名、凭据、证书、手机号）。

**必停项**（CLAUDE.md）：执行迁移（第 3 节）、RLS 与权限、计量、契约、新增第三方服务、模型供应商开通——每一项都须人批准后再做，Claude 不代为执行。

**总顺序**：第 0 节准备 → 第 1 节 Terraform 分四阶段 apply → 第 2 节镜像 → 第 3 节数据库（副本演练 → 主实例迁移，0058 先于学习服务）→
第 4 节 k8s 按依赖部署 → 第 5 节开关与内容逐项解除 pending → 第 6 节走查 → 第 7 节回滚口径（任一步失败时）。

---

## 0. 前置：用户侧准备

### 0.1 两类身份（RAM）
| 身份 | 用途 | 凭据形态 |
|---|---|---|
| Terraform 子账号 `<Terraform RAM 用户>` | Claude 会话执行 init / plan / apply；读写状态桶与锁表 | 环境变量（见 0.5），由用户在云环境设置里配置；建议改为扮演 `<Terraform RAM 角色>` 取 STS 临时凭据 |
| 运维人员 `<运维 RAM 用户>` | 跳板上的 psql、kubectl、控制台操作 | 扮演运维角色取临时凭据，不落盘、不进命令历史（`HISTCONTROL=ignorespace`） |

**Terraform 子账号的最小权限策略清单**（由人在 RAM 控制台写成自定义策略，第二人审阅后挂载；动作逐个列出，只读的查询类动作可按产品前缀放行，
资源尽量限定到 `cn-beijing` 与占位资源名；写类动作不用通配符 `*`——与 `deploy/iac/check_iac.py` 规则 R19 口径不同：R19 管 IaC 里建的策略（动作与资源都不用 `*`），本节的子账号策略里，不支持资源级授权的动作只能写 `Resource: "*"`）。
策略草案已写成 JSON（占位名替换后粘贴进 RAM 控制台，三条分别建自定义策略，挂给子账号（走 STS 时挂给 `<Terraform RAM 角色>`，见本节末）；动作名以控制台校验为准，报“无效操作”的逐个核实后改草案；`deploy/tests/test_ram_policies.py` 静态核对）：`ram/terraform-allow-infra.json`（网络、ECS、ACK、负载均衡、服务角色 PassRole）、`ram/terraform-allow-data.json`（RDS、OSS 业务桶与状态桶、锁表、日志、ACR、云防火墙；KMS 与 RAM 只读）、`ram/terraform-deny.json`（删除 / 释放、全部 RAM 写动作、读日志正文 / 镜像仓库登录凭据 / kubeconfig / 实例自定义数据 / 数据库日志与备份下载、公网连接、桶策略与 CORS、账号级阻止公共访问的修改）。状态桶用 OSS 完全托管加密（AES256，LT-03 决定记录第 12 条），子账号不需要也不授任何 KMS 解密动作（Decrypt、GenerateDataKey 显式拒绝）。下表是各产品的动作类别：

| 产品（RAM 动作前缀） | 允许的动作类别 | 对应 IaC 文件 |
|---|---|---|
| 专有网络 `vpc:` | VPC、交换机、NAT 网关、SNAT 条目、EIP 申请与绑定、网关终端节点与路由表挂载的创建 / 查询 / 修改 / 打标签 | `network.tf`、`oss.tf` |
| 云服务器 `ecs:` | 安全组与安全组规则的创建 / 查询 / 修改；密钥对查询（只读）；节点所需的实例与云盘查询 | `network.tf`、`ack.tf` |
| 容器服务 `cs:` | 托管集群与节点池的创建 / 查询 / 修改、组件安装与查询 | `ack.tf` |
| 云数据库 `rds:` | 实例创建 / 查询 / 修改、SSL 与 pg_hba 设置、白名单、备份策略 | `rds.tf` |
| 对象存储 `oss:` | 账号级 `ListBuckets`（provider 建桶前查重名，只返回桶名；单独一条 Statement）；业务桶的创建与 ACL、阻止公共访问、版本控制、加密、生命周期；**状态桶**的对象读写（GetObject、PutObject、ListObjects，只限状态桶与 `<状态前缀>`） | `oss.tf`、0.2 |
| 表格存储 `ots:` | 锁表的行读写（GetRow、PutRow、UpdateRow、DeleteRow——锁释放需要删行，只限锁表）与表查询 | 0.2 |
| 容器镜像服务 `cr:` | 企业版实例、命名空间、仓库、VPC 访问端点的创建 / 查询 | `acr.tf` |
| 云防火墙 `yundun-cloudfirewall:` 或 `cloudfw:`（以控制台为准） | NAT 边界访问控制策略的创建 / 查询 / 修改 | `egress.tf` |
| 日志服务 `log:` | Project、日志库、索引的创建 / 查询 / 修改 | `sls.tf` |
| 密钥管理 `kms:` | **不授任何动作**（IaC 已没有 KMS 资源与数据源，安全审查 S4）；现阶段不买 KMS 实例、IaC 不含自建密钥（LT-03 决定记录第 14、15 条），不授任何写动作；Decrypt、GenerateDataKey、GetSecretValue 显式拒绝 | — |
| 访问控制 `ram:` | **只读**（Get / List 类，plan 刷新状态用）与容器服务、RDS 加密服务角色的 PassRole；**不给任何写动作**（安全审查 B1：能建策略与角色就能给自己提权）——`kms.tf` 的 `secret_reader` 策略、角色与挂载由人用管理员身份在阶段 R apply | `ack.tf`；`kms.tf` 见阶段 R |
| 安全令牌 `sts:` | 不授：GetCallerIdentity（`data.alicloud_account.current` 取账号 ID）不需要 RAM 授权。扮演 `<Terraform RAM 角色>` 时，AssumeRole 只授给该角色的精确 ARN（不挂系统策略 AliyunSTSAssumeRoleAccess） | `providers.tf` |
| 计费 `bss:` / `bssapi:` | **不授**：下单接口能买任何产品；ACR 企业版实例（包年包月）在阶段 B' 由人用管理员身份 apply，云防火墙由人在控制台购买 | `acr.tf` |

另加一条**显式拒绝**（防止误删，与“不执行 destroy”一致）：各产品的删除 / 释放类动作（例如释放 RDS 实例、删除集群、删除桶、计划删除 KMS 密钥、
删除日志 Project、删除 ACR 实例），以及 `ram:` 的创建用户、创建 AccessKey、给用户挂策略（防止提权）。若某次 plan 里的“修改”需要先删后建，
apply 会被这条拒绝挡下——这正是要停下来由人决定的情形。
一次性授权与账号级设置（由主账号在控制台完成；首次阶段 B apply 时实际撞到的：RDS 的服务关联角色（`ServiceLinkedRole.NotExist`）、日志服务未开通（`AccountNotExist`）；草案拒绝了 `ram:CreateServiceLinkedRole` 与账号级阻止公共访问的修改）：容器服务的服务角色；RDS 云盘加密（AliyunRDSInstanceEncryptionDefaultRole）与 ECS 云盘加密的服务角色；NAT 网关、ACR VPC 端点、云防火墙首次使用时的服务关联角色；**OSS 账号级阻止公共访问开启**（安全审查 B3：即使桶级设置被改，桶也不能公开）。
集群建好后（阶段 C 验收后）由人在 ACK 授权管理里收回 Terraform 身份自动获得的集群 RBAC（建集群的身份默认是 cluster-admin），并核实草案已拒绝取 kubeconfig。
走 STS 时：三条策略挂给 `<Terraform RAM 角色>`，子账号只有扮演该角色的权限；状态桶不配桶策略，不需改动。

### 0.2 状态桶与锁表（Terraform 远程后端的引导，由人在控制台创建；不在本目录的 Terraform 里）
状态桶必须先于第一次 `terraform init` 存在（鸡生蛋问题），所以由人手工创建，**不**交给本目录的 Terraform 管理：
1. OSS 状态桶 `<状态桶名>`：地域 cn-beijing、私有 ACL、阻止公共访问、**版本控制开启**、服务端加密选 **OSS 完全托管**（AES256；不用 KMS，免买 KMS 实例，LT-03 决定记录第 12 条）、
   不配公共读与 CORS、不配会删除当前版本的生命周期规则（历史版本可设保留期）；不配 Bucket 授权策略（子账号的访问已由 RAM 策略限定到本桶与 `<状态前缀>/`，桶策略只会多一处要维护的授权；子账号也被拒绝改桶策略）；开访问日志。
2. 表格存储实例 `<OTS 实例名>`（cn-beijing）与锁表 `<锁表名>`：主键只有一列 `LockID`（字符串）；实例的网络访问须允许 Claude 会话所在出口
   （公网访问），并用 RAM 策略只放行 `<Terraform RAM 用户>`。
3. 第二人核对：桶为私有且版本控制已开、加密为 OSS 完全托管、锁表主键正确；把桶名、前缀、锁表名写进变更记录（不进仓库）。

### 0.3 需放行的域名（由用户在 Claude 会话的云环境设置里配置；以 provider 实际访问为准，首次 plan 被拦时按报错补）
- OpenTofu（LT-03 决定记录第 10 条）：`registry.opentofu.org`；`tofu` 二进制与 aliyun/alicloud provider 的下载跳转地址（`github.com` 发布地址与
  `objects.githubusercontent.com`，可能还有 `release-assets.githubusercontent.com`，以下载时的跳转为准）。初始化脚本直接下载锁定版本的发布包并核对校验和，不用 `get.opentofu.org` 安装脚本。不放行 `registry.terraform.io`、`releases.hashicorp.com`。版本检查关闭（`CHECKPOINT_DISABLE=1`）。
- 阿里云 OpenAPI（华北 2）：VPC、ECS、ACK（cs）、RDS、KMS、RAM、STS、OSS（含状态桶与业务桶的 `cn-beijing` 端点）、表格存储（`<OTS 实例名>`
  的 `cn-beijing` 端点）、ACR（cr）、云防火墙、日志服务（`cn-beijing` 端点与 `<日志 Project>` 子域）、负载均衡（slb）、计费（bss）、端点发现（location）。
- 不放行其他公网目的地；模型供应商主机名属于学习服务 Pod 的出网（第 5 节），与 Claude 会话无关。

### 0.4 计费类资源清单（apply 前由人确认计费；**包年包月**须先在控制台确认时长与续费方式）
| 资源 | 计费方式 | 何时产生 | 提醒 |
|---|---|---|---|
| ACR 企业版（`alicloud_cr_ee_instance.main`） | **包年包月**（`acr_period_months` 缺省 1 个月，手动续费） | 第 1 节阶段 B | apply 即下单、不可按量退订；规格缺省 Advanced（待人核实） |
| 云防火墙（含 NAT 边界防火墙） | **包年包月**（不在 Terraform 里，由人在控制台购买并为 NAT 网关开启） | 第 1 节阶段 D 之前 | 没开时 SNAT 不建（`nat_firewall_confirmed`） |
| RDS PostgreSQL 16 高可用（`alicloud_db_instance.main`） | 按量（Postpaid） | 阶段 B | 副本演练每次另起一个按量实例，用完即释放 |
| ACK Pro 集群管理费 + 节点 ECS + 系统盘 | 按量 | 阶段 C | `node_count` 缺省 2 |
| 内网负载均衡（web Service 由 ACK 创建，`slb.s1.small`） | 按量 | 第 4 节 | |
| NAT 网关（Enhanced，PayAsYouGo）+ EIP（PayByTraffic，10 Mbps） | 按量 | 阶段 D | |
| 云盘（ESSD，PVC 2 × 20Gi） | 按量 | 第 4 节 | 存储类回收策略 Retain：删 PVC 不删盘，盘仍计费 |
| OSS（业务桶、状态桶）、SLS、OOS 加密参数（应用凭据，是否收费待人核实）、表格存储、ARMS 托管 Prometheus | 按量 | 第 0 节起 | ARMS 与 SLS 为新增云服务（必停，LT-05 决定记录第 1 条） |

预估费用由执行者按阿里云价格计算器或各产品询价结果写进 plan 摘要，注明口径（规格、时长、日期）；金额以控制台下单页为准。

### 0.5 需配置的环境变量（**只写名字，不写值**；值只在运行环境与 OOS 加密参数里）
| 在哪里 | 变量名 | 说明 |
|---|---|---|
| Claude 会话（Terraform） | `ALICLOUD_ACCESS_KEY`、`ALICLOUD_SECRET_KEY`、`ALICLOUD_SECURITY_TOKEN`（STS 时） | provider 与 OSS 后端都认这组名字；新名字 `ALIBABA_CLOUD_ACCESS_KEY_ID`、`ALIBABA_CLOUD_ACCESS_KEY_SECRET`、`ALIBABA_CLOUD_SECURITY_TOKEN` 后端是否认以所用 OpenTofu 版本文档为准，**两组不混用** |
| Claude 会话（Terraform） | `TF_DATA_DIR`、`TF_IN_AUTOMATION`、`TF_INPUT`、`CHECKPOINT_DISABLE` | `TF_DATA_DIR` 指向 `<计划目录>/.terraform`，让后端缓存与 provider 不落仓库 |
| 有工具的校验机 | `IAC_PLUGIN_DIR`、`CONTRACT_STRICT` | `make iac-validate` 的 provider 离线目录与严格模式 |
| 云效 Flow（镜像） | `ACR_REGISTRY`、`PYTHON_IMAGE`、`NODE_IMAGE`、`IMAGE_TAG`、`TRIVY_DB_REPOSITORY`、`TRIVY_JAVA_DB_REPOSITORY`、`SIGN_MODE`、`COSIGN_KEY_REF`、`COSIGN_PUB_REF`、`COSIGN_PASSWORD` | 前九个见 `deploy/ci/pipeline.sh`；`COSIGN_PASSWORD` 是 cosign 约定的私钥口令变量（只由 CI 注入，不打印） |
| 运维跳板（数据库） | `PRIMARY_DSN`、`REPLICA_DSN`、`PGPASSFILE`、`HISTCONTROL` | 主 / 副分开放，同一会话不同时设两个；连接串不带口令，口令只在 `PGPASSFILE`（0600） |
| 有凭据的 staging 执行环境（模型冒烟） | `SMOKE_ENV`、`MAX_CALLS` | `make model-smoke` 的参数（第 5 节） |
| 集群里各服务 | 见 `deploy/config/env-inventory.yaml` | 密钥类只经 `secretKeyRef`（第 4.1 节的 Secret 表），开关类取值见 `deploy/flags/staging.yaml` |

### 0.6 开工前的阻断项（不解决不进入第 1 节阶段 A）
1. **`nat_firewall_confirmed`（已解决，LT-03 决定记录第 8 条，2026-10-24）**：变量是 bool、没有缺省值（必须显式给出），不再有变量级 `== true`
   校验（`check_iac.py` 规则 R11 核对）；每条 SNAT 仍以 `var.nat_firewall_confirmed` 为前置条件。阶段 A–C 与 D1 一律传
   `-var nat_firewall_confirmed=false`（SNAT 不在 `-target` 里，出网保持不通）；D2 之前由人在控制台为 NAT 网关开启 NAT 边界防火墙、
   第二人核实后才传 `true`。
2. 许可证（已解决，LT-03 决定记录第 10 条，2026-10-27）：用 OpenTofu（MPL-2.0），不用 Terraform（BSL 1.1）。本手册命令里的 `terraform` 一律换成 `tofu`，
   子命令与参数相同；`make iac-validate` 自动识别 `tofu`。必填变量可用 `TF_VAR_<变量名>` 环境变量给出（列表写 JSON），此时 plan 不带 `-var-file`。
3. 包年包月与新增云服务（ACR 企业版、云防火墙、ARMS、SLS）已由人确认计费并开通。
4. `terraform.tfvars` 必填项已由人准备好（放 `<计划目录>`，不进仓库）：`k8s_version`、`node_key_pair_name`、`oss_bucket_name`、`office_vpn_cidrs`、
   `cloud_service_endpoint_cidrs`（只在 100.64.0.0/10 里、不宽于 /24）、`model_egress_fqdns`（供应商未开通时为空列表）、`nat_firewall_confirmed`、`rds_disk_encryption`（建 RDS 实例那一次传 deferred，之后一律传 ServiceKey，见阶段 B-enc）。
5. **OSS 账号级阻止公共访问已由主账号开启、第二人核实**（安全审查 B3：子账号能改桶级阻止公共访问与 ACL，桶不被公开全靠这一层）；0.1 节末的一次性授权已完成；`deploy/runbooks/ram/` 三条策略已替换占位名、在 RAM 控制台校验动作名后挂载。

---

## 1. Terraform（Claude 会话执行；每次 apply 前逐次确认）

### 1.1 版本锁定与工作目录
- 版本：用 OpenTofu 1.x（锁定到具体小版本，由初始化脚本安装）；`versions.tf` 要求 `>= 1.6.0, < 2.0.0`、provider `aliyun/alicloud ~> 1.230`；首次 init 生成的 `.terraform.lock.hcl`（只含 provider 摘要）
  由人另起 PR 提交到 `deploy/iac/alicloud/`，之后每次 init 都带 `-lockfile=readonly`。
- `<计划目录>`：仓库与会话临时目录**之外**的专用目录（0700），放 `backend.hcl`、`staging.tfvars`、`.terraform/`（经 `TF_DATA_DIR`）与计划文件；
  计划文件含状态快照，apply 后即删，不提交、不上传。命令里的 `<计划目录>` 一律写绝对路径（`-chdir` 会改变相对路径的基准）。

### 1.2 init（远程后端，`-backend-config` 参数清单）
`backend "oss" {}` 是部分配置，参数在 init 时给出（写在 `<计划目录>/backend.hcl`，不提交）：

| 参数 | 取值 |
|---|---|
| `bucket` | `<状态桶名>` |
| `prefix` | `<状态前缀>`（例如按环境区分的目录名） |
| `key` | `<状态文件名>` |
| `region` | `cn-beijing` |
| `tablestore_endpoint` | `https://<OTS 实例名>.cn-beijing.ots.aliyuncs.com` |
| `tablestore_table` | `<锁表名>` |
| `encrypt` | `true`（后端自身的服务端加密；另有状态桶的缺省加密 OSS 完全托管，见 0.2） |
| `acl` | `private` |

```
export TF_DATA_DIR=<计划目录>/.terraform TF_IN_AUTOMATION=1 TF_INPUT=0 CHECKPOINT_DISABLE=1
terraform -chdir=deploy/iac/alicloud init -input=false -backend-config=<计划目录>/backend.hcl
# 锁文件提交后：terraform -chdir=deploy/iac/alicloud init -input=false -lockfile=readonly -backend-config=<计划目录>/backend.hcl
terraform -chdir=deploy/iac/alicloud state list
# 首次应为空；不用会打印属性值的 state show / state pull
```

### 1.3 validate 与静态检查（全部通过才能 plan）
```
terraform -chdir=deploy/iac/alicloud fmt -check -recursive
terraform -chdir=deploy/iac/alicloud validate -no-color
python3 -I deploy/iac/check_iac.py deploy/iac/alicloud
make iac-validate IAC_PLUGIN_DIR=<provider 离线目录> CONTRACT_STRICT=1
make deploy-check
```
- `make iac-validate`（`deploy/scripts/iac-validate.sh`）在临时副本里 `init -backend=false` + `validate`，再跑 tflint、checkov（`--skip-download`）、tfsec；
  provider 离线目录用 `terraform -chdir=deploy/iac/alicloud providers mirror <provider 离线目录>` 准备一次。严格模式下缺工具即失败。
- validate 报的属性名问题（provider 版本差异：云防火墙 NAT 边界策略、节点池加密参数、`tags`、RDS 的 `pg_hba_conf` 等）改 IaC 属于另一次变更，
  改完重跑本节，**不在 plan 阶段临时绕过**。

### 1.4 plan（总是 `-out`）与审阅清单
```
terraform -chdir=deploy/iac/alicloud plan -input=false -lock=true -var-file=<计划目录>/staging.tfvars -out=<计划目录>/<阶段>.tfplan <本阶段的 -target 列表>
terraform -chdir=deploy/iac/alicloud show -no-color <计划目录>/<阶段>.tfplan
terraform -chdir=deploy/iac/alicloud show -json <计划目录>/<阶段>.tfplan | python3 -I -c 'import json,sys,collections; p=json.load(sys.stdin); rc=[r for r in p.get("resource_changes",[]) if r["change"]["actions"]!=["no-op"]]; [print("/".join(r["change"]["actions"]), r["address"]) for r in rc]; print(dict(collections.Counter("/".join(r["change"]["actions"]) for r in rc)))'
# 最后一条只打印动作与资源地址、按动作计数，不打印任何属性值
```
**plan 摘要（Claude 每次 apply 前贴给用户，用户逐次确认）必须列出**：
1. **新增 / 修改 / 删除数量**（`Plan: <n> to add, <n> to change, <n> to destroy` 与上面的按动作计数一致）；**删除数必须为 0**，出现 `delete`、
   `delete/create`、`create/delete`（替换）一律停下，不 apply（RDS、OSS、ACK 都开了删除保护，见 `rollback.md` 第 4 节）。
2. **包年包月或按量计费资源**：逐个列出本次新增的计费类资源（对照 0.4 表）与预估费用口径；出现 `alicloud_cr_ee_instance` 时单独提示“包年包月、下单即扣费”。
3. **安全组与出网规则**：`alicloud_security_group_rule.*` 的方向、协议、端口、网段与优先级——入方向只收 VPC；出方向只有 VPC、内网 DNS 53、元数据 80、
   `cloud_service_endpoint_cidrs` 的 443、公网 443；100.64.0.0/10 与 169.254.0.0/16 的拒绝排在公网 443 之前，全拒绝在最后；
   云防火墙访问控制只放行 `model_egress_fqdns` 的 TCP 443、最后一条拒绝其余；SNAT 只在访问控制之后。
4. **公网暴露检查**：没有 `alicloud_forward_entry`（DNAT）、没有 RDS 外网连接、没有公网负载均衡、ACK `slb_internet_enabled = false`、节点无公网带宽、
   EIP 只绑 NAT、OSS 私有且阻止公共访问、ACR 没有公网访问入口。
5. **会造成破坏或暴露的“修改”**（草案没有拒绝、靠这里核对）：OSS 生命周期规则（不得有删除当前版本的过期规则）与版本控制（不得暂停）、日志库留存（不低于 180 天）、RDS 备份保留期、任何删除保护被关闭、EIP 只绑 NAT 网关、ACK 不开公网 API Server。
6. **加密与 RAM 变更**：RDS `encryption_key`（阶段 B 建实例时为空，B-enc 改为 `ServiceKey`，之后不得变回）、OSS `sse_algorithm = AES256`、节点系统盘 `system_disk_encrypted = true`、ACK `disable_encryption = true`（LT-03 决定记录第 15 条）；计划里出现 `alicloud_kms_*` 一律停下；`alicloud_ram_policy.secret_reader` 的动作与资源逐个列出（只有 `oos:GetSecretParameter`、11 个加密参数名）、
   `alicloud_ram_role.secret_reader` 的信任主体只是 `kube-system:ack-secret-manager`（RAM 资源只在阶段 R 由人 apply；Claude 的计划里出现 RAM 写变更一律停下）。
7. 其他：地域与可用区全部 cn-beijing；标签齐全（R08）；本阶段之外的资源没有出现在计划里。

### 1.5 apply（只用已审阅的计划文件）
```
terraform -chdir=deploy/iac/alicloud apply -input=false <计划目录>/<阶段>.tfplan
```
- 只 apply 用户刚确认过的那个计划文件；不用不带计划文件的 apply、不加自动批准参数；计划过期（状态已变）报错时重新 plan、重新确认。
- apply 后删除该计划文件；`terraform output` 的标识与内网地址只给人填部署配置，不进仓库、不贴进 PR。

### 1.6 分阶段 apply 与每阶段验收（阶段之间都要过 1.4 的审阅与确认）
`-target` 只用于首次分阶段创建；全部阶段完成后必须做一次**不带 `-target`** 的 plan，结果应为 `No changes`。

**阶段 A：网络**（现阶段不买 KMS 实例，IaC 不含自建密钥，LT-03 决定记录第 14、15 条）
```
-target=alicloud_vpc.main -target=alicloud_vswitch.node -target=alicloud_vswitch.pod -target=alicloud_vswitch.db
-target=alicloud_security_group.nodes -target=alicloud_security_group_rule.ingress_vpc -target=alicloud_security_group_rule.egress_vpc
-target=alicloud_security_group_rule.egress_dns_udp_136 -target=alicloud_security_group_rule.egress_dns_tcp_136
-target=alicloud_security_group_rule.egress_dns_udp_138 -target=alicloud_security_group_rule.egress_dns_tcp_138
-target=alicloud_security_group_rule.egress_metadata -target=alicloud_security_group_rule.egress_cloud_endpoints
-target=alicloud_security_group_rule.egress_deny_cloud_internal -target=alicloud_security_group_rule.egress_deny_link_local
-target=alicloud_security_group_rule.egress_https -target=alicloud_security_group_rule.egress_deny_rest
-target=alicloud_slb_acl.office_vpn -target=alicloud_slb_acl_entry_attachment.office_vpn
```
验收（控制台或只读查询，第二人复核）：VPC 网段 10.20.0.0/16，六个交换机分在两个可用区；安全组为普通安全组、组内互通 Drop，规则与优先级同 1.4 第 3 条；
访问控制列表只含 `office_vpn_cidrs`；计划里没有任何 `alicloud_kms_*` 资源。

**KMS（暂缓；LT-03 决定记录第 14、15 条）**：现阶段不买 KMS 实例，IaC 已不含自建密钥；RDS 云盘、节点盘与云盘存储类用各产品的服务密钥，OSS 用 AES256，
ACK Secret 不做信封加密，应用凭据放 OOS 加密参数（`specs/launch-lt03-cloud-staging/kms-alternatives.md`）。买实例后另起变更：加回自建密钥、
子账号临时挂回 KMS 写动作（apply 后撤掉）、在已有集群上开启 Secret 落盘加密、RDS 换自建密钥，再评估凭据切回凭据管家。

**阶段 B：RDS / OSS / ACR / SLS**
```
-target=alicloud_db_instance.main -target=alicloud_db_backup_policy.main
# B-enc：RDS 建好后紧接着单独一次 plan / apply（目标同上一行的 RDS 实例），只把 rds_disk_encryption 由 deferred 改为 ServiceKey
# （PostgreSQL 创建时不收服务密钥，LT-03 决定记录第 15 条）；计划里只应有 alicloud_db_instance.main 一处修改（encryption_key → ServiceKey），
# 此时库里没有数据。之后每次 plan 都传 ServiceKey（ACK 集群以此为前置条件，传 deferred 时阶段 C 起的 plan 直接失败）。
# B-enc 调用 rds:ModifyDBInstanceConfig（ConfigName=encryptionKey；provider 1.294.0 源码核实，已在数据策略里）；其他权限不足按报错逐个补、过测试，不放宽成 rds:Modify*；
# 若计划显示 RDS 要替换（delete/create），按 1.4 第 1 条停下
-target=alicloud_oss_bucket.main -target=alicloud_oss_bucket_acl.main -target=alicloud_oss_bucket_public_access_block.main
-target=alicloud_vpc_gateway_endpoint.oss -target=alicloud_vpc_gateway_endpoint_route_table_attachment.oss
-target=alicloud_log_project.main -target=alicloud_log_store.app -target=alicloud_log_store_index.app -target=alicloud_log_store.audit
# B'：ACR 企业版实例（包年包月、下单即扣费；由人用管理员身份执行，同阶段 R 的做法：子账号没有计费下单权限）
-target=alicloud_cr_ee_instance.main
# B2：ACR 命名空间、仓库、VPC 端点（Claude 会话，B' 之后）
-target=alicloud_cr_ee_namespace.main -target=alicloud_cr_ee_repo.images -target=alicloud_cr_vpc_endpoint_linked_vpc.main
```
验收：RDS 只有内网地址、白名单只 VPC 网段、SSL 开启、pg_hba 只有 hostssl、每日备份 + 日志备份（PITR）、删除保护、云盘加密已开启（服务密钥，B-enc 之后）；OSS 私有、阻止公共访问、
版本控制、服务端加密 AES256、三条生命周期、网关终端节点策略只放本桶；ACR 实例运行中、命名空间私有、12 个私有仓库、VPC 端点已关联；SLS 的 `app` 与 `audit`
日志库留存 180 天、服务端加密、`app` 只建白名单字段索引、没有全文索引。
另核：OSS 账号级阻止公共访问仍开启；子账号策略里没有任何计费下单权限（`bss:` / `bssapi:`）。

**阶段 C：ACK**
```
-target=alicloud_cs_managed_kubernetes.main -target=alicloud_cs_kubernetes_node_pool.default
```
验收：API Server 只有内网地址；Terway 且 NetworkPolicy 生效；组件含 `arms-prometheus`、`logtail-ds`、`ack-secret-manager`，nginx-ingress 禁用；Secret 落盘
未做 KMS 信封加密（`disable_encryption`，决定记录第 15 条）；RRSA 开启；节点池节点 Ready、无公网地址、系统盘加密（ECS 服务密钥）。**此时还没有 SNAT**：节点初始化与组件镜像拉取若需要公网而失败，
停下由人决定（不临时放开出网）。

**阶段 R：RAM（由人用管理员身份执行，不在 Claude 会话里；阶段 C 之后，因为角色信任集群的 OIDC）**
运维管理员在跳板上用自己的临时凭据、同一个状态后端执行（Terraform 子账号没有 RAM 写权限）：
```
tofu -chdir=deploy/iac/alicloud plan -input=false -lock=true -out=<计划目录>/R.tfplan -target=alicloud_ram_policy.secret_reader -target=alicloud_ram_role.secret_reader -target=alicloud_ram_role_policy_attachment.secret_reader
```
第二人审阅策略文档（只有 `oos:GetSecretParameter`、只列 11 个加密参数名、没有 KMS 动作）与信任策略（只信任本集群 OIDC 的 `kube-system:ack-secret-manager`）后再 apply；之后这三个资源的任何变更都走同一流程。
首次同步若报还需要 KMS 等动作：只能改 `kms.tf` 后按本阶段由人 apply（补的动作逐个列资源，不用 `Resource: "*"`），不在 RAM 控制台手工加。
验收：RAM 策略只列 11 个加密参数名；角色信任主体正确；ack-secret-manager 能同步 Secret。

**阶段 D：防火墙与 NAT**（分两步；供应商主机名未定时可推迟到第 5 节 `model_provider` 解除时再做——staging 其余部分不依赖公网出网）
```
# D1：NAT 网关与 EIP（Claude 会话）
-target=alicloud_nat_gateway.main -target=alicloud_eip_address.nat
# D1'：EIP 绑定（由人用管理员身份执行，同阶段 R 的做法；子账号没有 AssociateEipAddress，防止把 EIP 绑到节点上暴露公网）
-target=alicloud_eip_association.nat
# 由人：在云防火墙控制台为这台 NAT 网关开启 NAT 边界防火墙，第二人核实后才把 nat_firewall_confirmed 设为 true（见 0.6 第 1 条）
# D2：访问控制与 SNAT
-target=alicloud_cloud_firewall_nat_firewall_control_policy.model_vendor -target=alicloud_cloud_firewall_nat_firewall_control_policy.deny_rest
-target=alicloud_snat_entry.node -target=alicloud_snat_entry.pod
```
验收：没有 DNAT 条目；EIP 只绑 NAT；NAT 边界防火墙开启；访问控制顺序为各供应商主机名（1…n）后接“拒绝其余”；四条 SNAT。出网核对放到第 6 节。

**收尾**：全部阶段验收后，由人从子账号上撤掉两条允许策略里的写类语句（只留查询类与状态桶、锁表的读写，供以后 plan 核对漂移；以后的变更再按需临时挂回、用完即撤；LT-03 决定记录第 11 条）。不带 `-target` 的 plan 必须是 `No changes`；`terraform -chdir=deploy/iac/alicloud state list` 的资源与 `*.tf` 一致；状态桶里出现新版本、锁表无残留锁。

---

## 2. 镜像（云效 Flow 私有构建机，由人触发；`deploy/ci/README.md`）
1. 由人把上游基础镜像同步到 ACR 的 `starbuddy-base-python` / `starbuddy-base-node` 并记下摘要；把 trivy 漏洞库同步到 ACR。
2. 由人生成 cosign 密钥对：私钥文件存凭据管家（不买 KMS 实例期间凭据管家不可用，私钥存放方式待人确认，LT-03 lessons；构建时挂载为 `COSIGN_KEY_REF`，口令经 `COSIGN_PASSWORD` 注入），公钥文件为 `COSIGN_PUB_REF`，同一把公钥配进
   ACK 容器安全策略（staging 命名空间只运行本 ACR 实例、带有效签名的镜像）。
3. 在云效导入 `deploy/ci/flow.yaml`（字段以云效文档为准），填变量（0.5 表），**只手动触发**；流水线执行 `bash deploy/ci/pipeline.sh`：
   构建 → 运行用户 10001 与健康检查核对 → SBOM（syft）→ 扫描（trivy，高危 / 严重或密钥即失败）→ 推送 → cosign 签名与 SBOM 证明 → 本地公钥验签。
4. 验收：流水线通过；制品 `dist/image-digests.txt` 恰好 10 行（`deploy/ci/images.txt` 的 10 个短名），`dist/sbom/`、`dist/scan/` 齐全；
   由第二人在跳板上对每个摘要复验：
   ```
   cosign verify --insecure-ignore-tlog=true --key <验签公钥文件> <ACR_REGISTRY>/starbuddy-<短名>@sha256:<摘要>
   cosign verify-attestation --insecure-ignore-tlog=true --key <验签公钥文件> --type spdxjson <ACR_REGISTRY>/starbuddy-<短名>@sha256:<摘要>
   ```
5. 按摘要填部署副本（第 4.3 节）：`deploy/k8s/staging.yaml` 里的 `ACR_REGISTRY/starbuddy/starbuddy-<服务>@sha256:DIGEST` 换成 `dist/image-digests.txt` 的对应行，
   只按摘要部署、不用标签。

---

## 3. 数据库（RDS；由人在 VPC 内跳板执行，**执行迁移须人批准**）
首次部署的特殊之处：主实例是新建的空库，还没有 `deploy_meta`，因此从它恢复出的副本也**没有标签**——执行器的 mark-replica 子命令只接受库里标签为
`staging` 的副本，在这里会按设计拒绝，属预期；首次演练直接用 `--target-label staging-replica` 演练（执行器在空库上写入 `staging-replica` 标签）。
主实例迁移完成后，以后每次演练都按 `rds-restore-and-migrate-rehearsal.md` 的标准流程（含 mark-replica）。

### 3.1 准备
1. **先核对云盘加密已开启**（阶段 B-enc；RDS 控制台“数据安全性”里云盘加密为服务密钥），没开不往下做。
   **再核对 RDS 不开放修改的两个日志参数**（`specs/plt-db-log-redaction` 决定记录：IaC 里不写，只能人工核对）：
   `psql "$PRIMARY_DSN" -X -At -c "SHOW log_statement" -c "SHOW log_error_verbosity" -c "SHOW log_parameter_max_length" -c "SHOW log_parameter_max_length_on_error"`；
   `log_statement` 只能是 `none` 或 `ddl`（`mod` / `all` 会把写入语句连同数据记进日志），后两项必须是 `0`；不符合就停下，由人向阿里云提工单改为
   `none` / `terse`，改好前不执行迁移、不部署服务。`log_error_verbosity` 记下实际取值（不是 `terse` 时报错日志会带 DETAIL，可能含字段值），写进变更记录。
   RDS 控制台建高权限账号（口令只在 OOS 加密参数与跳板的 `PGPASSFILE`）与业务库 `<库名>`；下载 RDS CA 证书：放进集群 ConfigMap `starbuddy-rds-ca`
   （键 `ca.pem`），跳板上另存 `<CA 路径>`。
2. 在主实例上发起一次手动数据备份，按 `rds-restore-and-migrate-rehearsal.md` 第 1 节**恢复到新实例**（同一 VPC、数据库交换机、不申请外网地址、
   只允许 SSL、云盘加密同主实例），记下 `<副本内网主机名>`。

### 3.2 副本上的核对与演练（`REPLICA_DSN`）
```
export REPLICA_DSN='postgresql://<高权限账号>@<副本内网主机名>:5432/<库名>?sslmode=verify-full&sslrootcert=<CA 路径>'
psql "$REPLICA_DSN" -X -c "SELECT ssl, version FROM pg_stat_ssl WHERE pid = pg_backend_pid()"
psql "$REPLICA_DSN" -X -c "SELECT rolname, rolsuper, rolcreaterole, rolbypassrls FROM pg_roles WHERE rolname = current_user"
psql "$REPLICA_DSN" -X -c "SELECT name, default_version, installed_version FROM pg_available_extensions WHERE name = 'pgcrypto'"
```
- `ssl` 必须为 t；另用 `sslmode=disable` 从 VPC 内连接副本必须失败（pg_hba 只有 hostssl）。
- **BYPASSRLS 与 pgcrypto 核对**（LT-03 待人确认 3）：`db/bootstrap/roles.sql` 要建两个 `BYPASSRLS` 的 NOLOGIN 角色（`starbuddy_authz`、`starbuddy_eraser`），
  PostgreSQL 16 要求创建者自己有 BYPASSRLS（或为超级用户）；迁移 0009 要在 `starbuddy_ext` 里装 pgcrypto。上面两条查询只是预判，
  下面的 `--with-bootstrap` 演练才是实测：任一不能做，**停下**（必停项，另议属主设计），不在主实例上尝试。

```
python3 deploy/migrate/migrate.py plan --dsn-env REPLICA_DSN --target-label staging-replica
# 首次应列出全部迁移（0001–0058），复制打印出的“副本演练确认串”
python3 deploy/migrate/migrate.py rehearse --dsn-env REPLICA_DSN --target-label staging-replica --replica --replica-host-allow <副本内网主机名> --with-bootstrap --confirm staging-replica:rehearse:<个数>:<最后版本>
```
- 演练：bootstrap 角色 → 全部迁移 up → 校验 → 逐个 down 回到空基线 → 校验 → 再 up → 校验（覆盖草稿迁移 0010–0058 的 up / down / up）；
- 再单独演练只回滚草稿段（0010–0058）再升回：
  ```
  python3 deploy/migrate/migrate.py down --dsn-env REPLICA_DSN --target-label staging-replica --replica --replica-host-allow <副本内网主机名> --to 0009 --confirm staging-replica:down:<个数>:0009
  python3 deploy/migrate/migrate.py up --dsn-env REPLICA_DSN --target-label staging-replica --replica --replica-host-allow <副本内网主机名> --confirm staging-replica:up:<个数>:<最后版本>
  ```
- 演练后核对：
  ```
  psql "$REPLICA_DSN" -X -c "SELECT rolname, rolbypassrls, rolcanlogin FROM pg_roles WHERE rolname IN ('starbuddy_authz', 'starbuddy_eraser')"
  psql "$REPLICA_DSN" -X -c "SELECT extname, extnamespace::regnamespace FROM pg_extension WHERE extname = 'pgcrypto'"
  psql "$REPLICA_DSN" -X -c "SELECT has_function_privilege('starbuddy_learning', 'platform.learning_meter_check(uuid, text, uuid, text)', 'EXECUTE')"
  ```
  两个角色 `rolbypassrls = t`、`rolcanlogin = f`；pgcrypto 在 `starbuddy_ext`（或已有的 schema）；0058 的执行权为 t。
- 任一步失败：记录失败的版本与报错首行，**不在主实例上执行**，回到开发修迁移（迁移内容变更属必停项）。
- 收尾：释放副本实例，删除跳板上副本那一行 `PGPASSFILE`，`unset REPLICA_DSN`。

### 3.3 主实例（`PRIMARY_DSN`；须人批准、复核人在场）
**真实数据门槛（LT-03 决定记录第 15 条）**：不买 KMS 实例期间，staging 只放合成数据；导入内部家庭的真实数据之前，须先买 KMS 实例并切回自建密钥
（RDS 换自建密钥、开启 ACK Secret 落盘加密、评估凭据切回凭据管家），另行确认。
1. 集群级角色（每个环境由人执行一次，不属于迁移；迁移 0001 会断言它们存在）：
   ```
   export PRIMARY_DSN='postgresql://<高权限账号>@<主实例内网主机名>:5432/<库名>?sslmode=verify-full&sslrootcert=<CA 路径>'
   psql "$PRIMARY_DSN" -X -v ON_ERROR_STOP=1 -1 -f db/bootstrap/roles.sql
   ```
2. 迁移（0058 在这一批里，**必须在第 4 节部署学习服务之前完成**；缺 0058 或学习服务角色没有执行权时学习服务拒绝启动）：
   ```
   python3 deploy/migrate/migrate.py plan --dsn-env PRIMARY_DSN --target-label staging
   python3 deploy/migrate/migrate.py up --dsn-env PRIMARY_DSN --target-label staging --confirm staging:up:<个数>:<最后版本>
   psql "$PRIMARY_DSN" -X -c "SELECT label FROM deploy_meta.target; SELECT max(version), count(*) FROM deploy_meta.schema_migrations"
   psql "$PRIMARY_DSN" -X -c "SELECT has_function_privilege('starbuddy_learning', 'platform.learning_meter_check(uuid, text, uuid, text)', 'EXECUTE')"
   ```
   标签为 `staging`、版本与副本演练一致、执行权为 t。
3. 登录角色（由人建，口令用 psql 的 `\password` 交互设置，不进命令历史）：每个登录角色**只继承一个**组角色——学习服务 `starbuddy_learning`、洞察服务
   `starbuddy_insight`、知识服务 `starbuddy_kge_read`、审核台两个（`starbuddy_kge`、`starbuddy_review`）、BFF `starbuddy_bff`、调度器 `starbuddy_scheduler`；
   各服务连接串按 `deploy/config/README.md` 第 8 节写成 `postgresql://<登录角色>@<RDS 内网主机名>:5432/<库名>?sslmode=verify-full&sslrootcert=/run/rds-ca/ca.pem`，
   只存 OOS 加密参数（4.1）。
4. 收尾：`unset PRIMARY_DSN`，删除跳板上的 `PGPASSFILE` 行；变更记录写版本核对与执行结果。

---

## 4. k8s（由人在跳板上执行；kubeconfig 用 ACK 内网地址，经 VPN，文件 0600、不进仓库）

### 4.0 各服务的启动前置（不满足时按设计拒绝启动或模块回 501；部署前逐项确认）
| 服务 | 启动前置 | 未满足时 |
|---|---|---|
| 学习服务（含三科适配器） | 0058 已执行且有执行权；连库 verify-full + `/run/rds-ca/ca.pem`；`LEARNING_MASTERY_CONFIG_AYA` 指向**签字**配置（未签字或合成配置只允许 test） | 拒绝启动；再练 / 复测 / 今日配置未签字时只是对应模块回 501 |
| 知识服务 | 页面快照卷（`knowledge-snapshot`）、只读角色与时区 | 快照为空时以启动日志为准；快照写入方式待人确认（LT-03 待人确认 7） |
| 洞察服务 | `INSIGHT_POLICY`、`INSIGHT_REPORT_CHECK`、`INSIGHT_SIE03` 不收合成占位；首份报告一致性词表未签字只允许 test | 拒绝启动 |
| 调度器 | 签字时间表 `schedule.signed.yaml` | 拒绝启动 |
| BFF | 同意书版本（真实登记）、白名单 OTP 条目与审计文件、`BFF_SHELL_ORIGIN` 为 https | 拒绝启动 |
| 前端 | 两份同意书版本与 BFF 一致、`WEB_HOST` 为 Pod IP | 拒绝启动 |
| 审核台后端 | 两个登录角色 | 拒绝启动 |

**教研签字前的部署范围（LT-03 决定记录第 9 条，2026-10-24）**：先部署基础设施、镜像、迁移演练，以及不依赖签字的知识服务、BFF、前端、审核台后端；
学习服务（含三科适配器）、洞察服务、调度器等签字后再部署，**不放松任何启动校验、不设合成配置**。签字前 BFF 调用学习 / 洞察服务的接口回 503 属预期
（第 5 节 `signed_content`）。

### 4.1 应用凭据同步（OOS 加密参数 → ack-secret-manager；LT-03 决定记录第 15 条）
- 在 OOS 控制台（cn-beijing）建加密参数，名字 `starbuddy-staging/<Secret 名>`（`kms.tf` 的 `app_secrets` 名单；取值只在控制台输入，不进仓库与命令历史）；
  文件类条目（签字配置、`otp.json`）先核对加密参数的取值长度上限，超限时由人另定办法（lessons 待人确认）；在 ACK 里建
  ack-secret-manager 的同步规则（`SecretStore` 用 `OOS.OOSAuth` 经 RRSA 扮演阶段 R 的角色，`ExternalSecret` 的 provider 写 `oos`）；应用的 ServiceAccount 不挂令牌（清单里 7 个 Deployment 都是 `automountServiceAccountToken: false`）。
- **集群权限（补偿控制：ACK Secret 落盘不做信封加密，LT-03 决定记录第 15 条 6a；由人在 ACK 授权管理配置）**：
  - 只有 `<部署者 RAM 身份>` 有集群“管理员”预置角色；其他身份（运维、开发、值守）不授“管理员 / 运维人员 / 开发人员”预置角色（都能读 Secret 或建 Pod），
    需要时用自定义 ClusterRole：只读 Pod、日志与事件，**不含** `secrets`、`pods` 的创建、`pods/exec`、`deployments` 的修改；
  - 同步规则的限制覆盖**整个集群**（不只 `starbuddy-staging`）：ack-secret-manager 的角色信任的是控制器自己的 ServiceAccount，任何命名空间里能建
    同步规则的人都能借它把 11 个参数同步到自己的命名空间——`externalsecrets`、`secretstores`、`clusterexternalsecrets`、`clustersecretstores` 的创建 / 修改只给部署者；
  - 由人核对系统组件（`arms-prometheus`、`logtail-ds` 等）有没有集群范围读 Secret 的 ClusterRoleBinding，结果写进变更记录；
  - 核对（对每个非部署者身份 `<身份>` 逐条执行，全部应为 `no`；第 6 节需要 `exec` 的走查项只由部署者执行）：
    ```
    kubectl auth can-i get secrets -n starbuddy-staging --as=<身份>
    kubectl auth can-i list secrets --all-namespaces --as=<身份>
    kubectl auth can-i create pods -n starbuddy-staging --as=<身份>
    kubectl auth can-i create pods/exec -n starbuddy-staging --as=<身份>
    kubectl auth can-i patch deployments -n starbuddy-staging --as=<身份>
    kubectl auth can-i create externalsecrets.alibabacloud.com --all-namespaces --as=<身份>
    kubectl auth can-i create secretstores.alibabacloud.com --all-namespaces --as=<身份>
    ```
- 同步后的 k8s Secret 名与键（必须与清单的 `secretKeyRef` / `secretName` 一致，与 `kms.tf` 的 `app_secrets` 名单一致）：

| Secret | 键 |
|---|---|
| `starbuddy-learning` | `database-url`、`service-token`、`insight-client-token`、`practice-ticket-secret`（模型供应商开通后另加 `route-client-zhixing`、`route-client-wenli`） |
| `starbuddy-adapters` | `aya-token`、`zhixing-token`、`wenli-token` |
| `starbuddy-knowledge` | `database-url`、`learning-token` |
| `starbuddy-insight` | `database-url`、`bff-token` |
| `starbuddy-review-console` | `kge-database-url`、`review-database-url` |
| `starbuddy-bff` | `database-url`、`log-hmac-key`、`auth-secret`、`trust-secret` |
| `starbuddy-web` | `presence-hmac-key` |
| `starbuddy-scheduler` | `database-url` |
| `starbuddy-signed-config` | 文件：`mastery-aya.signed.json`、`practice.signed.json`、`retest.signed.json`、`today.signed.json`、`claims.signed.json`、`report-check.signed.json`、`sie03.signed.json`、`schedule.signed.yaml` |
| `starbuddy-staging-otp` | 文件：`otp.json`（白名单固定码条目，属密钥） |
| `starbuddy-model-vendors` | `bailian-url`、`bailian-api-key`、`ark-url`、`ark-api-key`（`model_provider` 解除前不建、不接线） |

- 核对（只看名字与字节数，**不看取值**；不用 `get secret -o yaml` / `-o json`）：
  ```
  kubectl -n starbuddy-staging get secret
  kubectl -n starbuddy-staging describe secret starbuddy-bff
  ```
  逐个 Secret 核对键名齐全；令牌各不相同、长度达标（≥ 32 字符）由建凭据的人核对。

### 4.2 ConfigMap 注入核对（非密钥，可直接查看）
| ConfigMap | 键 | 说明 |
|---|---|---|
| `starbuddy-staging` | `shell-origin` | `https://<内网访问地址>`（staging 必须 https） |
| `starbuddy-staging` | `consent-versions` | `privacy_policy=privacy-policy@<版本>,guardian_consent_under14=guardian-consent@<版本>`（合规顾问提供的真实版本，不收 `synthetic-*`） |
| `starbuddy-staging` | `consent-version-privacy`、`consent-version-guardian` | 与上一行登记的两份版本一致 |
| `starbuddy-rds-ca` | `ca.pem` | RDS CA 证书（3.1） |
```
kubectl -n starbuddy-staging get configmap starbuddy-staging -o yaml
kubectl -n starbuddy-staging get configmap starbuddy-rds-ca
```

### 4.3 部署副本（填占位，不提交）
1. 把 `deploy/k8s/staging.yaml`、`deploy/k8s/observability.yaml` 复制到跳板的 `<部署目录>`（仓库之外），按 IaC 输出、流水线摘要与 ARMS 控制台填写全部
   `<由人填写…>`：web Service 的交换机、证书 ID 与 `slb_acl_id`、镜像摘要、NetworkPolicy `metrics-from-collector` 的
   采集组件命名空间与 Pod 标签、AliyunLogConfig 的 `project`（`log_project`）。学习服务 Pod 的出网注解与模型接线行在 `model_provider` 解除前保持原样。
2. 再把副本按资源拆成几份：`00-base.yaml`（Namespace、StorageClass、PVC、全部 Service、全部 NetworkPolicy）与 `10-learning.yaml`、`20-knowledge.yaml`、
   `21-insight.yaml`、`30-scheduler.yaml`、`40-bff.yaml`、`50-web.yaml`、`51-review-console.yaml`（各一个 Deployment）。
3. 核对：
   ```
   grep -rn "由人填写\|ACR_REGISTRY/\|DIGEST" <部署目录>
   kubectl apply --dry-run=server -f <部署目录>/00-base.yaml
   kubectl diff -f <部署目录>/00-base.yaml
   ```
   第一条的输出只允许是注释行，以及学习服务 Pod 的出网注解 `starbuddy/model-egress-hosts`（`model_provider` 解除前保持占位）；
   开发开关（`deploy/flags/staging.yaml` 的 `dev_only`）一个都不出现；`BFF_TRUST_CLIENT_IP` 为 `"0"`。每个拆分文件都先 dry-run 与 diff 再 apply。

### 4.4 按依赖顺序部署（每步 readyz 与日志核对通过才进入下一步）
就绪探针就是 `GET /readyz`（适配器为 exec TCP），所以 `kubectl wait` 的 Ready 即 readyz 通过；日志是一行一个 JSON，核对没有启动拒绝、没有反复重启。
```
kubectl apply -f <部署目录>/00-base.yaml
kubectl -n starbuddy-staging get svc,networkpolicy,pvc
```
1. **库连接核对**（跳板，每个登录角色各一次；连接串不带口令）：
   ```
   psql "postgresql://<登录角色>@<RDS 内网主机名>:5432/<库名>?sslmode=verify-full&sslrootcert=<CA 路径>" -X -c "SELECT current_user, ssl FROM pg_stat_ssl WHERE pid = pg_backend_pid()"
   ```
   `ssl = t`；同一角色用 `sslmode=disable` 必须失败。
2. **学习服务**（含三科适配器；前提：3.3 已完成、4.0 学习服务前置满足）：
   ```
   kubectl apply -f <部署目录>/10-learning.yaml
   kubectl -n starbuddy-staging rollout status deployment/learning --timeout=300s
   kubectl -n starbuddy-staging wait --for=condition=Ready pod -l app=learning --timeout=300s
   kubectl -n starbuddy-staging logs deployment/learning -c learning --since=10m
   kubectl -n starbuddy-staging logs deployment/learning -c adapter-aya --since=10m
   ```
   Pod 为 4/4；同样看 `adapter-zhixing`、`adapter-wenli` 的日志。
3. **知识服务、洞察服务**：
   ```
   kubectl apply -f <部署目录>/20-knowledge.yaml -f <部署目录>/21-insight.yaml
   kubectl -n starbuddy-staging wait --for=condition=Ready pod -l app=knowledge --timeout=300s
   kubectl -n starbuddy-staging wait --for=condition=Ready pod -l app=insight --timeout=300s
   kubectl -n starbuddy-staging logs deployment/knowledge -c knowledge --since=10m
   kubectl -n starbuddy-staging logs deployment/insight -c insight --since=10m
   ```
4. **调度器**（单实例、Recreate）：
   ```
   kubectl apply -f <部署目录>/30-scheduler.yaml
   kubectl -n starbuddy-staging wait --for=condition=Ready pod -l app=scheduler --timeout=300s
   kubectl -n starbuddy-staging logs deployment/scheduler -c scheduler --since=10m
   ```
5. **BFF**（单实例、Recreate）：首次部署先让 `starbuddy-staging-otp` 的 `otp.json` 为 `{"entries":[]}`（没有生效条目时 BFF 自动新建审计文件；
   条目文件已有生效条目而审计文件不存在会拒绝启动，`deploy/config/README.md` 第 3 节），起来后再由管理员按人下发条目：
   ```
   kubectl apply -f <部署目录>/40-bff.yaml
   kubectl -n starbuddy-staging wait --for=condition=Ready pod -l app=bff --timeout=300s
   kubectl -n starbuddy-staging logs deployment/bff -c bff --since=10m
   ```
6. **前端、审核台后端**：
   ```
   kubectl apply -f <部署目录>/50-web.yaml -f <部署目录>/51-review-console.yaml
   kubectl -n starbuddy-staging wait --for=condition=Ready pod -l app=web --timeout=300s
   kubectl -n starbuddy-staging wait --for=condition=Ready pod -l app=review-console --timeout=300s
   kubectl -n starbuddy-staging logs deployment/web -c web --since=10m
   kubectl -n starbuddy-staging logs deployment/review-console -c review-console --since=10m
   kubectl -n starbuddy-staging get svc web
   ```
   web Service 的地址必须是 VPC 内网地址（没有公网地址）。
7. **服务间 readyz 一并核对**（从前端容器经 Service 的 clusterIP 访问，同时验证 `*_EXTRA_HOST` 的 Host 放行；每条应打印 200）：
   ```
   for u in http://10.96.100.10:8788/readyz http://10.96.100.12:8790/readyz http://10.96.100.14:8791/readyz http://10.96.100.13:8792/readyz http://10.96.100.30:8787/readyz; do
     kubectl -n starbuddy-staging exec deployment/web -c web -- node -e "fetch(process.argv[1]).then(r=>{console.log(process.argv[1], r.status);process.exit(r.status===200?0:1)}).catch(()=>process.exit(1))" "$u"
   done
   ```
8. **采集**（ARMS 与 SLS 组件就绪后）：
   ```
   kubectl apply -f <部署目录>/observability.yaml
   ```

---

## 5. 开关与内容（`deploy/flags/staging.yaml` 的 pending 项逐个解除）
开关取值已按矩阵写在清单里；pending 只说明“开关开、但还不可用”。每解除一项，另起 PR 改矩阵（去掉该 pending），涉及配置、契约或人设的照必停项走 `[NEEDS-HUMAN]`。
**解除之前，相关接口返回 501 / 503 属预期**，走查时按预期记录，不算故障。

| pending | 涉及（矩阵 id） | 解除前提 | 解除前的预期 |
|---|---|---|---|
| `migration_0058` | `math`、`daofa`、`chinese_grading`、`today_plan`、`diagnosis`、`practice`、`retest`、`today_snapshot` | 第 3 节主实例执行 0058，学习服务就绪且执行权核对为 t | 学习服务拒绝启动；BFF 调学习服务的接口不可用（本手册的顺序保证 0058 先于学习服务） |
| `signed_content` | `today_plan`、`diagnosis`、`practice`、`retest`、`star_map`、`today_snapshot` | **教研签字**：掌握配置（`mastery-aya`）、再练、复测、今日编排配置与题面来源放进 `starbuddy-signed-config` 后 `kubectl -n starbuddy-staging rollout restart deployment/learning` | 再练 501 `PRACTICE_UNAVAILABLE`、复测 501 `RETEST_UNAVAILABLE`、今日编排 501 `TODAY_UNAVAILABLE`、诊断题面 501；星图不展示未经签字的结论（规则 11） |
| `model_provider` | `daofa`、`chinese_grading` | **模型供应商开通**：按 `deploy/config/README.md` 第 7 节由人完成开通、协议与登记、填写快照名与 `allowed_hosts`、批准后改 `approved`；建 `starbuddy-model-vendors` 并解开清单里的接线行；IaC 填 `model_egress_fqdns` 并完成第 1 节阶段 D；学习服务 Pod 加 443 出网策略（写法见清单注释） | 模型路由接口 503 `ROUTE_UNAVAILABLE`，道法判分推理与作文批改不可用；超限时 503 且 `detail` 为 `ROUTE_BUDGET_EXCEEDED` |
| `human` | `chinese_grading` | 由人确认语文“只开批改”的口径（没有专门开关，靠选科与内容范围约束） | 文砺的 diagnose / plan 照常可用 |
| `migration_0059` | `internal_test_ops` | 副本演练后在主实例执行 0059（LT-06 问题反馈表、退出记录表与库函数）；之后由人决定是否把 BFF、前端的 `STARBUDDY_INTERNAL_TEST` 与审核台后端的 `REVIEW_CONSOLE_INTERNAL_TEST` 一起设为 `internal_test`（步骤见 `docs/ops/internal-test-runbook.md`） | 三处都不设：没有问题反馈、退出测试入口与审核台的反馈、退出状态页（路由不注册，404）；BFF 不做退出判断 |
| `kms_instance` | `internal_test_ops` | **真实数据门槛**（LT-03 决定记录第 15 条，见 3.3）：买 KMS 实例、IaC 加回自建密钥，RDS 换自建密钥、ACK 开启 Secret 落盘加密（去掉 `disable_encryption`），评估凭据切回凭据管家，并经用户另行确认 | 内部成员测试不开放；staging 只放合成数据 |
| `migration_0060` | `model_call_log` | 副本演练后在主实例执行 0060（模型调用记录表 `platform.model_call_log`、聚合函数 `platform.model_call_stats` 与留存删除函数，specs/plt-model-call-log）；之后由人决定是否把学习服务的 `LEARNING_MODEL_CALL_LOG` 设为 `1`（启动时核对 0060 与 INSERT 权限，不过拒绝启动），并把留存删除任务 `model-call-purge` 写进签字时间表 `schedule.signed.yaml`；打开后看 `starbuddy_model_call_log_written_total` 增长，告警 `StarbuddyModelCallLogWriteFailed`、`StarbuddyModelCallLogDropped` 不响 | 不设：调用记录只写结构化日志与指标（与之前一致），没有可对账的库内记录；运营与成本导出读不到 `platform.model_call_stats` 的数据 |

- `model_provider` 解除后的验证（由人在有凭据的 staging 执行环境，限次）：`make model-smoke SMOKE_ENV=staging MAX_CALLS=5`（只打印状态、耗时、token 与快照名是否一致）；
  点点人设回归按 `evals/persona/README.md` 的 staging 步骤（`compare.py` 必须显式给 `--max-calls`），并跑 `make eval-persona`。
- 保持关、不解除：`english`、`mock_exam`、`monthly_plan`、`stage_whiteboard`、`knowledge_read_cache`、`family_goals`、`subject_gate`；`dev_only` 一律不设。
- 公共知识层内容（地图、题库、页面快照）只经审核台由教研签字发布；个人层数据永不进入公共层（规则 13）。

---

## 6. 走查（内部成员测试开放前；两人一组，按项记录通过 / 预期不可用 / 失败；不买 KMS 实例期间只用合成数据，见 3.3 真实数据门槛）
测试账号用内部成员（成年员工）的白名单号码；走查记录不写手机号、验证码与任何学生信息。
1. **内部访问（VPN）**：经 VPN 打开 `https://<内网访问地址>`，证书由私有 CA 签发且受信；不在 `office_vpn_cidrs` 的来源访问失败；公网无入口
   （`kubectl -n starbuddy-staging get svc web` 只有内网地址；IaC 无 DNAT、无公网负载均衡）。
2. **OTP 白名单登录**：管理员在 OOS 加密参数里更新 `starbuddy-staging/starbuddy-staging-otp`（条目窗口 ≤ 24 小时、id 不复用），同步后用白名单号码登录成功；同一条目再次使用失败
   （成功即作废）；非白名单号码的表现与现状一致（不投递）；BFF 应用日志与 SLS 里没有验证码与手机号原文；审计文件行数增加：
   `kubectl -n starbuddy-staging exec deployment/bff -c bff -- wc -l /var/lib/starbuddy/audit/otp-audit.jsonl`。
3. **同意书版本**：前端展示的两份文书版本与 `starbuddy-staging` ConfigMap 登记一致；家长签署后记录的是登记版本；监护人同意书未签时的行为按产品设计核对，结果以实际为准记录。
4. **三科判分链路**：
   - 数学（阿鸦）：一次判分返回带 `evidence_ids`、`confidence`、`source_version`、`model_version`、`human_review_required`；计量经 BFF 预留、结算（指标
     `starbuddy_meter_reserve_total`、`starbuddy_meter_settle_total` 增加；学习服务写入前核对 `starbuddy_learning_meter_check_total` 有放行）；
   - 道法（知行）、语文批改（文砺）：模型供应商未开通时为 503 `ROUTE_UNAVAILABLE`（适配器按不可用处理，界面给可读提示），预留应回滚（`starbuddy_meter_release_total` 增加、不扣次）
     ——属预期；开通后重走一遍。
5. **星图**：只展示已在判断账本里“成立 / 假设”的陈述（规则 11）；掌握配置未签字时按第 5 节记录为预期不可用。
6. **家长端**：周报、对账卡、为什么 / 我不同意、授权与删除——删除请求提交后按设计生效（上线测试通过条件：100% 生效）；家长只看到按档案授权的孩子（规则 5）。
7. **审核台**：教研账号能登录、看到复核队列；`starbuddy_review_queue_backlog` 与界面一致。
8. **指标与日志**：ARMS 里六个后端的采集目标都在、`starbuddy_ready` 为 1；`starbuddy_http_requests_total` 有数；SLS `app` 日志库能按 `service`、`route`、
   `request_id` 查到，抽查若干行没有手机号、令牌、作答原文；适配器与前端日志不进 SLS（按设计）。
9. **告警测试**：ARMS 控制台导入 `deploy/observability/alerts.yaml` 与 `deploy/observability/dashboards/*.json`，由人配置接收人与通知渠道；先用控制台的通知测试，
   再临时加一条只在控制台存在的测试规则（表达式恒为 1、持续 0 分钟），确认值守收到后删除。之后核对 `StarbuddyServiceDown`、`StarbuddyNotReady` 等规则状态为正常。
   阈值仍是占位缺省，待人签字（SLO 通过线）。
10. **恢复演练**：按 `weekly-restore-verify.md` 做第一次（没有上周报告时不给 `--baseline`，报告作为下周基线）；同时按 `rds-restore-and-migrate-rehearsal.md`
    第 6 节核对探针、NetworkPolicy（不带 `starbuddy/db-client` 标签的 Pod 连 RDS 失败）、服务端强制 TLS、服务间 Host 与出网（阶段 D 完成后：学习服务 Pod
    访问白名单主机名 443 成功、其他公网与其他 Pod 出网失败）。

---

## 7. 回滚（不含 destroy；任何销毁须另行确认）
| 阶段 | 回滚方式 |
|---|---|
| 第 0 节准备 | 撤销 RAM 策略挂载、停用子账号凭据；状态桶与锁表保留（不删） |
| 第 1 节 Terraform | 某阶段 apply 失败：状态已在远程后端，修正后重新 plan、逐次确认、再 apply（失败的资源会在下一次 plan 里出现）；变更有误：回到上一版 IaC 提交再 plan / apply（`rollback.md` 第 4 节），plan 里出现删除或替换就停下；**不执行 destroy**——要撤掉某个资源，另行写明理由、范围与计费影响，经用户单独确认（包年包月资源下单后不可按量退订）；锁表残留锁只在确认没有进行中的 apply 后由人处理 |
| 第 2 节镜像 | 不部署未验签的摘要；有问题的版本不填进部署副本，重新从上一版提交触发流水线；已部署的按 `rollback.md` 第 1 节换回上一版摘要 |
| 第 3 节数据库 | 副本演练失败：主实例不动，释放副本，回到开发修迁移；主实例 up 中途失败：每个迁移单独一个事务，已执行的是前缀，先前向修复，必须回退时先在副本演练 `down --to <版本>`，再按 `rollback.md` 第 2 节在主实例执行（不要 down 0058，学习服务会拒绝启动）；数据被错误改写：`rollback.md` 第 3 节按时间点恢复 |
| 第 4 节 k8s | 停服务用缩容（`kubectl -n starbuddy-staging scale deployment/<服务> --replicas=0`），不删命名空间与 PVC（存储类 Retain，盘与审计文件要保留）；换回上一版镜像或上一版部署副本按 `rollback.md` 第 1 节；凭据错误在 OOS 加密参数里改回上一个取值，等同步后 `rollout restart` |
| 第 5 节开关与内容 | 用上一版部署副本把开关取值改回关（或撤回接线行、撤下供应商密钥），`kubectl apply` 后 `rollout status`；矩阵 PR 同步回退 |
| 第 6 节走查 | 走查中发现个人信息泄漏风险：先停对外入口（web 缩容到 0），再按事故流程处理；OTP 条目由管理员撤下 |
