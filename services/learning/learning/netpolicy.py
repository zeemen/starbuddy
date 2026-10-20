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
