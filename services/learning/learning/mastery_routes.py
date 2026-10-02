"""learning-internal 1.1.0 的四个接口（KGE-04 第二步 PR 3，specs/kge-04-mastery-step2/spec.md 第 3 节）：

- POST /internal/v1/mastery/get、/mastery/explain：只给编排层（编排层专用令牌 LEARNING_ORCHESTRATOR_CLIENT_TOKEN，BFF 与洞察服务
  令牌一律 401；请求体 context.caller 必须与令牌身份一致，即 orchestrator，否则 403 CALLER_MISMATCH）；请求与响应按 knowledge-service v0.9 的同名 schema 校验，响应不合契约回 500（不把不合契约的数据交出去）。
- POST /internal/v1/verification：只接受洞察服务令牌（LEARNING_INSIGHT_CLIENT_TOKEN，与 BFF 服务令牌不能互换）。
  学习服务没有读 insight.dispute 的权限（0032 未授），不能由异议记录反查学生；学生由洞察服务从异议记录取出后附带，
  BFF 与客户端没有途径调用本接口（lessons PR 3 待人确认 P）。只建 verify_self_report 任务、不改掌握；响应不含题目。
- POST /internal/v1/verification/cancel：只接受洞察服务令牌；洞察服务写异议的事务在建任务之后回滚时的补偿：按任务 ID + 学生撤销
  open 的 verify_self_report 任务，幂等，不改掌握；不存在或不属于该学生同一个 404。

- POST /internal/v1/profile-page/ensure（PR 4）：惰性编译学情档案页。编排层令牌（trigger 必须是 session_start）或洞察服务令牌
  （trigger 必须是 weekly，周报生成前调），不符 403 CALLER_MISMATCH；BFF 令牌 401。响应只有结果编码、检查状态与时间，没有判断与文字。
- POST /internal/v1/profile-page/read（PR 4）：Agent 完整版（读取时按判断现状重核后渲染，含待办引用）；只认编排层令牌。
  学生、家长视图不走这里（BFF 经 platform.profile_page_statement_v 读引用、经洞察服务 insight.explain 取文字）。
  没有档案页配置或该科不在配置里：409 PROFILE_UNAVAILABLE。

鉴权同既有内部接口（Bearer，sha256 后常量时间比较）；请求体限 64 KB，JSON 拒绝 NaN / Infinity 与重复键。
日志只记接口名、状态码、错误码、耗时与 request_id（UUID 形状才记）；不记学生 ID、节点、证据与请求体。
"""
from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .contract import internal_contract, knowledge_contract
from .diagnosis.api import _body
from .errors import IngestError
from .mastery import api as M
from .mastery.profile import COMPILER_VERSION, ProfileUnavailable
from .mastery.recompute import MasteryUnavailable, cancel_verification, schedule_verification

log = logging.getLogger("starbuddy.learning")
UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)
INTERNAL = "1.0.0"  # internal_contract 的键：/internal/v1 这一组路径（文件 info.version 已是 1.1.0）；键名是插件契约版本，不随本文件升版，
                    # 改名牵涉 learning.contract 与各服务测试里的 internal_contract("1.0.0")，影响面不小，只加注释


def _problem(e: IngestError, rid: str | None) -> JSONResponse:
    return JSONResponse(e.problem(rid), status_code=e.status, media_type="application/problem+json")


def register(app: FastAPI, *, orchestrator_ok: Callable[[Request], bool], insight_ok: Callable[[Request], bool], state: dict,
             clock: Callable[[], datetime] | None = None) -> None:
    """state["plan"] = (pool, client)；state["mastery_configs"]：已校验的掌握配置（学科 → MasteryConfig）；
    state["mastery"]：MasteryEngine 或 None（没有配置任何学科）。"""
    now = clock or (lambda: datetime.now(timezone.utc))

    async def run(name: str, request: Request, authorized: Callable[[Request], bool],
                  fn: Callable[[dict], Awaitable[dict]], rid_of: Callable[[dict], Any]) -> JSONResponse:
        started = time.monotonic()
        rid: str | None = None
        record: dict = {"method": name}
        try:
            if not authorized(request):
                raise IngestError(401, "UNAUTHENTICATED", "missing or invalid service token")
            body = await _body(request)
            candidate = rid_of(body)
            rid = candidate if isinstance(candidate, str) and UUID_RE.fullmatch(candidate) else None
            out = await fn(body)
            record["status"] = 200
            return JSONResponse(out)
        except IngestError as e:
            record.update(status=e.status, code=e.code)
            return _problem(e, rid)
        except Exception as e:  # noqa: BLE001 不记异常消息：可能带出数据
            record.update(status=500, code="INTERNAL", error_class=type(e).__name__[:48])
            return _problem(IngestError(500, "INTERNAL", "internal error", retryable=True), rid)
        finally:
            if rid:
                record["request_id"] = rid
            record["duration_ms"] = round((time.monotonic() - started) * 1000)
            log.info(json.dumps(record, ensure_ascii=False))

    def ctx_rid(body: dict) -> Any:
        ctx = body.get("context")
        return ctx.get("request_id") if isinstance(ctx, dict) else None

    def check_request(schema: str, body: dict) -> None:
        if knowledge_contract().validate(schema, body):
            raise IngestError(400, "INVALID_REQUEST", "request does not match the contract")
        if body["context"]["caller"] != "orchestrator":
            # 编排层令牌：请求体里的调用方必须与令牌身份一致（知识服务契约“caller 由令牌决定”）
            raise IngestError(403, "CALLER_MISMATCH", "context.caller does not match the service token")

    def check_response(schema: str, out: dict) -> dict:
        if knowledge_contract().validate(schema, out):
            raise RuntimeError("mastery response does not match the contract")
        return out

    async def get(body: dict) -> dict:
        check_request("MasteryGetRequest", body)
        pool, _ = state["plan"]
        return check_response("MasteryGetResponse", await M.mastery_get(pool, state["mastery_configs"], body))

    async def explain(body: dict) -> dict:
        check_request("MasteryExplainRequest", body)
        pool, _ = state["plan"]
        engine = state.get("mastery")
        kw = {"item_type_of": engine.item_type_of} if engine is not None else {}
        t = engine.clock() if engine is not None else now()
        return check_response("MasteryExplainResponse", await M.mastery_explain(pool, state["mastery_configs"], body, t, **kw))

    async def verification(body: dict) -> dict:
        c = internal_contract(INTERNAL)
        if c.validate("VerificationRequest", body):
            raise IngestError(400, "INVALID_REQUEST", "request does not match the contract")
        engine = state.get("mastery")
        if engine is None:
            raise IngestError(409, "MASTERY_UNAVAILABLE", "no mastery configuration for this subject")
        try:
            t = await schedule_verification(engine, body["student_ref"], body["subject"], body["node_id"], body["cognitive_level"],
                                            body["reason"], body["dispute_ref"])
        except MasteryUnavailable:
            raise IngestError(409, "MASTERY_UNAVAILABLE", "mastery is not available for this unit") from None
        out = {"task_id": t["task_id"], "node_id": t["node_id"], "cognitive_level": t["cognitive_level"], "status": t["status"],
               "due_at": t["due_at"].isoformat(), "item_count": body["item_count"], "created": bool(t["created"])}
        if c.validate("VerificationTask", out):
            raise RuntimeError("verification response does not match the contract")
        return out

    async def cancel(body: dict) -> dict:
        c = internal_contract(INTERNAL)
        if c.validate("VerificationCancelRequest", body):
            raise IngestError(400, "INVALID_REQUEST", "request does not match the contract")
        engine = state.get("mastery")
        if engine is None:
            raise IngestError(404, "TASK_NOT_FOUND", "verification task not found")
        out = await cancel_verification(engine, body["student_ref"], body["task_id"])
        if out is None:
            raise IngestError(404, "TASK_NOT_FOUND", "verification task not found")
        if c.validate("VerificationCancelResult", out):
            raise RuntimeError("cancel response does not match the contract")
        return out

    # ---------- 学情档案页（PR 4）----------
    def profile_pages():
        pages = state.get("profile")
        if pages is None:
            raise IngestError(409, "PROFILE_UNAVAILABLE", "profile page is not configured")
        return pages

    async def ensure(body: dict, caller: str) -> dict:
        c = internal_contract(INTERNAL)
        if c.validate("ProfilePageEnsureRequest", body):
            raise IngestError(400, "INVALID_REQUEST", "request does not match the contract")
        # 触发点由令牌决定：编排层只能是会话开始，洞察服务只能是周报
        if body["trigger"] != {"orchestrator": "session_start", "insight": "weekly"}[caller]:
            raise IngestError(403, "CALLER_MISMATCH", "trigger does not match the service token")
        try:
            r = await profile_pages().ensure(body["student_ref"], body["subject"], body["trigger"])
        except ProfileUnavailable:
            raise IngestError(409, "PROFILE_UNAVAILABLE", "profile page is not available for this subject") from None
        out = {"subject": body["subject"], "outcome": r.outcome, "check_status": r.check_status,
               "compiled_at": None if r.compiled_at is None else r.compiled_at.isoformat(), "compiler_version": COMPILER_VERSION}
        if c.validate("ProfilePageEnsureResult", out):
            raise RuntimeError("profile ensure response does not match the contract")
        return out

    async def read(body: dict) -> dict:
        c = internal_contract(INTERNAL)
        if c.validate("ProfilePageReadRequest", body):
            raise IngestError(400, "INVALID_REQUEST", "request does not match the contract")
        try:
            out = await profile_pages().read(body["student_ref"], body["subject"])
        except ProfileUnavailable:
            raise IngestError(409, "PROFILE_UNAVAILABLE", "profile page is not available for this subject") from None
        if c.validate("ProfilePage", out):
            raise RuntimeError("profile page response does not match the contract")
        return out

    @app.post("/internal/v1/profile-page/ensure")
    async def profile_ensure(request: Request) -> JSONResponse:
        # 按令牌区分调用方：依赖 Settings.from_env 启动时“各令牌互不相同”的校验（同一令牌不会同时匹配两种身份）
        caller = "orchestrator" if orchestrator_ok(request) else ("insight" if insight_ok(request) else None)
        return await run("profile_page_ensure", request, lambda r: caller is not None,
                         lambda b: ensure(b, caller or ""), lambda b: b.get("request_id"))

    @app.post("/internal/v1/profile-page/read")
    async def profile_read(request: Request) -> JSONResponse:
        return await run("profile_page_read", request, orchestrator_ok, read, lambda b: b.get("request_id"))

    @app.post("/internal/v1/mastery/get")
    async def mastery_get(request: Request) -> JSONResponse:
        return await run("mastery_get", request, orchestrator_ok, get, ctx_rid)

    @app.post("/internal/v1/mastery/explain")
    async def mastery_explain(request: Request) -> JSONResponse:
        return await run("mastery_explain", request, orchestrator_ok, explain, ctx_rid)

    @app.post("/internal/v1/verification/cancel")
    async def verify_cancel(request: Request) -> JSONResponse:
        return await run("verification_cancel", request, insight_ok, cancel, lambda b: b.get("request_id"))

    @app.post("/internal/v1/verification")
    async def verify(request: Request) -> JSONResponse:
        return await run("verification", request, insight_ok, verification, lambda b: b.get("request_id"))
