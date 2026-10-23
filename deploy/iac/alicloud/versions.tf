# LT-03 境内 staging 基础设施草案（specs/launch-lt03-cloud-staging 决定记录：阿里云华北 2（北京）、托管 K8s ACK、只内网 / VPN、
# Terraform 仅 validate 与静态检查）。本目录只有声明，没有状态文件、没有凭据、没有真实账号 ID；plan / apply 由人在批准后执行
# （步骤见 README.md）。工具与 provider 的许可证核对见 README.md 第 1 节。
terraform {
  # Terraform 1.6 起为 BSL 1.1：只作开发工具在本机 / CI 内部使用、不分发、不对外提供托管服务；许可证不合时改用 OpenTofu（MPL-2.0），
  # 本目录的写法两者通用（README.md 第 1 节）。
  required_version = ">= 1.6.0, < 2.0.0"

  required_providers {
    alicloud = {
      source = "aliyun/alicloud" # 阿里云官方 provider，MPL-2.0
      # 由人在第一次 validate 前锁定到当时的确切版本并提交 .terraform.lock.hcl（锁文件只含 provider 摘要，不含状态与凭据）
      version = "~> 1.230"
    }
  }

  # 状态只放 OSS 远端（加密、版本化、另建的状态桶 + 表格存储锁），本仓库永远不提交状态文件。
  # 这里是部分配置：bucket、prefix、key、tablestore_endpoint 等在 terraform init 时由人用 -backend-config 给出（README.md 第 3 节）；
  # 只做 validate 时用 terraform init -backend=false，不连远端。
  backend "oss" {}
}
