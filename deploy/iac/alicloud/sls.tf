# 日志服务（SLS）：集群容器日志（logtail-ds 采集标准输出）与控制面日志。留存不少于 180 天（《网络安全法》第二十一条），服务端加密。
# 不记个人信息的口径在应用侧：各服务日志只记方法、状态码、错误码、请求 ID（UUID）、耗时、学生 ID 的带密钥哈希等，不记请求体、
# 作答、手机号、验证码、令牌与连接串（各服务的日志约定与测试；BFF 访问日志见 services/bff/src/http/server.ts）。这里不建全文索引，
# 不把日志投递到 OSS 以外的地方；查询权限只给运维 RAM 用户组（由人授权）。

resource "alicloud_log_project" "main" {
  project_name = local.name
  description  = "StarBuddy staging 日志（不含请求体与个人信息原文）"
  tags         = local.tags
}

resource "alicloud_log_store" "app" {
  project_name          = alicloud_log_project.main.project_name
  logstore_name         = "app"
  retention_period      = var.log_retention_days
  shard_count           = 2
  auto_split            = true
  max_split_shard_count = 8
  append_meta           = false # 不追加客户端公网 IP 等元数据

  encrypt_conf {
    enable       = true
    encrypt_type = "default"
  }
}

# LT-05：应用日志是一行一个 JSON（ts、level、service、logger、route、request_id、status、duration_ms…，各服务 obs 模块集中脱敏），
# 采集配置见 deploy/k8s/observability.yaml（AliyunLogConfig，标准输出 + 标准错误，JSON 展开一层）。这里只给白名单字段建字段索引，
# **不建全文索引**（R21：没有 full_text 块；字段名只收白名单，作答、内容、手机号、令牌一类字段即使出现也不可检索）。
resource "alicloud_log_store_index" "app" {
  project  = alicloud_log_project.main.project_name
  logstore = alicloud_log_store.app.logstore_name

  field_search {
    name             = "service"
    type             = "text"
    token            = ","
    enable_analytics = true
  }
  field_search {
    name             = "level"
    type             = "text"
    token            = ","
    enable_analytics = true
  }
  field_search {
    name             = "route"
    type             = "text"
    token            = ","
    enable_analytics = true
  }
  field_search {
    name             = "event"
    type             = "text"
    token            = ","
    enable_analytics = true
  }
  field_search {
    name             = "request_id"
    type             = "text"
    token            = ","
    enable_analytics = false
  }
  field_search {
    name             = "status"
    type             = "long"
    enable_analytics = true
  }
  field_search {
    name             = "duration_ms"
    type             = "long"
    enable_analytics = true
  }
}

resource "alicloud_log_store" "audit" {
  project_name          = alicloud_log_project.main.project_name
  logstore_name         = "audit"
  retention_period      = var.log_retention_days
  shard_count           = 1
  auto_split            = true
  max_split_shard_count = 4
  append_meta           = false

  encrypt_conf {
    enable       = true
    encrypt_type = "default"
  }
}
