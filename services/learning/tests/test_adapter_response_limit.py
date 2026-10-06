"""适配器超大响应（渗透测试 F5，安全审查 S6）：学习服务读适配器响应体的上限是 MAX_RESPONSE_BYTES（5 MB）。

用本机真实的 HTTP 假适配器（只监听 127.0.0.1，随机端口），而不是 httpx.MockTransport：要覆盖真实连接上
带 Content-Length、无 Content-Length 的分块流式、gzip 解压后超限三种形态。全部是合成数据（CLAUDE.md 规则 8）。

约定（adapter_client._post）：超限一律 502 ADAPTER_RESULT_REJECTED，不重试、不落库，错误信息与日志里不出现响应内容；
刚好等于上限的响应照常处理。
"""
from __future__ import annotations

import gzip
import http.server
import json
import logging
import threading
from collections.abc import Callable

import httpx
import pytest

from conftest import ADAPTER_CREDENTIAL, _answer_in_version, counts, fake_grade, grade_body, settings
from learning.adapter_client import MAX_RESPONSE_BYTES, call_adapter
from learning.app import create_app
from learning.config import AdapterTarget
from learning.contract import internal_contract
from learning.errors import IngestError

MARKER = "SYNTHETIC-F5-MARKER-7c1d"
NOTHING = {"evidence_event": 0, "attempt": 0, "grade_result": 0, "grade_dimension": 0, "error_attribution": 0, "skill_observation": 0}
ONE_GRADE = {"evidence_event": 2, "attempt": 1, "grade_result": 1, "grade_dimension": 1, "error_attribution": 1, "skill_observation": 0}
CHUNK = 64 * 1024

# 应答：(响应体, 形态)。形态 "length" 带 Content-Length；"chunked" 分块、不带 Content-Length；"gzip" 带 Content-Encoding: gzip
Reply = tuple[bytes, str]


def padded(obj: dict, size: int) -> bytes:
    """合法 JSON 对象，末尾补空白到正好 size 字节（JSON 允许尾随空白）。"""
    raw = json.dumps(obj).encode()
    assert len(raw) <= size
    return raw + b" " * (size - len(raw))


class FakeHTTPAdapter:
    def __init__(self) -> None:
        self.calls = 0
        self.responder: Callable[[dict], Reply] = lambda body: (b"{}", "length")
        self.sent = 0  # 最近一次实际写出的响应体字节数（分块形态）
        fake = self

        class Handler(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args) -> None:  # 不往测试输出里打访问日志
                pass

            def do_POST(self) -> None:
                fake.calls += 1
                n = int(self.headers.get("content-length") or 0)
                body = json.loads(self.rfile.read(n) or b"{}")
                if self.headers.get("authorization") != f"Bearer {ADAPTER_CREDENTIAL}":
                    self.send_response(401)
                    self.send_header("content-length", "0")
                    self.end_headers()
                    return
                payload, mode = fake.responder(body)
                fake.sent = 0
                self.send_response(200)
                self.send_header("content-type", "application/json")
                try:
                    if mode == "chunked":
                        self.send_header("transfer-encoding", "chunked")
                        self.end_headers()
                        for i in range(0, len(payload), CHUNK):
                            part = payload[i:i + CHUNK]
                            self.wfile.write(f"{len(part):x}\r\n".encode() + part + b"\r\n")
                            fake.sent += len(part)
                        self.wfile.write(b"0\r\n\r\n")
                    else:
                        if mode == "gzip":
                            payload = gzip.compress(payload)
                            self.send_header("content-encoding", "gzip")
                        self.send_header("content-length", str(len(payload)))
                        self.end_headers()
                        self.wfile.write(payload)
                        fake.sent = len(payload)
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    self.close_connection = True  # 学习服务读到上限就断开：正是要的行为

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.base_url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture(scope="module")
def http_adapter():
    fake = FakeHTTPAdapter()
    assert fake.server.server_address[0] == "127.0.0.1"
    fake.start()
    yield fake
    fake.stop()


@pytest.fixture
def adapter(http_adapter):
    http_adapter.calls = 0
    http_adapter.sent = 0
    return http_adapter


# ---------- 适配器客户端（不连库）----------
def _junk(size: int) -> bytes:
    """超限响应：合法 JSON、开头就带标记，确认标记已被读进来过，仍不出现在错误里。"""
    return padded({"note": MARKER}, size)


async def _call(adapter) -> dict:
    target = AdapterTarget(adapter.base_url, ADAPTER_CREDENTIAL)
    async with httpx.AsyncClient(trust_env=False, follow_redirects=False) as client:
        return await call_adapter(client, target, "grade", {"synthetic": True}, 10.0)


@pytest.mark.parametrize("mode", ["length", "chunked"])
async def test_response_exactly_at_limit_is_accepted(adapter, mode):
    adapter.responder = lambda body: (_junk(MAX_RESPONSE_BYTES), mode)
    assert await _call(adapter) == {"note": MARKER}
    assert adapter.calls == 1


@pytest.mark.parametrize("mode,size", [
    ("length", MAX_RESPONSE_BYTES + 1),        # 带 Content-Length，只多 1 字节
    ("chunked", MAX_RESPONSE_BYTES + 1),       # 无 Content-Length 的流式，只多 1 字节
    ("chunked", 8 * MAX_RESPONSE_BYTES),       # 无 Content-Length 的流式，远超上限：读到上限就断开
    ("gzip", MAX_RESPONSE_BYTES + 1),          # 压缩后很小、解压后超限：按解压后的字节数算
])
async def test_oversized_response_is_rejected_without_retry_or_echo(adapter, caplog, mode, size):
    caplog.set_level(logging.DEBUG)
    adapter.responder = lambda body: (_junk(size), mode)
    with pytest.raises(IngestError) as ei:
        await _call(adapter)
    e = ei.value
    assert (e.status, e.code, e.retryable) == (502, "ADAPTER_RESULT_REJECTED", False)
    assert adapter.calls == 1, "超限不是连接失败，不重试"
    problem = json.dumps(e.problem(), ensure_ascii=False)
    assert MARKER not in str(e) and MARKER not in problem
    assert MARKER not in caplog.text
    if mode == "chunked" and size > 2 * MAX_RESPONSE_BYTES:
        # 不读完整个流：服务端写出去的远少于它想写的（余量留给套接字缓冲）
        assert adapter.sent < size // 2


# ---------- 经学习服务写入接口（连库）----------
@pytest.fixture
async def real_api(pools, adapter):
    app_pool, _ = pools
    s = settings(adapters={"aya": AdapterTarget(adapter.base_url, ADAPTER_CREDENTIAL)})
    app = create_app(s, pool=app_pool)  # 不注入 transport：走真实的本机 HTTP 连接
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://learning.test") as client:
            client.headers["authorization"] = "Bearer synthetic-bff-to-learning-credential-0001"
            yield client


def _grade_reply(size: int, mode: str) -> Callable[[dict], Reply]:
    """按契约形状的合法判分结果，补空白到 size 字节；body 里带标记，超限时不得回显。"""
    def reply(body: dict) -> Reply:
        out = _answer_in_version(body, fake_grade(body, "source_unverified"))
        return padded(out, size), mode
    return reply


@pytest.mark.parametrize("mode", ["length", "chunked"])
async def test_ingest_accepts_grade_response_exactly_at_limit(real_api, pools, student, adapter, mode):
    _, admin = pools
    adapter.responder = _grade_reply(MAX_RESPONSE_BYTES, mode)
    r = await real_api.post("/internal/v0/grade", json=grade_body(student))
    assert r.status_code == 200, r.text[:500]
    assert adapter.calls == 1
    assert await counts(admin, student) == ONE_GRADE


@pytest.mark.parametrize("mode,size", [("length", MAX_RESPONSE_BYTES + 1), ("chunked", MAX_RESPONSE_BYTES + 1),
                                       ("chunked", 8 * MAX_RESPONSE_BYTES)])
async def test_ingest_rejects_oversized_response_and_writes_nothing(real_api, pools, student, adapter, caplog, mode, size):
    _, admin = pools
    caplog.set_level(logging.DEBUG)
    adapter.responder = _grade_reply(size, mode)
    body = grade_body(student, text=f"作答-{MARKER}")  # 适配器把作答原文回写进证据：超限时也不得回显
    r = await real_api.post("/internal/v0/grade", json=body)
    assert r.status_code == 502, r.text[:500]
    p = r.json()
    assert p["code"] == "ADAPTER_RESULT_REJECTED" and p["retryable"] is False
    assert internal_contract().validate("Problem", p) == []
    assert MARKER not in r.text
    assert MARKER not in caplog.text
    assert adapter.calls == 1
    assert await counts(admin, student) == NOTHING
    # 同一个键换回正常大小的响应可以重试成功（失败已标记，不占着键）
    adapter.responder = _grade_reply(64 * 1024, "length")
    r2 = await real_api.post("/internal/v0/grade", json=body)
    assert r2.status_code == 200, r2.text[:500]
    assert await counts(admin, student) == ONE_GRADE
