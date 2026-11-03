"""同一组工具以 MCP 工具暴露（spec 待人确认 #4）：工具名 = 契约 operationId，inputSchema 由契约的请求 schema 打包生成。

工具实现与 HTTP 共用 ToolCore.call（鉴权、caller 准入、契约校验、响应校验一处实现）；这里只做协议适配：
成功时 structured_content 是契约的统一信封，失败时 is_error = true、structured_content 是契约的 Problem。
caller 由 caller_of(ctx) 决定：经 HTTP（streamable HTTP，挂在知识服务的 /mcp）时从 Authorization 令牌取；
进程内测试用固定的调用方。监听地址与 HTTP 相同（config.KNOWLEDGE_HOST，缺省 127.0.0.1，不接受通配地址）。
"""
from __future__ import annotations

import json
from functools import lru_cache
from typing import Any, Callable

import mcp_types as types
from mcp.server import Server
from mcp.server.transport_security import TransportSecuritySettings

from .contract import PUBLIC_TOOLS, Contract
from .errors import not_found, unauthenticated
from .tools import ToolCore

SERVER_NAME = "starbuddy-knowledge"
SERVER_VERSION = "0.1.0"
LOOPBACK = ("127.0.0.1", "localhost", "::1")

CallerOf = Callable[[Any], "str | None"]


def _result(status: int, body: dict) -> types.CallToolResult:
    return types.CallToolResult(content=[types.TextContent(text=json.dumps(body, ensure_ascii=False, sort_keys=True))],
                                structured_content=body, is_error=status != 200)


def build_mcp_server(core: ToolCore, caller_of: CallerOf) -> Server:
    contract: Contract = core.contract

    # 只暴露公共层工具（7 个）；个人层三个（evidence.write、mastery.get、mastery.explain）在 MCP 不列、不接（HTTP 仍回契约的 501）
    public = {name: t for name, t in contract.tools.items() if name in PUBLIC_TOOLS}

    @lru_cache(maxsize=None)
    def tool_list() -> tuple[types.Tool, ...]:
        return tuple(types.Tool(name=t.operation_id, description=t.summary, input_schema=contract.bundle(t.request_schema))
                     for t in sorted(public.values(), key=lambda t: t.operation_id))

    async def list_tools(ctx: Any, params: Any) -> types.ListToolsResult:
        return types.ListToolsResult(tools=list(tool_list()))

    async def call_tool(ctx: Any, params: types.CallToolRequestParams) -> types.CallToolResult:
        spec = contract.by_operation_id(params.name)
        if spec is None or spec.name not in public:
            return _result(404, not_found().problem())
        caller = caller_of(ctx)
        if caller is None:
            return _result(401, unauthenticated().problem())
        status, body = await core.call(spec.name, params.arguments if params.arguments is not None else {}, caller)
        return _result(status, body)

    return Server(SERVER_NAME, version=SERVER_VERSION, on_list_tools=list_tools, on_call_tool=call_tool)


def _bare(host: str) -> str:
    h = host.strip()
    return h[1:-1] if h.startswith("[") and h.endswith("]") else h


def allowed_hosts(host: str, extra: str | None = None) -> list[str]:
    """Host 头允许列表（整个应用与 MCP 共用一份）：回环地址只收回环 Host；内网地址只收该地址。
    写法同 MCP SDK：精确值，或 `<主机>:*`（任意端口）；IPv6 地址加方括号（`[::1]`，不会重复加）。
    extra：staging 另收的一个内网 IPv4（KNOWLEDGE_EXTRA_HOST，本服务 Service 的 clusterIP；LT-03）"""
    h = _bare(host)
    names = ["127.0.0.1", "localhost", "[::1]"] if h in LOOPBACK else [f"[{h}]" if ":" in h else h]
    if extra:
        names.append(extra)
    return [x for n in names for x in (n, f"{n}:*")]


def host_allowed(value: str | None, allowed: list[str]) -> bool:
    """与 MCP SDK 的判断一致（精确或 `<主机>:*`），另要求端口只含数字"""
    if not value:
        return False
    if value in allowed:
        return True
    for a in allowed:
        if a.endswith(":*"):
            base = a[:-2]
            port = value[len(base) + 1:] if value.startswith(base + ":") else ""
            if port.isdigit() and len(port) <= 5:
                return True
    return False


def transport_security(host: str, extra: str | None = None) -> TransportSecuritySettings:
    """DNS 重绑定防护一律开启：Host 允许列表与整个应用相同（allowed_hosts）"""
    hosts = allowed_hosts(host, extra)
    origins = [f"http://{h}" for h in hosts] if _bare(host) in LOOPBACK else []
    return TransportSecuritySettings(enable_dns_rebinding_protection=True, allowed_hosts=hosts, allowed_origins=origins)
