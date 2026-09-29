"""知识服务的 HTTP 应用（FastAPI）：契约路径 POST /v0/<工具名>，另把 MCP（streamable HTTP）挂在 /mcp。

鉴权照学习服务内部接口的做法（services/learning/learning/app.py）：Bearer 服务间令牌，sha256 后常量时间比较，
全部比完再取结果；令牌决定 caller（bff / orchestrator / learning），请求体里的 context.caller 必须与之一致，否则 403。
学生与家长没有令牌（401）。请求体限 64 KB，JSON 拒绝 NaN / Infinity 与重复键。
启动时：加载契约；以 starbuddy_kge_read 建连接池并断言角色安全（不满足即拒绝启动）；配置了页面快照就逐页核对后加载
（不合格即拒绝启动，不带着坏快照对外服务）。
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
from contextlib import asynccontextmanager
from typing import Any

import asyncpg
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from .config import Settings
from .contract import Contract, contract as default_contract
from .errors import ServiceError, invalid, unauthenticated
from .mcp_server import build_mcp_server, transport_security
from .pages import PageSnapshot, load_snapshot_dir
from .reader import KgeReader, assert_reader_role, create_reader_pool
from .tools import ToolCore

MAX_BODY_BYTES = 64 * 1024
MCP_PATH = "/mcp"


def _digest(token: str) -> bytes:
    return hashlib.sha256(token.encode()).digest()


def caller_for(header: str | None, tokens: dict[str, str]) -> str | None:
    m = re.fullmatch(r"Bearer (.+)", header or "")
    if not m:
        return None
    presented = _digest(m.group(1))
    hits = [c for c, tok in sorted(tokens.items()) if hmac.compare_digest(presented, _digest(tok))]  # 全部比完
    return hits[0] if len(hits) == 1 else None


def _reject_constant(name: str) -> Any:
    raise ValueError(f"JSON 常量 {name} 不允许")


def _no_duplicates(pairs: list[tuple[str, Any]]) -> dict:
    out: dict = {}
    for k, v in pairs:
        if k in out:
            raise ValueError("JSON 对象有重复键")
        out[k] = v
    return out


def strict_json(raw: bytes) -> Any:
    return json.loads(raw, parse_constant=_reject_constant, object_pairs_hook=_no_duplicates)


async def _read_limited(request: Request) -> bytes:
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > MAX_BODY_BYTES:
        raise ServiceError(413, "INVALID_REQUEST", "request body too large")
    chunks, size = [], 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_BODY_BYTES:
            raise ServiceError(413, "INVALID_REQUEST", "request body too large")
        chunks.append(chunk)
    return b"".join(chunks)


def _problem(e: ServiceError) -> JSONResponse:
    return JSONResponse(e.problem(), status_code=e.status, media_type="application/problem+json")


class McpAuth:
    """/mcp 前置鉴权：没有有效令牌直接 401，不进 MCP 会话（call_tool 里还会按令牌再取一次 caller）"""

    def __init__(self, app: ASGIApp, tokens: dict[str, str]) -> None:
        self.app = app
        self.tokens = tokens

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
            if caller_for(headers.get("authorization"), self.tokens) is None:
                await _problem(unauthenticated())(scope, receive, send)
                return
        await self.app(scope, receive, send)


def create_app(settings: Settings, *, pool: asyncpg.Pool | None = None, contract: Contract | None = None,
               pages: PageSnapshot | None = None) -> FastAPI:
    """pool、contract、pages 供测试注入；不传时按配置创建。注入的池同样要过角色断言"""
    state: dict[str, Any] = {}
    the_contract = contract or default_contract()

    def mcp_caller(ctx: Any) -> str | None:
        req = getattr(ctx, "request", None)
        return caller_for(req.headers.get("authorization"), settings.tokens) if req is not None else None

    core_holder: dict[str, ToolCore] = {}

    class _LazyCore:  # MCP 服务在应用构造时建好，ToolCore 在 lifespan 里才有连接池
        contract = the_contract

        async def call(self, tool: str, body: Any, caller: str | None) -> tuple[int, dict]:
            return await core_holder["core"].call(tool, body, caller)

    mcp_server = build_mcp_server(_LazyCore(), mcp_caller)  # type: ignore[arg-type]
    mcp_app = mcp_server.streamable_http_app(streamable_http_path=MCP_PATH, json_response=True, stateless_http=True,
                                             host=settings.host, transport_security=transport_security(settings.host),
                                             max_request_body_size=MAX_BODY_BYTES)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        own_pool = pool is None
        p = pool or await create_reader_pool(settings.database_url)
        try:
            if not own_pool:
                async with p.acquire() as c:
                    await assert_reader_role(c)
            snap = pages
            if snap is None and settings.page_snapshot is not None:
                snap = load_snapshot_dir(settings.page_snapshot)
            core_holder["core"] = state["core"] = ToolCore(KgeReader(p), the_contract, snap)
            async with mcp_server.session_manager.run():
                yield
        finally:
            core_holder.pop("core", None)
            if own_pool:
                await p.close()

    app = FastAPI(title="StarBuddy knowledge service (internal)", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.knowledge = state

    def route(tool: str):
        async def handler(request: Request) -> JSONResponse:
            try:
                caller = caller_for(request.headers.get("authorization"), settings.tokens)
                if caller is None:
                    raise unauthenticated()
                raw = await _read_limited(request)
                try:
                    body = strict_json(raw)
                except (ValueError, UnicodeDecodeError):
                    raise invalid("request body is not valid JSON") from None
            except ServiceError as e:
                return _problem(e)
            status, out = await state["core"].call(tool, body, caller)
            if status == 200:
                return JSONResponse(out)
            return JSONResponse(out, status_code=status, media_type="application/problem+json")
        handler.__name__ = tool.replace(".", "_")
        return handler

    for spec in the_contract.tools.values():
        app.add_api_route(spec.path, route(spec.name), methods=["POST"])
    app.mount("/", McpAuth(mcp_app, settings.tokens))
    return app
