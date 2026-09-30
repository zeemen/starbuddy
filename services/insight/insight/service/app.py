"""洞察服务的 HTTP 应用（FastAPI）：契约路径 POST /v0/<工具名>。

鉴权照知识服务（services/knowledge/knowledge/service/app.py）：Bearer 服务间令牌，sha256 后常量时间比较、全部比完再取结果；
令牌决定 caller（bff / orchestrator），请求体里的 context.caller 必须与之一致，否则 403。学生与家长没有令牌（401）。
请求体限 64 KB，JSON 拒绝 NaN / Infinity 与重复键；整个应用按 Host 允许列表校验（不在列表回 400）。
启动时：加载契约、判断策略与报告检查词表（合成占位只允许 test 环境）、字段字典；以 starbuddy_insight 建连接池并断言角色安全
（注入的池同样断言），不满足即拒绝启动。不提供 MCP（本任务不需要）。首份报告只有服务内接口（insight.jobs），不对外开放。
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

from ..dispute import VerificationScheduler, default_scheduler
from ..guardian_requests import GuardianRequests
from ..policy import Policy, load_check_config, load_policy
from ..predict import MasterySource
from ..shared import forbidden_matcher
from ..sie03_policy import Sie03Policy, load_sie03
from ..store import InsightStore, assert_insight_role, create_pool
from ..store_sie03 import Sie03Store
from .config import Settings
from .contract import Contract, contract as default_contract
from .errors import ServiceError, invalid, unauthenticated
from .tools import ActorVerifier, ToolCore

MAX_BODY_BYTES = 64 * 1024
LOOPBACK = ("127.0.0.1", "localhost", "::1")


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


def _bare(host: str) -> str:
    h = host.strip()
    return h[1:-1] if h.startswith("[") and h.endswith("]") else h


def allowed_hosts(host: str) -> list[str]:
    """Host 头允许列表：回环地址只收回环 Host；内网地址只收该地址。精确值或 `<主机>:<数字端口>`"""
    h = _bare(host)
    names = ["127.0.0.1", "localhost", "[::1]"] if h in LOOPBACK else [f"[{h}]" if ":" in h else h]
    return [x for n in names for x in (n, f"{n}:*")]


def host_allowed(value: str | None, allowed: list[str]) -> bool:
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


class HostGuard:
    """整个应用的 Host 校验：lifespan 放行；http 的 Host 不在列表里回 400；其他 scope（websocket 等）一律拒绝"""

    def __init__(self, app: ASGIApp, allowed: list[str]) -> None:
        self.app = app
        self.allowed = list(allowed)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "lifespan":
            await self.app(scope, receive, send)
            return
        if scope["type"] != "http":
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1008})
            return
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
        if not host_allowed(headers.get("host"), self.allowed):
            await _problem(ServiceError(400, "INVALID_REQUEST", "invalid host header"))(scope, receive, send)
            return
        await self.app(scope, receive, send)


def create_app(settings: Settings, *, pool: asyncpg.Pool | None = None, contract: Contract | None = None,
               policy: Policy | None = None, verifier: ActorVerifier | None = None, sie03: Sie03Policy | None = None,
               scheduler: VerificationScheduler | None = None, mastery: MasterySource | None = None,
               guardian: GuardianRequests | None = None) -> FastAPI:
    """pool、contract、policy、verifier 与 SIE-03 的 sie03（配置）、scheduler（验证任务，缺省接口桩）、mastery（掌握行来源，
    缺省没有）、guardian（导出与删除，缺省不接线）供测试注入；不传时按配置创建。注入的池同样要过角色断言"""
    state: dict[str, Any] = {}
    the_contract = contract or default_contract()
    the_policy = policy or load_policy(settings.policy_path, env=settings.env)
    check_cfg = load_check_config(settings.report_check_path, env=settings.env)  # 启动即校验；报告由 insight.jobs 使用
    the_sie03 = sie03 or load_sie03(settings.sie03_path, env=settings.env)  # 启动即校验（含占位文案的禁用推断检查）

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        own_pool = pool is None
        p = pool or await create_pool(settings.database_url)
        try:
            if not own_pool:
                async with p.acquire() as c:
                    await assert_insight_role(c)
            store = InsightStore(p)
            ops = Sie03Store(p)
            state.update(store=store, ops=ops, policy=the_policy, sie03=the_sie03, check_cfg=check_cfg,
                         core=ToolCore(store, the_contract, the_policy, forbidden_matcher(), verifier, sie03=the_sie03, ops=ops,
                                       scheduler=scheduler or default_scheduler(settings.env), mastery=mastery,
                                       guardian=guardian))
            yield
        finally:
            state.clear()
            if own_pool:
                await p.close()

    app = FastAPI(title="StarBuddy insight service (internal)", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.insight = state

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
    app.add_middleware(HostGuard, allowed=allowed_hosts(settings.host))
    return app
