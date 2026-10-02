"""配置：全部来自环境变量，不写进仓库（做法同 services/knowledge/knowledge/service/config.py）。

只允许开发与测试环境（CLAUDE.md 规则 9：不接触生产）。pilot 与生产的部署配置由人决定。
  INSIGHT_ENV                    dev | test
  INSIGHT_DATABASE_URL           asyncpg 连接串；登录角色只继承 starbuddy_insight（迁移 0010），不能是超级用户、不能有 BYPASSRLS、
                                 CREATEROLE、CREATEDB，不能继承其他应用角色或 pg_ 预置角色（启动时断言，不满足即拒绝启动）
  INSIGHT_TOKEN_BFF              BFF → 洞察服务的服务间令牌（≥ 32 字符）
  INSIGHT_TOKEN_ORCHESTRATOR     编排层 → 洞察服务
                                 两个令牌至少配一个、各不相同；令牌决定 caller（契约 InsightContext.caller 的两个取值），
                                 学生与家长没有令牌，只能经 BFF 按档案授权后间接访问（规则 5）
  INSIGHT_POLICY                 判断策略配置（JSON；缺省 config/claims.synthetic.json，合成占位只允许 test 环境）
  INSIGHT_REPORT_CHECK           首份报告一致性检查词表（缺省 config/report-check.synthetic.json，合成占位只允许 test 环境）
  INSIGHT_SIE03                  SIE-03 配置：异议、预测与对账、触发规则参数与占位文案（缺省 config/sie03.synthetic.json，合成占位只允许 test）
  INSIGHT_LEARNING_URL           学习服务地址（https 或本机回环）：“我其实会”的验证任务经学习服务 POST /internal/v1/verification 建
  INSIGHT_LEARNING_TOKEN         洞察服务 → 学习服务验证任务接口的令牌（≥ 32 字符；学习服务侧 LEARNING_INSIGHT_CLIENT_TOKEN），与调用方令牌各不相同。
                                 两个都设才启用真实验证任务；只设一个拒绝启动。非 test 环境不设 = i_know 回 501；test 环境用接口桩
  INSIGHT_HOST                   监听地址，缺省 127.0.0.1；不接受 0.0.0.0、:: 等通配地址（只在服务内网监听，由平台侧网关转发）
  INSIGHT_PORT                   缺省 8791（契约 servers）
"""
from __future__ import annotations

import ipaddress
import os
import re
import socket
from dataclasses import dataclass, field
from pathlib import Path

from ..policy import CONFIG_DIR

ALLOWED_ENVS = ("dev", "test")
CALLERS = ("bff", "orchestrator")
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8791
_WILDCARD = {"", "*", "0.0.0.0", "::", "[::]", "0:0:0:0:0:0:0:0"}


def _resolved(h: str) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    """把监听地址解析成 IP：IP 字面量直接取；IPv4 旧式写法（"0"、"0x0" 等）按 inet_aton 取；主机名经 getaddrinfo"""
    bare = h[1:-1] if h.startswith("[") and h.endswith("]") else h
    try:
        return [ipaddress.ip_address(bare)]
    except ValueError:
        pass
    try:
        return [ipaddress.IPv4Address(socket.inet_aton(bare))]
    except OSError:
        pass
    try:
        infos = socket.getaddrinfo(bare, None, type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError):
        raise RuntimeError("INSIGHT_HOST 无法解析：请配置具体地址") from None
    return [ipaddress.ip_address(str(info[4][0]).split("%", 1)[0]) for info in infos]


def check_host(host: str) -> str:
    """监听地址只能是具体地址；通配地址（含解析后是 0.0.0.0 / :: 的写法）一律拒绝"""
    h = host.strip()
    if h.lower() in _WILDCARD:
        raise RuntimeError("INSIGHT_HOST 不能是通配地址（0.0.0.0、:: 等）：只在服务内网监听，请配置具体地址")
    ips = _resolved(h)
    if not ips or any(ip.is_unspecified for ip in ips):
        raise RuntimeError("INSIGHT_HOST 不能是通配地址（解析结果是 0.0.0.0 或 ::）")
    return h


@dataclass(frozen=True)
class Settings:
    env: str
    database_url: str
    tokens: dict[str, str] = field(default_factory=dict)  # caller → 令牌
    policy_path: Path = CONFIG_DIR / "claims.synthetic.json"
    report_check_path: Path = CONFIG_DIR / "report-check.synthetic.json"
    sie03_path: Path = CONFIG_DIR / "sie03.synthetic.json"
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    learning: tuple[str, str] | None = None  # (学习服务地址, 验证任务接口令牌)；KGE-04 第二步 PR 3

    @staticmethod
    def from_env(environ: dict[str, str] | None = None) -> "Settings":
        e = dict(os.environ if environ is None else environ)
        env = e.get("INSIGHT_ENV", "")
        if env not in ALLOWED_ENVS:
            raise RuntimeError(f"INSIGHT_ENV 必须是 {ALLOWED_ENVS} 之一；本服务不连接 pilot 或生产环境")
        database_url = e.get("INSIGHT_DATABASE_URL", "")
        if not database_url:
            raise RuntimeError("缺少 INSIGHT_DATABASE_URL")
        tokens: dict[str, str] = {}
        for caller in CALLERS:
            tok = e.get(f"INSIGHT_TOKEN_{caller.upper()}")
            if tok is None:
                continue
            if len(tok) < 32:
                raise RuntimeError(f"INSIGHT_TOKEN_{caller.upper()} 少于 32 个字符")
            tokens[caller] = tok
        if not tokens:
            raise RuntimeError("至少要配置一个调用方令牌（INSIGHT_TOKEN_BFF / _ORCHESTRATOR）")
        if len(set(tokens.values())) != len(tokens):
            raise RuntimeError("各调用方的令牌必须各不相同")
        learning = None
        l_url, l_tok = e.get("INSIGHT_LEARNING_URL") or "", e.get("INSIGHT_LEARNING_TOKEN") or ""
        if bool(l_url) != bool(l_tok):
            raise RuntimeError("INSIGHT_LEARNING_URL 与 INSIGHT_LEARNING_TOKEN 必须同时设置或同时不设")
        if l_url:
            if len(l_tok) < 32:
                raise RuntimeError("INSIGHT_LEARNING_TOKEN 少于 32 个字符")
            if l_tok in tokens.values():
                raise RuntimeError("INSIGHT_LEARNING_TOKEN 不能与调用方令牌相同")
            if not (l_url.startswith("https://") or re.match(r"http://(127\.0\.0\.1|localhost|\[::1\])(:\d+)?/?$", l_url)):
                raise RuntimeError("INSIGHT_LEARNING_URL 必须是 https，或本机回环地址")
            learning = (l_url.rstrip("/"), l_tok)
        port_raw = e.get("INSIGHT_PORT", str(DEFAULT_PORT))
        if not port_raw.isdigit() or not 0 < int(port_raw) < 65536:
            raise RuntimeError("INSIGHT_PORT 不合法")
        return Settings(env=env, database_url=database_url, tokens=tokens,
                        policy_path=Path(e.get("INSIGHT_POLICY") or CONFIG_DIR / "claims.synthetic.json"),
                        report_check_path=Path(e.get("INSIGHT_REPORT_CHECK") or CONFIG_DIR / "report-check.synthetic.json"),
                        sie03_path=Path(e.get("INSIGHT_SIE03") or CONFIG_DIR / "sie03.synthetic.json"),
                        host=check_host(e.get("INSIGHT_HOST", DEFAULT_HOST)), port=int(port_raw), learning=learning)
