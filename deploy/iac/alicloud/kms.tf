# 密钥管理（KMS）：一把对称主密钥，加密 RDS 云盘、OSS 服务端加密、节点系统盘与 K8s Secret 落盘（ACK 的 encryption_provider_key）；
# 另一把只给凭据管家（见下）。
# 应用密钥（服务间令牌、HMAC 密钥、连接串、模型供应商 Key 等）存 KMS 凭据管家，由人在控制台创建（取值不进 Terraform 状态）；
# 集群里经 ack-secret-manager（RRSA 授权，见 ack.tf）同步成同名 k8s Secret，Deployment 只经 secretKeyRef 引用（deploy/k8s/staging.yaml）。
# 安全审查采纳建议：凭据管家另用一把专用主密钥（alicloud_kms_key.secrets；人在控制台创建凭据时选它作加密密钥），
# ack-secret-manager 只能用它 Decrypt、不能动主密钥；可读的凭据逐个列名（不用通配符 *，规则 R19），名单与 k8s 的 Secret 名一致（部署测试核对）。
# 集群内的 RBAC（由人配置，不在本仓库）：只有部署者能在 starbuddy-staging 命名空间创建 / 修改同步规则（ExternalSecret 这一类 CR），
# 应用的 ServiceAccount 不挂令牌、没有任何 RBAC 权限——否则任何能建同步规则的人都能借 ack-secret-manager 的身份读走名单内的凭据。

locals {
  # 与 deploy/k8s/staging.yaml 里 secretKeyRef / secretName 引用的 Secret 名一致（deploy/tests/test_iac.py 核对）
  app_secrets = [
    "starbuddy-adapters",
    "starbuddy-bff",
    "starbuddy-insight",
    "starbuddy-knowledge",
    "starbuddy-learning",
    "starbuddy-model-vendors",
    "starbuddy-review-console",
    "starbuddy-scheduler",
    "starbuddy-signed-config",
    "starbuddy-staging-otp",
    "starbuddy-web",
  ]
}

resource "alicloud_kms_key" "secrets" {
  description            = "StarBuddy staging 凭据管家专用密钥（只加密应用凭据；ack-secret-manager 只能用它解密）"
  key_spec               = "Aliyun_AES_256"
  key_usage              = "ENCRYPT/DECRYPT"
  protection_level       = "SOFTWARE"
  automatic_rotation     = "Enabled"
  rotation_interval      = "31536000s"
  pending_window_in_days = 30
  deletion_protection    = "Enabled"
  status                 = "Enabled"
  tags                   = local.tags
}

resource "alicloud_kms_alias" "secrets" {
  alias_name = "alias/${local.name}-secrets"
  key_id     = alicloud_kms_key.secrets.id
}

resource "alicloud_kms_key" "main" {
  description            = "StarBuddy staging 主密钥（RDS、OSS、节点盘、K8s Secret 落盘加密）"
  key_spec               = "Aliyun_AES_256"
  key_usage              = "ENCRYPT/DECRYPT"
  protection_level       = "SOFTWARE"
  automatic_rotation     = "Enabled"
  rotation_interval      = "31536000s"
  pending_window_in_days = 30
  deletion_protection    = "Enabled"
  status                 = "Enabled"
  tags                   = local.tags
}

resource "alicloud_kms_alias" "main" {
  alias_name = "alias/${local.name}"
  key_id     = alicloud_kms_key.main.id
}

# ack-secret-manager 读凭据管家的最小权限：只读名单内的凭据（starbuddy-staging/<Secret 名>）、只用凭据专用密钥解密；
# 账号 ID 在 plan 时从数据源取，不写死
resource "alicloud_ram_policy" "secret_reader" {
  policy_name = "${local.name}-secret-reader"
  description = "ack-secret-manager 只读名单内的应用凭据"
  policy_document = jsonencode({
    Version = "1"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["kms:GetSecretValue"]
        Resource = [for s in local.app_secrets : "acs:kms:${var.region}:${data.alicloud_account.current.id}:secret/${var.name_prefix}-${var.environment}/${s}"]
      },
      {
        Effect   = "Allow"
        Action   = ["kms:Decrypt"]
        Resource = ["acs:kms:${var.region}:${data.alicloud_account.current.id}:key/${alicloud_kms_key.secrets.id}"]
      },
    ]
  })
}

resource "alicloud_ram_role" "secret_reader" {
  role_name   = "${local.name}-secret-reader"
  description = "ack-secret-manager 经 RRSA（集群 OIDC）扮演，只读应用凭据"
  assume_role_policy_document = jsonencode({
    Version = "1"
    Statement = [{
      Effect    = "Allow"
      Action    = "sts:AssumeRole"
      Principal = { Federated = [alicloud_cs_managed_kubernetes.main.rrsa_metadata[0].ram_oidc_provider_arn] }
      Condition = {
        StringEquals = {
          "oidc:aud" = "sts.aliyuncs.com"
          "oidc:iss" = alicloud_cs_managed_kubernetes.main.rrsa_metadata[0].rrsa_oidc_issuer_url
          "oidc:sub" = "system:serviceaccount:kube-system:ack-secret-manager"
        }
      }
    }]
  })
  tags = local.tags
}

resource "alicloud_ram_role_policy_attachment" "secret_reader" {
  policy_name = alicloud_ram_policy.secret_reader.policy_name
  policy_type = "Custom"
  role_name   = alicloud_ram_role.secret_reader.role_name
}
