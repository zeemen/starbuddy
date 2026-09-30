"""FastAPI 应用：内部接口，只供 BFF 调用（spec 范围第 2 条）。

接口定义：packages/contracts/openapi/learning-internal.v0.yaml（插件契约 0.9.0）与 learning-internal.v1.yaml
（插件契约 1.0.0 候选版，另有只查询的重放接口；specs/plugin-api-v1.0-step2/spec.md）。迁移期两套路径并存。
日志只记方法、状态码、错误码、request_id（必须是 UUID 形状）、耗时、是否重放；不记请求体与异常消息。
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
import time
from contextlib import asynccontextmanager

import asyncpg
import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .adapter_client import strict_json
from .config import Settings
from .contract import UnsupportedContractVersion, internal_contract, plugin_contract
from .diagnosis import api as diagnosis_api
from .diagnosis.engine import Params as DiagnosisParams
from .errors import IngestError
from .model_router import ModelRouter
from .model_router.config import load_for_deployment
from .route_api import MAX_ROUTE_BODY, RouteApiError, parse_request
from .route_api import route as route_call
from .service import IngestService

log = logging.getLogger("starbuddy.learning")
UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)
UUID4_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}")
# 契约允许 diagnose 带 200 道作答、每道 20000 字（UTF-8 最多约 12 MB），再加 500 条既有证据
MAX_BODY_BYTES = 16 * 1024 * 1024


async def _read_limited(request: Request) -> bytes:
    """边读边累计，超过上限立即中断（不信任 Content-Length，分块传输也一样）。"""
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > MAX_BODY_BYTES:
        raise IngestError(413, "INVALID_REQUEST", "request body too large")
    chunks, size = [], 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_BODY_BYTES:
            raise IngestError(413, "INVALID_REQUEST", "request body too large")
        chunks.append(chunk)
    return b"".join(chunks)


def _bearer_ok(header: str | None, expected: str) -> bool:
    m = re.fullmatch(r"Bearer (.+)", header or "")
    if not m:
        return False
    a = hashlib.sha256(m.group(1).encode()).digest()
    b = hashlib.sha256(expected.encode()).digest()
    return hmac.compare_digest(a, b)


def _problem(e: IngestError, request_id: str | None) -> JSONResponse:
    headers = {"retry-after": "2"} if e.code == "BUSY" else None
    return JSONResponse(e.problem(request_id), status_code=e.status, media_type="application/problem+json", headers=headers)


def _diagnosis(settings: Settings, pool, client: httpx.AsyncClient, ingest: IngestService, params: DiagnosisParams | None):
    """自适应诊断（SIE-02a）：合成题面只在 test 环境（配置层与构造函数各拦一道）；选题信息来自合成夹具或知识服务。"""
    from .diagnosis.presenter import SyntheticPresenter
    from .diagnosis.service import DiagnosisService
    from .diagnosis.source import FixtureSource, KnowledgeServiceSource

    presenter = SyntheticPresenter(settings.env)
    if settings.diagnosis_source == "knowledge":
        assert settings.knowledge is not None and settings.diagnosis_map_version is not None
        ks = KnowledgeServiceSource(client, settings.knowledge.base_url, settings.knowledge.credential, "aya",
                                    settings.diagnosis_targets, settings.diagnosis_map_version)

        async def factory(grade):
            await ks.load(grade)
            return ks
    else:
        fixture = FixtureSource(settings.env)

        async def factory(grade):
            return fixture
    return DiagnosisService(pool, ingest, factory, presenter, params or DiagnosisParams())


SYNTHETIC_DB = re.compile(r"^starbuddy_(dev|test)(_[a-z0-9_]+)?$")  # 与合成种子（db/seeds/dev/*.sql）的库名核对一致


async def _check_diagnosis_env(pool) -> None:
    """合成题面除 LEARNING_ENV=test 外再核对库名（安全审查 S6）：只能连一次性开发 / 测试库；
    诊断作答走 v1 写入路径（候选契约 1.0.0），签字前只在测试里开（STARBUDDY_ALLOW_CANDIDATE_CONTRACT=1）。"""
    db = await pool.fetchval("SELECT current_database()")
    if not SYNTHETIC_DB.fullmatch(db or ""):
        raise RuntimeError("合成诊断题面只能连开发库或测试库（库名 starbuddy_dev* 或 starbuddy_test*）")
    try:
        plugin_contract("1.0.0")
    except UnsupportedContractVersion:
        raise RuntimeError("诊断作答走 v1 写入路径：签字前需 STARBUDDY_ALLOW_CANDIDATE_CONTRACT=1（只在测试里）") from None


def create_app(
    settings: Settings,
    *,
    pool: asyncpg.Pool | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    model_router: ModelRouter | None = None,
    diagnosis_params: DiagnosisParams | None = None,
) -> FastAPI:
    """pool、transport、model_router、diagnosis_params 供测试注入；不传时按配置创建。"""
    state: dict = {"router": model_router}

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        # 启动时就加载契约：共享库或契约文件缺失时立即失败，而不是等到第一个请求（安全审查 S6）
        plugin_contract()
        internal_contract()
        internal_contract("1.0.0")  # v1 信封契约（安全审查建议 9：文件缺失时启动即失败）
        own_pool = pool is None
        p = pool or await asyncpg.create_pool(settings.database_url, min_size=1, max_size=10)
        # 迁移 0016 必须先于本版本执行：v0、v1 写入都要查封存表（安全审查必须改 1）
        if not await p.fetchval("SELECT to_regclass('platform.replay_seal') IS NOT NULL"):
            if own_pool:
                await p.close()
            raise RuntimeError("platform.replay_seal is missing: run migration 0016 before starting this version")
        # trust_env=False：不读 HTTP(S)_PROXY 等环境变量，含学生作答的请求体不会被悄悄送进代理（安全审查 S1）
        client = httpx.AsyncClient(transport=transport, trust_env=False, follow_redirects=False)
        state["service"] = IngestService(settings, p, client)
        if settings.diagnosis_presenter == "synthetic":
            try:
                await _check_diagnosis_env(p)
            except RuntimeError:
                await client.aclose()
                if own_pool:
                    await p.close()
                raise
            state["diagnosis"] = _diagnosis(settings, p, client, state["service"], diagnosis_params)
        # 有路由客户端时才建模型路由：环境从 LEARNING_ENV 读，配置不合规（出境、未批准供应商等）即拒绝启动
        own_router = state["router"] is None and bool(settings.route_clients)
        if own_router:
            state["router"] = ModelRouter.from_config(load_for_deployment())
        try:
            yield
        finally:
            if own_router:
                await state["router"].aclose()
            await client.aclose()
            if own_pool:
                await p.close()

    app = FastAPI(title="StarBuddy learning service (internal)", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    async def handle(method: str, request: Request, version: str = "0.9.0") -> JSONResponse:
        started = time.monotonic()
        rid: str | None = None
        record: dict = {"method": method}
        try:
            if not _bearer_ok(request.headers.get("authorization"), settings.service_credential):
                raise IngestError(401, "UNAUTHENTICATED", "missing or invalid service token")
            raw = await _read_limited(request)
            try:
                body = strict_json(raw)
            except ValueError:
                raise IngestError(400, "INVALID_REQUEST", "request body is not valid JSON") from None
            inner = body.get("request", body) if isinstance(body, dict) and version != "0.9.0" else body  # v1 是信封
            candidate = inner.get("context", {}).get("request_id") if isinstance(inner, dict) and isinstance(inner.get("context"), dict) else None
            rid = candidate if isinstance(candidate, str) and UUID_RE.fullmatch(candidate) else None
            summary, replayed = await state["service"].ingest(method, body, version)
            record.update(status=200, replayed=replayed)
            return JSONResponse(summary, headers={"idempotent-replayed": "true" if replayed else "false"})
        except IngestError as e:
            record.update(status=e.status, code=e.code)
            return _problem(e, rid)
        except Exception as e:  # 不记异常消息：可能带出数据
            record.update(status=500, code="INTERNAL", error_class=type(e).__name__[:48])
            # 5xx 一律是结果未知（评审 N18）：提交阶段出错时事务可能已提交，BFF 必须同 key 重试
            return _problem(IngestError(500, "INTERNAL", "internal error", retryable=True), rid)
        finally:
            if rid:
                record["request_id"] = rid
            record["duration_ms"] = round((time.monotonic() - started) * 1000)
            log.info(json.dumps(record, ensure_ascii=False))

    diagnosis_api.register(app, lambda r: _bearer_ok(r.headers.get("authorization"), settings.service_credential), state)

    @app.post("/internal/v0/grade")
    async def grade(request: Request) -> JSONResponse:
        return await handle("grade", request)

    @app.post("/internal/v0/diagnose")
    async def diagnose(request: Request) -> JSONResponse:
        return await handle("diagnose", request)

    @app.post("/internal/v1/grade")
    async def grade_v1(request: Request) -> JSONResponse:
        return await handle("grade", request, "1.0.0")

    @app.post("/internal/v1/diagnose")
    async def diagnose_v1(request: Request) -> JSONResponse:
        return await handle("diagnose", request, "1.0.0")

    @app.post("/internal/v1/model/route")
    async def model_route(request: Request) -> JSONResponse:
        """各科适配器调模型的唯一入口（learning-internal.v1）：令牌绑定学科；日志不记 messages 与 output。"""
        started = time.monotonic()
        record: dict = {"method": "model_route"}
        try:
            header = request.headers.get("authorization")
            # 全部比完再取结果：不按命中顺序提前返回（安全审查建议 1）
            hits = [s for s, tok in sorted(settings.route_clients.items()) if _bearer_ok(header, tok)]
            subject = hits[0] if len(hits) == 1 else None
            if subject is None:
                raise RouteApiError(401, "UNAUTHENTICATED", "missing or invalid route client token")
            record["subject"] = subject
            if state["router"] is None:
                raise RouteApiError(503, "ROUTE_UNAVAILABLE", "model router is not configured", retryable=True)
            declared = request.headers.get("content-length")
            if declared and declared.isdigit() and int(declared) > MAX_ROUTE_BODY:
                raise RouteApiError(413, "INVALID_REQUEST", "request body too large")
            raw = b""
            async for chunk in request.stream():
                raw += chunk
                if len(raw) > MAX_ROUTE_BODY:
                    raise RouteApiError(413, "INVALID_REQUEST", "request body too large")
            try:
                body = strict_json(raw)
            except ValueError:
                raise RouteApiError(400, "INVALID_REQUEST", "request body is not valid JSON") from None
            req = parse_request(body, subject)
            record.update(task=req.task.value, data_class=req.data_class.value)
            out = await route_call(state["router"], req)
            record["status"] = 200
            return JSONResponse(out)
        except RouteApiError as e:
            record.update(status=e.status, code=e.code)
            return JSONResponse(e.problem(), status_code=e.status, media_type="application/problem+json")
        except Exception as e:  # noqa: BLE001 不记异常消息：可能带出请求内容
            record.update(status=500, code="INTERNAL", error_class=type(e).__name__[:48])
            return JSONResponse(RouteApiError(500, "INTERNAL", "internal error", retryable=True).problem(), status_code=500,
                                media_type="application/problem+json")
        finally:
            record["duration_ms"] = round((time.monotonic() - started) * 1000)
            log.info(json.dumps(record, ensure_ascii=False))

    @app.get("/internal/v1/replay/{method}/{key}")
    async def replay(method: str, key: str, request: Request) -> JSONResponse:
        """只查询、不执行（learning-internal.v1，评审 N17）。不存在、参数不匹配都返回同样的 404，不透露 key 是否存在。"""
        started = time.monotonic()
        record: dict = {"method": f"replay:{method}" if method in ("grade", "diagnose") else "replay:invalid"}
        try:
            if not _bearer_ok(request.headers.get("authorization"), settings.service_credential):
                raise IngestError(401, "UNAUTHENTICATED", "missing or invalid service token")
            student = request.query_params.get("student_ref", "")
            usage = request.query_params.get("usage_id", "")
            if (method not in ("grade", "diagnose") or not UUID4_RE.fullmatch(key)
                    or not UUID_RE.fullmatch(student) or not UUID_RE.fullmatch(usage)):
                raise IngestError(400, "INVALID_REQUEST", "method, idempotency_key, student_ref and usage_id are required")
            r = await state["service"].replay(method, key, student.lower(), usage.lower())
            if r.kind == "found":
                record.update(status=200, replayed=True)
                return JSONResponse(r.summary, headers={"idempotent-replayed": "true"})
            if r.kind == "busy":
                raise IngestError(409, "BUSY", "another request with this key is still running", retryable=True)
            raise IngestError(404, "REPLAY_NOT_FOUND", "no recorded result for this key")
        except IngestError as e:
            record.update(status=e.status, code=e.code)
            return _problem(e, None)
        except Exception as e:  # noqa: BLE001
            record.update(status=500, code="INTERNAL", error_class=type(e).__name__[:48])
            return _problem(IngestError(500, "INTERNAL", "internal error", retryable=True), None)
        finally:
            record["duration_ms"] = round((time.monotonic() - started) * 1000)
            log.info(json.dumps(record, ensure_ascii=False))

    return app
