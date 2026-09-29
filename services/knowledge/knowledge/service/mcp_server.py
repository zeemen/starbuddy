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

from .contract import Contract
from .errors import unauthenticated
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

    @lru_cache(maxsize=None)
    def tool_list() -> tuple[types.Tool, ...]:
        return tuple(types.Tool(name=t.operation_id, description=t.summary, input_schema=contract.bundle(t.request_schema))
                     for t in sorted(contract.tools.values(), key=lambda t: t.operation_id))

    async def list_tools(ctx: Any, params: Any) -> types.ListToolsResult:
        return types.ListToolsResult(tools=list(tool_list()))

    async def call_tool(ctx: Any, params: types.CallToolRequestParams) -> types.CallToolResult:
        spec = contract.by_operation_id(params.name)
        if spec is None:
            return _result(404, {"type": "/problems/content-version-not-found", "title": "CONTENT_VERSION_NOT_FOUND",
                                 "status": 404, "code": "CONTENT_VERSION_NOT_FOUND", "retryable": False})
        caller = caller_of(ctx)
        if caller is None:
            return _result(401, unauthenticated().problem())
        status, body = await core.call(spec.name, params.arguments if params.arguments is not None else {}, caller)
        return _result(status, body)

    return Server(SERVER_NAME, version=SERVER_VERSION, on_list_tools=list_tools, on_call_tool=call_tool)


def transport_security(host: str) -> TransportSecuritySettings:
    """DNS 重绑定防护一律开启：回环地址只收回环 Host；内网地址只收该地址"""
    if host in LOOPBACK:
        return TransportSecuritySettings(enable_dns_rebinding_protection=True,
                                         allowed_hosts=["127.0.0.1:*", "localhost:*", "[::1]:*"],
                                         allowed_origins=["http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*"])
    h = f"[{host}]" if ":" in host else host
    return TransportSecuritySettings(enable_dns_rebinding_protection=True, allowed_hosts=[f"{h}:*"], allowed_origins=[])
