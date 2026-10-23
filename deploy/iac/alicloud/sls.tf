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
