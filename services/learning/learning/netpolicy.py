"""staging 运行环境的网络口径（LT-01 决定记录第 1、5 条，specs/launch-lt01-deploy-packaging）。

学习服务与审核台后端共用（review_console 也从这里取）：
- 运行环境：dev / test 不变；新增 staging（只用于境内 staging 的内部成员测试）；pilot / 生产仍一律拒绝。
- 监听地址：缺省 127.0.0.1；只有 staging 能显式配置为容器网络的内网地址（RFC 1918 IPv4 字面量），dev / test 只收 127.0.0.1。
- 服务间地址：https，或本机回环的 http（与原规则一致）；staging 另收内网 IPv4 字面量的 http（容器网络）。不收主机名、公网地址、带账号口令的地址。
- 数据库主机（审核台命令行与调度器子命令的连接核对）：dev / test 只收本机；staging 另收内网 IPv4 字面量。
"""
from __future__ import annotations

import ipaddress
import re

STAGING = "staging"
LOCAL_ENVS = ("dev", "test")
SERVICE_ENVS = LOCAL_ENVS + (STAGING,)
DEFAULT_HOST = "127.0.0.1"

_PRIVATE_V4 = tuple(ipaddress.IPv4Network(n) for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"))
_LOOPBACK_HTTP = re.compile(r"http://(127\.0\.0\.1|localhost|\[::1\])(:\d+)?(/|$)")
_HTTP_V4 = re.compile(r"http://(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})(:\d{1,5})?(/|$)")


def private_ipv4(host: str) -> bool:
    """RFC 1918 的 IPv4 字面量（点分十进制、不带前导零；主机名、IPv6、旧式写法一律 False）"""
    try:
        ip = ipaddress.IPv4Address(host)
    except ValueError:
        return False
    return str(ip) == host and any(ip in n for n in _PRIVATE_V4)


def service_url_ok(url: str, env: str) -> bool:
    """服务间地址：https、本机回环的 http；staging 另收内网 IPv4 字面量的 http"""
    if url.startswith("https://") or _LOOPBACK_HTTP.match(url):
        return True
    if env != STAGING:
        return False
    m = _HTTP_V4.match(url)
    return bool(m) and private_ipv4(m.group(1))


# 连库 TLS（LT-03 决定记录（续）第 6 条）：staging 的连接串必须要求 TLS 并校验证书与主机名，CA 用固定路径（k8s 把 ConfigMap
# starbuddy-rds-ca 只读挂到这里）；文件不存在同样拒绝。dev / test 不检查。测试可以把 RDS_CA_PATH 换成本机的系统 CA 包。
RDS_CA_PATH = "/run/rds-ca/ca.pem"
TLS_REQUIRED = f"staging 的连库串必须带 sslmode=verify-full&sslrootcert={RDS_CA_PATH}（且该 CA 文件存在）"


def db_tls_ok(url: str) -> bool:
    """连接串的查询参数里 sslmode 恰好一个且为 verify-full、sslrootcert 恰好一个且等于 RDS_CA_PATH，且这个文件存在"""
    import os
    from urllib.parse import parse_qs, urlsplit
    try:
        u = urlsplit(url)
        q = parse_qs(u.query, keep_blank_values=True)
    except ValueError:
        return False
    return (u.scheme in ("postgres", "postgresql") and q.get("sslmode") == ["verify-full"]
            and q.get("sslrootcert") == [RDS_CA_PATH] and os.path.isfile(RDS_CA_PATH))


def check_db_tls(url: str, env: str, name: str) -> None:
    """staging 下连接串不合 db_tls_ok 即拒绝启动（信息不带连接串）；其他环境不检查"""
    if env == STAGING and not db_tls_ok(url):
        raise RuntimeError(f"{name}：{TLS_REQUIRED}")


IMAGE_ENV_VAR = "STARBUDDY_IMAGE_ENV"


def check_image_env(env: str, environ) -> None:
    """镜像固化的环境标记（LT-01 安全审查 S10）：镜像构建时写入 STARBUDDY_IMAGE_ENV（staging 镜像为 staging，本机冒烟镜像为 test）；
    设了就必须与运行环境一致——staging 镜像拒绝以 dev / test 运行，反之亦然。不设（源码直接运行）不核对。
    已知局限：运行时可以用同名环境变量覆盖，只是防误用，不是防篡改。"""
    marked = environ.get(IMAGE_ENV_VAR)
    if marked is not None and marked != env:
        raise RuntimeError(f"镜像环境标记是 {marked}，运行环境却是 {env}：拒绝启动（{IMAGE_ENV_VAR}）")


def listen_host(raw: str | None, env: str, name: str) -> str:
    """监听地址：不设 = 127.0.0.1；dev / test 设了只能是 127.0.0.1；staging 只能是 127.0.0.1 或内网 IPv4 字面量"""
    if raw is None or raw == "":
        return DEFAULT_HOST
    if raw == DEFAULT_HOST:
        return raw
    if env != STAGING:
        raise RuntimeError(f"{name} 只有 staging 能改（dev / test 只监听 127.0.0.1）")
    if not private_ipv4(raw):
        raise RuntimeError(f"{name} 在 staging 只能是 127.0.0.1 或内网 IPv4 地址（RFC 1918，不收通配地址与主机名）")
    return raw
