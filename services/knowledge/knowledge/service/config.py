"""配置：全部来自环境变量，不写进仓库（做法同 services/learning/learning/config.py）。

只允许开发、测试与 staging 环境（CLAUDE.md 规则 9：不接触生产）。pilot 与生产的部署配置由人决定。
  KNOWLEDGE_ENV                    dev | test | staging（staging：LT-01 决定记录第 1 条；KNOWLEDGE_HOST 只收 127.0.0.1 或内网 IPv4）
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
  KNOWLEDGE_READ_CACHE             （PLT-19）公共层只读缓存开关：1 开、0 或不设关（缺省关；关时行为与响应逐字节不变）
  KNOWLEDGE_READ_CACHE_SIZE        缓存条目上限（LRU），缺省 1024，范围 1–10000（见 cache.py）
  KNOWLEDGE_READ_CACHE_MAX_BYTES   缓存总字节上限（按条目 JSON 长度估算，超出按 LRU 淘汰），缺省 64 MiB，范围 64 KiB–1 GiB；
                                   单条超过它的 1/64（缺省 1 MiB）不入缓存
  KNOWLEDGE_READ_CACHE_LOG_EVERY   每多少次缓存判断打一条计数日志，缺省 1000
  以上数值只收 ASCII 十进制数字，不合格即拒绝启动
"""
from __future__ import annotations

import ipaddress
import os
import re
import socket
from dataclasses import dataclass, field
from pathlib import Path

ALLOWED_ENVS = ("dev", "test")
# LT-01 决定记录第 1 条：服务另收 staging；staging 的监听地址只收 127.0.0.1 或内网 IPv4 字面量；pilot / 生产仍拒绝。
# 公共层只读缓存（KNOWLEDGE_READ_CACHE）不是开发开关，staging 照常可设（上线测试开关矩阵里保持关）
STAGING = "staging"
SERVICE_ENVS = ALLOWED_ENVS + (STAGING,)
CALLERS = ("bff", "orchestrator", "learning")
_PRIVATE_V4 = tuple(ipaddress.IPv4Network(n) for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"))


def private_ipv4(host: str) -> bool:
    """RFC 1918 的 IPv4 字面量（点分十进制、不带前导零；主机名与旧式写法一律 False）"""
    try:
        ip = ipaddress.IPv4Address(host)
    except ValueError:
        return False
    return str(ip) == host and any(ip in n for n in _PRIVATE_V4)
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8790
DEFAULT_CACHE_SIZE = 1024
MAX_CACHE_SIZE = 10_000
DEFAULT_CACHE_MAX_BYTES = 64 * 1024 * 1024
MIN_CACHE_MAX_BYTES = 64 * 1024
MAX_CACHE_MAX_BYTES = 1024 * 1024 * 1024
DEFAULT_CACHE_LOG_EVERY = 1000
MAX_CACHE_LOG_EVERY = 10_000_000
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
    read_cache: bool = False  # PLT-19：缺省关
    read_cache_size: int = DEFAULT_CACHE_SIZE
    read_cache_max_bytes: int = DEFAULT_CACHE_MAX_BYTES
    read_cache_log_every: int = DEFAULT_CACHE_LOG_EVERY

    @staticmethod
    def from_env(environ: dict[str, str] | None = None) -> "Settings":
        e = dict(os.environ if environ is None else environ)
        env = e.get("KNOWLEDGE_ENV", "")
        if env not in SERVICE_ENVS:
            raise RuntimeError(f"KNOWLEDGE_ENV 必须是 {SERVICE_ENVS} 之一；本服务不连接 pilot 或生产环境")
        marked = e.get("STARBUDDY_IMAGE_ENV")  # 镜像固化的环境标记（LT-01 安全审查 S10）：设了就必须与运行环境一致
        if marked is not None and marked != env:
            raise RuntimeError(f"镜像环境标记是 {marked}，运行环境却是 {env}：拒绝启动（STARBUDDY_IMAGE_ENV）")
        if env == STAGING and e.get("KNOWLEDGE_CONTRACT_DIR"):
            raise RuntimeError("KNOWLEDGE_CONTRACT_DIR 只给测试换契约目录：staging 拒绝启动（LT-01 契约审查 8）")
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
        cache_flag = e.get("KNOWLEDGE_READ_CACHE", "0")
        if cache_flag not in ("0", "1"):
            raise RuntimeError("KNOWLEDGE_READ_CACHE 只能是 0 或 1")
        size = _bounded_int(e, "KNOWLEDGE_READ_CACHE_SIZE", DEFAULT_CACHE_SIZE, 1, MAX_CACHE_SIZE)
        max_bytes = _bounded_int(e, "KNOWLEDGE_READ_CACHE_MAX_BYTES", DEFAULT_CACHE_MAX_BYTES, MIN_CACHE_MAX_BYTES, MAX_CACHE_MAX_BYTES)
        log_every = _bounded_int(e, "KNOWLEDGE_READ_CACHE_LOG_EVERY", DEFAULT_CACHE_LOG_EVERY, 1, MAX_CACHE_LOG_EVERY)
        host = check_host(e.get("KNOWLEDGE_HOST", DEFAULT_HOST))
        if env == STAGING and host != DEFAULT_HOST and not private_ipv4(host):
            raise RuntimeError("KNOWLEDGE_HOST 在 staging 只能是 127.0.0.1 或内网 IPv4 地址（RFC 1918）")
        return Settings(env=env, database_url=database_url, tokens=tokens, page_snapshot=Path(snap) if snap else None,
                        host=host, port=int(port_raw),
                        page_snapshot_pointer=Path(pointer) if pointer else None, snapshot_hmac_key=key,
                        page_snapshot_root=Path(root) if root else None, read_cache=cache_flag == "1",
                        read_cache_size=size, read_cache_max_bytes=max_bytes, read_cache_log_every=log_every)


def _bounded_int(e: dict[str, str], name: str, default: int, low: int, high: int) -> int:
    """只收 ASCII 十进制数字（不收空白、正负号与其他文字的数字），范围 [low, high]；不合格一律 RuntimeError"""
    raw = e.get(name, str(default))
    if not re.fullmatch(r"[0-9]+", raw) or not low <= int(raw) <= high:
        raise RuntimeError(f"{name} 必须是 {low}–{high} 的整数")
    return int(raw)
