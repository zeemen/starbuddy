#!/usr/bin/env python3
"""IaC 静态规则检查（LT-03 范围第 1 条，specs/launch-lt03-cloud-staging）：不依赖 terraform / tflint / checkov / tfsec，只用标准库。

本机没有这些工具、也不联网下载（不重装依赖原则），所以用一个够用的 HCL 小解析器（块、属性、字符串、数字、布尔、列表、引用；
插值与函数调用按原文保留）读 deploy/iac/<厂商>/*.tf，逐条核对本仓库的口径。工具在的环境另跑 make iac-validate（deploy/scripts/iac-validate.sh）。

规则：
  R01 目录里没有状态文件、计划文件、真实变量文件（*.tfvars，示例除外）、.terraform/、override.tf
  R02 没有硬编码凭据：不出现口令 / 密钥类属性（password、access_key、secret_key、secret_data…）；provider 只写地域；
      全文没有 AccessKey、私钥、API Key 样式的字面量
  R03 没有真实账号 ID：全文没有 16 位数字、带数字账号的 ARN（账号 ID 只经数据源 alicloud_account 在 plan 时取得）
  R04 地域只能是 cn-beijing：provider 的 region 只引用 var.region，var.region 缺省 cn-beijing 且有校验；任何地域 / 可用区字面量都在华北 2
  R05 没有 0.0.0.0/0 入方向：安全组入方向规则、RDS 白名单、负载均衡访问控制条目都只收内网或变量；没有 DNAT（alicloud_forward_entry）
  R06 RDS 不开公网：没有外网连接资源（alicloud_db_connection）；PostgreSQL 16、白名单只收内网、KMS 云盘加密、删除保护；
      有备份策略（每日备份、日志备份即 PITR，两者保留 ≥ 7 天）；
      真正强制 TLS（LT-03 决定记录（续）第 6 条）：ssl_action = Open，SSL 证书签给内网连接地址（connection_string_prefix 与
      ssl_connection_string 同一个前缀 + 内网域名后缀变量，后缀缺省不是外网的），pg_hba 至少一条且全部是 hostssl（来源内网、
      口令认证 scram-sha-256 / md5），没有 host / hostnossl / trust
  R07 OSS 不公开：私有 ACL、阻止公共访问、KMS 服务端加密、版本控制、生命周期；没有公共读写、桶策略、静态网站、CORS
  R08 支持标签的资源都带 tags（不支持标签的类型在 NO_TAGS 里逐个登记）
  R09 KMS 加密开启：主密钥自动轮转与删除保护；节点系统盘 KMS 加密；ACK Secret 落盘加密；日志库服务端加密
  R10 ACK 不开公网：API Server 不开公网负载均衡、不让集群另建 NAT、节点不分配公网带宽
  R11 出网白名单：有 NAT 网关；云防火墙 NAT 边界出方向只放行 var.model_egress_fqdns 的 TCP 443（主机名类型），最后一条拒绝其余全部；
      模型供应商主机名变量没有缺省值（由人填写）；安全组出公网只开 TCP 443；
      云防火墙没开不能静默放开出网：确认变量 nat_firewall_confirmed 没有缺省值且校验必须为 true，每条 SNAT 都 depends_on
      两类访问控制、并以 var.nat_firewall_confirmed 为前置条件
  R12 日志留存 180–365 天（下限：《网络安全法》第二十一条；上限：最小必要，LT-05 安全审查）；var.log_retention_days 的校验写明上下限
  R13 版本锁定：terraform 有 required_version；alicloud provider 来源 aliyun/alicloud 且有版本约束；backend 只写部分配置（不含取值）
  R14 ACR 私有：命名空间缺省私有、不自动建仓；仓库私有；没有公网访问控制入口
  R15 网段：VPC、交换机、Service 网段的缺省值都是 RFC 1918，Service 网段不与 VPC 重叠
  R16 安全组出方向白名单（安全审查 B1）：普通安全组；放行规则只能是下面几种——VPC 网段（var.vpc_cidr）、内网 DNS
      100.100.2.136/32 与 100.100.2.138/32 的 53（TCP / UDP）、元数据 100.100.100.200/32 的 TCP 80、
      var.cloud_service_endpoint_cidrs（for_each，变量没有缺省值且校验限在 100.x）的 TCP 443、0.0.0.0/0 的 TCP 443；
      必须有拒绝规则：100.64.0.0/10 与 169.254.0.0/16 在 0.0.0.0/0:443 之前拒绝，0.0.0.0/0 全协议拒绝排在最后
  R17 弹性公网 IP 只能绑 NAT 网关（instance_type = Nat，instance_id 引用 alicloud_nat_gateway）
  R18 不建公网负载均衡：SLB 必须 address_type = intranet，ALB / NLB 必须 address_type = Intranet
  R19 RAM 策略与角色信任策略里不出现通配符 *（动作与资源逐个列出）
  R20 监控与日志采集（LT-05）：ACK 装了 arms-prometheus 与 logtail-ds 两个组件且没有禁用；不在 IaC 里建告警联系人、联系人组、
      通知策略、机器人（接收人与通知渠道由人在控制台配置，联系方式不进仓库与状态文件）
  R21 日志索引（LT-05）：日志库索引不建全文索引（没有 full_text 块），字段索引只收白名单字段（LOG_INDEX_FIELDS）；app 日志库必须有索引
用法：python3 deploy/iac/check_iac.py [目录]   退出码 0 通过；1 有违规；2 目录读不了或解析失败。
"""
from __future__ import annotations

import ipaddress
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

# ---------------------------------------------------------------- 词法与解析（只覆盖本仓库 IaC 草案用到的 HCL 写法）


@dataclass
class Tok:
    kind: str   # IDENT NUM STR NL PUNCT
    text: str
    start: int
    end: int


@dataclass(frozen=True)
class Expr:
    """没有展开的表达式（引用、插值、函数调用、对象）：保留原文"""
    raw: str


@dataclass
class Body:
    attrs: dict[str, Any] = field(default_factory=dict)
    blocks: list["Block"] = field(default_factory=list)


@dataclass
class Block:
    type: str
    labels: list[str]
    body: Body
    file: str = ""


class ParseError(ValueError):
    pass


_PUNCT2 = ("==", "!=", "<=", ">=", "&&", "||", "=>", "...")
_PUNCT1 = set("{}[]()=,.:?!<>+-*/%")


def tokenize(src: str) -> list[Tok]:
    out: list[Tok] = []
    i, n = 0, len(src)
    while i < n:
        c = src[i]
        if c == "\n":
            out.append(Tok("NL", "\n", i, i + 1))
            i += 1
        elif c in " \t\r":
            i += 1
        elif c == "#" or src.startswith("//", i):
            j = src.find("\n", i)
            i = n if j < 0 else j
        elif src.startswith("/*", i):
            j = src.find("*/", i + 2)
            if j < 0:
                raise ParseError("块注释没有结束")
            i = j + 2
        elif c == '"':
            j, depth = i + 1, 0
            while j < n:
                ch = src[j]
                if ch == "\\":
                    j += 2
                    continue
                if src.startswith("${", j) or src.startswith("%{", j):
                    depth += 1
                    j += 2
                    continue
                if ch == "}" and depth:
                    depth -= 1
                elif ch == '"' and not depth:
                    break
                elif ch == "\n" and not depth:
                    raise ParseError("字符串没有结束")
                j += 1
            if j >= n:
                raise ParseError("字符串没有结束")
            out.append(Tok("STR", src[i + 1:j], i, j + 1))
            i = j + 1
        elif src.startswith("<<", i) and re.match(r"<<-?[A-Za-z_]+\n", src[i:]):
            m = re.match(r"<<-?([A-Za-z_]+)\n", src[i:])
            assert m
            tag, body_start = m.group(1), i + m.end()
            e = re.compile(rf"^\s*{tag}\s*$", re.M).search(src, body_start)
            if not e:
                raise ParseError("heredoc 没有结束")
            out.append(Tok("STR", src[body_start:e.start()], i, e.end()))
            i = e.end()
        elif c.isdigit():
            m = re.match(r"\d+(\.\d+)?", src[i:])
            assert m
            out.append(Tok("NUM", m.group(0), i, i + m.end()))
            i += m.end()
        elif c.isalpha() or c == "_":
            m = re.match(r"[A-Za-z_][A-Za-z0-9_-]*", src[i:])
            assert m
            out.append(Tok("IDENT", m.group(0), i, i + m.end()))
            i += m.end()
        else:
            two = next((p for p in _PUNCT2 if src.startswith(p, i)), None)
            if two:
                out.append(Tok("PUNCT", two, i, i + len(two)))
                i += len(two)
            elif c in _PUNCT1:
                out.append(Tok("PUNCT", c, i, i + 1))
                i += 1
            else:
                raise ParseError(f"不认识的字符 {c!r}")
    return out


def _unescape(s: str) -> str:
    return re.sub(r"\\(.)", lambda m: {"n": "\n", "t": "\t"}.get(m.group(1), m.group(1)), s)


class Parser:
    def __init__(self, src: str, file: str) -> None:
        self.src, self.file = src, file
        self.toks = tokenize(src)
        self.i = 0

    def peek(self, k: int = 0) -> Tok | None:
        j = self.i + k
        return self.toks[j] if j < len(self.toks) else None

    def skip_nl(self) -> None:
        while (t := self.peek()) is not None and t.kind == "NL":
            self.i += 1

    def body(self, closing: bool) -> Body:
        b = Body()
        while True:
            self.skip_nl()
            t = self.peek()
            if t is None:
                if closing:
                    raise ParseError("块没有结束")
                return b
            if t.kind == "PUNCT" and t.text == "}":
                if not closing:
                    raise ParseError("多余的 }")
                self.i += 1
                return b
            if t.kind != "IDENT":
                raise ParseError(f"这里应是属性名或块类型：{t.text!r}")
            self.i += 1
            nxt = self.peek()
            if nxt is not None and nxt.kind == "PUNCT" and nxt.text == "=":
                self.i += 1
                if t.text in b.attrs:
                    raise ParseError(f"属性重复：{t.text}")
                b.attrs[t.text] = self.expr()
                continue
            labels = []
            while (x := self.peek()) is not None and x.kind in ("STR", "IDENT"):
                labels.append(x.text)
                self.i += 1
            x = self.peek()
            if x is None or x.text != "{":
                raise ParseError(f"块 {t.text} 后面应是 {{")
            self.i += 1
            b.blocks.append(Block(t.text, labels, self.body(True), self.file))

    def expr(self) -> Any:
        start = self.i
        depth = 0
        while (t := self.peek()) is not None:
            if t.kind == "PUNCT" and t.text in "([{":
                depth += 1
            elif t.kind == "PUNCT" and t.text in ")]}":
                if depth == 0:
                    break
                depth -= 1
            elif t.kind == "NL" and depth == 0:
                break
            self.i += 1
        toks = [x for x in self.toks[start:self.i] if x.kind != "NL"]
        if not toks:
            raise ParseError("属性没有取值")
        return self.convert(toks)

    def convert(self, toks: list[Tok]) -> Any:
        if len(toks) == 1:
            t = toks[0]
            if t.kind == "STR":
                return _unescape(t.text)
            if t.kind == "NUM":
                return float(t.text) if "." in t.text else int(t.text)
            if t.kind == "IDENT" and t.text in ("true", "false"):
                return t.text == "true"
            if t.kind == "IDENT" and t.text == "null":
                return None
        if toks[0].text == "[" and toks[-1].text == "]" and self._matched(toks):
            items, cur, depth = [], [], 0
            for t in toks[1:-1]:
                if t.text in ("(", "[", "{"):
                    depth += 1
                elif t.text in (")", "]", "}"):
                    depth -= 1
                if t.text == "," and depth == 0:
                    if cur:
                        items.append(self.convert(cur))
                    cur = []
                else:
                    cur.append(t)
            if cur:
                items.append(self.convert(cur))
            return items
        return Expr(self.src[toks[0].start:toks[-1].end])

    @staticmethod
    def _matched(toks: list[Tok]) -> bool:
        depth = 0
        for k, t in enumerate(toks):
            if t.text in ("(", "[", "{"):
                depth += 1
            elif t.text in (")", "]", "}"):
                depth -= 1
                if depth == 0 and k != len(toks) - 1:
                    return False
        return depth == 0


def parse_file(path: Path) -> list[Block]:
    p = Parser(path.read_text(encoding="utf-8"), path.name)
    return p.body(False).blocks


# ---------------------------------------------------------------- 规则

OPEN = {"0.0.0.0/0", "::/0", "0.0.0.0"}
CRED_ATTRS = {"access_key", "secret_key", "security_token", "password", "account_password", "kms_encrypted_password",
              "secret_data", "private_key", "shared_credentials_file", "credentials_uri", "sts_endpoint", "ecs_role_name"}
FORBIDDEN_TYPES = {
    "alicloud_forward_entry": "R05 不建 DNAT（公网进不来）",
    "alicloud_db_connection": "R06 RDS 不建外网连接地址",
    "alicloud_oss_bucket_policy": "R07 OSS 不建桶策略（访问只经 RAM 授权）",
    "alicloud_cr_endpoint_acl_policy": "R14 ACR 不开公网访问控制入口",
    "alicloud_db_account": "R02 数据库账号不在 Terraform 里建（口令会进状态文件）",
    "alicloud_rds_account": "R02 数据库账号不在 Terraform 里建（口令会进状态文件）",
    "alicloud_kms_secret": "R02 凭据取值不进 Terraform（由人在凭据管家创建）",
    "alicloud_arms_alert_contact": "R20 告警联系人由人在控制台配置（联系方式不进仓库与状态文件）",
    "alicloud_arms_alert_contact_group": "R20 告警联系人组由人在控制台配置",
    "alicloud_arms_dispatch_rule": "R20 告警通知策略由人在控制台配置",
    "alicloud_arms_alert_robot": "R20 告警机器人（回调地址）由人在控制台配置",
}
# R20：ACK 必须装的采集组件（LT-05）
REQUIRED_ADDONS = ("arms-prometheus", "logtail-ds")
# R21：日志库字段索引只收这些字段，且必须是结构化日志字段白名单 LOG_FIELDS 的子集（安全审查 B：同一来源——LOG_FIELDS 从学习服务
# learning/obs.py 的源码里读，三份 Python obs.py 与 BFF log.ts 另由 deploy/tests/test_obs_shared.py 核对逐项相同；内容、标识类字段不可检索）
LOG_FIELDS_SOURCE = Path(__file__).resolve().parents[2] / "services" / "learning" / "learning" / "obs.py"


def _log_fields(path: Path) -> frozenset[str]:
    """从 obs.py 源码取 ENVELOPE 与 LOG_FIELDS（不导入服务的包：只用 ast 读常量）"""
    import ast
    env: tuple[str, ...] = ()
    extra: tuple[str, ...] = ()
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            if node.targets[0].id == "ENVELOPE":
                env = tuple(ast.literal_eval(node.value))
            elif node.targets[0].id == "LOG_FIELDS" and isinstance(node.value, ast.Call) and node.value.args:
                arg = node.value.args[0]
                if isinstance(arg, ast.BinOp) and isinstance(arg.right, ast.Tuple):
                    extra = tuple(ast.literal_eval(arg.right))
    if not env or not extra:
        raise RuntimeError(f"读不到日志字段白名单：{path}")
    return frozenset(env + extra)


LOG_FIELDS = _log_fields(LOG_FIELDS_SOURCE)
LOG_INDEX_FIELDS = frozenset({"service", "level", "logger", "route", "event", "request_id", "status", "duration_ms", "method", "code",
                              "outcome", "job", "ts"}) & LOG_FIELDS
# R12：日志留存的上下限（天）：至少 180 天（《网络安全法》第二十一条），至多 365 天（最小必要，日志里只有元数据也不无限期保留）
LOG_RETENTION_MIN, LOG_RETENTION_MAX = 180, 365
# 不支持 tags 参数的资源类型（R08）：逐个登记，新增资源类型不在这里就必须带 tags
NO_TAGS = {"alicloud_security_group_rule", "alicloud_snat_entry", "alicloud_eip_association", "alicloud_oss_bucket_acl",
           "alicloud_oss_bucket_public_access_block", "alicloud_rds_backup_policy", "alicloud_cr_ee_instance",
           "alicloud_cr_ee_namespace", "alicloud_cr_ee_repo", "alicloud_cr_ee_vpc_endpoint_linked_vpc", "alicloud_log_store",
           "alicloud_kms_alias", "alicloud_ram_policy", "alicloud_ram_role_policy_attachment", "alicloud_slb_acl_entry_attachment",
           "alicloud_cloud_firewall_nat_firewall_control_policy", "alicloud_vpc_gateway_endpoint_route_table_attachment",
           "alicloud_log_store_index"}
# R16：安全组出方向放行规则的白名单（cidr → 允许的 (协议, 端口) 组合；None = 任意）
EGRESS_FIXED = {"100.100.2.136/32": {("udp", "53/53"), ("tcp", "53/53")}, "100.100.2.138/32": {("udp", "53/53"), ("tcp", "53/53")},
                "100.100.100.200/32": {("tcp", "80/80")}, "0.0.0.0/0": {("tcp", "443/443")}}
PG_HBA_METHODS = {"scram-sha-256", "md5"}
SECRET_TEXT = [
    (re.compile(r"LTAI[0-9A-Za-z]{12,}"), "阿里云 AccessKey ID 样式"),
    (re.compile(r"AKIA[0-9A-Z]{16}"), "AccessKey 样式"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), "私钥"),
    (re.compile(r"\bsk-[A-Za-z0-9]{20,}"), "API Key 样式"),
]
ACCOUNT_TEXT = [
    (re.compile(r"(?<![\d.])\d{16}(?![\d.])"), "16 位数字（疑似阿里云账号 ID）"),
    (re.compile(r"acs:[a-z-]*:[a-z0-9-]*:\d{6,}:"), "写死账号 ID 的 ARN"),
]
REGION_RE = re.compile(r"^(cn|ap|us|eu|me|rus|na)-[a-z]+(-\d+)?(-[a-z])?$")


@dataclass
class Finding:
    rule: str
    file: str
    message: str

    def __str__(self) -> str:
        return f"{self.rule} {self.file}：{self.message}"


def walk(value: Any) -> Iterator[Any]:
    if isinstance(value, list):
        for v in value:
            yield from walk(v)
    elif isinstance(value, Body):
        for v in value.attrs.values():
            yield from walk(v)
        for b in value.blocks:
            yield from walk(b.body)
    else:
        yield value


def attrs_named(body: Body, names: set[str]) -> Iterator[str]:
    for k in body.attrs:
        if k in names:
            yield k
    for b in body.blocks:
        yield from attrs_named(b.body, names)


def sub(body: Body, typ: str) -> list[Block]:
    return [b for b in body.blocks if b.type == typ]


def is_private_cidr(s: Any) -> bool:
    if not isinstance(s, str) or s in OPEN:
        return False
    try:
        net = ipaddress.ip_network(s, strict=False)
    except ValueError:
        return False
    return net.version == 4 and any(net.subnet_of(ipaddress.ip_network(p)) for p in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"))


class Checker:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.findings: list[Finding] = []
        self.blocks: list[Block] = []
        self.texts: dict[str, str] = {}

    def add(self, rule: str, file: str, msg: str) -> None:
        self.findings.append(Finding(rule, file, msg))

    # ------------------------------------------------ 取值
    def variables(self) -> dict[str, Block]:
        return {b.labels[0]: b for b in self.blocks if b.type == "variable" and b.labels}

    def resources(self, typ: str | None = None) -> list[Block]:
        return [b for b in self.blocks if b.type == "resource" and (typ is None or b.labels[0] == typ)]

    def resolve(self, v: Any) -> Any:
        """var.X 解析为变量缺省值（没有缺省值返回 Expr）；其他原样返回"""
        if isinstance(v, Expr):
            m = re.fullmatch(r"var\.([A-Za-z_][A-Za-z0-9_]*)", v.raw.strip())
            if m and m.group(1) in self.variables():
                d = self.variables()[m.group(1)].body.attrs.get("default", Expr("<无缺省>"))
                return d
        return v

    # ------------------------------------------------ 入口
    def run(self) -> list[Finding]:
        self.r01_files()
        for p in sorted(self.root.glob("*.tf")):
            text = p.read_text(encoding="utf-8")
            self.texts[p.name] = text
            try:
                self.blocks += parse_file(p)
            except ParseError as e:
                self.add("R00", p.name, f"解析失败：{e}")
        if not self.blocks:
            self.add("R00", str(self.root), "没有任何 .tf 内容")
            return self.findings
        for check in (self.r02_credentials, self.r03_account, self.r04_region, self.r05_ingress, self.r06_rds, self.r07_oss,
                      self.r08_tags, self.r09_kms, self.r10_ack, self.r11_egress, self.r12_logs, self.r13_versions, self.r14_acr,
                      self.r15_cidrs, self.r16_sg_egress, self.r17_eip, self.r18_lb, self.r19_ram, self.r20_monitoring,
                      self.r21_log_index, self.forbidden_types):
            check()
        return self.findings

    def r01_files(self) -> None:
        for p in self.root.rglob("*"):
            rel = str(p.relative_to(self.root))
            name = p.name
            bad = (name == ".terraform" or ".tfstate" in name or name.endswith(".tfplan")
                   or (".tfvars" in name and not name.endswith(".example"))
                   or name in ("override.tf", "override.tf.json") or name.endswith("_override.tf") or name == "crash.log")
            if bad:
                self.add("R01", rel, "状态、计划、真实变量文件或本地 provider 缓存不进仓库")

    def forbidden_types(self) -> None:
        for b in self.resources():
            if b.labels[0] in FORBIDDEN_TYPES:
                self.add(FORBIDDEN_TYPES[b.labels[0]][:3], b.file, f"{b.labels[0]}.{b.labels[1]}：{FORBIDDEN_TYPES[b.labels[0]][4:]}")

    def r02_credentials(self) -> None:
        for b in self.blocks:
            for k in attrs_named(b.body, CRED_ATTRS):
                self.add("R02", b.file, f"{b.type} {' '.join(b.labels)}：不写凭据类属性 {k}")
            if b.type == "provider":
                extra = set(b.body.attrs) - {"region", "alias"}
                if extra:
                    self.add("R02", b.file, f"provider 只写地域（多了 {sorted(extra)}）；凭据经 RAM 角色 / 运行环境提供")
                for a in sub(b.body, "assume_role"):
                    if not isinstance(a.body.attrs.get("role_arn"), Expr):
                        self.add("R02", b.file, "assume_role 的 role_arn 只能引用变量")
        for f, text in self.texts.items():
            for pat, what in SECRET_TEXT:
                if pat.search(text):
                    self.add("R02", f, f"出现了{what}")

    def r03_account(self) -> None:
        for f, text in self.texts.items():
            for pat, what in ACCOUNT_TEXT:
                if pat.search(text):
                    self.add("R03", f, f"出现了{what}")

    def r04_region(self) -> None:
        provs = [b for b in self.blocks if b.type == "provider" and b.labels == ["alicloud"]]
        if not provs:
            self.add("R04", "-", "没有 alicloud provider")
        for p in provs:
            r = p.body.attrs.get("region")
            if not (isinstance(r, Expr) and r.raw.strip() == "var.region"):
                self.add("R04", p.file, "provider 的 region 只能引用 var.region")
        v = self.variables().get("region")
        if v is None or v.body.attrs.get("default") != "cn-beijing":
            self.add("R04", v.file if v else "-", "var.region 缺省必须是 cn-beijing")
        elif not any('var.region == "cn-beijing"' in getattr(x.body.attrs.get("condition"), "raw", "") for x in sub(v.body, "validation")):
            self.add("R04", v.file, 'var.region 必须有校验 var.region == "cn-beijing"')
        for b in self.blocks:
            for val in walk(b.body):
                if isinstance(val, str) and REGION_RE.fullmatch(val) and not (val == "cn-beijing" or val.startswith("cn-beijing-")):
                    self.add("R04", b.file, f"{b.type} {' '.join(b.labels)}：地域 / 可用区只能在华北 2（{val}）")

    def _source_ok(self, v: Any) -> bool:
        r = self.resolve(v)
        if isinstance(r, list):
            return bool(r) and all(self._source_ok(x) for x in r)
        return is_private_cidr(r)

    def r05_ingress(self) -> None:
        for b in self.resources("alicloud_security_group_rule"):
            a = b.body.attrs
            if a.get("type") == "ingress":
                for k in ("cidr_ip", "ipv6_cidr_ip"):
                    if k in a and not self._source_ok(a[k]):
                        self.add("R05", b.file, f"{b.labels[1]}：入方向来源只收内网网段（{k}）")
                if "source_security_group_id" not in a and "cidr_ip" not in a:
                    self.add("R05", b.file, f"{b.labels[1]}：入方向规则必须写明内网来源")
        for b in self.resources("alicloud_slb_acl_entry_attachment"):
            e = b.body.attrs.get("entry")
            if isinstance(e, str) and e in OPEN:
                self.add("R05", b.file, f"{b.labels[1]}：访问控制条目不收 0.0.0.0/0")
        v = self.variables().get("office_vpn_cidrs")
        if v is not None:
            if "default" in v.body.attrs:
                self.add("R05", v.file, "office_vpn_cidrs 不给缺省值（由人填写）")
            if not any("0.0.0.0/0" in getattr(x.body.attrs.get("condition"), "raw", "") for x in sub(v.body, "validation")):
                self.add("R05", v.file, "office_vpn_cidrs 必须有拒绝 0.0.0.0/0 的校验")

    def _num(self, v: Any) -> float | None:
        r = self.resolve(v)
        return float(r) if isinstance(r, (int, float)) and not isinstance(r, bool) else None

    def r06_rds(self) -> None:
        dbs = self.resources("alicloud_db_instance")
        if not dbs:
            self.add("R06", "-", "没有 RDS 实例")
        for b in dbs:
            a, n = b.body.attrs, b.labels[1]
            if a.get("engine") != "PostgreSQL":
                self.add("R06", b.file, f"{n}：engine 必须是 PostgreSQL")
            ver = self.resolve(a.get("engine_version"))
            if not (isinstance(ver, str) and ver.startswith("16.")):
                self.add("R06", b.file, f"{n}：engine_version 必须是 16（与迁移演练一致）")
            if a.get("ssl_action") != "Open":
                self.add("R06", b.file, f"{n}：必须强制 TLS（ssl_action = \"Open\"）")
            self._rds_tls(b)
            if "security_ips" not in a or not self._source_ok(a["security_ips"]):
                self.add("R06", b.file, f"{n}：白名单只收内网网段")
            if "vswitch_id" not in a:
                self.add("R06", b.file, f"{n}：必须放在 VPC 交换机里")
            if a.get("deletion_protection") is not True:
                self.add("R06", b.file, f"{n}：必须开删除保护")
            if "encryption_key" not in a:
                self.add("R09", b.file, f"{n}：云盘必须用 KMS 加密（encryption_key）")
            pol = [p for p in self.resources("alicloud_rds_backup_policy")
                   if isinstance(p.body.attrs.get("instance_id"), Expr) and f"alicloud_db_instance.{n}." in p.body.attrs["instance_id"].raw + "."]
            if not pol:
                self.add("R06", b.file, f"{n}：缺备份策略（alicloud_rds_backup_policy）")
            for p in pol:
                pa = p.body.attrs
                if pa.get("enable_backup_log") is not True:
                    self.add("R06", p.file, f"{p.labels[1]}：必须开日志备份（PITR）")
                for k in ("backup_retention_period", "log_backup_retention_period"):
                    x = self._num(pa.get(k))
                    if x is None or x < 7:
                        self.add("R06", p.file, f"{p.labels[1]}：{k} 至少 7 天")
                if len(self.resolve(pa.get("preferred_backup_period")) or []) != 7:
                    self.add("R06", p.file, f"{p.labels[1]}：每天备份（preferred_backup_period 七天全选）")

    def _rds_tls(self, b: Block) -> None:
        """R06 真正强制 TLS：证书签给内网地址、pg_hba 只有 hostssl"""
        a, n = b.body.attrs, b.labels[1]
        prefix, ssl_cs = a.get("connection_string_prefix"), a.get("ssl_connection_string")
        if not isinstance(prefix, Expr) or not isinstance(ssl_cs, str) and not isinstance(ssl_cs, Expr):
            self.add("R06", b.file, f"{n}：必须显式写 connection_string_prefix 与 ssl_connection_string（证书只签内网连接地址）")
        else:
            raw = ssl_cs.raw if isinstance(ssl_cs, Expr) else ssl_cs
            if not raw.strip('"').startswith("${" + prefix.raw.strip() + "}") or "var.rds_internal_domain_suffix" not in raw:
                self.add("R06", b.file, f"{n}：ssl_connection_string 必须是内网前缀（connection_string_prefix）+ var.rds_internal_domain_suffix")
        v = self.variables().get("rds_internal_domain_suffix")
        d = v.body.attrs.get("default") if v is not None else None
        if not (isinstance(d, str) and d.startswith(".") and d.endswith(".rds.aliyuncs.com") and "pgsql" not in d and "public" not in d):
            self.add("R06", v.file if v else b.file, "rds_internal_domain_suffix 的缺省必须是内网域名后缀（不是外网的 .pgsql.）")
        hba = sub(b.body, "pg_hba_conf")
        if not hba:
            self.add("R06", b.file, f"{n}：必须有 pg_hba_conf（只收 hostssl）")
        for h in hba:
            ha = h.body.attrs
            if ha.get("type") != "hostssl":
                self.add("R06", b.file, f"{n}：pg_hba 只能是 hostssl（不走 TLS 的连接在服务端就拒绝），不能是 {ha.get('type')!r}")
            if not self._source_ok(ha.get("address")):
                self.add("R06", b.file, f"{n}：pg_hba 的来源只收内网网段")
            if ha.get("method") not in PG_HBA_METHODS:
                self.add("R06", b.file, f"{n}：pg_hba 只用口令认证（{sorted(PG_HBA_METHODS)}），不收 {ha.get('method')!r}")

    def r07_oss(self) -> None:
        for b in self.blocks:
            for val in walk(b.body):
                if isinstance(val, str) and val in ("public-read", "public-read-write"):
                    self.add("R07", b.file, f"{b.type} {' '.join(b.labels)}：OSS 不公开（{val}）")
        for b in self.resources("alicloud_oss_bucket"):
            n, body = b.labels[1], b.body
            for k in ("acl", "policy"):
                if k in body.attrs:
                    self.add("R07", b.file, f"{n}：不在桶上写 {k}（ACL 用 alicloud_oss_bucket_acl = private）")
            for k in ("website", "cors_rule", "referer_config"):
                if sub(body, k):
                    self.add("R07", b.file, f"{n}：不配 {k}")
            sse = sub(body, "server_side_encryption_rule")
            if not sse or sse[0].body.attrs.get("sse_algorithm") != "KMS" or "kms_master_key_id" not in sse[0].body.attrs:
                self.add("R09", b.file, f"{n}：必须 KMS 服务端加密")
            ver = sub(body, "versioning")
            if not ver or ver[0].body.attrs.get("status") != "Enabled":
                self.add("R07", b.file, f"{n}：必须开版本控制")
            if not sub(body, "lifecycle_rule"):
                self.add("R07", b.file, f"{n}：必须有生命周期规则")
            ref = f"alicloud_oss_bucket.{n}."

            def refers(x: Block) -> bool:
                v = x.body.attrs.get("bucket")
                return isinstance(v, Expr) and (v.raw + ".").startswith(ref)
            acl = [x for x in self.resources("alicloud_oss_bucket_acl") if refers(x)]
            if not acl or any(x.body.attrs.get("acl") != "private" for x in acl):
                self.add("R07", b.file, f"{n}：必须有 alicloud_oss_bucket_acl（private）")
            pab = [x for x in self.resources("alicloud_oss_bucket_public_access_block") if refers(x)]
            if not pab or any(x.body.attrs.get("block_public_access") is not True for x in pab):
                self.add("R07", b.file, f"{n}：必须阻止公共访问（alicloud_oss_bucket_public_access_block）")

    def r08_tags(self) -> None:
        for b in self.resources():
            if b.labels[0] not in NO_TAGS and b.labels[0] not in FORBIDDEN_TYPES and "tags" not in b.body.attrs:
                self.add("R08", b.file, f"{b.labels[0]}.{b.labels[1]}：缺 tags（不支持标签的类型须登记在 NO_TAGS）")

    def r09_kms(self) -> None:
        keys = self.resources("alicloud_kms_key")
        if not keys:
            self.add("R09", "-", "没有 KMS 主密钥")
        for k in keys:
            a = k.body.attrs
            if a.get("automatic_rotation") != "Enabled":
                self.add("R09", k.file, f"{k.labels[1]}：必须自动轮转")
            if a.get("deletion_protection") != "Enabled":
                self.add("R09", k.file, f"{k.labels[1]}：必须开删除保护")
        for p in self.resources("alicloud_cs_kubernetes_node_pool"):
            a = p.body.attrs
            if a.get("system_disk_encrypted") is not True or "system_disk_kms_key" not in a:
                self.add("R09", p.file, f"{p.labels[1]}：节点系统盘必须 KMS 加密")
            for d in sub(p.body, "data_disks"):
                if d.body.attrs.get("encrypted") not in (True, "true") or "kms_key_id" not in d.body.attrs:
                    self.add("R09", p.file, f"{p.labels[1]}：节点数据盘必须 KMS 加密")
        for c in self.resources("alicloud_cs_managed_kubernetes"):
            if "encryption_provider_key" not in c.body.attrs:
                self.add("R09", c.file, f"{c.labels[1]}：K8s Secret 落盘必须 KMS 加密（encryption_provider_key）")
        for s in self.resources("alicloud_log_store"):
            e = sub(s.body, "encrypt_conf")
            if not e or e[0].body.attrs.get("enable") is not True:
                self.add("R09", s.file, f"{s.labels[1]}：日志库必须开服务端加密")

    def r10_ack(self) -> None:
        cs = self.resources("alicloud_cs_managed_kubernetes")
        if not cs:
            self.add("R10", "-", "没有托管 K8s 集群")
        for c in cs:
            a = c.body.attrs
            if a.get("slb_internet_enabled") is not False:
                self.add("R10", c.file, f"{c.labels[1]}：API Server 不开公网（slb_internet_enabled = false）")
            if a.get("new_nat_gateway") is not False:
                self.add("R10", c.file, f"{c.labels[1]}：不让集群另建 NAT（new_nat_gateway = false，出网走白名单 NAT）")
        for p in self.resources("alicloud_cs_kubernetes_node_pool"):
            bw = self._num(p.body.attrs.get("internet_max_bandwidth_out"))
            if "internet_max_bandwidth_out" in p.body.attrs and (bw is None or bw > 0):
                self.add("R10", p.file, f"{p.labels[1]}：节点不分配公网带宽")

    def r11_egress(self) -> None:
        if not self.resources("alicloud_nat_gateway"):
            self.add("R11", "-", "没有 NAT 网关（出网白名单的落点）")
        pols = self.resources("alicloud_cloud_firewall_nat_firewall_control_policy")
        accepts = [p for p in pols if p.body.attrs.get("acl_action") == "accept"]
        drops = [p for p in pols if p.body.attrs.get("acl_action") == "drop"]
        if not accepts or not drops:
            self.add("R11", "-", "云防火墙 NAT 边界要有放行模型供应商与拒绝其余的两类规则")
        for p in accepts:
            a = p.body.attrs
            dest = a.get("destination")
            ok = (a.get("direction") == "out" and a.get("destination_type") == "domain" and isinstance(dest, Expr)
                  and "var.model_egress_fqdns" in dest.raw and a.get("dest_port") == "443/443" and a.get("proto") == "TCP")
            if not ok:
                self.add("R11", p.file, f"{p.labels[1]}：出方向只放行 var.model_egress_fqdns 的 TCP 443（主机名类型）")
        for p in drops:
            a = p.body.attrs
            order = a.get("new_order")
            if a.get("destination") != "0.0.0.0/0" or a.get("direction") != "out" or a.get("proto") != "ANY" \
                    or not (isinstance(order, Expr) and "length(var.model_egress_fqdns)" in order.raw):
                self.add("R11", p.file, f"{p.labels[1]}：最后一条拒绝其余全部出网（排在白名单之后）")
        v = self.variables().get("model_egress_fqdns")
        if v is None:
            self.add("R11", "-", "缺变量 model_egress_fqdns")
        elif v.body.attrs.get("default") not in (None, []) or "default" in v.body.attrs and v.body.attrs["default"] != []:
            self.add("R11", v.file, "model_egress_fqdns 不写真实主机名（缺省为空，由人填写）")
        for b in self.resources("alicloud_security_group_rule"):
            a = b.body.attrs
            if a.get("type") == "egress" and a.get("policy", "accept") == "accept" and isinstance(a.get("cidr_ip"), str) \
                    and a["cidr_ip"] in OPEN:
                if not (a.get("ip_protocol") == "tcp" and a.get("port_range") == "443/443"):
                    self.add("R11", b.file, f"{b.labels[1]}：出公网只开 TCP 443")
        # 云防火墙没开不能静默放开出网（安全审查采纳建议）
        cv = self.variables().get("nat_firewall_confirmed")
        if cv is None or "default" in cv.body.attrs or not any(
                "var.nat_firewall_confirmed == true" in getattr(x.body.attrs.get("condition"), "raw", "") for x in sub(cv.body, "validation")):
            self.add("R11", cv.file if cv else "-", "必须有确认变量 nat_firewall_confirmed（没有缺省值，校验必须为 true）")
        snats = self.resources("alicloud_snat_entry")
        if not snats:
            self.add("R11", "-", "没有 SNAT 条目（出网落点）")
        for s in snats:
            deps = " ".join(x.raw for x in s.body.attrs.get("depends_on", []) if isinstance(x, Expr))
            if "alicloud_cloud_firewall_nat_firewall_control_policy.deny_rest" not in deps \
                    or "alicloud_cloud_firewall_nat_firewall_control_policy.model_vendor" not in deps:
                self.add("R11", s.file, f"{s.labels[1]}：SNAT 必须 depends_on 云防火墙的白名单与“拒绝其余”两类访问控制")
            pre = [p for lc in sub(s.body, "lifecycle") for p in sub(lc.body, "precondition")]
            if not any(getattr(p.body.attrs.get("condition"), "raw", "").strip() == "var.nat_firewall_confirmed" for p in pre):
                self.add("R11", s.file, f"{s.labels[1]}：SNAT 必须以 var.nat_firewall_confirmed 为前置条件")

    def r16_sg_egress(self) -> None:
        for g in self.resources("alicloud_security_group"):
            if g.body.attrs.get("security_group_type") != "normal":
                self.add("R16", g.file, f"{g.labels[1]}：用普通安全组（出方向要靠优先级 + 拒绝规则收窄）")
        rules = [b for b in self.resources("alicloud_security_group_rule") if b.body.attrs.get("type") == "egress"]
        accepts = [b for b in rules if b.body.attrs.get("policy", "accept") == "accept"]
        drops = [b for b in rules if b.body.attrs.get("policy") == "drop"]
        ep = self.variables().get("cloud_service_endpoint_cidrs")

        def prio(b: Block) -> float:
            x = self._num(b.body.attrs.get("priority"))
            return 1.0 if x is None else x
        for b in accepts:
            a, n = b.body.attrs, b.labels[1]
            cidr, proto, ports = a.get("cidr_ip"), a.get("ip_protocol"), a.get("port_range")
            if "ipv6_cidr_ip" in a or "source_security_group_id" in a or "prefix_list_id" in a:
                self.add("R16", b.file, f"{n}：出方向放行只按白名单里的 IPv4 网段写")
                continue
            if isinstance(cidr, Expr) and cidr.raw.strip() == "var.vpc_cidr":
                continue
            if isinstance(cidr, Expr) and cidr.raw.strip() == "each.value":
                fe = b.body.attrs.get("for_each")
                ok_var = ep is not None and "default" not in ep.body.attrs and any(
                    "100." in getattr(x.body.attrs.get("condition"), "raw", "") for x in sub(ep.body, "validation"))
                if not (isinstance(fe, Expr) and "var.cloud_service_endpoint_cidrs" in fe.raw and ok_var
                        and (proto, ports) == ("tcp", "443/443")):
                    self.add("R16", b.file, f"{n}：按变量放行只收 var.cloud_service_endpoint_cidrs 的 TCP 443（变量没有缺省值、校验限在 100.x）")
                continue
            allowed = EGRESS_FIXED.get(cidr) if isinstance(cidr, str) else None
            if allowed is None or (proto, ports) not in allowed:
                self.add("R16", b.file, f"{n}：出方向放行超出白名单（{cidr} {proto} {ports}）")
        https = [b for b in accepts if b.body.attrs.get("cidr_ip") == "0.0.0.0/0"]
        cloud_accepts = [b for b in accepts if b.body.attrs.get("cidr_ip") in EGRESS_FIXED and b.body.attrs.get("cidr_ip") != "0.0.0.0/0"
                         or getattr(b.body.attrs.get("cidr_ip"), "raw", "") == "each.value"]
        for must in ("100.64.0.0/10", "169.254.0.0/16"):
            d = [b for b in drops if b.body.attrs.get("cidr_ip") == must and b.body.attrs.get("ip_protocol") == "all"]
            if not d:
                self.add("R16", "-", f"必须有出方向拒绝 {must} 的规则（这些去向不经 NAT，不过云防火墙）")
                continue
            if any(prio(x) >= prio(h) for x in d for h in https):
                self.add("R16", d[0].file, f"拒绝 {must} 必须排在 0.0.0.0/0:443 之前（优先级数字更小）")
            if must == "100.64.0.0/10" and any(prio(c) >= prio(x) for x in d for c in cloud_accepts):
                self.add("R16", d[0].file, "100.x 的具体放行（DNS、元数据、服务端点）必须排在拒绝 100.64.0.0/10 之前")
        last = [b for b in drops if b.body.attrs.get("cidr_ip") == "0.0.0.0/0" and b.body.attrs.get("ip_protocol") == "all"]
        if not last:
            self.add("R16", "-", "必须有出方向 0.0.0.0/0 全协议拒绝的规则")
        elif accepts and min(prio(x) for x in last) <= max(prio(x) for x in accepts):
            self.add("R16", last[0].file, "0.0.0.0/0 全协议拒绝必须排在所有放行规则之后")

    def r17_eip(self) -> None:
        for b in self.resources("alicloud_eip_association"):
            a = b.body.attrs
            iid = a.get("instance_id")
            if a.get("instance_type") != "Nat" or not (isinstance(iid, Expr) and iid.raw.strip().startswith("alicloud_nat_gateway.")):
                self.add("R17", b.file, f"{b.labels[1]}：弹性公网 IP 只能绑 NAT 网关")

    def r18_lb(self) -> None:
        want = {"alicloud_slb_load_balancer": "intranet", "alicloud_slb": "intranet",
                "alicloud_alb_load_balancer": "Intranet", "alicloud_nlb_load_balancer": "Intranet"}
        for typ, val in want.items():
            for b in self.resources(typ):
                if b.body.attrs.get("address_type") != val:
                    self.add("R18", b.file, f"{typ}.{b.labels[1]}：不建公网负载均衡（address_type 必须是 {val}）")

    def r19_ram(self) -> None:
        for typ, attr in (("alicloud_ram_policy", "policy_document"), ("alicloud_ram_role", "assume_role_policy_document")):
            for b in self.resources(typ):
                doc = b.body.attrs.get(attr)
                raw = doc.raw if isinstance(doc, Expr) else str(doc)
                if "*" in raw:
                    self.add("R19", b.file, f"{typ}.{b.labels[1]}：{attr} 里不用通配符 *（动作与资源逐个列出）")

    def r20_monitoring(self) -> None:
        clusters = self.resources("alicloud_cs_managed_kubernetes")
        for c in clusters:
            addons = {a.body.attrs.get("name"): a for a in sub(c.body, "addons")}
            for need in REQUIRED_ADDONS:
                a = addons.get(need)
                if a is None or a.body.attrs.get("disabled") in (True, "true"):
                    self.add("R20", c.file, f"{c.labels[1]}：必须装采集组件 {need}（且不禁用）")

    def r21_log_index(self) -> None:
        idx = self.resources("alicloud_log_store_index")
        app = [x for x in idx if isinstance(x.body.attrs.get("logstore"), Expr)
               and x.body.attrs["logstore"].raw.strip() == "alicloud_log_store.app.logstore_name"]
        if self.resources("alicloud_log_store") and not app:
            self.add("R21", "-", "app 日志库必须有索引（alicloud_log_store_index，只建白名单字段的字段索引）")
        for x in idx:
            if sub(x.body, "full_text"):
                self.add("R21", x.file, f"{x.labels[1]}：不建全文索引（full_text）")
            fields = sub(x.body, "field_search")
            if not fields:
                self.add("R21", x.file, f"{x.labels[1]}：只建字段索引（field_search）")
            for f in fields:
                name = f.body.attrs.get("name")
                if name not in LOG_INDEX_FIELDS:
                    self.add("R21", x.file, f"{x.labels[1]}：字段索引只收白名单字段（{name!r} 不在 LOG_INDEX_FIELDS）")
                for nested in ("json_keys",):
                    if sub(f.body, nested):
                        self.add("R21", x.file, f"{x.labels[1]}：字段 {name!r} 不展开子字段索引")

    def r12_logs(self) -> None:
        stores = self.resources("alicloud_log_store")
        if not stores:
            self.add("R12", "-", "没有日志库")
        for s in stores:
            x = self._num(s.body.attrs.get("retention_period"))
            if x is None or x < LOG_RETENTION_MIN:
                self.add("R12", s.file, f"{s.labels[1]}：日志留存至少 {LOG_RETENTION_MIN} 天")
            elif x > LOG_RETENTION_MAX:
                self.add("R12", s.file, f"{s.labels[1]}：日志留存至多 {LOG_RETENTION_MAX} 天（最小必要）")
        # 留存变量本身要有上下限校验（tfvars 改了也拦得住）
        for v in self.blocks:
            if v.type == "variable" and v.labels and v.labels[0] == "log_retention_days":
                cond = " ".join(getattr(c.body.attrs.get("condition"), "raw", str(c.body.attrs.get("condition")))
                                for c in sub(v.body, "validation"))
                if not re.search(rf">=\s*{LOG_RETENTION_MIN}\b", cond) or not re.search(rf"<=\s*{LOG_RETENTION_MAX}\b", cond):
                    self.add("R12", v.file, f"var.log_retention_days 必须校验 {LOG_RETENTION_MIN}–{LOG_RETENTION_MAX} 天")
        for c in self.resources("alicloud_cs_managed_kubernetes"):
            t = c.body.attrs.get("control_plane_log_ttl")
            if t is None or (isinstance(t, (str, int)) and int(t) < 180):
                self.add("R12", c.file, f"{c.labels[1]}：控制面日志留存至少 180 天")

    def r13_versions(self) -> None:
        tf = [b for b in self.blocks if b.type == "terraform"]
        if not tf or "required_version" not in tf[0].body.attrs:
            self.add("R13", "-", "terraform 块必须有 required_version")
            return
        rp = sub(tf[0].body, "required_providers")
        ali = rp[0].body.attrs.get("alicloud") if rp else None
        raw = getattr(ali, "raw", "")
        if 'source = "aliyun/alicloud"' not in raw or not re.search(r'version\s*=\s*"[~>=<\s\d.,]+"', raw):
            self.add("R13", tf[0].file, "alicloud provider 来源必须是 aliyun/alicloud，且有版本约束")
        for be in sub(tf[0].body, "backend"):
            if be.body.attrs or be.body.blocks:
                self.add("R13", tf[0].file, "backend 只写部分配置（取值在 init 时用 -backend-config 给出）")

    def r14_acr(self) -> None:
        for n in self.resources("alicloud_cr_ee_namespace"):
            a = n.body.attrs
            if a.get("default_visibility") != "PRIVATE" or a.get("auto_create") is not False:
                self.add("R14", n.file, f"{n.labels[1]}：命名空间缺省私有、不自动建仓")
        for r in self.resources("alicloud_cr_ee_repo"):
            if r.body.attrs.get("repo_type") != "PRIVATE":
                self.add("R14", r.file, f"{r.labels[1]}：仓库必须私有")

    def r15_cidrs(self) -> None:
        v = self.variables()
        nets = {}
        for name in ("vpc_cidr", "service_cidr", "node_vswitch_cidrs", "pod_vswitch_cidrs", "db_vswitch_cidrs"):
            d = v[name].body.attrs.get("default") if name in v else None
            vals = d if isinstance(d, list) else [d]
            if d is None or not all(is_private_cidr(x) for x in vals):
                self.add("R15", v[name].file if name in v else "-", f"{name} 的缺省值必须是 RFC 1918 网段")
            else:
                nets[name] = [ipaddress.ip_network(x) for x in vals]
        if "vpc_cidr" in nets and "service_cidr" in nets and nets["vpc_cidr"][0].overlaps(nets["service_cidr"][0]):
            self.add("R15", v["service_cidr"].file, "Service 网段不能与 VPC 重叠")
        if "vpc_cidr" in nets:
            for name in ("node_vswitch_cidrs", "pod_vswitch_cidrs", "db_vswitch_cidrs"):
                if name in nets and not all(x.subnet_of(nets["vpc_cidr"][0]) for x in nets[name]):
                    self.add("R15", v[name].file, f"{name} 必须落在 VPC 网段里")


def check(root: Path) -> list[Finding]:
    return Checker(root).run()


def main(argv: list[str]) -> int:
    root = Path(argv[1]) if len(argv) > 1 else Path(__file__).resolve().parent / "alicloud"
    if not root.is_dir():
        print(f"iac-check：目录不存在：{root}")
        return 2
    found = check(root)
    for f in found:
        print(f"  {f}")
    if any(f.rule == "R00" for f in found):
        print(f"iac-check：失败（解析不了，{len(found)} 条）")
        return 2
    if found:
        print(f"iac-check：失败（{len(found)} 条违规）")
        return 1
    print(f"iac-check：通过（{root.name}：{len(list(root.glob('*.tf')))} 个文件，规则 R01–R21）")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
