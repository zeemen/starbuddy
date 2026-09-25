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

from .adapter_client import strict_json
from .config import Settings
from .errors import IngestError
from .service import IngestService

log = logging.getLogger("starbuddy.learning")
UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)
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
        # trust_env=False：不读 HTTP(S)_PROXY 等环境变量，含学生作答的请求体不会被悄悄送进代理（安全审查 S1）
        client = httpx.AsyncClient(transport=transport, trust_env=False, follow_redirects=False)
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
            raw = await _read_limited(request)
            try:
                body = strict_json(raw)
            except ValueError:
                raise IngestError(400, "INVALID_REQUEST", "request body is not valid JSON") from None
            candidate = body.get("context", {}).get("request_id") if isinstance(body, dict) else None
            rid = candidate if isinstance(candidate, str) and UUID_RE.fullmatch(candidate) else None
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
