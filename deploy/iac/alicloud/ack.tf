# 托管 K8s（ACK Pro，决定记录第 2 条，沿用 deploy/k8s/staging.yaml）：API Server 不开公网（只经 VPC / VPN 访问），Terway 网络
# （Pod 直接用 VPC 内网地址，NetworkPolicy 生效），Secret 落盘用 KMS 加密，控制面日志进 SLS，RRSA 给 ack-secret-manager 最小权限。

resource "alicloud_cs_managed_kubernetes" "main" {
  name                 = local.name
  cluster_spec         = "ack.pro.small"
  version              = var.k8s_version
  vswitch_ids          = alicloud_vswitch.node[*].id
  pod_vswitch_ids      = alicloud_vswitch.pod[*].id
  service_cidr         = var.service_cidr
  new_nat_gateway      = false # 用 network.tf 的 NAT（只 SNAT + 云防火墙白名单），不让集群另建
  slb_internet_enabled = false # API Server 只有内网地址
  proxy_mode           = "ipvs"
  security_group_id    = alicloud_security_group.nodes.id
  deletion_protection  = true
  enable_rrsa          = true
  # Secret 落盘加密（KMS 主密钥）
  encryption_provider_key = alicloud_kms_key.main.id
  # 控制面日志（审计、API Server 等）进 SLS，留存同 log_retention_days
  control_plane_log_project    = alicloud_log_project.main.project_name
  control_plane_log_ttl        = tostring(var.log_retention_days)
  control_plane_log_components = ["apiserver", "kcm", "scheduler", "ccm"]
  tags                         = local.tags

  addons {
    name   = "terway-eniip"
    config = jsonencode({ NetworkPolicy = "true" }) # NetworkPolicy 生效：deploy/k8s 的缺省拒绝出网
  }
  addons {
    name = "csi-plugin"
  }
  addons {
    name = "managed-csiprovisioner"
  }
  addons {
    name   = "logtail-ds"
    config = jsonencode({ IngressDashboardEnabled = "false", sls_project_name = alicloud_log_project.main.project_name })
  }
  addons {
    name = "ack-secret-manager" # 凭据管家 → k8s Secret（只读 starbuddy-staging/ 前缀，kms.tf 的 RAM 角色）
  }
  addons {
    name     = "nginx-ingress-controller"
    disabled = true # 不对外：前端只经内网负载均衡（deploy/k8s 的 web Service）
  }
}

resource "alicloud_cs_kubernetes_node_pool" "default" {
  cluster_id            = alicloud_cs_managed_kubernetes.main.id
  node_pool_name        = "${local.name}-default"
  vswitch_ids           = alicloud_vswitch.node[*].id
  instance_types        = var.node_instance_types
  desired_size          = var.node_count
  security_group_ids    = [alicloud_security_group.nodes.id]
  image_type            = "AliyunLinux3"
  runtime_name          = "containerd"
  key_name              = var.node_key_pair_name
  install_cloud_monitor = true
  # 不给节点分配公网地址（不设 internet_max_bandwidth_out）；出网只经 NAT
  system_disk_category  = "cloud_essd"
  system_disk_size      = 120
  system_disk_encrypted = true
  system_disk_kms_key   = alicloud_kms_key.main.id
  labels {
    key   = "starbuddy/pool"
    value = "default"
  }
  tags = local.tags
}
