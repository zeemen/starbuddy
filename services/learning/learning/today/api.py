"""今日计划跨科编排的内部接口（只供 BFF 调用；接口定义 services/learning/openapi/today-internal.v0.yaml，不进 packages/contracts）。

鉴权、请求体限制与日志同复测与再练（retest/api.py）：Bearer 服务间令牌（只认 BFF 的 LEARNING_SERVICE_TOKEN），64 KB 上限，
拒绝 NaN 与重复键，未知键一律 400。日志只记接口名、状态码、错误码、耗时，不记请求体与学生 ID。
没配 LEARNING_TODAY_CONFIG（或未签字配置出现在 test / dev 以外）、没开 LEARNING_INTERNAL_V1 时一律 501 TODAY_UNAVAILABLE。
"""
from __future__ import annotations

import json
import logging
import time
from typing import Callable

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from ..contract import internal_v1_enabled
from ..diagnosis.api import _body
from ..errors import IngestError

log = logging.getLogger("starbuddy.learning.today")


def register(app: FastAPI, authorized: Callable[[Request], bool], state: dict) -> None:
    def svc():
        t = state.get("today")
        # learning-internal v1 未签字：编排要调 /plan 与再练、复测（都在 v1 之后），没开 LEARNING_INTERNAL_V1=1 时与没配一样 501
        if t is None or not internal_v1_enabled():
            raise IngestError(501, "TODAY_UNAVAILABLE", "today orchestration is not configured on this service")
        return t

    @app.post("/internal/v1/today")
    async def today(request: Request) -> JSONResponse:
        started = time.monotonic()
        record: dict = {"method": "today"}
        try:
            if not authorized(request):
                raise IngestError(401, "UNAUTHENTICATED", "missing or invalid service token")
            t = svc()
            out = await t.today(await _body(request))
            record["status"] = 200
            return JSONResponse(out)
        except IngestError as e:
            record.update(status=e.status, code=e.code)
            return JSONResponse(e.problem(), status_code=e.status, media_type="application/problem+json",
                                headers={"retry-after": "2"} if e.retryable else None)
        except Exception as e:  # noqa: BLE001 不记异常消息：可能带出数据
            record.update(status=500, code="INTERNAL", error_class=type(e).__name__[:48])
            return JSONResponse(IngestError(500, "INTERNAL", "internal error", retryable=True).problem(), status_code=500,
                                media_type="application/problem+json")
        finally:
            record["duration_ms"] = round((time.monotonic() - started) * 1000)
            log.info(json.dumps(record, ensure_ascii=False))
