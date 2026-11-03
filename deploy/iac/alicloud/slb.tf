# 内网负载均衡的访问控制（决定记录第 2 条：只内网 + VPN / 办公网白名单，不公开域名）：
# 负载均衡本身由 ACK 按 deploy/k8s/staging.yaml 的 web Service（type: LoadBalancer + intranet 注解）创建；这里只建白名单访问控制，
# 由人把输出的 slb_acl_id 填进那条 Service 的 acl-id 注解。

resource "alicloud_slb_acl" "office_vpn" {
  name       = "${local.name}-office-vpn"
  ip_version = "ipv4"
  tags       = local.tags
}

resource "alicloud_slb_acl_entry_attachment" "office_vpn" {
  for_each = toset(var.office_vpn_cidrs)
  acl_id   = alicloud_slb_acl.office_vpn.id
  entry    = each.key
  comment  = "办公网 / VPN"
}
