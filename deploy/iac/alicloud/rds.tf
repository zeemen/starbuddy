# 托管 Postgres（RDS PostgreSQL 16，高可用；ADR 10 选项 B）：只有 VPC 内网地址（不建外网连接 alicloud_db_connection，规则 R06）、
# 白名单只收 VPC 网段、云盘加密（服务密钥，决定记录第 15 条）、每日数据备份 + 日志备份（按时间点恢复 PITR）、删除保护。
# 强制 TLS（LT-03 决定记录（续）第 6 条，规则 R06）：
#   - 服务端：开 SSL（ssl_action = "Open"），证书签给内网连接地址（ssl_connection_string = 内网前缀 + 内网域名后缀）；
#     pg_hba 只有 hostssl 条目（只收 VPC 网段、口令认证），没有 host / hostnossl——不走 TLS 的连接在服务端就被拒；
#   - 客户端：各服务的连接串必须 sslmode=verify-full&sslrootcert=/run/rds-ca/ca.pem（不合格启动即拒，见 deploy/config/README.md 第 8 节）。
#   待人核实：RDS 的 pg_hba 系统条目在设置自定义条目后是否仍保留 host 类型（演练时从 VPC 内用 sslmode=disable 连接，必须失败），
#   内网域名后缀（var.rds_internal_domain_suffix）与 connection_string_prefix 的取值规则以控制台为准。
# 账号与库不在 Terraform 里建（口令会进状态文件）：由人在控制台建高权限账号，用 deploy/migrate 执行 db/bootstrap 与迁移（须人批准）。

locals {
  rds_connection_prefix = "${local.name}-pg"
}

resource "alicloud_db_instance" "main" {
  instance_name            = local.name
  engine                   = "PostgreSQL"
  engine_version           = var.rds_engine_version
  instance_type            = var.rds_instance_type
  instance_storage         = var.rds_storage_gb
  db_instance_storage_type = "cloud_essd"
  instance_charge_type     = "Postpaid"
  category                 = "HighAvailability"
  zone_id                  = var.zone_ids[0]
  zone_id_slave_a          = var.zone_ids[1]
  vswitch_id               = join(",", alicloud_vswitch.db[*].id)
  security_ips             = [var.vpc_cidr] # 只收 VPC 内网；没有 0.0.0.0/0、没有公网地址
  connection_string_prefix = local.rds_connection_prefix
  ssl_action               = "Open"
  ssl_connection_string    = "${local.rds_connection_prefix}${var.rds_internal_domain_suffix}" # 证书只签内网地址
  ca_type                  = "aliyun"
  # 云盘加密用 RDS 服务密钥（LT-03 决定记录第 15 条：不买 KMS 实例）。provider 文档：PostgreSQL 创建时 encryption_key 只收自建密钥 ID、
  # 不收 ServiceKey，所以分两步——阶段 B 建实例传 rds_disk_encryption = "deferred"（空库），紧接着单独 plan / apply 传 "ServiceKey" 开启；
  # 之后一律传 "ServiceKey"（encryption_key 是 computed，再传 deferred 也不会关闭加密）。执行迁移前核对云盘加密已开启（手册第 3 节）
  encryption_key = var.rds_disk_encryption == "ServiceKey" ? "ServiceKey" : null
  pg_hba_conf {
    type        = "hostssl"
    database    = "all"
    user        = "all"
    address     = var.vpc_cidr
    method      = "scram-sha-256"
    priority_id = 1
  }
  # 服务端日志不带标识原文（specs/plt-db-log-redaction，规则 R22）：慢查询与报错日志不写绑定参数（学生 ID、usage_id、幂等键），
  # 报错只写一行（不写 DETAIL：唯一约束冲突的键值、CHECK 失败的整行）；客户端仍收到完整错误。待人核实：控制台是否允许修改、是否需重启
  parameters {
    name  = "log_parameter_max_length"
    value = "0"
  }
  parameters {
    name  = "log_parameter_max_length_on_error"
    value = "0"
  }
  parameters {
    name  = "log_duration"
    value = "off"
  }
  parameters {
    name  = "log_min_duration_sample"
    value = "-1"
  }
  parameters {
    name  = "log_transaction_sample_rate"
    value = "0"
  }
  deletion_protection = true
  # 迁移 0001–0058 用到 pgcrypto（WITH SCHEMA starbuddy_ext）；由人确认 RDS 允许的扩展清单（README.md 第 5 节）
  tags = local.tags
}

resource "alicloud_db_backup_policy" "main" {
  instance_id                 = alicloud_db_instance.main.id
  preferred_backup_period     = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
  preferred_backup_time       = "18:00Z-19:00Z" # 北京时间 02:00–03:00
  backup_retention_period     = var.rds_backup_retention_days
  enable_backup_log           = true # 日志备份：按时间点恢复（PITR）
  log_backup_retention_period = var.rds_log_backup_retention_days
  # compress_type 不写：PostgreSQL 云盘实例不收 "4"（2026-10-30 apply 报 IncorrectCompressType），用 RDS 缺省
}
