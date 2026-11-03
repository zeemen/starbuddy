# 应用凭据（LT-03 决定记录第 14、15 条：现阶段不买 KMS 实例，不建自建密钥、不用凭据管家）：
# 应用密钥（服务间令牌、HMAC 密钥、连接串、模型供应商 Key 等）存 OOS 加密参数（运维编排，阿里云托管密钥加密），由人在控制台创建
# （取值不进 Terraform 状态与仓库）；集群里经 ack-secret-manager（RRSA 授权，见 ack.tf；同步规则 provider 用 oos）同步成同名 k8s Secret，
# Deployment 只经 secretKeyRef 引用（deploy/k8s/staging.yaml）。可读的参数逐个列名（不用通配符 *，规则 R19），名单与 k8s 的 Secret 名一致（部署测试核对）。
# 残余风险（specs/launch-lt03-cloud-staging/kms-alternatives.md 第 7 项）：加密参数的密钥由阿里云托管，没有专用密钥这层隔离；
# 买了 KMS 实例后再评估切回凭据管家与专用密钥（届时自建密钥另起变更）。
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

# ack-secret-manager 读 OOS 加密参数的最小权限：只读名单内的参数（starbuddy-staging/<Secret 名>）；账号 ID 在 plan 时从数据源取，不写死。
# 读取时是否还要 KMS 动作（加密参数用阿里云托管密钥）以首次同步的报错为准，补的动作同样逐个列资源（lessons 待人确认）
resource "alicloud_ram_policy" "secret_reader" {
  policy_name = "${local.name}-secret-reader"
  description = "ack-secret-manager 只读名单内的应用凭据（OOS 加密参数）"
  policy_document = jsonencode({
    Version = "1"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["oos:GetSecretParameter"]
        Resource = [for s in local.app_secrets : "acs:oos:${var.region}:${data.alicloud_account.current.id}:secretparameter/${var.name_prefix}-${var.environment}/${s}"]
      },
    ]
  })
}

resource "alicloud_ram_role" "secret_reader" {
  role_name   = "${local.name}-secret-reader"
  description = "ack-secret-manager 经 RRSA（集群 OIDC）扮演，只读应用凭据（OOS 加密参数）"
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
