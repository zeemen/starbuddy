# 变量：仓库里只有缺省的口径值与占位，真实取值由人在 plan 前写进不提交的 tfvars（terraform.tfvars.example 列出要填的项）。
# 没有缺省值的变量 = 必须由人填写；validate 不需要取值。

variable "region" {
  description = "地域：只允许华北 2（北京）（决定记录第 1 条；境内、与模型主平台同云）"
  type        = string
  default     = "cn-beijing"
  validation {
    condition     = var.region == "cn-beijing"
    error_message = "region 只能是 cn-beijing（华北 2）。"
  }
}

variable "zone_ids" {
  description = "两个可用区（节点、数据库与 NAT 的交换机各放一个）；必须是华北 2 的可用区，由人按库存选定"
  type        = list(string)
  default     = ["cn-beijing-h", "cn-beijing-i"]
  validation {
    condition     = length(var.zone_ids) == 2 && alltrue([for z in var.zone_ids : can(regex("^cn-beijing-[a-z]$", z))])
    error_message = "zone_ids 必须是两个 cn-beijing-<字母> 可用区。"
  }
}

variable "name_prefix" {
  description = "资源名前缀"
  type        = string
  default     = "starbuddy"
}

variable "environment" {
  description = "环境：本草案只用于 staging（12 月前只做境内 staging、不对外）"
  type        = string
  default     = "staging"
  validation {
    condition     = var.environment == "staging"
    error_message = "本草案只用于 staging。"
  }
}

variable "extra_tags" {
  description = "附加标签（例如负责人、成本中心）；不放个人信息"
  type        = map(string)
  default     = {}
}

# ---------------------------------------------------------------- 网络（全部 RFC 1918；与 deploy/k8s/staging.yaml 的 clusterIP 网段一致）
variable "vpc_cidr" {
  description = "VPC 网段"
  type        = string
  default     = "10.20.0.0/16"
}

variable "node_vswitch_cidrs" {
  description = "节点交换机（两个可用区各一个）"
  type        = list(string)
  default     = ["10.20.0.0/20", "10.20.16.0/20"]
}

variable "pod_vswitch_cidrs" {
  description = "Pod 交换机（Terway 网络，Pod 直接拿 VPC 内网地址；服务的 *_HOST 取 Pod IP，属 RFC 1918）"
  type        = list(string)
  default     = ["10.20.32.0/19", "10.20.64.0/19"]
}

variable "db_vswitch_cidrs" {
  description = "数据库交换机（RDS 主备各在一个可用区）"
  type        = list(string)
  default     = ["10.20.96.0/24", "10.20.97.0/24"]
}

variable "service_cidr" {
  description = "集群 Service 网段：deploy/k8s/staging.yaml 里显式指定的 clusterIP（10.96.100.x）必须落在这里，且不与 VPC 重叠（deploy/tests 核对）"
  type        = string
  default     = "10.96.0.0/16"
}

variable "office_vpn_cidrs" {
  description = "可访问内网负载均衡（前端入口）的办公网 / VPN 来源网段，由人填写；不收 0.0.0.0/0，掩码不宽于 /16"
  type        = list(string)
  validation {
    condition     = alltrue([for c in var.office_vpn_cidrs : can(cidrhost(c, 0)) && tonumber(split("/", c)[1]) >= 16 && c != "0.0.0.0/0"])
    error_message = "office_vpn_cidrs 只收掩码 /16 及更窄的网段，不收 0.0.0.0/0。"
  }
}

variable "model_egress_fqdns" {
  description = "出网白名单：只放行境内已备案模型供应商的接入主机名（443），由人在开通后填写，须与路由表 allowed_hosts、docs/compliance/vendors.md 一致"
  type        = list(string)
  validation {
    condition     = alltrue([for h in var.model_egress_fqdns : can(regex("^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$", h))])
    error_message = "model_egress_fqdns 只收完整主机名（小写，不收通配符与 IP）。"
  }
}

variable "nat_firewall_confirmed" {
  description = "由人确认：已在云防火墙控制台为 network.tf 的 NAT 网关开启 NAT 边界防火墙（egress.tf 的访问控制只在开启后生效）；没有缺省值，必须显式给出。为 false 时任何 SNAT 都过不了前置条件（出网保持不通），分阶段 apply 的前几阶段传 false"
  type        = bool
}

variable "cloud_service_endpoint_cidrs" {
  description = "阿里云服务内网端点（ACK 管控、SLS、KMS、STS、OSS 网关终端节点）解析出的地址，由人按 cn-beijing 的内网端点填写（/32 或不宽于 /24，只在 100.64.0.0/10 里）；节点安全组只对这些地址开 TCP 443"
  type        = list(string)
  validation {
    condition = alltrue([for c in var.cloud_service_endpoint_cidrs : can(cidrhost(c, 0)) && tonumber(split("/", c)[1]) >= 24
    && cidrsubnet(c, 0, 0) == c && startswith(c, "100.") && tonumber(split(".", c)[1]) >= 64 && tonumber(split(".", c)[1]) <= 127])
    error_message = "cloud_service_endpoint_cidrs 只收 100.64.0.0/10 里、掩码 /24 及更窄的网段。"
  }
}

# ---------------------------------------------------------------- 托管 K8s（ACK Pro）
variable "k8s_version" {
  description = "ACK 集群版本（由人按控制台可选版本填写，例如 1.31.x-aliyun.1）"
  type        = string
}

variable "node_instance_types" {
  description = "节点规格（由人按库存与预算选定）"
  type        = list(string)
  default     = ["ecs.g7.xlarge"]
}

variable "node_count" {
  description = "节点数（staging 两个可用区各一）"
  type        = number
  default     = 2
}

variable "node_key_pair_name" {
  description = "节点登录密钥对名称（公钥由人在 ECS 控制台登记；这里只写名字，不写私钥或口令）"
  type        = string
}

# ---------------------------------------------------------------- 托管 Postgres（RDS PostgreSQL）
variable "rds_engine_version" {
  description = "PostgreSQL 主版本：与本机迁移演练一致（db/tests/pg-ephemeral.sh 用 16），迁移 0001–0058 在 16 上演练通过"
  type        = string
  default     = "16.0"
  validation {
    condition     = startswith(var.rds_engine_version, "16.")
    error_message = "rds_engine_version 必须是 PostgreSQL 16（与迁移演练的版本一致）。"
  }
}

variable "rds_instance_type" {
  description = "RDS 规格（由人按预算选定，高可用系列）"
  type        = string
  default     = "pg.n2.2c.2m"
}

variable "rds_internal_domain_suffix" {
  description = "RDS PostgreSQL 内网连接地址的域名后缀（与 connection_string_prefix 拼成内网连接地址，SSL 证书只签它；外网后缀不收）；待人按控制台核实"
  type        = string
  default     = ".pg.rds.aliyuncs.com"
  validation {
    condition     = var.rds_internal_domain_suffix == ".pg.rds.aliyuncs.com"
    error_message = "只收 RDS PostgreSQL 的内网域名后缀（.pg.rds.aliyuncs.com）；外网地址不签证书、不建连接。"
  }
}

variable "rds_storage_gb" {
  description = "RDS 存储（GB，ESSD 云盘）"
  type        = number
  default     = 100
}

variable "rds_disk_encryption" {
  description = "RDS 云盘加密：建实例时传 deferred（PostgreSQL 创建时不收服务密钥），建好后紧接着传 ServiceKey 开启并一直保持（rds.tf）"
  type        = string
  validation {
    condition     = contains(["deferred", "ServiceKey"], var.rds_disk_encryption)
    error_message = "rds_disk_encryption 只收 deferred（仅建实例那一次）或 ServiceKey。"
  }
}

variable "rds_backup_retention_days" {
  description = "数据备份保留天数（≥ 7）"
  type        = number
  default     = 14
  validation {
    condition     = var.rds_backup_retention_days >= 7
    error_message = "数据备份至少保留 7 天。"
  }
}

variable "rds_log_backup_retention_days" {
  description = "日志备份（按时间点恢复 PITR 的依据）保留天数（≥ 7，且不长于数据备份）"
  type        = number
  default     = 14
  validation {
    condition     = var.rds_log_backup_retention_days >= 7
    error_message = "日志备份至少保留 7 天。"
  }
}

# ---------------------------------------------------------------- 对象存储（OSS）
variable "oss_bucket_name" {
  description = "私有桶名（全局唯一，由人填写；只放页面快照、备份导出与归档，不公开）"
  type        = string
}

variable "oss_noncurrent_days" {
  description = "历史版本保留天数（到期删除）"
  type        = number
  default     = 30
}

variable "oss_archive_prefix_days" {
  description = "archive/ 前缀下的对象多少天后转归档存储"
  type        = number
  default     = 30
}

variable "oss_expire_days" {
  description = "tmp/ 前缀下的对象多少天后删除"
  type        = number
  default     = 7
}

# ---------------------------------------------------------------- 镜像仓库（ACR 企业版）
variable "acr_instance_type" {
  description = "ACR 企业版规格（Basic / Standard / Advanced）：缺省 Advanced——支持镜像加签（待人核实）；流水线的发布门槛是 cosign 验签（deploy/ci/README.md 第 4 节）"
  type        = string
  default     = "Advanced"
}

variable "acr_period_months" {
  description = "ACR 企业版购买时长（月；包年包月，由人确认计费后再 apply）"
  type        = number
  default     = 1
}

# ---------------------------------------------------------------- 日志服务（SLS）
variable "log_retention_days" {
  description = "日志留存天数：不少于 180 天（《网络安全法》第二十一条：网络日志留存不少于六个月），不多于 365 天（最小必要；LT-05 安全审查）"
  type        = number
  default     = 180
  validation {
    condition     = var.log_retention_days >= 180 && var.log_retention_days <= 365
    error_message = "日志留存 180–365 天。"
  }
}
