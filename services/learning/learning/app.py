"""FastAPI 应用：两个内部接口，只供 BFF 调用（spec 范围第 2 条）。

接口定义：packages/contracts/openapi/learning-internal.v0.yaml。
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

from .config import Settings
from .errors import IngestError
from .service import IngestService

log = logging.getLogger("starbuddy.learning")
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)


def _bearer_ok(header: str | None, expected: str) -> bool:
    m = re.fullmatch(r"Bearer (.+)", header or "")
    if not m:
        return False
    a = hashlib.sha256(m.group(1).encode()).digest()
    b = hashlib.sha256(expected.encode()).digest()
    return hmac.compare_digest(a, b)


def _problem(e: IngestError, request_id: str | None) -> JSONResponse:
    return JSONResponse(e.problem(request_id), status_code=e.status, media_type="application/problem+json")


def create_app(
    settings: Settings,
    *,
    pool: asyncpg.Pool | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> FastAPI:
    """pool、transport 供测试注入；不传时按配置创建。"""
    state: dict = {}

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        own_pool = pool is None
        p = pool or await asyncpg.create_pool(settings.database_url, min_size=1, max_size=10)
        client = httpx.AsyncClient(transport=transport) if transport else httpx.AsyncClient()
        state["service"] = IngestService(settings, p, client)
        try:
            yield
        finally:
            await client.aclose()
            if own_pool:
                await p.close()

    app = FastAPI(title="StarBuddy learning service (internal)", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    async def handle(method: str, request: Request) -> JSONResponse:
        started = time.monotonic()
        rid: str | None = None
        record: dict = {"method": method}
        try:
            if not _bearer_ok(request.headers.get("authorization"), settings.service_credential):
                raise IngestError(401, "UNAUTHENTICATED", "missing or invalid service token")
            raw = await request.body()
            if len(raw) > 1024 * 1024:
                raise IngestError(413, "INVALID_REQUEST", "request body too large")
            try:
                body = json.loads(raw)
            except ValueError:
                raise IngestError(400, "INVALID_REQUEST", "request body is not valid JSON") from None
            candidate = body.get("context", {}).get("request_id") if isinstance(body, dict) else None
            rid = candidate if isinstance(candidate, str) and UUID_RE.match(candidate) else None
            summary, replayed = await state["service"].ingest(method, body)
            record.update(status=200, replayed=replayed)
            return JSONResponse(summary, headers={"idempotent-replayed": "true" if replayed else "false"})
        except IngestError as e:
            record.update(status=e.status, code=e.code)
            return _problem(e, rid)
        except Exception as e:  # 不记异常消息：可能带出数据
            record.update(status=500, code="INTERNAL", error_class=type(e).__name__[:48])
            return _problem(IngestError(500, "INTERNAL", "internal error"), rid)
        finally:
            if rid:
                record["request_id"] = rid
            record["duration_ms"] = round((time.monotonic() - started) * 1000)
            log.info(json.dumps(record, ensure_ascii=False))

    @app.post("/internal/v0/grade")
    async def grade(request: Request) -> JSONResponse:
        return await handle("grade", request)

    @app.post("/internal/v0/diagnose")
    async def diagnose(request: Request) -> JSONResponse:
        return await handle("diagnose", request)

    return app
