"""LT-03 IaC 草案（deploy/iac/alicloud）的静态规则检查（deploy/iac/check_iac.py，只用标准库，不依赖 terraform 等工具）：
- 仓库里的草案通过全部规则；
- 每条规则逐条改坏（在临时副本里改一处），断言检查能发现、且是对应的规则号（等同变异测试，先跑对照组）；
- 草案与部署清单一致：k8s 显式 clusterIP 落在 Service 网段、镜像清单与构建脚本 / ACR 仓库一致、出网白名单变量三处口径一致；
- 工具许可证与人工步骤写在 README。"""
from __future__ import annotations

import importlib.util
import ipaddress
import re
import shutil
import sys
from pathlib import Path

import pytest

from conftest import DEPLOY, ROOT

IAC = DEPLOY / "iac" / "alicloud"
_spec = importlib.util.spec_from_file_location("check_iac", DEPLOY / "iac" / "check_iac.py")
check_iac = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
sys.modules["check_iac"] = check_iac  # dataclass 需要模块在 sys.modules 里
_spec.loader.exec_module(check_iac)


def run(root: Path) -> list:
    return check_iac.check(root)


def test_repo_draft_passes():
    found = run(IAC)
    assert found == [], "\n".join(map(str, found))
    assert len(list(IAC.glob("*.tf"))) >= 10


# (名字, 文件, 原文, 替换, 期望的规则号)；原文必须恰好出现一次
MUTATIONS = [
    ("提交了状态文件", None, "terraform.tfstate", "{}", "R01"),
    ("提交了真实变量文件", None, "terraform.tfvars", "region = \"cn-beijing\"\n", "R01"),
    ("provider 写了 AccessKey", "providers.tf", "  region = var.region\n", "  region = var.region\n  access_key = \"x\"\n", "R02"),
    ("出现 AccessKey 样式字面量", "outputs.tf", "output \"vpc_id\" {", "# LTAI5tSyntheticSynthetic0\noutput \"vpc_id\" {", "R02"),
    ("RDS 在 Terraform 里建账号", "rds.tf", "resource \"alicloud_db_backup_policy\"",
     "resource \"alicloud_rds_account\" \"app\" {\n  db_instance_id = alicloud_db_instance.main.id\n  account_name = \"app\"\n  account_password = var.x\n}\n\nresource \"alicloud_db_backup_policy\"", "R02"),
    ("写死账号 ID", "kms.tf", "${data.alicloud_account.current.id}:secret", "1234567890123456:secret", "R03"),
    ("地域改成杭州", "variables.tf", "default     = \"cn-beijing\"\n", "default     = \"cn-hangzhou\"\n", "R04"),
    ("provider 写死地域", "providers.tf", "  region = var.region\n", "  region = \"cn-beijing\"\n", "R04"),
    ("可用区跨地域", "variables.tf", "[\"cn-beijing-h\", \"cn-beijing-i\"]", "[\"cn-beijing-h\", \"cn-shanghai-b\"]", "R04"),
    ("安全组入方向 0.0.0.0/0", "network.tf", "  cidr_ip           = var.vpc_cidr\n  policy            = \"accept\"\n  priority          = 1\n  description       = \"VPC 内网\"",
     "  cidr_ip           = \"0.0.0.0/0\"\n  policy            = \"accept\"\n  priority          = 1\n  description       = \"VPC 内网\"", "R05"),
    ("建 DNAT", "network.tf", "resource \"alicloud_eip_address\" \"nat\" {",
     "resource \"alicloud_forward_entry\" \"x\" {\n  forward_table_id = alicloud_nat_gateway.main.forward_table_ids\n}\n\nresource \"alicloud_eip_address\" \"nat\" {", "R05"),
    ("访问控制条目放开全网", "slb.tf", "  entry    = each.key", "  entry    = \"0.0.0.0/0\"", "R05"),
    ("办公网段给了缺省值", "variables.tf", "  description = \"可访问内网负载均衡", "  default = [\"10.0.0.0/8\"]\n  description = \"可访问内网负载均衡", "R05"),
    ("RDS 开外网连接", "rds.tf", "resource \"alicloud_db_backup_policy\"",
     "resource \"alicloud_db_connection\" \"pub\" {\n  instance_id = alicloud_db_instance.main.id\n}\n\nresource \"alicloud_db_backup_policy\"", "R06"),
    ("RDS 白名单放开全网", "rds.tf", "security_ips             = [var.vpc_cidr]", "security_ips             = [\"0.0.0.0/0\"]", "R06"),
    ("RDS 不强制 TLS", "rds.tf", "ssl_action               = \"Open\"", "ssl_action               = \"Close\"", "R06"),
    ("RDS 版本不是 16", "variables.tf", "default     = \"16.0\"", "default     = \"14.0\"", "R06"),
    ("RDS 不开日志备份（没有 PITR）", "rds.tf", "enable_backup_log           = true", "enable_backup_log           = false", "R06"),
    ("RDS 备份只留 3 天", "variables.tf", "  default     = 14\n  validation {\n    condition     = var.rds_backup_retention_days >= 7",
     "  default     = 3\n  validation {\n    condition     = var.rds_backup_retention_days >= 7", "R06"),
    ("RDS 不开删除保护", "rds.tf", "  deletion_protection = true\n", "  deletion_protection = false\n", "R06"),
    ("OSS 公共读", "oss.tf", "  acl    = \"private\"", "  acl    = \"public-read\"", "R07"),
    ("OSS 不阻止公共访问", "oss.tf", "block_public_access = true", "block_public_access = false", "R07"),
    ("OSS 不开版本控制", "oss.tf", "    status = \"Enabled\"", "    status = \"Suspended\"", "R07"),
    ("OSS 配静态网站", "oss.tf", "  versioning {", "  website {\n    index_document = \"index.html\"\n  }\n\n  versioning {", "R07"),
    ("资源缺标签", "network.tf", "  description = \"StarBuddy staging（只内网 / VPN 访问）\"\n  tags        = local.tags\n",
     "  description = \"StarBuddy staging（只内网 / VPN 访问）\"\n", "R08"),
    ("OSS 不加密", "oss.tf", "  server_side_encryption_rule {\n    sse_algorithm = \"AES256\"\n  }\n", "", "R09"),
    ("OSS 选 KMS 却不给主密钥", "oss.tf", "    sse_algorithm = \"AES256\"", "    sse_algorithm = \"KMS\"", "R09"),
    ("自建主密钥不轮转", "kms.tf", "locals {",
     "resource \"alicloud_kms_key\" \"x\" {\n  automatic_rotation  = \"Disabled\"\n  deletion_protection = \"Enabled\"\n  tags                = local.tags\n}\n\nlocals {", "R09"),
    ("自建主密钥不开删除保护", "kms.tf", "locals {",
     "resource \"alicloud_kms_key\" \"x\" {\n  automatic_rotation  = \"Enabled\"\n  deletion_protection = \"Disabled\"\n  tags                = local.tags\n}\n\nlocals {", "R09"),
    ("节点盘不加密", "ack.tf", "system_disk_encrypted = true", "system_disk_encrypted = false", "R09"),
    ("Secret 落盘既不加密也不显式声明", "ack.tf", "  disable_encryption = true\n", "", "R09"),
    ("RDS 云盘不加密", "rds.tf", "  encryption_key = var.rds_disk_encryption == \"ServiceKey\" ? \"ServiceKey\" : null\n", "", "R09"),
    # 安全审查建议 S1 / S2：填空值、永远停在 deferred 都要被发现
    ("RDS 加密填 null", "rds.tf", "  encryption_key = var.rds_disk_encryption == \"ServiceKey\" ? \"ServiceKey\" : null\n", "  encryption_key = null\n", "R09"),
    ("RDS 加密填空串", "rds.tf", "  encryption_key = var.rds_disk_encryption == \"ServiceKey\" ? \"ServiceKey\" : null\n", "  encryption_key = \"\"\n", "R09"),
    ("RDS 加密变量给缺省 deferred", "variables.tf", "variable \"rds_disk_encryption\" {\n", "variable \"rds_disk_encryption\" {\n  default     = \"deferred\"\n", "R09"),
    ("集群不要求 RDS 已加密", "ack.tf", "      condition     = var.rds_disk_encryption == \"ServiceKey\"\n", "      condition     = true\n", "R09"),
    ("Secret 落盘密钥填 null", "ack.tf", "  disable_encryption = true\n", "  encryption_provider_key = null\n", "R09"),
    ("OSS 选 KMS 主密钥填空串", "oss.tf", "    sse_algorithm = \"AES256\"", "    sse_algorithm     = \"KMS\"\n    kms_master_key_id = \"\"", "R09"),
    ("日志库不加密", "sls.tf", "    enable       = true\n    encrypt_type = \"default\"\n  }\n}\n\n# LT-05：应用日志",
     "    enable       = false\n    encrypt_type = \"default\"\n  }\n}\n\n# LT-05：应用日志", "R09"),
    ("API Server 开公网", "ack.tf", "slb_internet_enabled = false", "slb_internet_enabled = true", "R10"),
    ("集群另建 NAT", "ack.tf", "new_nat_gateway      = false", "new_nat_gateway      = true", "R10"),
    ("节点分配公网带宽", "ack.tf", "  install_cloud_monitor = true\n", "  install_cloud_monitor = true\n  internet_max_bandwidth_out = 10\n", "R10"),
    ("出网白名单放行任意地址", "egress.tf", "  destination_type      = \"domain\"\n  destination           = var.model_egress_fqdns[count.index]",
     "  destination_type      = \"net\"\n  destination           = \"0.0.0.0/0\"", "R11"),
    ("出网白名单放行 80 端口", "egress.tf", "  dest_port             = \"443/443\"", "  dest_port             = \"80/80\"", "R11"),
    ("没有最后的拒绝规则", "egress.tf", "  acl_action            = \"drop\"", "  acl_action            = \"accept\"", "R11"),
    ("仓库里写了模型供应商主机名", "variables.tf", "  description = \"出网白名单：", "  default = [\"api.vendor.example.invalid\"]\n  description = \"出网白名单：", "R11"),
    ("安全组出公网全开", "network.tf", "  ip_protocol       = \"tcp\"\n  port_range        = \"443/443\"\n  cidr_ip           = \"0.0.0.0/0\"",
     "  ip_protocol       = \"all\"\n  port_range        = \"-1/-1\"\n  cidr_ip           = \"0.0.0.0/0\"", "R11"),
    ("SNAT 不等云防火墙的访问控制", "network.tf",
     "snat_entry_name   = \"${local.name}-pod-${count.index}\"\n  depends_on = [\n    alicloud_eip_association.nat,\n    alicloud_cloud_firewall_nat_firewall_control_policy.model_vendor,\n"
     "    alicloud_cloud_firewall_nat_firewall_control_policy.deny_rest,\n  ]",
     "snat_entry_name   = \"${local.name}-pod-${count.index}\"\n  depends_on = [alicloud_eip_association.nat]", "R11"),
    ("SNAT 不要确认前置条件", "network.tf",
     "（nat_firewall_confirmed，见 egress.tf）\n  depends_on = [\n    alicloud_eip_association.nat,\n    alicloud_cloud_firewall_nat_firewall_control_policy.model_vendor,\n"
     "    alicloud_cloud_firewall_nat_firewall_control_policy.deny_rest,\n  ]\n  lifecycle {\n    precondition {\n      condition     = var.nat_firewall_confirmed",
     "（nat_firewall_confirmed，见 egress.tf）\n  depends_on = [\n    alicloud_eip_association.nat,\n    alicloud_cloud_firewall_nat_firewall_control_policy.model_vendor,\n"
     "    alicloud_cloud_firewall_nat_firewall_control_policy.deny_rest,\n  ]\n  lifecycle {\n    precondition {\n      condition     = true", "R11"),
    ("云防火墙确认变量给了缺省值", "variables.tf", "  description = \"由人确认：", "  default     = true\n  description = \"由人确认：", "R11"),
    ("云防火墙确认变量不是 bool", "variables.tf", "分阶段 apply 的前几阶段传 false\"\n  type        = bool",
     "分阶段 apply 的前几阶段传 false\"\n  type        = string", "R11"),
    ("RDS 的 pg_hba 收不走 TLS 的连接", "rds.tf", "    type        = \"hostssl\"", "    type        = \"host\"", "R06"),
    ("RDS 的 pg_hba 免口令", "rds.tf", "    method      = \"scram-sha-256\"", "    method      = \"trust\"", "R06"),
    ("RDS 没有 pg_hba", "rds.tf", "  pg_hba_conf {\n    type        = \"hostssl\"\n    database    = \"all\"\n    user        = \"all\"\n"
     "    address     = var.vpc_cidr\n    method      = \"scram-sha-256\"\n    priority_id = 1\n  }\n", "", "R06"),
    ("RDS 的 SSL 证书签给外网地址", "rds.tf", "\"${local.rds_connection_prefix}${var.rds_internal_domain_suffix}\"",
     "\"${local.rds_connection_prefix}.pgsql.rds.aliyuncs.com\"", "R06"),
    ("RDS 内网后缀缺省改成外网", "variables.tf", "  default     = \".pg.rds.aliyuncs.com\"", "  default     = \".pgsql.rds.aliyuncs.com\"", "R06"),
    ("RDS 不写 SSL 连接地址", "rds.tf", "  ssl_connection_string    = \"${local.rds_connection_prefix}${var.rds_internal_domain_suffix}\" # 证书只签内网地址\n", "", "R06"),
    ("安全组整段放行阿里云内部网段", "network.tf", "  port_range        = \"80/80\"\n  cidr_ip           = \"100.100.100.200/32\"",
     "  port_range        = \"80/80\"\n  cidr_ip           = \"100.64.0.0/10\"", "R16"),
    ("内网 DNS 放开全部协议端口", "network.tf", "  ip_protocol       = \"udp\"\n  port_range        = \"53/53\"\n  cidr_ip           = \"100.100.2.136/32\"",
     "  ip_protocol       = \"all\"\n  port_range        = \"-1/-1\"\n  cidr_ip           = \"100.100.2.136/32\"", "R16"),
    ("不拒绝阿里云内部网段", "network.tf", "  policy            = \"drop\"\n  priority          = 10\n  description       = \"阿里云内部服务网段其余",
     "  policy            = \"accept\"\n  priority          = 10\n  description       = \"阿里云内部服务网段其余", "R16"),
    ("拒绝内部网段排在 443 之后", "network.tf", "  priority          = 10\n  description       = \"阿里云内部服务网段其余",
     "  priority          = 60\n  description       = \"阿里云内部服务网段其余", "R16"),
    ("没有最后的全拒绝", "network.tf", "  policy            = \"drop\"\n  priority          = 100", "  policy            = \"drop\"\n  priority          = 5", "R16"),
    ("安全组改回企业级", "network.tf", "security_group_type = \"normal\"", "security_group_type = \"enterprise\"", "R16"),
    ("服务端点变量给了缺省值", "variables.tf", "  description = \"阿里云服务内网端点", "  default     = [\"100.64.0.0/10\"]\n  description = \"阿里云服务内网端点", "R16"),
    ("服务端点放开全部端口", "network.tf", "  ip_protocol       = \"tcp\"\n  port_range        = \"443/443\"\n  cidr_ip           = each.value",
     "  ip_protocol       = \"all\"\n  port_range        = \"-1/-1\"\n  cidr_ip           = each.value", "R16"),
    ("弹性公网 IP 绑到节点", "network.tf", "  instance_type = \"Nat\"", "  instance_type = \"EcsInstance\"", "R17"),
    ("建公网负载均衡", "slb.tf", "resource \"alicloud_slb_acl\" \"office_vpn\" {",
     "resource \"alicloud_slb_load_balancer\" \"pub\" {\n  load_balancer_name = \"x\"\n  address_type       = \"internet\"\n  tags               = local.tags\n}\n\n"
     "resource \"alicloud_slb_acl\" \"office_vpn\" {", "R18"),
    ("建公网 ALB", "slb.tf", "resource \"alicloud_slb_acl\" \"office_vpn\" {",
     "resource \"alicloud_alb_load_balancer\" \"pub\" {\n  load_balancer_name = \"x\"\n  address_type       = \"Internet\"\n  tags               = local.tags\n}\n\n"
     "resource \"alicloud_slb_acl\" \"office_vpn\" {", "R18"),
    ("RAM 策略用通配符动作", "kms.tf", "Action   = [\"oos:GetSecretParameter\"]", "Action   = [\"oos:*\"]", "R19"),
    ("RAM 策略凭据用前缀通配", "kms.tf", "secretparameter/${var.name_prefix}-${var.environment}/${s}", "secretparameter/${var.name_prefix}-${var.environment}/*", "R19"),
    ("日志只留 30 天", "sls.tf", "  retention_period      = var.log_retention_days\n  shard_count           = 2",
     "  retention_period      = 30\n  shard_count           = 2", "R12"),
    ("provider 不锁版本", "versions.tf", "      version = \"~> 1.230\"\n", "", "R13"),
    ("backend 写了取值", "versions.tf", "  backend \"oss\" {}", "  backend \"oss\" {\n    bucket = \"x\"\n  }", "R13"),
    ("ACR 命名空间公开", "acr.tf", "default_visibility = \"PRIVATE\"", "default_visibility = \"PUBLIC\"", "R14"),
    ("ACR 仓库公开", "acr.tf", "repo_type   = \"PRIVATE\"", "repo_type   = \"PUBLIC\"", "R14"),
    ("VPC 用公网网段", "variables.tf", "default     = \"10.20.0.0/16\"", "default     = \"47.20.0.0/16\"", "R15"),
    ("Service 网段与 VPC 重叠", "variables.tf", "default     = \"10.96.0.0/16\"", "default     = \"10.20.0.0/16\"", "R15"),
    # LT-05 监控与日志采集
    ("ACK 不装 ARMS 采集组件", "ack.tf", "  addons {\n    name = \"arms-prometheus\"\n  }\n", "", "R20"),
    ("ARMS 采集组件被禁用", "ack.tf", "    name = \"arms-prometheus\"\n", "    name     = \"arms-prometheus\"\n    disabled = true\n", "R20"),
    ("ACK 不装日志采集组件", "ack.tf", "    name   = \"logtail-ds\"\n", "    name   = \"logtail-ds-off\"\n", "R20"),
    ("IaC 里建告警联系人", "sls.tf", "resource \"alicloud_log_store\" \"audit\" {",
     "resource \"alicloud_arms_alert_contact\" \"oncall\" {\n  alert_contact_name = \"x\"\n  email = var.x\n}\n\nresource \"alicloud_log_store\" \"audit\" {", "R20"),
    ("日志库建全文索引", "sls.tf", "  logstore = alicloud_log_store.app.logstore_name\n",
     "  logstore = alicloud_log_store.app.logstore_name\n  full_text {\n    case_sensitive = false\n  }\n", "R21"),
    ("日志索引收作答原文字段", "sls.tf", "    name             = \"route\"", "    name             = \"answer\"", "R21"),
    ("app 日志库没有索引", "sls.tf", "  logstore = alicloud_log_store.app.logstore_name\n", "  logstore = alicloud_log_store.audit.logstore_name\n", "R21"),
    # LT-05 安全审查 A：日志留存加上限
    ("日志留存无限期（3650 天）", "sls.tf", "  retention_period      = var.log_retention_days\n  shard_count           = 2",
     "  retention_period      = 3650\n  shard_count           = 2", "R12"),
    ("留存变量不校验上限", "variables.tf", "var.log_retention_days >= 180 && var.log_retention_days <= 365",
     "var.log_retention_days >= 180", "R12"),
    ("留存变量缺省超过上限", "variables.tf", "  type        = number\n  default     = 180\n  validation {\n    condition     = var.log_retention_days",
     "  type        = number\n  default     = 400\n  validation {\n    condition     = var.log_retention_days", "R12"),
    # 数据库服务端日志不带标识原文（specs/plt-db-log-redaction）
    ("慢查询日志写绑定参数", "rds.tf", "    name  = \"log_parameter_max_length\"\n    value = \"0\"", "    name  = \"log_parameter_max_length\"\n    value = \"-1\"", "R22"),
    ("报错日志写绑定参数", "rds.tf", "    name  = \"log_parameter_max_length_on_error\"\n    value = \"0\"", "    name  = \"log_parameter_max_length_on_error\"\n    value = \"64\"", "R22"),
    ("报错日志写 DETAIL", "rds.tf", "    value = \"terse\"", "    value = \"default\"", "R22"),
    ("记录全部语句", "rds.tf", "    value = \"none\"", "    value = \"all\"", "R22"),
    ("缺 log_error_verbosity", "rds.tf", "  parameters {\n    name  = \"log_error_verbosity\"\n    value = \"terse\"\n  }\n", "", "R22"),
    ("同一参数写两次", "rds.tf", "  parameters {\n    name  = \"log_statement\"", "  parameters {\n    name  = \"log_statement\"\n    value = \"ddl\"\n  }\n  parameters {\n    name  = \"log_statement\"", "R22"),
    ("参数名大写覆盖", "rds.tf", "  deletion_protection = true\n", "  parameters {\n    name  = \"LOG_STATEMENT\"\n    value = \"all\"\n  }\n  deletion_protection = true\n", "R22"),
    ("参数名引用局部值", "rds.tf", "  deletion_protection = true\n", "  parameters {\n    name  = local.name\n    value = \"all\"\n  }\n  deletion_protection = true\n", "R22"),
    ("dynamic 参数块", "rds.tf", "  deletion_protection = true\n", "  dynamic \"parameters\" {\n    for_each = var.extra\n    content {\n      name  = parameters.key\n      value = parameters.value\n    }\n  }\n  deletion_protection = true\n", "R22"),
    ("打开语句耗时日志", "rds.tf", "    value = \"off\"", "    value = \"on\"", "R22"),
    ("打开事务采样日志", "rds.tf", "    name  = \"log_transaction_sample_rate\"\n    value = \"0\"", "    name  = \"log_transaction_sample_rate\"\n    value = \"0.5\"", "R22"),
    ("SQL 洞察用表达式", "rds.tf", "  deletion_protection = true\n", "  deletion_protection = true\n  sql_collector_status     = upper(var.x)\n", "R22"),
    ("建只读实例", "rds.tf", "resource \"alicloud_db_backup_policy\" \"main\" {", "resource \"alicloud_db_readonly_instance\" \"ro\" {\n  master_db_instance_id = alicloud_db_instance.main.id\n  tags = local.tags\n}\n\nresource \"alicloud_db_backup_policy\" \"main\" {", "R22"),
    ("开 SQL 洞察", "rds.tf", "  deletion_protection = true\n", "  deletion_protection = true\n  sql_collector_status     = \"Enabled\"\n", "R22"),
]


def test_log_index_fields_come_from_obs_log_fields():
    """R21 的索引字段白名单是结构化日志字段白名单（learning/obs.py 的 LOG_FIELDS）的子集，同一来源"""
    assert check_iac.LOG_FIELDS_SOURCE.name == "obs.py" and "learning" in check_iac.LOG_FIELDS_SOURCE.parts
    assert check_iac.LOG_INDEX_FIELDS and check_iac.LOG_INDEX_FIELDS <= check_iac.LOG_FIELDS
    assert {"ts", "level", "service", "route", "request_id"} <= check_iac.LOG_INDEX_FIELDS
    assert not {"student", "account", "msg", "detail", "note"} & check_iac.LOG_INDEX_FIELDS, "自由文本与标识类字段不建索引"


def _mutate(tmp: Path, file: str | None, old: str, new: str) -> Path:
    dst = tmp / "alicloud"
    shutil.copytree(IAC, dst)
    if file is None:  # 新增一个文件
        (dst / old).write_text(new, encoding="utf-8")
        return dst
    p = dst / file
    text = p.read_text(encoding="utf-8")
    assert text.count(old) == 1, f"变异原文不唯一或找不到：{file} {old[:40]!r}"
    p.write_text(text.replace(old, new, 1), encoding="utf-8")
    return dst


def test_control_copy_passes(tmp_path):
    dst = tmp_path / "alicloud"
    shutil.copytree(IAC, dst)
    assert run(dst) == []


@pytest.mark.parametrize("name,file,old,new,rule", MUTATIONS, ids=[m[0] for m in MUTATIONS])
def test_each_rule_catches_its_mutation(tmp_path, name, file, old, new, rule):
    found = run(_mutate(tmp_path, file, old, new))
    assert any(f.rule == rule for f in found), f"{name}：没有被 {rule} 发现（{[str(f) for f in found]}）"


def test_parser_basics():
    p = check_iac.Parser('a = "x"\nb = [1, "y", var.z]\nc {\n  d = true\n  e "l" { f = null }\n}\n', "t.tf")
    body = p.body(False)
    assert body.attrs["a"] == "x" and body.attrs["b"][:2] == [1, "y"] and body.attrs["b"][2].raw == "var.z"
    assert body.blocks[0].body.attrs["d"] is True and body.blocks[0].body.blocks[0].labels == ["l"]
    for bad in ('a = "x', "a {", "a = ", "}"):
        with pytest.raises(check_iac.ParseError):
            check_iac.Parser(bad, "t.tf").body(False)


def test_cli_exit_codes(tmp_path):
    assert check_iac.main(["x", str(IAC)]) == 0
    assert check_iac.main(["x", str(tmp_path / "missing")]) == 2
    bad = _mutate(tmp_path, "rds.tf", "ssl_action               = \"Open\"", "ssl_action               = \"Close\"")
    assert check_iac.main(["x", str(bad)]) == 1


# ---------------------------------------------------------------- 与部署清单的一致性


def _var_default(name: str):
    c = check_iac.Checker(IAC)
    c.run()
    return c.variables()[name].body.attrs["default"]


def test_k8s_cluster_ips_inside_service_cidr(k8s_docs):
    svc = ipaddress.ip_network(_var_default("service_cidr"))
    vpc = ipaddress.ip_network(_var_default("vpc_cidr"))
    ips = [d["spec"]["clusterIP"] for d in k8s_docs if d["kind"] == "Service" and "clusterIP" in d["spec"]]
    assert ips
    for ip in ips:
        assert ipaddress.ip_address(ip) in svc and ipaddress.ip_address(ip) not in vpc, ip


def test_k8s_network_policies_match_iac_cidrs(k8s_docs):
    pols = {d["metadata"]["name"]: d for d in k8s_docs if d["kind"] == "NetworkPolicy"}
    rds = pols["rds-egress"]["spec"]["egress"][0]
    assert sorted(t["ipBlock"]["cidr"] for t in rds["to"]) == sorted(_var_default("db_vswitch_cidrs"))
    assert rds["ports"] == [{"protocol": "TCP", "port": 5432}]
    web = pols["web-ingress-from-slb"]["spec"]["ingress"][0]
    assert [f["ipBlock"]["cidr"] for f in web["from"]] == [_var_default("vpc_cidr")] and web["ports"] == [{"protocol": "TCP", "port": 3000}]
    # 默认策略不放行任何 ipBlock 出网（模型供应商出网在解开 pending 时另加，只给学习服务）
    assert "ipBlock" not in repr(pols["default-deny-egress"]["spec"]["egress"])


def test_k8s_learning_egress_template_excludes_internal_ranges():
    """安全审查 B1：学习服务出网策略的写法（注释里的模板）except 必须排除 RFC 1918、100.64.0.0/10（阿里云内部服务）、169.254.0.0/16，
    只放 TCP 443——这些去向不经 NAT，不过云防火墙的主机名白名单"""
    import yaml
    text = (DEPLOY / "k8s" / "staging.yaml").read_text(encoding="utf-8")
    lines = [ln.split("#", 1)[1] for ln in text.splitlines() if ln.lstrip().startswith("#") and ("- to: [{ipBlock: {cidr: 0.0.0.0/0" in ln or "ports: [{protocol: TCP, port: 443}]" in ln)]
    assert len(lines) == 2, "注释里要有学习服务出网策略的完整写法（to 与 ports 两行）"
    indent = min(len(ln) - len(ln.lstrip()) for ln in lines)
    rule = yaml.safe_load("\n".join(ln[indent:] for ln in lines))[0]
    block = rule["to"][0]["ipBlock"]
    assert block["cidr"] == "0.0.0.0/0" and rule["ports"] == [{"protocol": "TCP", "port": 443}]
    excepted = [ipaddress.ip_network(x) for x in block["except"]]
    for must in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "100.64.0.0/10", "169.254.0.0/16"):
        assert any(ipaddress.ip_network(must).subnet_of(e) for e in excepted), must


def test_image_list_consistent_with_build_script_and_acr():
    acr = (IAC / "acr.tf").read_text(encoding="utf-8")
    m = re.search(r"images\s*=\s*\[([^\]]*)\]", acr)
    assert m
    tf_images = re.findall(r'"([a-z-]+)"', m.group(1))
    build = (DEPLOY / "scripts" / "build-images.sh").read_text(encoding="utf-8")
    loop = re.search(r"for n in ([a-z -]+); do\n  img=", build)
    assert loop
    assert sorted(tf_images) == sorted(loop.group(1).split())
    ci = (DEPLOY / "ci" / "images.txt").read_text(encoding="utf-8")
    ci_images = [ln.split()[0] for ln in ci.splitlines() if ln.strip() and not ln.startswith("#")]
    assert sorted(ci_images) == sorted(tf_images)


def test_secret_reader_list_matches_k8s_secrets():
    """ack-secret-manager 能读的 OOS 加密参数逐个列名（R19 不收通配符）：名单与 k8s 引用的 Secret 名一致；没有任何 KMS 动作（决定记录第 15 条）"""
    kms = (IAC / "kms.tf").read_text(encoding="utf-8")
    m = re.search(r"app_secrets\s*=\s*\[([^\]]*)\]", kms)
    assert m
    listed = set(re.findall(r'"([a-z0-9-]+)"', m.group(1)))
    k8s = (DEPLOY / "k8s" / "staging.yaml").read_text(encoding="utf-8")
    used = set(re.findall(r"secretKeyRef: \{name: ([a-z0-9-]+)", k8s)) | set(re.findall(r"secretName: ([a-z0-9-]+)", k8s))
    assert listed == used
    assert "secretparameter/${var.name_prefix}-${var.environment}/${s}" in kms and '"kms:' not in kms
    # 安全审查建议 S3：动作集合恰好一个、只有一条 Statement；信任条件锁定为本集群 OIDC 与 kube-system:ack-secret-manager
    policy = kms[kms.index('resource "alicloud_ram_policy" "secret_reader"'):kms.index('resource "alicloud_ram_role" "secret_reader"')]
    assert re.findall(r"Action\s*=\s*\[([^\]]*)\]", policy) == ['"oos:GetSecretParameter"'] and policy.count("Effect") == 1
    trust = kms[kms.index('resource "alicloud_ram_role" "secret_reader"'):kms.index('resource "alicloud_ram_role_policy_attachment"')]
    assert re.search(r'"oidc:sub"\s*=\s*"system:serviceaccount:kube-system:ack-secret-manager"', trust)
    assert re.search(r'"oidc:aud"\s*=\s*"sts\.aliyuncs\.com"', trust) and "rrsa_oidc_issuer_url" in trust
    assert "Federated = [alicloud_cs_managed_kubernetes.main.rrsa_metadata[0].ram_oidc_provider_arn]" in trust
    assert "ExternalSecret" in kms and "RBAC" in kms, "注明只有部署者能建同步规则"


def test_region_and_no_real_values_in_examples():
    ex = (IAC / "terraform.tfvars.example").read_text(encoding="utf-8")
    assert "model_egress_fqdns = []" in ex and "<由人填写" in ex
    assert not re.search(r"\b\d{16}\b|LTAI", ex)
    gi = (IAC / ".gitignore").read_text(encoding="utf-8")
    for pat in ("*.tfstate", "*.tfvars", ".terraform/", "*.tfplan"):
        assert pat in gi


def test_readme_documents_licenses_and_human_steps():
    text = (IAC / "README.md").read_text(encoding="utf-8")
    for must in ("BSL 1.1", "OpenTofu", "MPL-2.0", "tflint", "checkov", "Apache-2.0", "tfsec", "MIT",
                 "terraform init -backend=false", "terraform validate", "由人", "plan", "apply", "make iac-validate"):
        assert must in text, must


def test_iac_validate_script_skips_without_tools_and_strict_fails():
    text = (DEPLOY / "scripts" / "iac-validate.sh").read_text(encoding="utf-8")
    assert "exit 3" in text and "command -v" in text and "-backend=false" in text and "IAC_PLUGIN_DIR" in text
    assert "export CHECKPOINT_DISABLE=1" in text and "--skip-download" in text, "不连上游做版本检查"
    assert "plan" not in re.sub(r"#.*", "", text).replace("plugin", ""), "只 validate 与静态检查，不执行 plan / apply"
    assert "apply" not in re.sub(r"#.*", "", text)
    make = (ROOT / "Makefile").read_text(encoding="utf-8")
    block = make.split("iac-validate:", 1)[1].split("\n\n", 1)[0]
    assert 'CONTRACT_STRICT)" != "1"' in block and "rc -eq 3" in block
