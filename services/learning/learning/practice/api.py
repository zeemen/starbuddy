"""错题再练的内部接口（只供 BFF 调用；接口定义 services/learning/openapi/practice-internal.v0.yaml）。

鉴权与诊断接口同一做法：Bearer 服务间令牌（只认 BFF 的 LEARNING_SERVICE_TOKEN；编排层、洞察服务、路由客户端令牌一律 401），
sha256 后常量时间比较。请求体限 64 KB，JSON 拒绝 NaN 与重复键，未知键一律 400（purpose、channel 由服务端写，请求里给了即 400）。
日志只记接口名、状态码、错误码、耗时，不记请求体（作答、票据）与学生 ID。学习服务不认学生身份：BFF 负责“只认学生本人”。
只查不执行的重放不另建：BFF 用既有的 GET /internal/v1/replay/grade/{key}（作答的 idempotency_key 就是判分请求的键）。
"""
from __future__ import annotations

import json
import logging
import time
from typing import Awaitable, Callable

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .. import processing
from ..diagnosis.api import _body, _keys
from ..errors import IngestError
from . import answer as answer_mod

log = logging.getLogger("starbuddy.learning.practice")


def register(app: FastAPI, authorized: Callable[[Request], bool], state: dict) -> None:
    # 库层处理判据（LT-06 决定记录（续）第 4 条）：三个接口都先问库（退出、删除、没有有效同意 → 404 STUDENT_NOT_FOUND）
    allowed = processing.gate(state)

    def svc():
        p = state.get("practice")
        if p is None:
            raise IngestError(501, "PRACTICE_UNAVAILABLE", "practice is not configured on this service")
        return p

    async def run(name: str, request: Request, fn: Callable[[], Awaitable[tuple[int, dict, dict | None]]]) -> JSONResponse:
        started = time.monotonic()
        record: dict = {"method": f"practice:{name}"}
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
            # 5xx 一律是结果未知（与 learning-internal v1 同一规则）：作答由 BFF 用同一个键重试或查重放
            return JSONResponse(IngestError(500, "INTERNAL", "internal error", retryable=True).problem(), status_code=500,
                                media_type="application/problem+json")
        finally:
            record["duration_ms"] = round((time.monotonic() - started) * 1000)
            log.info(json.dumps(record, ensure_ascii=False))

    def strings(b: dict, *names: str) -> None:
        if not all(isinstance(b[k], str) for k in names):
            raise IngestError(400, "INVALID_REQUEST", "bad field types")

    @app.post("/internal/v1/practice/queue")
    async def queue(request: Request) -> JSONResponse:
        async def fn():
            b = await _body(request)
            _keys(b, {"student_ref", "subject"})
            strings(b, "student_ref", "subject")
            await allowed(b["student_ref"])
            return 200, await svc().queue(b["student_ref"], b["subject"]), None
        return await run("queue", request, fn)

    @app.post("/internal/v1/practice/next")
    async def next_item(request: Request) -> JSONResponse:
        async def fn():
            b = await _body(request)
            _keys(b, {"student_ref", "subject", "entry_id"})
            strings(b, "student_ref", "subject", "entry_id")
            await allowed(b["student_ref"])
            return 200, await svc().next(b["student_ref"], b["subject"], b["entry_id"]), None
        return await run("next", request, fn)

    @app.post("/internal/v1/practice/answer")
    async def answer(request: Request) -> JSONResponse:
        async def fn():
            b = await _body(request)
            _keys(b, {"student_ref", "subject", "ticket", "answer", "idempotency_key", "usage_id"})
            strings(b, "student_ref", "subject", "ticket", "idempotency_key", "usage_id")
            await allowed(b["student_ref"])
            out, replayed = await answer_mod.answer(svc(), student=b["student_ref"], subject=b["subject"], ticket=b["ticket"],
                                                    answer=b["answer"], key=b["idempotency_key"], usage_id=b["usage_id"])
            return 200, out, {"idempotent-replayed": "true" if replayed else "false"}
        return await run("answer", request, fn)
