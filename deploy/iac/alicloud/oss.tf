# 对象存储（OSS）私有桶：页面快照、RDS 逻辑备份导出、日志归档。私有 ACL + 阻止公共访问、KMS 服务端加密、版本控制、生命周期。
# 不开静态网站、不配 CORS、不建公共读策略；访问只经 RAM 授权（集群内经 RRSA，另行授权）；VPC 内经 OSS 网关终端节点访问（只放行本桶）。

resource "alicloud_oss_bucket" "main" {
  bucket          = var.oss_bucket_name
  storage_class   = "Standard"
  redundancy_type = "ZRS"
  force_destroy   = false

  server_side_encryption_rule {
    sse_algorithm     = "KMS"
    kms_master_key_id = alicloud_kms_key.main.id
  }

  versioning {
    status = "Enabled"
  }

  lifecycle_rule {
    id      = "noncurrent-expire"
    enabled = true
    prefix  = ""
    noncurrent_version_expiration {
      days = var.oss_noncurrent_days
    }
    abort_multipart_upload {
      days = 7
    }
  }

  lifecycle_rule {
    id      = "archive"
    enabled = true
    prefix  = "archive/"
    transitions {
      days          = var.oss_archive_prefix_days
      storage_class = "Archive"
    }
  }

  lifecycle_rule {
    id      = "tmp-expire"
    enabled = true
    prefix  = "tmp/"
    expiration {
      days = var.oss_expire_days
    }
  }

  tags = local.tags
}

resource "alicloud_oss_bucket_acl" "main" {
  bucket = alicloud_oss_bucket.main.bucket
  acl    = "private"
}

resource "alicloud_oss_bucket_public_access_block" "main" {
  bucket              = alicloud_oss_bucket.main.bucket
  block_public_access = true
}

# OSS 网关终端节点（安全审查 B1）：VPC 内访问 OSS 走终端节点，终端节点策略只放行本桶与它下面的对象、只放行用得到的动作；
# 其他桶（含别人的桶）经这条路一律拒绝——防止拿到节点上临时凭据的人把数据写到外部桶。
# 待人确认：cn-beijing 是否已支持 OSS 网关终端节点、service_name 的写法、以及 provider 里这两个资源的参数名（以 terraform validate 为准）；
# 不支持时退回“节点安全组只对 OSS 内网端点地址开 443”（cloud_service_endpoint_cidrs），并另行评审数据外流风险。
resource "alicloud_vpc_gateway_endpoint" "oss" {
  gateway_endpoint_name = "${local.name}-oss"
  service_name          = "com.aliyun.${var.region}.oss"
  vpc_id                = alicloud_vpc.main.id
  policy_document = jsonencode({
    Version = "1"
    Statement = [{
      Effect    = "Allow"
      Principal = "*"
      Action    = ["oss:GetObject", "oss:PutObject", "oss:ListObjects", "oss:GetBucketInfo", "oss:AbortMultipartUpload", "oss:ListParts"]
      Resource  = ["acs:oss:*:*:${alicloud_oss_bucket.main.bucket}", "acs:oss:*:*:${alicloud_oss_bucket.main.bucket}/*"]
    }]
  })
  tags = local.tags
}

resource "alicloud_vpc_gateway_endpoint_route_table_attachment" "oss" {
  gateway_endpoint_id = alicloud_vpc_gateway_endpoint.oss.id
  route_table_id      = alicloud_vpc.main.route_table_id
}
