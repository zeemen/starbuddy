"""Terraform（OpenTofu）专用 RAM 子账号的策略草案（deploy/runbooks/ram，staging-first-deploy.md 第 0.1 节）的静态核对（安全审查 B1–B3）：
- 写类动作逐个列出；通配只用于登记过的查询类前缀（PRODUCT_READ_WILDCARDS），日志、镜像仓库等会读到正文或凭据的产品只能逐个列查询动作；
- 子账号没有任何 RAM 写动作（策略与角色由人用管理员身份 apply，防止经新策略 / 新角色提权）；sts 只有 GetCallerIdentity；
- 会读到数据或凭据的动作、删除 / 释放、提权、公开暴露类动作都在拒绝策略里；允许与拒绝不重叠（含通配展开）；
- 状态桶、锁表、日志、业务桶、KMS Decrypt 只限占位资源，资源里不出现整段通配；业务桶没有对象级动作；
- 单条策略不超过阿里云自定义策略的长度上限；只有占位、没有真实账号 ID。动作名以 RAM 控制台校验为准（由人核实）。"""
from __future__ import annotations

import fnmatch
import json
import re

import pytest

from conftest import DEPLOY

RAM = DEPLOY / "runbooks" / "ram"
ALLOW = ["terraform-allow-infra.json", "terraform-allow-data.json"]
DENY = "terraform-deny.json"
MAX_POLICY_CHARS = 6144  # 阿里云单条自定义策略文档的长度上限
# 只有这些产品允许查询类通配；其余产品（log、cr、oss 对象、sts…）逐个列动作
PRODUCT_READ_WILDCARDS = {
    "vpc": {"Describe*", "List*", "Get*"}, "ecs": {"Describe*", "List*"}, "cs": {"Describe*", "Get*", "List*"},
    "slb": {"Describe*", "List*"}, "rds": {"Describe*"}, "kms": {"Describe*", "List*"}, "yundun-cloudfirewall": {"Describe*"},
    "ram": {"Get*", "List*"},
}
# 业务桶上允许的配置读取通配（只读桶级配置，不含对象）
BUCKET_CONFIG_WILDCARD = "oss:GetBucket*"
SENSITIVE_READS = {"kms:GetSecretValue", "log:GetLogs", "log:GetLogStoreLogs", "log:GetCursorOrData", "log:GetHistograms",
                   "log:GetContextLogs", "log:PullLogs", "cr:GetAuthorizationToken", "cs:DescribeClusterUserKubeconfig",
                   "ecs:DescribeUserData", "rds:DescribeSQLLogRecords", "rds:DescribeSlowLogRecords", "rds:DescribeErrorLogs",
                   "rds:DescribeBackups", "cs:DescribeClusterAttachScripts", "cs:DescribeClusterCerts",
                   "cs:DescribeSubaccountK8sClusterUserConfig", "cs:DescribeExternalAgent", "ecs:DescribeInvocationResults",
                   "vpc:DescribeSslVpnClientCert", "vpc:DescribeVpnConnection", "rds:DescribeBinlogFiles", "rds:DescribeLogBackupFiles",
                   "rds:DescribeSQLLogFiles", "rds:DescribeCrossRegionBackups", "rds:DescribeCrossRegionLogBackupFiles"}
ESCALATION = {"ram:CreateUser", "ram:UpdateUser", "ram:CreateAccessKey", "ram:UpdateAccessKey", "ram:CreateLoginProfile",
              "ram:AttachPolicyToUser", "ram:AttachPolicyToGroup", "ram:AddUserToGroup", "ram:CreatePolicy", "ram:CreatePolicyVersion",
              "ram:SetDefaultPolicyVersion", "ram:CreateRole", "ram:UpdateRole", "ram:AttachPolicyToRole", "ram:CreateServiceLinkedRole"}
EXPOSURE = {"rds:AllocateInstancePublicConnection", "oss:PutBucketPolicy", "oss:PutBucketCors", "oss:PutBucketWebsite",
            "oss:DeleteBucketPublicAccessBlock", "oss:PutPublicAccessBlock", "oss:DeletePublicAccessBlock", "ecs:AllocatePublicIpAddress",
            "rds:ModifySQLCollectorPolicy"}
DELETES = {"vpc:DeleteVpc", "vpc:DeleteVSwitch", "vpc:DeleteNatGateway", "vpc:DeleteSnatEntry", "vpc:ReleaseEipAddress",
           "vpc:DeleteVpcGatewayEndpoint", "ecs:DeleteSecurityGroup", "ecs:RevokeSecurityGroup", "ecs:RevokeSecurityGroupEgress",
           "cs:DeleteCluster", "cs:DeleteClusterNodepool", "cs:RemoveNodePoolNodes", "slb:DeleteAccessControlList",
           "rds:DeleteDBInstance", "oss:DeleteBucket", "oss:DeleteObject", "ots:DeleteTable", "ots:DeleteInstance",
           "kms:ScheduleKeyDeletion", "kms:DisableKey", "log:DeleteProject", "log:DeleteLogStore", "cr:DeleteNamespace",
           "cr:DeleteRepository", "yundun-cloudfirewall:DeleteNatFirewallControlPolicy", "ram:DeleteRole", "ram:DeletePolicy"}
# 只能限定到占位资源的动作前缀（资源里不得有整段通配）
SCOPED = ("oss:", "ots:", "log:Create", "log:Update", "log:Tag", "log:Untag", "kms:Decrypt", "kms:GenerateDataKey", "ram:PassRole")
PLACEHOLDER_OK = {"acs:oss:*:*:<状态桶名>/<状态前缀>/*", "acs:ram::*:role/aliyuncs*"}


def load(name: str) -> dict:
    return json.loads((RAM / name).read_text(encoding="utf-8"))


def as_list(v) -> list[str]:
    return [v] if isinstance(v, str) else list(v)


def statements(name: str) -> list[dict]:
    doc = load(name)
    assert doc["Version"] == "1"
    return doc["Statement"]


def actions(name: str, effect: str) -> set[str]:
    out: set[str] = set()
    for s in statements(name):
        if s["Effect"] == effect:
            out |= set(as_list(s["Action"]))
    return out


ALLOWED = set().union(*(actions(n, "Allow") for n in ALLOW))
DENIED = actions(DENY, "Deny")


def allow_matches(action: str) -> bool:
    return any(fnmatch.fnmatchcase(action, a) for a in ALLOWED)


@pytest.mark.parametrize("name", ALLOW + [DENY])
def test_policy_shape_and_size(name):
    doc = load(name)
    assert set(doc) == {"Version", "Statement"}
    for s in doc["Statement"]:
        assert isinstance(s["Action"], list) and isinstance(s["Resource"], (list, str)), "Action 一律写列表"
        assert s["Effect"] in ("Allow", "Deny") and s["Action"] and s["Resource"]
        assert not any(a == "*" or a.endswith(":*") for a in s["Action"]), "不用整个产品的通配动作"
    compact = json.dumps(doc, ensure_ascii=False, separators=(",", ":"))
    assert len(compact) <= MAX_POLICY_CHARS, f"{name} 超过单条策略长度上限"
    raw = (RAM / name).read_text(encoding="utf-8")
    assert not re.search(r"\b\d{16}\b|LTAI", raw), "只写占位，不写真实账号 ID 或凭据"


def test_allow_wildcards_only_registered_read_prefixes():
    for name in ALLOW:
        for s in statements(name):
            assert s["Effect"] == "Allow", f"{name} 只放允许语句（拒绝统一在 {DENY}）"
            for a in as_list(s["Action"]):
                if "*" not in a:
                    continue
                product, verb = a.split(":", 1)
                if a == BUCKET_CONFIG_WILDCARD:
                    assert as_list(s["Resource"]) == ["acs:oss:*:*:<业务桶名>"], "桶配置读取只限业务桶"
                    continue
                assert verb in PRODUCT_READ_WILDCARDS.get(product, set()), f"{name}：{a} 不是登记过的查询类通配"


def test_no_ram_writes_and_sts_only_identity():
    ram_allowed = {a for a in ALLOWED if a.startswith("ram:")}
    assert ram_allowed <= {"ram:Get*", "ram:List*", "ram:PassRole"}, "子账号不授 RAM 写动作（策略与角色由人用管理员身份 apply）"
    assert {a for a in ALLOWED if a.startswith("sts:")} == {"sts:GetCallerIdentity"}


def test_scoped_actions_never_on_wildcard_resources():
    for name in ALLOW:
        for s in statements(name):
            res = as_list(s["Resource"])
            for a in as_list(s["Action"]):
                if a.startswith(SCOPED):
                    for r in res:
                        assert r != "*" and (r in PLACEHOLDER_OK or not re.search(r"(^|[:/])\*$", r)), f"{name}：{a} 的资源 {r} 过宽"


def test_business_bucket_has_no_object_actions_and_decrypt_only_state_key():
    for name in ALLOW:
        for s in statements(name):
            res, acts = as_list(s["Resource"]), as_list(s["Action"])
            if any(r.startswith("acs:oss:*:*:<业务桶名>") for r in res):
                assert all("/" not in r for r in res) and not ({"oss:GetObject", "oss:PutObject", "oss:ListObjects"} & set(acts))
            if "kms:Decrypt" in acts or "kms:GenerateDataKey" in acts:
                assert res == ["acs:kms:cn-beijing:*:key/<状态桶 KMS 密钥 ID>"]
            if "ram:PassRole" in acts:
                assert all(r.startswith("acs:ram::*:role/aliyun") for r in res), "PassRole 只给云服务的服务角色"


@pytest.mark.parametrize("group,required", [("读到数据或凭据", SENSITIVE_READS), ("提权", ESCALATION), ("公开暴露", EXPOSURE), ("删除 / 释放", DELETES)])
def test_deny_covers(group, required):
    assert required <= DENIED, f"{group}：{sorted(required - DENIED)}"


def test_allow_and_deny_do_not_overlap_even_through_wildcards():
    assert not (ALLOWED & DENIED), sorted(ALLOWED & DENIED)
    # 拒绝里的动作能被允许通配覆盖的，只能是查询类里的敏感读取（拒绝优先）；写类动作不得被任何允许通配覆盖
    for d in DENIED:
        if allow_matches(d):
            assert d in SENSITIVE_READS, f"{d} 被允许通配覆盖"


def test_state_backend_resources_are_scoped_to_placeholders():
    data = statements("terraform-allow-data.json")
    obj = [s for s in data if "oss:PutObject" in s["Action"]]
    assert obj and obj[0]["Resource"] == ["acs:oss:*:*:<状态桶名>/<状态前缀>/*"]
    lock = [s for s in data if "ots:PutRow" in s["Action"]]
    assert lock and lock[0]["Resource"] == ["acs:ots:cn-beijing:*:instance/<OTS 实例名>/table/<锁表名>"]


def test_runbook_points_to_policy_files_and_human_applies_ram():
    t = (DEPLOY / "runbooks" / "staging-first-deploy.md").read_text(encoding="utf-8")
    for name in ALLOW + [DENY]:
        assert f"ram/{name}" in t
    assert "阶段 R" in t and "管理员身份" in t, "RAM 资源由人用管理员身份单独 apply"
    gate = t.split("### 0.6")[1].split("\n## ")[0]
    assert "账号级阻止公共访问" in gate, "B3 的前提（OSS 账号级阻止公共访问）列为开工阻断项"
    stage_b = t.split("**阶段 B：")[1].split("**阶段 C：")[0]
    assert "账号级阻止公共访问" in stage_b and "bssapi:CreateInstance" in stage_b, "阶段 B 验收核对账号级阻止并撤掉下单权限"
    stage_a = t.split("**阶段 A：")[1].split("**阶段 B：")[0]
    stage_c = t.split("**阶段 C：")[1].split("**阶段 R：")[0]
    assert "alicloud_ram_" not in stage_a and "alicloud_ram_" not in stage_c, "Claude 执行的阶段不含 RAM 资源"
