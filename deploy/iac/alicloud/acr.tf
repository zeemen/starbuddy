# 镜像仓库（ACR 企业版）：私有命名空间与仓库（不自动建仓、缺省私有），只开 VPC 访问端点（集群拉镜像不出公网），不配公网访问控制入口。
# 镜像由 deploy/ci 的流水线构建、生成 SBOM、扫描、签名后推送；基础镜像也从这里的镜像（同步自上游，按摘要锁定）拉取。
# 签名与验签：流水线推送后用 cosign 签名并立即验签（deploy/ci/README.md 第 4 节）；ACK 容器安全策略用同一把公钥验签（由人配置）。
# 实例本身不在 IaC 里（LT-03 决定记录第 16 条）：包年包月实例由人在控制台下单、续费（子账号不碰计费接口，provider 读实例要调计费查询），
# 这里只管命名空间、仓库与 VPC 端点，实例 ID 经变量 acr_instance_id 传入；规格（缺省选高级版，支持镜像加签）与续费方式按手册 B' 人工核对。

locals {
  # 与 deploy/scripts/build-images.sh、deploy/ci/images.txt 的镜像清单一致（deploy/tests/test_iac.py 核对）
  images      = ["learning", "insight", "knowledge", "scheduler", "review-console", "bff", "adapter-aya", "adapter-zhixing", "adapter-wenli", "web"]
  base_images = ["base-python", "base-node"] # 上游基础镜像同步到这里（按摘要锁定）
}

resource "alicloud_cr_ee_namespace" "main" {
  instance_id        = var.acr_instance_id
  name               = var.name_prefix
  auto_create        = false
  default_visibility = "PRIVATE"
}

resource "alicloud_cr_ee_repo" "images" {
  for_each    = toset(concat(local.images, local.base_images))
  instance_id = var.acr_instance_id
  namespace   = alicloud_cr_ee_namespace.main.name
  name        = "starbuddy-${each.key}"
  summary     = "StarBuddy staging 镜像 starbuddy-${each.key}"
  repo_type   = "PRIVATE"
}

resource "alicloud_cr_vpc_endpoint_linked_vpc" "main" {
  instance_id                      = var.acr_instance_id
  vpc_id                           = alicloud_vpc.main.id
  vswitch_id                       = alicloud_vswitch.node[0].id
  module_name                      = "Registry"
  enable_create_dns_record_in_pvzt = true
}
