"""学习服务调适配器的客户端（learning/adapter_client.py）：把重试、错误归类、编码转发、严格 JSON、超时与响应体上限逐条钉住。

不连库、不起端口：用 httpx.MockTransport 的本机假适配器，全部是合成数据（CLAUDE.md 规则 8）。
真实连接上的 Content-Length / 分块 / gzip 三种超限形态见 tests/test_adapter_response_limit.py。
scripts/adapter_client_mutations.py 把这里钉住的每条检查改坏，断言本文件能发现。
"""
from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator, Awaitable, Callable

import httpx
import pytest

from learning import adapter_client
from learning.adapter_client import MAX_RESPONSE_BYTES, call_adapter, strict_json
from learning.config import AdapterTarget
from learning.errors import IngestError

CRED = "synthetic-learning-to-adapter-credential-01"
TARGET = AdapterTarget("http://aya-adapter.invalid", CRED)
LIMIT = 5 * 1024 * 1024          # 规格值（安全审查 S6），故意不从被测模块取：改大上限要能被发现
# 契约形状的请求体（plugin-api.v1.0 的 context）；UUID 全是合成的固定值，idempotency_key 是小写 UUIDv4
KEY = "5b8e1c52-0000-4000-8000-00000000c0a1"
USAGE = "5b8e1c52-0000-4000-8000-00000000b0a1"
BODY = {
    "context": {
        "request_id": "5b8e1c52-0000-4000-8000-00000000a0a1",
        "contract_version": "1.0.0",
        "subject": "aya",
        "student_ref": "5b8e1c52-0000-4000-8000-00000000d0a1",
        "data_class": "L2",
        "usage_id": USAGE,
        "idempotency_key": KEY,
    },
    "synthetic": True,
}
JOB_ID = "5b8e1c52-0000-4000-8000-00000000e0a1"
MARKER = "SYNTHETIC-DETAIL-MARKER-3f9a"

Handler = Callable[[httpx.Request], Awaitable[httpx.Response]]


class Fake:
    """按顺序取应答；每次请求记下 URL、请求头、请求体与 timeout 扩展。应答用完后重复最后一个。"""

    def __init__(self, *replies: Callable[[httpx.Request], httpx.Response]) -> None:
        self.replies = list(replies)
        self.requests: list[httpx.Request] = []

    async def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        reply = self.replies[min(len(self.requests), len(self.replies)) - 1]
        return reply(request)

    @property
    def calls(self) -> int:
        return len(self.requests)


async def call(fake: Fake, method: str = "grade", timeout: float = 10.0) -> dict:
    async with httpx.AsyncClient(transport=httpx.MockTransport(fake.handler)) as client:
        return await asyncio.wait_for(call_adapter(client, TARGET, method, dict(BODY), timeout), 8)


async def fails(fake: Fake, **kw) -> IngestError:
    with pytest.raises(IngestError) as ei:
        await call(fake, **kw)
    return ei.value


def ok(obj: object = None) -> Callable[[httpx.Request], httpx.Response]:
    return lambda r: httpx.Response(200, json={"ok": True} if obj is None else obj)


def raw(status: int, content: bytes) -> Callable[[httpx.Request], httpx.Response]:
    return lambda r: httpx.Response(status, content=content, headers={"content-type": "application/json"})


def problem(status: int, **fields) -> Callable[[httpx.Request], httpx.Response]:
    """不完全合规的错误体（缺字段、application/json）：客户端也要稳妥处理。"""
    return lambda r: httpx.Response(status, json={"type": "/problems/x", "title": "x", "status": status, **fields})


def contract_problem(status: int, code: str, retryable: bool) -> Callable[[httpx.Request], httpx.Response]:
    """完全合规的 Problem（plugin-api.v1.0：type、title、status、code、retryable，application/problem+json）。"""
    body = {"type": f"/problems/{code.lower()}", "title": "synthetic problem", "status": status, "code": code, "retryable": retryable}
    return lambda r: httpx.Response(status, content=json.dumps(body).encode(), headers={"content-type": "application/problem+json"})


def connect_error(r: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("synthetic connect failure", request=r)


def read_timeout(r: httpx.Request) -> httpx.Response:
    raise httpx.ReadTimeout("synthetic read timeout", request=r)


def connect_timeout(r: httpx.Request) -> httpx.Response:
    raise httpx.ConnectTimeout("synthetic connect timeout", request=r)


def same_request(fake: Fake) -> None:
    """重试的两次请求：URL、请求头、整个请求体逐字节相同，键还是原来那个。"""
    assert fake.calls == 2
    a, b = fake.requests
    assert a.content == b.content
    assert json.loads(b.content) == BODY and json.loads(b.content)["context"]["idempotency_key"] == KEY
    assert a.url == b.url
    assert a.headers.multi_items() == b.headers.multi_items()


# ---------- 成功路径与请求形状 ----------
async def test_success_posts_to_contract_path_with_bearer_and_timeout():
    fake = Fake(ok({"verdict": "correct"}))
    assert await call(fake, method="grade", timeout=7.5) == {"verdict": "correct"}
    assert fake.calls == 1
    r = fake.requests[0]
    assert r.method == "POST"
    assert str(r.url) == "http://aya-adapter.invalid/plugin/v0/grade"
    assert r.headers["authorization"] == f"Bearer {CRED}"
    assert json.loads(r.content) == BODY
    assert r.extensions["timeout"] == {"connect": 7.5, "read": 7.5, "write": 7.5, "pool": 7.5}


# ---------- 重试：只在连接失败或 retryable: true 时，同一个键重试一次 ----------
async def test_connect_failure_is_retried_once_with_same_request():
    fake = Fake(connect_error, ok())
    assert await call(fake) == {"ok": True}
    same_request(fake)


async def test_connect_failure_twice_is_503_after_exactly_two_calls():
    fake = Fake(connect_error)
    e = await fails(fake)
    assert (e.status, e.code, e.retryable) == (503, "UPSTREAM_UNAVAILABLE", True)
    assert fake.calls == 2


async def test_retryable_problem_is_retried_once_with_same_request():
    fake = Fake(problem(503, code="BUSY", retryable=True), ok())
    assert await call(fake) == {"ok": True}
    same_request(fake)


async def test_retryable_problem_twice_raises_last_error_after_two_calls():
    fake = Fake(problem(503, code="BUSY", retryable=True))
    e = await fails(fake)
    assert (e.status, e.code, e.retryable, e.upstream_code) == (502, "UPSTREAM_UNAVAILABLE", True, "BUSY")
    assert fake.calls == 2


@pytest.mark.parametrize("flag", [False, None, "true", 1])
async def test_non_retryable_problem_is_not_retried(flag):
    fields = {"code": "ITEM_NOT_FOUND"} if flag is None else {"code": "ITEM_NOT_FOUND", "retryable": flag}
    fake = Fake(problem(404, **fields), ok())
    e = await fails(fake)
    assert (e.status, e.code, e.retryable) == (404, "UPSTREAM_REJECTED", False)
    assert fake.calls == 1


async def test_contract_problem_rate_limited_is_retried_once():
    """完全合规的 Problem：429 RATE_LIMITED、retryable: true、application/problem+json。"""
    fake = Fake(contract_problem(429, "RATE_LIMITED", True))
    e = await fails(fake)
    assert (e.status, e.code, e.retryable, e.upstream_code) == (429, "UPSTREAM_REJECTED", True, "RATE_LIMITED")
    same_request(fake)


async def test_contract_problem_then_success():
    fake = Fake(contract_problem(429, "RATE_LIMITED", True), ok())
    assert await call(fake) == {"ok": True}
    same_request(fake)


# 钉现状：retryable: true 的 401 与 409 也按“retryable 才重试”重试一次（是否对 401/403 禁止重试待人确认）
async def test_retryable_401_is_retried_once_then_502():
    fake = Fake(problem(401, code="UNAUTHENTICATED", retryable=True))
    e = await fails(fake)
    assert (e.status, e.code, e.retryable) == (502, "UPSTREAM_UNAVAILABLE", True)
    same_request(fake)


async def test_retryable_409_is_retried_once_and_keeps_status():
    fake = Fake(problem(409, code="IDEMPOTENCY_CONFLICT", retryable=True))
    e = await fails(fake)
    assert (e.status, e.code, e.retryable, e.upstream_code) == (409, "UPSTREAM_REJECTED", True, "IDEMPOTENCY_CONFLICT")
    same_request(fake)


# ---------- 错误归类 ----------
@pytest.mark.parametrize("status", [400, 404, 409, 422, 429, 499])
async def test_client_errors_keep_status(status):
    e = await fails(Fake(problem(status)))
    assert (e.status, e.code, e.title) == (status, "UPSTREAM_REJECTED", "adapter rejected the request")


@pytest.mark.parametrize("status", [401, 403, 407])
async def test_adapter_auth_errors_become_502(status):
    """适配器的 401/403 是学习服务与适配器之间的问题，不能让 BFF 误以为是它自己的令牌。"""
    e = await fails(Fake(problem(status)))
    assert (e.status, e.code, e.title) == (502, "UPSTREAM_UNAVAILABLE", "adapter failed")


@pytest.mark.parametrize("status", [301, 500, 502, 503, 504])
async def test_other_errors_become_502(status):
    e = await fails(Fake(problem(status)))
    assert (e.status, e.code) == (502, "UPSTREAM_UNAVAILABLE")


async def test_error_body_not_json_is_still_classified():
    e = await fails(Fake(raw(500, b"<html>synthetic</html>")))
    assert (e.status, e.code, e.upstream_code) == (502, "UPSTREAM_UNAVAILABLE", None)


# ---------- 只转编码，detail 不转发 ----------
@pytest.mark.parametrize("code,forwarded", [
    ("ITEM_NOT_FOUND", True),
    ("A", True),
    ("A1_B2", True),
    ("A" * 48, True),
    ("A" * 49, False),          # 长度上限
    ("张三A", False),           # isupper() 会放过（安全审查 B1）
    ("ITEM_NOT_FOUND 张三", False),  # 前缀像编码也不行：必须整串匹配
    ("ITEM_NOT_FOUND\n", False),
    ("item_not_found", False),
    ("1ITEM", False),
    ("_ITEM", False),
    ("ITEM-NOT-FOUND", False),
    ("", False),
    (123, False),
    (None, False),
])
async def test_only_code_shaped_upstream_code_is_forwarded(code, forwarded):
    e = await fails(Fake(problem(422, code=code)))
    assert e.upstream_code == (code if forwarded else None)
    if not forwarded:
        assert "upstream_code" not in e.problem()


async def test_problem_detail_is_never_forwarded():
    fake = Fake(problem(422, code="BAD_INPUT", detail=f"学生作答 {MARKER}", title=f"t {MARKER}"))
    e = await fails(fake)
    assert e.detail is None
    assert MARKER not in str(e) and MARKER not in json.dumps(e.problem(), ensure_ascii=False)
    assert e.upstream_code == "BAD_INPUT"


# ---------- 严格 JSON 与对象形状 ----------
@pytest.mark.parametrize("body", [b'{"score": NaN}', b'{"score": Infinity}', b'{"score": -Infinity}'])
async def test_non_finite_numbers_are_rejected(body):
    fake = Fake(raw(200, body))
    e = await fails(fake)
    assert (e.status, e.code, e.retryable) == (502, "ADAPTER_RESULT_REJECTED", False)
    assert fake.calls == 1


def test_strict_json_rejects_constants_directly():
    for s in (b"NaN", b"[Infinity]", b'{"a": -Infinity}'):
        with pytest.raises(ValueError):
            strict_json(s)
    assert strict_json(b'{"a": 1.5}') == {"a": 1.5}


async def test_invalid_json_is_rejected():
    e = await fails(Fake(raw(200, b"{not json")))
    assert (e.status, e.code) == (502, "ADAPTER_RESULT_REJECTED")


@pytest.mark.parametrize("body", [b"[1, 2]", b'"text"', b"null", b"42", b"true"])
async def test_non_object_body_is_rejected(body):
    fake = Fake(raw(200, body))
    e = await fails(fake)
    assert (e.status, e.code, e.title) == (502, "ADAPTER_RESULT_REJECTED", "adapter returned a non-object body")
    assert fake.calls == 1


# ---------- 202：异步任务不支持 ----------
async def test_202_is_501_not_success():
    fake = Fake(lambda r: httpx.Response(202, json={"job_id": "SYN-JOB-1", "retryable": True}))
    e = await fails(fake)
    assert (e.status, e.code, e.retryable) == (501, "ASYNC_NOT_SUPPORTED", False)
    assert fake.calls == 1


async def test_contract_job_accepted_is_501():
    """完全合规的 JobAccepted（plugin-api.v1.0）：sprint 1 不支持异步任务，不当成功、不重试。"""
    job = {"job_id": JOB_ID, "method": "grade", "status": "queued", "poll_after_ms": 1000,
           "usage_id": USAGE, "idempotency_key": KEY}
    fake = Fake(lambda r: httpx.Response(202, json=job), ok())
    e = await fails(fake)
    assert (e.status, e.code, e.retryable) == (501, "ASYNC_NOT_SUPPORTED", False)
    assert fake.calls == 1


# ---------- 超时 ----------
async def test_httpx_timeout_is_504_without_retry():
    fake = Fake(read_timeout, ok())
    e = await fails(fake)
    assert (e.status, e.code, e.retryable) == (504, "UPSTREAM_TIMEOUT", True)
    assert fake.calls == 1


async def test_connect_timeout_is_504_without_retry():
    """ConnectTimeout 既是 TimeoutException 也是 TransportError：按超时处理，不当连接失败重试。"""
    fake = Fake(connect_timeout, ok())
    e = await fails(fake)
    assert (e.status, e.code, e.retryable) == (504, "UPSTREAM_TIMEOUT", True)
    assert fake.calls == 1


class _AsyncioSpy:
    """只替换被测模块命名空间里的 asyncio：记下 asyncio.timeout 的参数，其余照常转给真 asyncio。"""

    def __init__(self) -> None:
        self.budgets: list[object] = []

    def timeout(self, delay):
        self.budgets.append(delay)
        return asyncio.timeout(delay)

    def __getattr__(self, name: str):
        return getattr(asyncio, name)


async def test_total_budget_is_two_timeouts_plus_one_second(monkeypatch):
    spy = _AsyncioSpy()
    monkeypatch.setattr(adapter_client, "asyncio", spy)
    assert await call(Fake(ok()), timeout=7.5) == {"ok": True}
    assert spy.budgets == [2 * 7.5 + 1]


async def _drip() -> AsyncIterator[bytes]:
    """每隔一会儿送一个字节，永不结束：httpx 的单次读写超时管不住它。"""
    yield b"{"
    while True:
        await asyncio.sleep(0.05)
        yield b" "


async def test_total_budget_cuts_a_dripping_adapter():
    timeout = 1.0                                  # 总预算 = 2 × timeout + 1 = 3.0 秒
    fake = Fake(lambda r: httpx.Response(200, content=_drip()))
    t0 = time.monotonic()
    e = await fails(fake, timeout=timeout)
    elapsed = time.monotonic() - t0
    assert (e.status, e.code, e.retryable) == (504, "UPSTREAM_TIMEOUT", True)
    assert 2.8 <= elapsed < 4.5, elapsed           # 墙钟只防“没有总预算”；预算的精确值由上面的 spy 用例钉住
    assert fake.calls == 1


# ---------- 响应体上限 5 MB（`>`：刚好等于上限照常处理） ----------
def test_limit_is_five_megabytes():
    assert MAX_RESPONSE_BYTES == LIMIT


def _padded(size: int) -> bytes:
    head = b'{"ok": true}'
    return head + b" " * (size - len(head))


async def _chunks(payload: bytes, step: int = 64 * 1024) -> AsyncIterator[bytes]:
    for i in range(0, len(payload), step):
        yield payload[i:i + step]


@pytest.mark.parametrize("stream", [False, True])
async def test_response_exactly_at_limit_is_accepted(stream):
    payload = _padded(LIMIT)
    fake = Fake(lambda r: httpx.Response(200, content=_chunks(payload) if stream else payload))
    assert await call(fake) == {"ok": True}


@pytest.mark.parametrize("stream", [False, True])
async def test_response_one_byte_over_limit_is_rejected_without_retry(stream):
    payload = _padded(LIMIT + 1)
    fake = Fake(lambda r: httpx.Response(200, content=_chunks(payload) if stream else payload), ok())
    e = await fails(fake)
    assert (e.status, e.code, e.retryable) == (502, "ADAPTER_RESULT_REJECTED", False)
    assert e.title == "adapter response too large"
    assert fake.calls == 1


async def test_oversized_error_body_is_rejected_too():
    """错误响应同样受上限约束，不读完、不重试。"""
    fake = Fake(raw(503, _padded(LIMIT + 1)), ok())
    e = await fails(fake)
    assert (e.status, e.code) == (502, "ADAPTER_RESULT_REJECTED")
    assert fake.calls == 1
