"""诊断会话的内部接口（只供 BFF 调用；接口定义 services/learning/openapi/diagnosis-internal.v0.yaml）。

鉴权与既有内部接口同一做法：Bearer 服务间令牌，sha256 后常量时间比较。请求体限 64 KB，JSON 拒绝 NaN 与重复键，
未知键一律 400。日志只记接口名、状态码、错误码、耗时，不记请求体（作答、书写原文）与学生 ID。
学习服务不认学生身份：BFF 负责“只认学生本人”，这里只核对会话属于请求里的 student_ref（不属于与不存在同样 404）。
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Awaitable, Callable

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .. import processing
from ..errors import IngestError

log = logging.getLogger("starbuddy.learning.diagnosis")
MAX_BODY = 64 * 1024


def _reject_constant(name: str) -> Any:
    raise ValueError(name)


def _no_dup(pairs: list[tuple[str, Any]]) -> dict:
    out: dict = {}
    for k, v in pairs:
        if k in out:
            raise ValueError("duplicate key")
        out[k] = v
    return out


async def _body(request: Request) -> dict:
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > MAX_BODY:
        raise IngestError(413, "INVALID_REQUEST", "request body too large")
    raw, size = [], 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_BODY:
            raise IngestError(413, "INVALID_REQUEST", "request body too large")
        raw.append(chunk)
    try:
        v = json.loads(b"".join(raw), parse_constant=_reject_constant, object_pairs_hook=_no_dup)
    except ValueError:
        raise IngestError(400, "INVALID_REQUEST", "request body is not valid JSON") from None
    if not isinstance(v, dict):
        raise IngestError(400, "INVALID_REQUEST", "request body must be an object")
    return v


def _keys(body: dict, required: set[str], optional: set[str] = frozenset()) -> None:
    if not required <= set(body) or set(body) - required - optional:
        raise IngestError(400, "INVALID_REQUEST", "unexpected or missing fields")


def _int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def register(app: FastAPI, authorized: Callable[[Request], bool], state: dict) -> None:
    # 库层处理判据（LT-06 决定记录（续）第 4 条）：开始、探针、作答、暂停、放弃都先问库（退出、删除、没有有效同意 → 404 STUDENT_NOT_FOUND）；
    # 读当前题（GET）不写也不调模型，照常
    allowed = processing.gate(state)

    def svc():
        d = state.get("diagnosis")
        if d is None:
            raise IngestError(501, "DIAGNOSIS_UNAVAILABLE", "adaptive diagnosis is not configured on this service")
        return d

    async def run(name: str, request: Request, fn: Callable[[], Awaitable[tuple[int, dict, dict | None]]]) -> JSONResponse:
        started = time.monotonic()
        record: dict = {"method": f"diagnosis:{name}"}
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

    @app.post("/internal/v1/diagnosis/sessions")
    async def start(request: Request) -> JSONResponse:
        async def fn():
            b = await _body(request)
            _keys(b, {"student_ref", "subject", "usage_id", "idempotency_key"})
            if not all(isinstance(b[k], str) for k in b):
                raise IngestError(400, "INVALID_REQUEST", "fields must be strings")
            await allowed(b["student_ref"])
            r = await svc().start(b["student_ref"].lower(), b["subject"], b["usage_id"].lower(), b["idempotency_key"])
            out = {"session_id": r.session_id, "usage_id": r.usage_id, "status": r.status, "created": r.created}
            return (201 if r.created else 200), out, {"idempotent-replayed": "true" if r.replayed else "false"}
        return await run("start", request, fn)

    @app.get("/internal/v1/diagnosis/sessions/{session_id}")
    async def current(session_id: str, request: Request) -> JSONResponse:
        async def fn():
            # 学生 ID 走请求头，不进 URL（访问日志、代理日志里不留学生 ID；契约审查建议 9）
            if "student_ref" in request.query_params:
                raise IngestError(400, "INVALID_REQUEST", "student_ref goes in the x-student-ref header, not the URL")
            student = request.headers.get("x-student-ref", "").lower()
            return 200, await svc().current(student, session_id.lower()), None
        return await run("current", request, fn)

    @app.post("/internal/v1/diagnosis/sessions/{session_id}/probe")
    async def probe(session_id: str, request: Request) -> JSONResponse:
        async def fn():
            b = await _body(request)
            # probe_id 与 probe_key 二选一：BFF 写探针时遇到“同一步已有探针”（同一派生幂等键、内容不同）就按键找回那一条（S3）
            _keys(b, {"student_ref", "seq"}, {"probe_id", "probe_key"})
            ref = [k for k in ("probe_id", "probe_key") if k in b]
            if not (isinstance(b["student_ref"], str) and _int(b["seq"]) and len(ref) == 1 and isinstance(b[ref[0]], str)):
                raise IngestError(400, "INVALID_REQUEST", "bad field types")
            await allowed(b["student_ref"])
            out = await svc().probe(b["student_ref"].lower(), session_id.lower(), b["seq"], b.get("probe_id", "").lower() or None,
                                    b.get("probe_key", "").lower() or None)
            return 200, out, {"idempotent-replayed": "true" if out.pop("replayed") else "false"}
        return await run("probe", request, fn)

    @app.post("/internal/v1/diagnosis/sessions/{session_id}/answer")
    async def answer(session_id: str, request: Request) -> JSONResponse:
        async def fn():
            b = await _body(request)
            _keys(b, {"student_ref", "seq", "answer", "elapsed_sec", "idempotency_key"}, {"work"})
            if not (isinstance(b["student_ref"], str) and _int(b["seq"])):
                raise IngestError(400, "INVALID_REQUEST", "bad field types")
            await allowed(b["student_ref"])
            out = await svc().answer(b["student_ref"].lower(), session_id.lower(), b["seq"], b["answer"], b.get("work"),
                                     b["elapsed_sec"], b["idempotency_key"])
            return 200, out, {"idempotent-replayed": "true" if out.pop("replayed") else "false"}
        return await run("answer", request, fn)

    @app.post("/internal/v1/diagnosis/sessions/{session_id}/pause")
    async def pause(session_id: str, request: Request) -> JSONResponse:
        async def fn():
            b = await _body(request)
            _keys(b, {"student_ref"}, {"seq", "step_elapsed_sec"})
            if not isinstance(b["student_ref"], str) or ("seq" in b and not _int(b["seq"])):
                raise IngestError(400, "INVALID_REQUEST", "bad field types")
            await allowed(b["student_ref"])
            out = await svc().pause(b["student_ref"].lower(), session_id.lower(), b.get("seq"), b.get("step_elapsed_sec"))
            return 200, out, {"idempotent-replayed": "true" if out.pop("replayed") else "false"}
        return await run("pause", request, fn)

    @app.post("/internal/v1/diagnosis/sessions/{session_id}/abandon")
    async def abandon(session_id: str, request: Request) -> JSONResponse:
        async def fn():
            b = await _body(request)
            _keys(b, {"student_ref"})
            if not isinstance(b["student_ref"], str):
                raise IngestError(400, "INVALID_REQUEST", "bad field types")
            await allowed(b["student_ref"])
            out = await svc().abandon(b["student_ref"].lower(), session_id.lower())
            return 200, out, {"idempotent-replayed": "true" if out.pop("replayed") else "false"}
        return await run("abandon", request, fn)
