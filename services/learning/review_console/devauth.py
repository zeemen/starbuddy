"""开发身份桩（staff）：PR 4 的 staff 登录之前，给本地开发与端到端测试用。做法同 BFF 星图的 x-dev-account-id：
- 只有 REVIEW_CONSOLE_ENV 为 dev / test 且 REVIEW_CONSOLE_DEV_AUTH=1 时启用；
- 请求必须来自 loopback，且不带任何转发头（经同机代理转来的请求也来自 loopback）；
- Host 头必须是 loopback 地址（挡 DNS 重绑定：恶意网页把自己的域名解析到 127.0.0.1 后的“同源”请求，安全审查 PR 3 #1）；
- 请求头 x-dev-staff-id 只认下面的合成 staff 账号（与 apps/web/src/dev/staff.ts 一致，规则 8）。
满足 StaffAuthenticator 的约定：只返回合成 staff；签字、发布、撤下时库里的函数还会再核对 active staff。PR 4 删除本文件。
"""
from __future__ import annotations

import uuid

from starlette.requests import Request

DEV_STAFF = frozenset({
    uuid.UUID("5f0e0000-0000-4000-8000-0000000000e1"),
    uuid.UUID("5f0e0000-0000-4000-8000-0000000000e2"),
})
LOOPBACK = frozenset({"127.0.0.1", "::1"})
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "[::1]"})
FORWARDED = ("forwarded", "x-forwarded-for", "x-forwarded-host", "x-forwarded-proto", "x-real-ip", "via")


def dev_enabled(environ: dict[str, str]) -> bool:
    return environ.get("REVIEW_CONSOLE_ENV") in ("dev", "test") and environ.get("REVIEW_CONSOLE_DEV_AUTH") == "1"


async def dev_staff_auth(request: Request) -> uuid.UUID | None:
    if request.client is None or request.client.host not in LOOPBACK:
        return None
    if any(h in request.headers for h in FORWARDED):
        return None
    if host_name(request.headers.get("host", "")) not in LOOPBACK_HOSTS:
        return None
    raw = request.headers.get("x-dev-staff-id", "")
    try:
        staff = uuid.UUID(raw)
    except ValueError:
        return None
    return staff if staff in DEV_STAFF else None


def host_name(host: str) -> str:
    """Host 头去掉端口：`127.0.0.1:8790` → `127.0.0.1`，`[::1]:8790` → `[::1]`。"""
    if host.startswith("["):
        return host[: host.find("]") + 1] if "]" in host else host
    return host.rsplit(":", 1)[0] if host.count(":") == 1 else host
