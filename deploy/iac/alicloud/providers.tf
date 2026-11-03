# provider 只声明地域；凭据一律不写在这里，由执行 plan / apply 的人经 RAM 角色（STS 临时凭据）或运行环境提供（README.md 第 3 节）。
provider "alicloud" {
  region = var.region
}

data "alicloud_account" "current" {}

locals {
  name = "${var.name_prefix}-${var.environment}"
  # 每个支持标签的资源都带这组标签（deploy/iac/check_iac.py 规则 R08 核对）
  tags = merge(var.extra_tags, {
    "app"         = "starbuddy"
    "environment" = var.environment
    "managed-by"  = "terraform"
    "data-class"  = "personal-minor" # 按个人信息从严；不买 KMS 实例期间只放合成数据（LT-03 决定记录第 15 条），真实数据门槛见手册 3.3
  })
}
