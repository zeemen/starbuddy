"""复测与验证题的内部接口（只供 BFF 调用；接口定义 services/learning/openapi/retest-internal.v0.yaml，不进 packages/contracts）。

鉴权、请求体限制与日志同错题再练（practice/api.py）：Bearer 服务间令牌（只认 BFF 的 LEARNING_SERVICE_TOKEN），64 KB 上限，
拒绝 NaN 与重复键，未知键一律 400（purpose、channel、hint_level_used 由服务端写，请求里给了即 400）。日志不记请求体与学生 ID。
只查不执行的重放：结果未知时 BFF 用既有的 GET /internal/v1/replay/grade/{key}；另有两个只读接口（审查意见 2）：
check（计次前预检条目仍到期、票据未用）与 replay（宵禁时按票据与键只找回原结果，不执行、不封存）。
"""
from __future__ import annotations

import json
import logging
import time
from typing import Awaitable, Callable

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from ..diagnosis.api import _body, _keys
from ..errors import IngestError

log = logging.getLogger("starbuddy.learning.retest")


def register(app: FastAPI, authorized: Callable[[Request], bool], state: dict) -> None:
    def svc():
        r = state.get("retest")
        if r is None:
            raise IngestError(501, "RETEST_UNAVAILABLE", "retest is not configured on this service")
        return r

    async def run(name: str, request: Request, fn: Callable[[], Awaitable[tuple[int, dict, dict | None]]]) -> JSONResponse:
        started = time.monotonic()
        record: dict = {"method": f"retest:{name}"}
        try:
            if not authorized(request):
                raise IngestError(401, "UNAUTHENTICATED", "missing or invalid service token")
            status, out, headers = await fn()
            record["status"] = status
            return JSONResponse(out, status_code=status, headers=headers)
        except IngestError as e:
            record.update(status=e.status, code=e.code)
            return JSONResponse(e.problem(), status_code=e.status, media_type="application/problem+json",
                                headers={"retry-after": "2"} if e.retryable else None)
        except Exception as e:  # noqa: BLE001 不记异常消息：可能带出作答
            record.update(status=500, code="INTERNAL", error_class=type(e).__name__[:48])
            return JSONResponse(IngestError(500, "INTERNAL", "internal error", retryable=True).problem(), status_code=500,
                                media_type="application/problem+json")
        finally:
            record["duration_ms"] = round((time.monotonic() - started) * 1000)
            log.info(json.dumps(record, ensure_ascii=False))

    def strings(b: dict, *names: str) -> None:
        if not all(isinstance(b[k], str) for k in names):
            raise IngestError(400, "INVALID_REQUEST", "bad field types")

    @app.post("/internal/v1/retest/queue")
    async def queue(request: Request) -> JSONResponse:
        async def fn():
            b = await _body(request)
            _keys(b, {"student_ref", "subject"})
            strings(b, "student_ref", "subject")
            return 200, await svc().queue(b["student_ref"], b["subject"]), None
        return await run("queue", request, fn)

    @app.post("/internal/v1/retest/next")
    async def next_item(request: Request) -> JSONResponse:
        async def fn():
            b = await _body(request)
            _keys(b, {"student_ref", "subject", "entry_id"})
            strings(b, "student_ref", "subject", "entry_id")
            return 200, await svc().next(b["student_ref"], b["subject"], b["entry_id"]), None
        return await run("next", request, fn)

    @app.post("/internal/v1/retest/answer")
    async def answer(request: Request) -> JSONResponse:
        async def fn():
            b = await _body(request)
            _keys(b, {"student_ref", "subject", "ticket", "answer", "idempotency_key", "usage_id"})
            strings(b, "student_ref", "subject", "ticket", "idempotency_key", "usage_id")
            out, replayed = await svc().answer(student=b["student_ref"], subject=b["subject"], ticket=b["ticket"], answer=b["answer"],
                                               key=b["idempotency_key"], usage_id=b["usage_id"])
            return 200, out, {"idempotent-replayed": "true" if replayed else "false"}
        return await run("answer", request, fn)

    @app.post("/internal/v1/retest/check")
    async def check(request: Request) -> JSONResponse:
        async def fn():
            b = await _body(request)
            _keys(b, {"student_ref", "subject", "ticket"})
            strings(b, "student_ref", "subject", "ticket")
            return 200, await svc().check(student=b["student_ref"], subject=b["subject"], ticket=b["ticket"]), None
        return await run("check", request, fn)

    @app.post("/internal/v1/retest/replay")
    async def replay(request: Request) -> JSONResponse:
        async def fn():
            b = await _body(request)
            _keys(b, {"student_ref", "subject", "ticket", "idempotency_key", "usage_id"})
            strings(b, "student_ref", "subject", "ticket", "idempotency_key", "usage_id")
            out = await svc().replay(student=b["student_ref"], subject=b["subject"], ticket=b["ticket"], key=b["idempotency_key"],
                                     usage_id=b["usage_id"])
            return 200, out, {"idempotent-replayed": "true"}
        return await run("replay", request, fn)
