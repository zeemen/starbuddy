# 出网白名单（ADR 09 §5；LT-02 lessons 待人确认 1：按主机名放行需要出网网关 / FQDN 策略）：云防火墙 NAT 边界的访问控制，
# 出方向只放行模型供应商接入主机名的 TCP 443（var.model_egress_fqdns，由人填写，与路由表 allowed_hosts、vendors.md、
# deploy/k8s 学习服务 Pod 注解三处一致），最后一条拒绝其余全部出网。集群内另有 NetworkPolicy：只有学习服务 Pod 能出网到 443。
# 前置（由人完成）：开通云防火墙（包年包月）并在控制台为 network.tf 的 NAT 网关开启 NAT 边界防火墙，然后把变量
# nat_firewall_confirmed 设为 true（没有缺省值，必须显式给出；为 false 时每条 SNAT 的前置条件都不通过，出网不会被静默放开；
# 前几个阶段的 plan 传 false 并用 -target 排除 SNAT，见 deploy/runbooks/staging-first-deploy.md 第 1 节）。
# 不用 Terraform 开启 NAT 边界防火墙：对应资源（alicloud_cloud_firewall_nat_firewall）的参数与计费方式待人核实，
# 所以只用“确认变量 + SNAT depends_on 访问控制”两道兜底（见 network.tf 的 alicloud_snat_entry）。

resource "alicloud_cloud_firewall_nat_firewall_control_policy" "model_vendor" {
  count                 = length(var.model_egress_fqdns)
  nat_gateway_id        = alicloud_nat_gateway.main.id
  direction             = "out"
  acl_action            = "accept"
  description           = "模型供应商接入 ${var.model_egress_fqdns[count.index]}（443）"
  source_type           = "net"
  source                = var.vpc_cidr
  destination_type      = "domain"
  destination           = var.model_egress_fqdns[count.index]
  domain_resolve_type   = "FQDN_AND_DNS"
  proto                 = "TCP"
  dest_port_type        = "port"
  dest_port             = "443/443"
  application_name_list = ["HTTPS"]
  ip_version            = "4"
  new_order             = tostring(count.index + 1)
  release               = "true"
}

resource "alicloud_cloud_firewall_nat_firewall_control_policy" "deny_rest" {
  nat_gateway_id        = alicloud_nat_gateway.main.id
  direction             = "out"
  acl_action            = "drop"
  description           = "白名单以外的出网一律拒绝"
  source_type           = "net"
  source                = var.vpc_cidr
  destination_type      = "net"
  destination           = "0.0.0.0/0"
  proto                 = "ANY"
  dest_port_type        = "port"
  dest_port             = "0/0"
  application_name_list = ["ANY"]
  ip_version            = "4"
  new_order             = tostring(length(var.model_egress_fqdns) + 1)
  release               = "true"
}
