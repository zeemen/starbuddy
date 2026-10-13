"""配置：全部来自环境变量，不写进仓库（做法同 services/learning/learning/config.py）。

只允许开发与测试环境（CLAUDE.md 规则 9：不接触生产）。pilot 与生产的部署配置由人决定。
  KNOWLEDGE_ENV                    dev | test
  KNOWLEDGE_DATABASE_URL           asyncpg 连接串；登录角色只继承 starbuddy_kge_read（迁移 0026），不能是超级用户、
                                   不能有 BYPASSRLS、不能继承 starbuddy_kge 或其他应用角色（启动时断言，不满足即拒绝启动）
  KNOWLEDGE_TOKEN_BFF              BFF → 知识服务的服务间令牌（≥ 32 字符）
  KNOWLEDGE_TOKEN_ORCHESTRATOR     编排层 → 知识服务
  KNOWLEDGE_TOKEN_LEARNING         学习服务 → 知识服务
                                   三个令牌至少配一个、各不相同；令牌决定 caller（契约 PublicContext.caller 的三个取值），
                                   学生与家长没有令牌，只能经 BFF 按档案授权后间接访问（规则 5）
  KNOWLEDGE_PAGE_SNAPSHOT          编译器导出的页面快照目录（knowledge.compiler.snapshot）；不设则 page.get 回 501
  KNOWLEDGE_PAGE_SNAPSHOT_POINTER  （KGE-06，与上一项二选一）快照指针文件：一行快照目录的绝对路径；启动时按它载入，
                                   进程收到 SIGHUP 时重读指针、核对后热切换（失败保留旧快照并计数，见 hotswap.py）
  KNOWLEDGE_PAGE_SNAPSHOT_ROOT     （可选，绝对路径）快照根目录：配置时载入的快照目录解析符号链接后必须在它里面
  KNOWLEDGE_SNAPSHOT_HMAC_KEY     （可选，≥ 32 字符）manifest 签名密钥；只留接口（签名格式与密钥管理待人确认），
                                   不设则按现状不验签并在每次载入时记录
  KNOWLEDGE_HOST                   HTTP 与 MCP 的监听地址，缺省 127.0.0.1；不接受 0.0.0.0、:: 等通配地址
                                   （只在服务内网监听，由平台侧网关转发；需要内网地址时显式配置具体地址）
  KNOWLEDGE_PORT                   缺省 8790（契约 servers）
"""
from __future__ import annotations

import ipaddress
import os
import socket
from dataclasses import dataclass, field
from pathlib import Path

ALLOWED_ENVS = ("dev", "test")
CALLERS = ("bff", "orchestrator", "learning")
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8790
_WILDCARD = {"", "*", "0.0.0.0", "::", "[::]", "0:0:0:0:0:0:0:0"}


def _resolved(h: str) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    """把监听地址解析成 IP：IP 字面量直接取；IPv4 旧式写法（"0"、"0x0"、"0.0" 等）按 inet_aton 取；主机名经 getaddrinfo"""
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
        raise RuntimeError("KNOWLEDGE_HOST 无法解析：请配置具体地址") from None
    return [ipaddress.ip_address(str(info[4][0]).split("%", 1)[0]) for info in infos]


def check_host(host: str) -> str:
    """监听地址只能是具体地址：回环、内网 IP 或主机名；通配地址一律拒绝。
    主机名与旧式 IPv4 写法先解析，解析结果里有 unspecified（0.0.0.0、::）同样拒绝（"0" 会被解析成 0.0.0.0）"""
    h = host.strip()
    if h.lower() in _WILDCARD:
        raise RuntimeError("KNOWLEDGE_HOST 不能是通配地址（0.0.0.0、:: 等）：只在服务内网监听，请配置具体地址")
    ips = _resolved(h)
    if not ips or any(ip.is_unspecified for ip in ips):
        raise RuntimeError("KNOWLEDGE_HOST 不能是通配地址（解析结果是 0.0.0.0 或 ::）")
    return h


@dataclass(frozen=True)
class Settings:
    env: str
    database_url: str
    tokens: dict[str, str] = field(default_factory=dict)  # caller → 令牌
    page_snapshot: Path | None = None
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    page_snapshot_pointer: Path | None = None
    page_snapshot_root: Path | None = None
    snapshot_hmac_key: str | None = field(default=None, repr=False)  # 不进 repr（日志、异常）

    @staticmethod
    def from_env(environ: dict[str, str] | None = None) -> "Settings":
        e = dict(os.environ if environ is None else environ)
        env = e.get("KNOWLEDGE_ENV", "")
        if env not in ALLOWED_ENVS:
            raise RuntimeError(f"KNOWLEDGE_ENV 必须是 {ALLOWED_ENVS} 之一；本服务不连接 pilot 或生产环境")
        database_url = e.get("KNOWLEDGE_DATABASE_URL", "")
        if not database_url:
            raise RuntimeError("缺少 KNOWLEDGE_DATABASE_URL")
        tokens: dict[str, str] = {}
        for caller in CALLERS:
            tok = e.get(f"KNOWLEDGE_TOKEN_{caller.upper()}")
            if tok is None:
                continue
            if len(tok) < 32:
                raise RuntimeError(f"KNOWLEDGE_TOKEN_{caller.upper()} 少于 32 个字符")
            tokens[caller] = tok
        if not tokens:
            raise RuntimeError("至少要配置一个调用方令牌（KNOWLEDGE_TOKEN_BFF / _ORCHESTRATOR / _LEARNING）")
        if len(set(tokens.values())) != len(tokens):
            raise RuntimeError("各调用方的令牌必须各不相同")
        snap = e.get("KNOWLEDGE_PAGE_SNAPSHOT")
        pointer = e.get("KNOWLEDGE_PAGE_SNAPSHOT_POINTER")
        if snap and pointer:
            raise RuntimeError("KNOWLEDGE_PAGE_SNAPSHOT 与 KNOWLEDGE_PAGE_SNAPSHOT_POINTER 只能设一个")
        if pointer and not os.path.isabs(pointer):
            raise RuntimeError("KNOWLEDGE_PAGE_SNAPSHOT_POINTER 必须是绝对路径")
        root = e.get("KNOWLEDGE_PAGE_SNAPSHOT_ROOT")
        if root and not os.path.isabs(root):
            raise RuntimeError("KNOWLEDGE_PAGE_SNAPSHOT_ROOT 必须是绝对路径")
        key = e.get("KNOWLEDGE_SNAPSHOT_HMAC_KEY") or None
        if key is not None and len(key) < 32:
            raise RuntimeError("KNOWLEDGE_SNAPSHOT_HMAC_KEY 少于 32 个字符")
        port_raw = e.get("KNOWLEDGE_PORT", str(DEFAULT_PORT))
        if not port_raw.isdigit() or not 0 < int(port_raw) < 65536:
            raise RuntimeError("KNOWLEDGE_PORT 不合法")
        return Settings(env=env, database_url=database_url, tokens=tokens, page_snapshot=Path(snap) if snap else None,
                        host=check_host(e.get("KNOWLEDGE_HOST", DEFAULT_HOST)), port=int(port_raw),
                        page_snapshot_pointer=Path(pointer) if pointer else None, snapshot_hmac_key=key,
                        page_snapshot_root=Path(root) if root else None)
