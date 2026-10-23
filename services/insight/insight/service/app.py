"""洞察服务的 HTTP 应用（FastAPI）：契约路径 POST /v0/<工具名>。

鉴权照知识服务（services/knowledge/knowledge/service/app.py）：Bearer 服务间令牌，sha256 后常量时间比较、全部比完再取结果；
令牌决定 caller（bff / orchestrator），请求体里的 context.caller 必须与之一致，否则 403。学生与家长没有令牌（401）。
请求体限 64 KB，JSON 拒绝 NaN / Infinity 与重复键；整个应用按 Host 允许列表校验（不在列表回 400）。
启动时：加载契约、判断策略与报告检查词表（合成占位只允许 test 环境）、字段字典；以 starbuddy_insight 建连接池并断言角色安全
（注入的池同样断言），不满足即拒绝启动。不提供 MCP（本任务不需要）。首份报告经契约工具 insight.report（SIE-02b 第二步）给 BFF；
BFF 代表学生 / 家长时的授权再核对缺省用 DbActorVerifier（迁移 0044 草稿 insight.verify_actor；函数不可用时一律不通过）。
另有一个服务内部接口 POST /internal/v1/monthly-plan/generate（PLT-14 第 2 步，service/monthly_route.py；不在契约里，
接口定义 services/insight/openapi/monthly-plan-internal.v0.yaml），同一套令牌、Host 校验与请求体限制。
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
from ..monthly import MonthlyConfig, TemplateLibrary, load_monthly_config, load_templates
from ..policy import Policy, load_check_config, load_policy
from ..predict import DbMasterySource, MasterySource
from ..shared import forbidden_matcher
from ..sie03_policy import Sie03Policy, load_sie03
from ..store import InsightStore, assert_insight_role, create_pool
from ..store_sie03 import Sie03Store
from . import health
from .config import Settings, check_db_tls
from .contract import Contract, contract as default_contract
from .errors import ServiceError, invalid, unauthenticated
from .monthly_route import register as register_monthly
from .tools import ActorVerifier, DbActorVerifier, ToolCore

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


def allowed_hosts(host: str, extra: str | None = None) -> list[str]:
    """Host 头允许列表：回环地址只收回环 Host；内网地址只收该地址。精确值或 `<主机>:<数字端口>`。
    extra：staging 另收的一个内网 IPv4（INSIGHT_EXTRA_HOST，本服务 Service 的 clusterIP；LT-03）"""
    h = _bare(host)
    names = ["127.0.0.1", "localhost", "[::1]"] if h in LOOPBACK else [f"[{h}]" if ":" in h else h]
    if extra:
        names.append(extra)
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
               guardian: GuardianRequests | None = None, monthly: MonthlyConfig | None = None,
               monthly_templates: TemplateLibrary | None = None) -> FastAPI:
    """pool、contract、policy、verifier（缺省 DbActorVerifier，调 0044 insight.verify_actor）与 SIE-03 的 sie03（配置）、scheduler（验证任务：test 环境缺省接口桩，其他环境配置了
    INSIGHT_LEARNING_URL / _TOKEN 就调学习服务，否则不可用）、mastery（掌握行来源，缺省直接读 platform.mastery_estimate 的节点键当前行）、
    guardian（导出与删除，缺省不接线）、monthly / monthly_templates（PLT-14 第 2 步月度计划的配置与模板库）供测试注入；不传时按配置创建。
    注入的池同样要过角色断言"""
    state: dict[str, Any] = {}
    if pool is None:
        check_db_tls(settings.database_url, settings.env)  # LT-03 决定记录（续）第 6 条：建池之前拒绝
    the_contract = contract or default_contract()
    the_policy = policy or load_policy(settings.policy_path, env=settings.env)
    check_cfg = load_check_config(settings.report_check_path, env=settings.env)  # 启动即校验；insight.report 与 insight.jobs 使用
    the_sie03 = sie03 or load_sie03(settings.sie03_path, env=settings.env)  # 启动即校验（含占位文案的禁用推断检查）
    # 月度计划（PLT-14 第 2 步）：配置与模板库启动即校验（模板库过禁用推断与“名次 / 概率 / 承诺”词表；合成或未签字只允许 test）
    # LT-01 决定记录第 9 条：月度计划开关关闭（settings.monthly_enabled 为假，staging 未显式配置 INSIGHT_MONTHLY_PLAN）时不加载
    # 配置与模板库，生成接口回 501；开关开时照旧启动即校验签字配置。dev / test 恒为开（行为不变）
    monthly_on = settings.monthly_enabled or monthly is not None
    the_monthly = (monthly or load_monthly_config(settings.monthly_path, env=settings.env)) if monthly_on else None
    the_templates = (monthly_templates or load_templates(settings.monthly_templates_path, env=settings.env)) if monthly_on else None

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
            the_mastery = mastery or DbMasterySource(p)
            state.update(store=store, ops=ops, policy=the_policy, sie03=the_sie03, check_cfg=check_cfg,
                         monthly=({"config": the_monthly, "templates": the_templates, "forbidden": forbidden_matcher(),
                                   "mastery": the_mastery} if monthly_on else None),
                         # 月度计划接口代表家长时的库内再核（0044 insight.verify_actor），与契约工具同一口径
                         verifier=verifier if verifier is not None else DbActorVerifier(p),
                         core=ToolCore(store, the_contract, the_policy, forbidden_matcher(), verifier or DbActorVerifier(p),
                                       sie03=the_sie03, ops=ops,
                                       scheduler=scheduler or default_scheduler(settings.env, settings.learning),
                                       mastery=mastery or DbMasterySource(p),
                                       guardian=guardian, check_cfg=check_cfg))
            state["ready_pool"] = p  # 就绪探针（LT-03）：角色断言与全部装配通过之后才放进来
            yield
        finally:
            state.clear()
            if own_pool:
                await p.close()

    app = FastAPI(title="StarBuddy insight service (internal)", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.insight = state
    # 健康端点（LT-03；services/insight/openapi/health.v0.yaml）：无需鉴权，响应只含状态
    health.register(app, lambda: health.pool_alive(state.get("ready_pool")))

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

    async def read_body(request: Request) -> Any:
        raw = await _read_limited(request)
        try:
            return strict_json(raw)
        except (ValueError, UnicodeDecodeError):
            raise invalid("request body is not valid JSON") from None

    # 服务内部接口（不在契约里；接口定义 services/insight/openapi/monthly-plan-internal.v0.yaml）
    register_monthly(app, caller_of=lambda r: caller_for(r.headers.get("authorization"), settings.tokens), read_body=read_body, state=state)
    app.add_middleware(HostGuard, allowed=allowed_hosts(settings.host, settings.extra_host))
    return app
