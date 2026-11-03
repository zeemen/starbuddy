# 托管 K8s（ACK Pro，决定记录第 2 条，沿用 deploy/k8s/staging.yaml）：API Server 不开公网（只经 VPC / VPN 访问），Terway 网络
# （Pod 直接用 VPC 内网地址，NetworkPolicy 生效），Secret 落盘暂不做 KMS 信封加密（决定记录第 15 条），控制面日志进 SLS，RRSA 给 ack-secret-manager 最小权限。

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
  # 读集群时 provider 会取 kubeconfig；不把客户端证书与私钥写进状态（LT-03 lessons 待人确认第 9 条；子账号另被拒绝取 kubeconfig）
  skip_set_certificate_authority = true
  # Secret 落盘不做 KMS 信封加密（LT-03 决定记录第 15 条 6a：不买 KMS 实例）。补偿：只有部署者能读 starbuddy-staging 的 Secret（RBAC），
  # 应用 ServiceAccount 不挂令牌，Secret 的权威来源在集群外（OOS 加密参数，kms.tf）。买了 KMS 实例后在本集群上开启（ACK Pro 支持已有集群开启）
  disable_encryption = true
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
  # LT-05（specs/launch-lt05-observability 决定记录第 1 条）：ARMS 托管 Prometheus 的采集组件。采集目标是 deploy/k8s/observability.yaml
  # 的 PodMonitor（各服务 GET /metrics，免鉴权、只在 Pod 内网；NetworkPolicy metrics-from-collector 只放行本组件）。
  # 告警规则 deploy/observability/alerts.yaml 由人在 ARMS 控制台导入；通知渠道与接收人由人配置（不在 IaC，不进仓库）。
  addons {
    name = "arms-prometheus"
    # 阶段 C 先不装（LT-03 决定记录第 17 条：没有 NAT 时连不上 ARMS 公网端点），阶段 D 之后 arms_prometheus_enabled 改 true
    disabled = !var.arms_prometheus_enabled
  }
  addons {
    name = "ack-secret-manager" # OOS 加密参数 → k8s Secret（只读名单内的参数，kms.tf 的 RAM 角色）
  }
  addons {
    name     = "nginx-ingress-controller"
    disabled = true # 不对外：前端只经内网负载均衡（deploy/k8s 的 web Service）
  }
  # RDS 云盘加密不能停在 deferred（rds.tf 两步写法，安全审查建议）：阶段 B-enc 之后的阶段都要传 ServiceKey，否则这里直接失败
  lifecycle {
    precondition {
      condition     = var.rds_disk_encryption == "ServiceKey"
      error_message = "先完成阶段 B-enc（RDS 云盘加密改为服务密钥），之后的 plan 一律传 rds_disk_encryption = \"ServiceKey\"。"
    }
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
  system_disk_encrypted = true # 不指定 system_disk_kms_key：用 ECS 云盘服务密钥（决定记录第 15 条）
  labels {
    key   = "starbuddy/pool"
    value = "default"
  }
  tags = local.tags
}
