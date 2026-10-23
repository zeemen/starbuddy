# 镜像仓库（ACR 企业版）：私有命名空间与仓库（不自动建仓、缺省私有），只开 VPC 访问端点（集群拉镜像不出公网），不配公网访问控制入口。
# 镜像由 deploy/ci 的流水线构建、生成 SBOM、扫描、签名后推送；基础镜像也从这里的镜像（同步自上游，按摘要锁定）拉取。
# 签名与验签：流水线推送后用 cosign 签名并立即验签（deploy/ci/README.md 第 4 节）；ACK 容器安全策略用同一把公钥验签（由人配置）。
# 规格缺省 Advanced（支持 ACR 自带的镜像加签，可另外开启；待人核实）。

locals {
  # 与 deploy/scripts/build-images.sh、deploy/ci/images.txt 的镜像清单一致（deploy/tests/test_iac.py 核对）
  images      = ["learning", "insight", "knowledge", "scheduler", "review-console", "bff", "adapter-aya", "adapter-zhixing", "adapter-wenli", "web"]
  base_images = ["base-python", "base-node"] # 上游基础镜像同步到这里（按摘要锁定）
}

resource "alicloud_cr_ee_instance" "main" {
  payment_type   = "Subscription"
  period         = var.acr_period_months
  renew_period   = 0
  renewal_status = "ManualRenewal"
  instance_type  = var.acr_instance_type
  instance_name  = local.name
}

resource "alicloud_cr_ee_namespace" "main" {
  instance_id        = alicloud_cr_ee_instance.main.id
  name               = var.name_prefix
  auto_create        = false
  default_visibility = "PRIVATE"
}

resource "alicloud_cr_ee_repo" "images" {
  for_each    = toset(concat(local.images, local.base_images))
  instance_id = alicloud_cr_ee_instance.main.id
  namespace   = alicloud_cr_ee_namespace.main.name
  name        = "starbuddy-${each.key}"
  summary     = "StarBuddy staging 镜像 starbuddy-${each.key}"
  repo_type   = "PRIVATE"
}

resource "alicloud_cr_ee_vpc_endpoint_linked_vpc" "main" {
  instance_id                      = alicloud_cr_ee_instance.main.id
  vpc_id                           = alicloud_vpc.main.id
  vswitch_id                       = alicloud_vswitch.node[0].id
  module_name                      = "Registry"
  enable_create_dns_record_in_pvzt = true
}
