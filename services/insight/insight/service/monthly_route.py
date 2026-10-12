"""洞察服务内部接口：POST /internal/v1/monthly-plan/generate（PLT-14 第 2 步默认设计 2；接口定义 services/insight/openapi/monthly-plan-internal.v0.yaml，
放在服务目录、不进 packages/contracts，沿用学习服务今日计划、复测、再练内部接口的先例）。

- 鉴权：Bearer 服务间令牌，编排层（调度）或 BFF 的令牌（学生与家长没有令牌，401）。
  - BFF（家长打开页面且本月没有快照时，第 3 步接入）必须带 actor = {role: guardian, actor_ref: 家长账号}，按 insight-service 契约
    “调用方、受众与代表谁”的口径在库内经 insight.verify_actor（0044）再核为该学生的有效绑定家长；
  - 编排层（调度器 PLT-13）不带 actor，带了 400。
- 顺序：请求体 → actor 再核（BFF）→ 月份须是本月 → 已有本月快照先返回（status = exists，不写新行）→ 再查权益 → 生成落库；
  与 0054 insight.save_monthly_plan 的顺序一致。
- 不泄露存在性与套餐档位：没带 actor 或再核不通过、档案不存在或已删除、没有权益，一律同一个回答（404，CONTENT_VERSION_NOT_FOUND，正文相同）。
  其他错误：请求体不合格 400；月份不是本月 409 MONTH_NOT_CURRENT（与学生无关）；库里守卫拒收 409 PLAN_REJECTED（可重试）。
- 结果只回状态、快照 ID、月份、生成时间与输入摘要，不回快照内容：家长与学生只经 0054 的两个视图函数按受众读。
- 日志只记接口名、调用方、状态码、错误码、耗时；不记学生 ID、账号与请求体。不计量（自动月报不计次）。
"""
from __future__ import annotations

import json
import logging
import re
import time
from datetime import date
from typing import Any, Callable

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from ..store_monthly import MonthlyError, generate_monthly_plan
from .errors import ServiceError, internal, invalid, not_found, unauthenticated

log = logging.getLogger("starbuddy.insight.monthly")
PATH = "/internal/v1/monthly-plan/generate"
CALLERS = frozenset({"bff", "orchestrator"})
UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
MONTH = re.compile(r"^(20[2-9][0-9])-(0[1-9]|1[0-2])$")
ERRORS = {
    "unavailable": not_found,  # 无权、不存在、没有权益同一个回答
    "month_not_current": lambda: ServiceError(409, "MONTH_NOT_CURRENT", "only the current month can be generated"),
    "rejected": lambda: ServiceError(409, "PLAN_REJECTED", "the plan was rejected by the database checks", retryable=True),
}


def parse_body(body: Any, caller: str) -> tuple[str, date, dict | None]:
    want = {"student_id", "month", "actor"} if caller == "bff" else {"student_id", "month"}
    if not isinstance(body, dict) or set(body) - want or not {"student_id", "month"} <= set(body):
        raise invalid("request body must have student_id and month (and actor only from the BFF)")
    sid, month, actor = body["student_id"], body["month"], body.get("actor")
    if not isinstance(sid, str) or not UUID.fullmatch(sid):
        raise invalid("student_id must be a lowercase uuid")
    m = MONTH.fullmatch(month) if isinstance(month, str) else None
    if m is None:
        raise invalid("month must be YYYY-MM")
    if actor is not None and not (isinstance(actor, dict) and set(actor) == {"role", "actor_ref"} and isinstance(actor["actor_ref"], str)
                                  and UUID.fullmatch(actor["actor_ref"]) and isinstance(actor["role"], str)):
        raise invalid("actor must be {role, actor_ref}")
    return sid, date(int(m.group(1)), int(m.group(2)), 1), actor


def register(app: FastAPI, *, caller_of: Callable[[Request], str | None], read_body: Callable[[Request], Any], state: dict) -> None:
    @app.post(PATH)
    async def monthly_plan_generate(request: Request) -> JSONResponse:
        started = time.monotonic()
        record: dict[str, Any] = {"method": "monthly_plan.generate"}
        try:
            caller = caller_of(request)
            record["caller"] = caller
            if caller not in CALLERS:
                raise unauthenticated()
            student, month, actor = parse_body(await read_body(request), caller)
            m = state.get("monthly")
            if m is None:
                raise ServiceError(501, "METHOD_NOT_IMPLEMENTED", "monthly plan is not configured")
            # BFF 代表家长：没带 actor、角色不是家长或库内再核不通过，与不存在、没有权益同一个回答
            if caller == "bff" and (actor is None or actor.get("role") != "guardian"
                                    or not await state["verifier"].verify(actor, "guardian", student)):
                raise not_found()
            try:
                out = await generate_monthly_plan(state["store"], student, month, state["policy"], m["config"], m["templates"],
                                                  m["forbidden"], m["mastery"])
            except MonthlyError as e:
                raise ERRORS.get(e.kind, internal)() from None
            record["status"] = 200
            return JSONResponse({"status": out.status, "plan_id": out.plan_id, "month": out.month.strftime("%Y-%m"),
                                 "generated_at": str(out.generated_at), "inputs_digest": out.inputs_digest})
        except ServiceError as e:
            record.update(status=e.status, code=e.code)
            return JSONResponse(e.problem(), status_code=e.status, media_type="application/problem+json")
        except Exception as e:  # noqa: BLE001 不记异常消息：可能带出数据
            record.update(status=500, code="INTERNAL", error_class=type(e).__name__[:48])
            return JSONResponse(internal().problem(), status_code=500, media_type="application/problem+json")
        finally:
            record["duration_ms"] = round((time.monotonic() - started) * 1000)
            log.info(json.dumps(record, ensure_ascii=False))
