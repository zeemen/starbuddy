# 输出只给执行 apply 的人填写部署配置用（不提交到仓库）：都是资源标识与内网地址，没有口令与密钥。
output "vpc_id" {
  value = alicloud_vpc.main.id
}

output "cluster_id" {
  value = alicloud_cs_managed_kubernetes.main.id
}

output "rds_private_host" {
  description = "RDS 内网连接地址（主机名）：写进各服务连接串（经 OOS 加密参数），配 sslmode=verify-full 与 sslrootcert（deploy/config/README.md 第 8 节）"
  value       = alicloud_db_instance.main.connection_string
}

output "slb_acl_id" {
  description = "填进 deploy/k8s/staging.yaml web Service 的 acl-id 注解"
  value       = alicloud_slb_acl.office_vpn.id
}

output "oss_bucket" {
  value = alicloud_oss_bucket.main.bucket
}

output "log_project" {
  value = alicloud_log_project.main.project_name
}

output "acr_instance_id" {
  value = alicloud_cr_ee_instance.main.id
}
