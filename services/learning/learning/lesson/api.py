"""讲评课的内部接口（只供 BFF 调用；接口定义 services/learning/openapi/lesson-internal.v0.yaml）。

鉴权与诊断、再练同一做法：只认 BFF 的会话令牌（LEARNING_SESSION_TOKEN，PLT-24），sha256 后常量时间比较。请求体限 64 KB，
JSON 拒绝 NaN 与重复键，未知键一律 400。日志只记接口名、状态码、错误码、耗时，不记请求体（提问、票据）、讲解与回答文字、学生 ID。
学习服务不认学生身份：BFF 负责“只认学生本人”，这里只核对会话与票据属于请求里的 student_ref。
没有配置讲评课（LEARNING_LESSON_TICKET_SECRET 未设）时三个接口一律 501 LESSON_UNAVAILABLE。
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
from .service import SUBJECTS, UUID_RE

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
            # 只收讲评课引用：节点与题由学习服务按讲评课目录补全（specs/lesson-catalog）；带了就是请求不合格（400）
            _keys(b, {"student_ref", "subject", "lesson_ref", "usage_id", "idempotency_key"})
            ref: Any = b["lesson_ref"]
            if not (all(isinstance(b[k], str) for k in b if k != "lesson_ref") and isinstance(ref, dict)
                    and set(ref) == {"lesson_id", "lesson_version"} and all(isinstance(v, str) for v in ref.values())):
                raise IngestError(400, "INVALID_REQUEST", "bad field types")
            s = svc()
            await allowed(b["student_ref"])
            out, replayed = await s.open(student=b["student_ref"].lower(), subject=b["subject"], lesson_id=ref["lesson_id"],
                                         lesson_version=ref["lesson_version"], usage_id=b["usage_id"].lower(), key=b["idempotency_key"])
            return (200 if replayed else 201), out, replay(replayed)
        return await run("open", request, fn)

    @app.post("/internal/v1/lesson/catalog")
    async def catalog(request: Request) -> JSONResponse:
        """讲评课目录（specs/lesson-catalog PR B）：只给编号、版本、标题与步数；节点与题不出学习服务。不计量、不建会话。"""
        async def fn():
            b = await _body(request)
            _keys(b, {"student_ref", "subject"})
            if not (isinstance(b["student_ref"], str) and isinstance(b["subject"], str)):
                raise IngestError(400, "INVALID_REQUEST", "bad field types")
            s = svc()
            if not UUID_RE.fullmatch(b["student_ref"].lower()):
                raise IngestError(400, "INVALID_REQUEST", "bad field values")
            if b["subject"] not in SUBJECTS:
                raise IngestError(400, "SUBJECT_NOT_SUPPORTED", "lessons are only available for aya in the pilot")
            await allowed(b["student_ref"])
            entries = await s.catalog.entries(b["subject"])
            return 200, {"subject": b["subject"], "lessons": [e.public() for e in entries]}, None
        return await run("catalog", request, fn)

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
