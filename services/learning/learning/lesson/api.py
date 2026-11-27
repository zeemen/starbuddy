"""讲评课的内部接口（只供 BFF 调用；接口定义 services/learning/openapi/lesson-internal.v0.yaml）。

鉴权与诊断、再练同一做法：只认 BFF 的会话令牌（LEARNING_SESSION_TOKEN，PLT-24），sha256 后常量时间比较。请求体限 64 KB，
JSON 拒绝 NaN 与重复键，未知键一律 400。日志只记接口名、状态码、错误码、耗时，不记请求体（提问、票据）、讲解与回答文字、学生 ID。
学习服务不认学生身份：BFF 负责“只认学生本人”，这里只核对会话与票据属于请求里的 student_ref。
没有配置讲评课（LEARNING_LESSON_TICKET_SECRET 未设）时两个接口一律 501 LESSON_UNAVAILABLE。
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Awaitable, Callable

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .. import processing
from ..diagnosis.api import _body, _keys
from ..errors import IngestError

log = logging.getLogger("starbuddy.learning.lesson")


def register(app: FastAPI, authorized: Callable[[Request], bool], state: dict) -> None:
    # 库层处理判据（LT-06）：打开与提问都先问库（退出、删除、没有有效同意 → 404 STUDENT_NOT_FOUND）
    allowed = processing.gate(state)

    def svc():
        s = state.get("lesson")
        if s is None:
            raise IngestError(501, "LESSON_UNAVAILABLE", "lessons are not configured on this service")
        return s

    async def run(name: str, request: Request, fn: Callable[[], Awaitable[tuple[int, dict, dict | None]]]) -> JSONResponse:
        started = time.monotonic()
        record: dict = {"method": f"lesson:{name}"}
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
        except Exception as e:  # noqa: BLE001 不记异常消息：可能带出讲解或提问
            record.update(status=500, code="INTERNAL", error_class=type(e).__name__[:48])
            return JSONResponse(IngestError(500, "INTERNAL", "internal error", retryable=True).problem(), status_code=500,
                                media_type="application/problem+json")
        finally:
            record["duration_ms"] = round((time.monotonic() - started) * 1000)
            log.info(json.dumps(record, ensure_ascii=False))

    def replay(flag: bool) -> dict:
        return {"idempotent-replayed": "true" if flag else "false"}

    @app.post("/internal/v1/lesson/open")
    async def open_lesson(request: Request) -> JSONResponse:
        async def fn():
            b = await _body(request)
            _keys(b, {"student_ref", "subject", "lesson_ref", "skill_node", "usage_id", "idempotency_key"}, {"item_ref"})
            ref: Any = b["lesson_ref"]
            if not (all(isinstance(b[k], str) for k in b if k != "lesson_ref") and isinstance(ref, dict)
                    and set(ref) == {"lesson_id", "lesson_version"} and all(isinstance(v, str) for v in ref.values())):
                raise IngestError(400, "INVALID_REQUEST", "bad field types")
            s = svc()
            await allowed(b["student_ref"])
            out, replayed = await s.open(student=b["student_ref"].lower(), subject=b["subject"], lesson_id=ref["lesson_id"],
                                         lesson_version=ref["lesson_version"], skill_node=b["skill_node"], item_ref=b.get("item_ref"),
                                         usage_id=b["usage_id"].lower(), key=b["idempotency_key"])
            return (200 if replayed else 201), out, replay(replayed)
        return await run("open", request, fn)

    @app.post("/internal/v1/lesson/ask")
    async def ask(request: Request) -> JSONResponse:
        async def fn():
            b = await _body(request)
            _keys(b, {"student_ref", "ticket", "question", "idempotency_key"})
            if not (isinstance(b["student_ref"], str) and isinstance(b["ticket"], str) and isinstance(b["idempotency_key"], str)):
                raise IngestError(400, "INVALID_REQUEST", "bad field types")
            s = svc()
            await allowed(b["student_ref"])
            out, replayed = await s.ask(student=b["student_ref"].lower(), ticket=b["ticket"], question=b["question"],
                                        ask_key=b["idempotency_key"])
            return 200, out, replay(replayed)
        return await run("ask", request, fn)
