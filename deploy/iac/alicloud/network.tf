# 网络：一个 VPC；节点、Pod（Terway）、数据库各两个交换机（两个可用区）；企业级安全组；NAT 网关只做 SNAT 出网（没有 DNAT，
# 公网进不来），出网目的地再由云防火墙 NAT 边界按主机名白名单放行（egress.tf）。staging 不对外：前端入口是内网负载均衡（slb.tf）。

resource "alicloud_vpc" "main" {
  vpc_name    = local.name
  cidr_block  = var.vpc_cidr
  description = "StarBuddy staging（只内网 / VPN 访问）"
  tags        = local.tags
}

resource "alicloud_vswitch" "node" {
  count        = 2
  vswitch_name = "${local.name}-node-${count.index}"
  vpc_id       = alicloud_vpc.main.id
  cidr_block   = var.node_vswitch_cidrs[count.index]
  zone_id      = var.zone_ids[count.index]
  tags         = local.tags
}

resource "alicloud_vswitch" "pod" {
  count        = 2
  vswitch_name = "${local.name}-pod-${count.index}"
  vpc_id       = alicloud_vpc.main.id
  cidr_block   = var.pod_vswitch_cidrs[count.index]
  zone_id      = var.zone_ids[count.index]
  tags         = local.tags
}

resource "alicloud_vswitch" "db" {
  count        = 2
  vswitch_name = "${local.name}-db-${count.index}"
  vpc_id       = alicloud_vpc.main.id
  cidr_block   = var.db_vswitch_cidrs[count.index]
  zone_id      = var.zone_ids[count.index]
  tags         = local.tags
}

# 节点安全组：入方向只收 VPC 内（集群内、内网负载均衡到节点、运维经 VPN 进 VPC）；没有任何 0.0.0.0/0 入方向规则。
# 用普通安全组：出方向要靠“优先级 + 拒绝规则”收窄（企业级安全组不支持拒绝规则与优先级，待人核实），规则按优先级从小到大匹配。
resource "alicloud_security_group" "nodes" {
  security_group_name = "${local.name}-nodes"
  vpc_id              = alicloud_vpc.main.id
  security_group_type = "normal"
  inner_access_policy = "Drop" # 组内互通也只按规则（集群内互访经下面的 VPC 网段规则）
  description         = "ACK 节点：入方向只收 VPC 内网"
  tags                = local.tags
}

resource "alicloud_security_group_rule" "ingress_vpc" {
  security_group_id = alicloud_security_group.nodes.id
  type              = "ingress"
  ip_protocol       = "all"
  port_range        = "-1/-1"
  cidr_ip           = var.vpc_cidr
  policy            = "accept"
  priority          = 1
  description       = "VPC 内网"
}

resource "alicloud_security_group_rule" "egress_vpc" {
  security_group_id = alicloud_security_group.nodes.id
  type              = "egress"
  ip_protocol       = "all"
  port_range        = "-1/-1"
  cidr_ip           = var.vpc_cidr
  policy            = "accept"
  priority          = 1
  description       = "VPC 内网（RDS、ACR VPC 端点、内网 DNS 经 VPC）"
}

# 阿里云内部服务网段（100.64.0.0/10）不经 NAT、也就不过云防火墙的主机名白名单（安全审查 B1），所以不整段放行，只开用得到的：
#   内网 DNS 100.100.2.136 / 100.100.2.138 的 53（UDP、TCP）、实例元数据 100.100.100.200 的 80（节点取 RAM 角色临时凭据），
#   以及 var.cloud_service_endpoint_cidrs（ACK 管控、SLS、KMS、STS、OSS 网关终端节点的内网端点解析出的地址，由人填写）的 443；
#   其余 100.64.0.0/10 在 0.0.0.0/0:443 之前拒绝（egress_deny_cloud_internal）。ACR 走 VPC 端点（在 VPC 网段内，见 egress_vpc）。
resource "alicloud_security_group_rule" "egress_dns_udp_136" {
  security_group_id = alicloud_security_group.nodes.id
  type              = "egress"
  ip_protocol       = "udp"
  port_range        = "53/53"
  cidr_ip           = "100.100.2.136/32"
  policy            = "accept"
  priority          = 1
  description       = "内网 DNS"
}

resource "alicloud_security_group_rule" "egress_dns_tcp_136" {
  security_group_id = alicloud_security_group.nodes.id
  type              = "egress"
  ip_protocol       = "tcp"
  port_range        = "53/53"
  cidr_ip           = "100.100.2.136/32"
  policy            = "accept"
  priority          = 1
  description       = "内网 DNS"
}

resource "alicloud_security_group_rule" "egress_dns_udp_138" {
  security_group_id = alicloud_security_group.nodes.id
  type              = "egress"
  ip_protocol       = "udp"
  port_range        = "53/53"
  cidr_ip           = "100.100.2.138/32"
  policy            = "accept"
  priority          = 1
  description       = "内网 DNS"
}

resource "alicloud_security_group_rule" "egress_dns_tcp_138" {
  security_group_id = alicloud_security_group.nodes.id
  type              = "egress"
  ip_protocol       = "tcp"
  port_range        = "53/53"
  cidr_ip           = "100.100.2.138/32"
  policy            = "accept"
  priority          = 1
  description       = "内网 DNS"
}

resource "alicloud_security_group_rule" "egress_metadata" {
  security_group_id = alicloud_security_group.nodes.id
  type              = "egress"
  ip_protocol       = "tcp"
  port_range        = "80/80"
  cidr_ip           = "100.100.100.200/32"
  policy            = "accept"
  priority          = 1
  description       = "实例元数据（RAM 角色临时凭据）"
}

resource "alicloud_security_group_rule" "egress_cloud_endpoints" {
  for_each          = toset(var.cloud_service_endpoint_cidrs)
  security_group_id = alicloud_security_group.nodes.id
  type              = "egress"
  ip_protocol       = "tcp"
  port_range        = "443/443"
  cidr_ip           = each.value
  policy            = "accept"
  priority          = 1
  description       = "阿里云服务内网端点（ACK 管控、SLS、KMS、STS、OSS）"
}

resource "alicloud_security_group_rule" "egress_deny_cloud_internal" {
  security_group_id = alicloud_security_group.nodes.id
  type              = "egress"
  ip_protocol       = "all"
  port_range        = "-1/-1"
  cidr_ip           = "100.64.0.0/10"
  policy            = "drop"
  priority          = 10
  description       = "阿里云内部服务网段其余去向一律拒绝（排在 0.0.0.0/0:443 之前）"
}

resource "alicloud_security_group_rule" "egress_deny_link_local" {
  security_group_id = alicloud_security_group.nodes.id
  type              = "egress"
  ip_protocol       = "all"
  port_range        = "-1/-1"
  cidr_ip           = "169.254.0.0/16"
  policy            = "drop"
  priority          = 10
  description       = "链路本地地址一律拒绝"
}

# 出公网只开 443：去向由 NAT 边界云防火墙按主机名白名单再收窄（egress.tf），集群内再由 NetworkPolicy 只放学习服务 Pod（deploy/k8s）
resource "alicloud_security_group_rule" "egress_https" {
  security_group_id = alicloud_security_group.nodes.id
  type              = "egress"
  ip_protocol       = "tcp"
  port_range        = "443/443"
  cidr_ip           = "0.0.0.0/0"
  policy            = "accept"
  priority          = 50
  description       = "HTTPS 出网（经 NAT + 云防火墙主机名白名单）"
}

resource "alicloud_security_group_rule" "egress_deny_rest" {
  security_group_id = alicloud_security_group.nodes.id
  type              = "egress"
  ip_protocol       = "all"
  port_range        = "-1/-1"
  cidr_ip           = "0.0.0.0/0"
  policy            = "drop"
  priority          = 100
  description       = "其余出网一律拒绝"
}

# NAT 网关：只做 SNAT（节点与 Pod 交换机出网），不建 DNAT（alicloud_forward_entry 禁止，check_iac.py 规则 R05）
resource "alicloud_nat_gateway" "main" {
  vpc_id           = alicloud_vpc.main.id
  nat_gateway_name = local.name
  nat_type         = "Enhanced"
  payment_type     = "PayAsYouGo"
  vswitch_id       = alicloud_vswitch.node[0].id
  network_type     = "internet"
  description      = "只做 SNAT 出网；出网目的地由云防火墙 NAT 边界按主机名白名单放行"
  tags             = local.tags
}

resource "alicloud_eip_address" "nat" {
  address_name         = "${local.name}-nat"
  isp                  = "BGP"
  internet_charge_type = "PayByTraffic"
  payment_type         = "PayAsYouGo"
  bandwidth            = "10"
  description          = "NAT 出网地址（只出不进）"
  tags                 = local.tags
}

resource "alicloud_eip_association" "nat" {
  allocation_id = alicloud_eip_address.nat.id
  instance_id   = alicloud_nat_gateway.main.id
  instance_type = "Nat"
}

resource "alicloud_snat_entry" "node" {
  count             = 2
  snat_table_id     = alicloud_nat_gateway.main.snat_table_ids
  source_vswitch_id = alicloud_vswitch.node[count.index].id
  snat_ip           = alicloud_eip_address.nat.ip_address
  snat_entry_name   = "${local.name}-node-${count.index}"
  # 安全审查采纳建议：云防火墙没开就不能静默放开出网——SNAT 排在白名单与“拒绝其余”两类访问控制之后建
  # （NAT 边界防火墙没开时访问控制建不成，SNAT 也就不会建），且必须由人确认已开启（nat_firewall_confirmed，见 egress.tf）
  depends_on = [
    alicloud_eip_association.nat,
    alicloud_cloud_firewall_nat_firewall_control_policy.model_vendor,
    alicloud_cloud_firewall_nat_firewall_control_policy.deny_rest,
  ]
  lifecycle {
    precondition {
      condition     = var.nat_firewall_confirmed
      error_message = "先在云防火墙控制台为这台 NAT 网关开启 NAT 边界防火墙，再把 nat_firewall_confirmed 设为 true。"
    }
  }
}

resource "alicloud_snat_entry" "pod" {
  count             = 2
  snat_table_id     = alicloud_nat_gateway.main.snat_table_ids
  source_vswitch_id = alicloud_vswitch.pod[count.index].id
  snat_ip           = alicloud_eip_address.nat.ip_address
  snat_entry_name   = "${local.name}-pod-${count.index}"
  depends_on = [
    alicloud_eip_association.nat,
    alicloud_cloud_firewall_nat_firewall_control_policy.model_vendor,
    alicloud_cloud_firewall_nat_firewall_control_policy.deny_rest,
  ]
  lifecycle {
    precondition {
      condition     = var.nat_firewall_confirmed
      error_message = "先在云防火墙控制台为这台 NAT 网关开启 NAT 边界防火墙，再把 nat_firewall_confirmed 设为 true。"
    }
  }
}
