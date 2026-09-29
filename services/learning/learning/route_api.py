"""学习服务的内部模型路由接口（specs/adapter-zhixing-sprint2/spec.md 设计要点 5）：POST /internal/v1/model/route。

各科适配器不持有任何供应商地址或 Key（ADR 04 第 1 条），模型调用经这里交给平台模型路由（learning.model_router）。
鉴权：每科一个路由客户端令牌（LEARNING_ROUTE_CLIENT_<SUBJECT>_TOKEN），令牌绑定学科，请求里的 subject 必须与令牌一致。
日志只记学科、任务、数据级别、状态与耗时，不记 messages 与 output。
"""
from __future__ import annotations

import re
from typing import Any

from .model_router import (DataClass, ModelRouter, RouteRejected, RouteRequest, RouteResult, RouteUnavailable, Task)

UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
UUID4_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
ROLES = frozenset({"system", "user", "assistant"})
MAX_MESSAGES = 50
MAX_CONTENT = 20_000
MAX_ROUTE_BODY = 1024 * 1024
FIELDS = {"task", "data_class", "subject", "prompt_template", "prompt_version", "messages", "usage_id", "idempotency_key", "deadline_ms"}


class RouteApiError(Exception):
    def __init__(self, status: int, code: str, title: str, retryable: bool = False) -> None:
        super().__init__(code)
        self.status, self.code, self.title, self.retryable = status, code, title, retryable

    def problem(self) -> dict[str, Any]:
        return {"type": f"/problems/{self.code.lower()}", "title": self.title, "status": self.status, "code": self.code,
                "retryable": self.retryable}


def parse_request(body: Any, client_subject: str) -> RouteRequest:
    """严格校验：未知字段、类型不对、超限一律 400；subject 必须等于令牌绑定的学科"""
    bad = RouteApiError(400, "INVALID_REQUEST", "invalid route request")
    if not isinstance(body, dict) or set(body) - FIELDS:
        raise bad
    try:
        task, dc = Task(body.get("task")), DataClass(body.get("data_class"))
    except ValueError:
        raise bad from None
    if body.get("subject") != client_subject:
        raise RouteApiError(403, "SUBJECT_MISMATCH", "subject does not match the route client")
    msgs = body.get("messages")
    if not isinstance(msgs, list) or not 1 <= len(msgs) <= MAX_MESSAGES:
        raise bad
    for m in msgs:
        if (not isinstance(m, dict) or set(m) != {"role", "content"} or m["role"] not in ROLES
                or not isinstance(m["content"], str) or len(m["content"]) > MAX_CONTENT):
            raise bad
    usage, key = body.get("usage_id"), body.get("idempotency_key")
    usage = usage.lower() if isinstance(usage, str) else usage  # 契约是 format: uuid，大小写都收（契约审查建议 1）
    if not isinstance(usage, str) or not UUID_RE.match(usage) or not isinstance(key, str) or not UUID4_RE.match(key):
        raise bad
    deadline = body.get("deadline_ms")
    if deadline is not None and (not isinstance(deadline, int) or isinstance(deadline, bool) or not 0 < deadline <= 60_000):
        raise bad
    for k in ("prompt_template", "prompt_version"):
        if not isinstance(body.get(k), str):
            raise bad
    return RouteRequest(task=task, data_class=dc, subject=client_subject, prompt_template=body["prompt_template"],
                        prompt_version=body["prompt_version"], messages=tuple(dict(m) for m in msgs),
                        usage_id=usage, idempotency_key=key, deadline_ms=deadline)


def to_response(res: RouteResult) -> dict[str, Any]:
    return {"output": res.output, "model_version": res.model_version, "source_version": res.source_version,
            "model_mismatch": res.model_mismatch}


async def route(router: ModelRouter, req: RouteRequest) -> dict[str, Any]:
    try:
        return to_response(await router.route(req))
    except RouteRejected:
        raise RouteApiError(400, "ROUTE_REJECTED", "route request rejected") from None
    except RouteUnavailable:
        raise RouteApiError(503, "ROUTE_UNAVAILABLE", "no allowed model provider available", retryable=True) from None
