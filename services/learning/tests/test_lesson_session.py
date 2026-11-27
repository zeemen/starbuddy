"""讲评课会话（specs/stage-lesson-session ③b-4）：打开讲评课与课内提问，经真实库（迁移 0064）与假适配器。

全部是合成数据（规则 8）：讲评课、台词与答疑都是“（合成）”占位；学生由 conftest 每个用例新建。
讲评课预留由测试库里的 learningtest.provision_meter（teach → lesson_whiteboard）以超级用户代建，与 BFF 的 meter_reserve 同一张表。
"""
from __future__ import annotations

import base64
import copy
import json
import re
import uuid
from pathlib import Path

import httpx
import pytest

from conftest import ADAPTER_CREDENTIAL, SESSION_CREDENTIAL, settings
from learning.app import create_app
from learning.lesson import service as lesson_service
from learning.lesson import ticket as lesson_ticket

SECRET = "synthetic-lesson-ticket-secret-0000000001"
OPENAPI = Path(__file__).resolve().parents[1] / "openapi/lesson-internal.v0.yaml"
NODE, ITEM = "aya:K-SYN-0001", "aya:SYN-UNV"
LESSON = {"lesson_id": "syn.lesson.open_a", "lesson_version": "0.1.0"}
ANSWER_TEXT = "（合成答疑）因为要凑成完全平方。"

DSL = {
    "dsl_version": "0.3.0", "title": "（合成）讲评课", "canvas": {"width": 960, "height": 440}, "roles": ["teacher", "buddy"],
    "narrated": True,
    "steps": [
        {"id": "s1", "say": "l1", "actions": [{"type": "speaker", "role": "teacher"},
                                              {"type": "write_text", "id": "t1", "x": 40, "y": 64, "text": "（合成）第一行"}]},
        {"id": "s2", "say": "l2", "actions": [{"type": "write_text", "id": "t2", "x": 40, "y": 140, "text": "（合成）第二行"}]},
        {"id": "s3", "actions": [{"type": "speaker", "role": "none"}]},
    ],
}
SCRIPT = {"dsl_version": "0.3.0", "lines": [{"id": "l1", "role": "teacher", "text": "（合成台词）先看第一行。"},
                                            {"id": "l2", "role": "teacher", "text": "（合成台词）再看第二行。"}]}


def envelope(segments: list[dict], hint: int) -> dict:
    return {"contract_version": "1.1.0",
            "result": {"segments": segments, "hint_level_used": hint, "ai_disclosure_shown": False, "referral_triggered": False},
            "evidence_ids": [], "source_version": "algo:aya-lesson-open@0.1.0;items:aya-lessons@syn00001",
            "model_version": "algo:aya-lesson-open@0.1.0", "confidence": 1, "semantics": "practice", "human_review_required": False}


class FakeLessonAdapter:
    """按契约形状回讲评课与答疑；记下每次 teach 请求；可改状态码、篡改下发内容、去掉答疑。"""

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.open_status = 200
        self.open_problem: dict = {}
        self.mutate_open = None
        self.ask_status = 200
        self.ask_problem: dict = {}

    async def handler(self, request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == f"Bearer {ADAPTER_CREDENTIAL}"
        assert request.url.path.endswith("/plugin/v0/teach")
        body = json.loads(request.content)
        self.calls.append(body)
        if "lesson_ref" in body:
            if self.open_status != 200:
                return httpx.Response(self.open_status, json=self.open_problem)
            seg = {"type": "whiteboard_dsl", "dsl_version": "0.3.0", "dsl": copy.deepcopy(DSL), "script": copy.deepcopy(SCRIPT)}
            if self.mutate_open:
                self.mutate_open(seg)
            return httpx.Response(200, json=envelope([seg], 0))
        if self.ask_status != 200:
            return httpx.Response(self.ask_status, json=self.ask_problem)
        return httpx.Response(200, json=envelope([{"type": "text", "text": ANSWER_TEXT}], 1))


@pytest.fixture
def candidate(monkeypatch):
    monkeypatch.setenv("STARBUDDY_ALLOW_CANDIDATE_CONTRACT", "1")


@pytest.fixture
def adapter() -> FakeLessonAdapter:
    return FakeLessonAdapter()


@pytest.fixture
async def lapi(pools, adapter, candidate):
    app_pool, _ = pools
    app = create_app(settings(lesson_ticket_secret=SECRET), pool=app_pool, transport=httpx.MockTransport(adapter.handler))
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://learning.test") as c:
            c.headers["authorization"] = f"Bearer {SESSION_CREDENTIAL}"
            yield c


async def reserve(admin, student: str, state: str = "reserved") -> tuple[str, str]:
    """代建一笔讲评课预留（teach → lesson_whiteboard），返回（usage_id，幂等键）"""
    usage, key = str(uuid.uuid4()), str(uuid.uuid4())
    async with admin.acquire() as c:
        await c.execute("SELECT learningtest.provision_meter($1::uuid, $2, $3::uuid, 'teach', false)", usage, key, student)
        if state != "reserved":
            await c.execute("SELECT learningtest.set_reservation($1::uuid, $2)", usage, state)
    return usage, key


def open_body(student: str, usage: str, key: str, **over) -> dict:
    b = {"student_ref": student, "subject": "aya", "lesson_ref": dict(LESSON), "skill_node": NODE, "item_ref": ITEM,
         "usage_id": usage, "idempotency_key": key}
    b.update(over)
    return b


Q = {"cursor": 1, "anchor": {"kind": "step", "id": "s1"}, "intent": "why_step"}


def ask_body(student: str, ticket: str, key: str | None = None, question: dict | None = None) -> dict:
    return {"student_ref": student, "ticket": ticket, "question": question or dict(Q), "idempotency_key": key or str(uuid.uuid4())}


async def opened(lapi, admin, student: str) -> dict:
    usage, key = await reserve(admin, student)
    r = await lapi.post("/internal/v1/lesson/open", json=open_body(student, usage, key))
    assert r.status_code == 201, r.text
    return r.json()


async def session_row(admin, session: str) -> dict:
    async with admin.acquire() as c:
        return dict(await c.fetchrow("SELECT status, question_count, usage_id::text, idempotency_key::text FROM platform.lesson_session "
                                     "WHERE id = $1::uuid", session))


# ---------- 打开讲评课 ----------
async def test_open_delivers_lesson_and_ticket(lapi, pools, adapter, student):
    _, admin = pools
    usage, key = await reserve(admin, student)
    r = await lapi.post("/internal/v1/lesson/open", json=open_body(student, usage, key))
    assert r.status_code == 201, r.text
    assert r.headers["idempotent-replayed"] == "false"
    out = r.json()
    assert out["dsl"] == DSL and out["script"] == SCRIPT and out["dsl_version"] == "0.3.0"
    assert out["usage_id"] == usage and out["question_limit"] == 20 and out["question_count"] == 0
    assert out["ticket"].startswith("lt1.")
    assert (await session_row(admin, out["session_id"]))["status"] == "open"
    # 适配器请求：契约 1.1.0、lesson 模式、计量挂在这笔预留上、幂等键是派生的 UUIDv4（不是预留键本身）、没交过题 → answer_submitted false
    req = adapter.calls[-1]
    assert req["context"]["contract_version"] == "1.1.0" and req["context"]["usage_id"] == usage
    assert req["context"]["idempotency_key"] != key and re.fullmatch(r"[0-9a-f-]{36}", req["context"]["idempotency_key"])
    assert req["mode"] == "lesson" and req["lesson_ref"] == LESSON and req["answer_submitted"] is False
    assert req["persona"] == {"persona_id": "aya", "prompt_version": "1.1.0"} and req["allowed_hint_level"] == 1
    assert "question" not in req and "conversation" not in req


async def test_open_replay_returns_same_session(lapi, pools, student):
    _, admin = pools
    usage, key = await reserve(admin, student)
    a = await lapi.post("/internal/v1/lesson/open", json=open_body(student, usage, key))
    b = await lapi.post("/internal/v1/lesson/open", json=open_body(student, usage, key))
    assert a.status_code == 201 and b.status_code == 200 and b.headers["idempotent-replayed"] == "true"
    assert a.json()["session_id"] == b.json()["session_id"] and a.json()["dsl"] == b.json()["dsl"]
    async with admin.acquire() as c:
        assert await c.fetchval("SELECT count(*) FROM platform.lesson_session WHERE student_id = $1::uuid", student) == 1
    # 同一个键换一节课 → 409
    other = open_body(student, usage, key, lesson_ref={"lesson_id": "syn.lesson.other", "lesson_version": "0.1.0"})
    r = await lapi.post("/internal/v1/lesson/open", json=other)
    assert r.status_code == 409 and r.json()["code"] == "IDEMPOTENCY_CONFLICT"


@pytest.mark.parametrize("state", ["settled", "released", "expired"])
async def test_open_requires_live_reservation(lapi, pools, adapter, student, state):
    _, admin = pools
    usage, key = await reserve(admin, student, state)
    r = await lapi.post("/internal/v1/lesson/open", json=open_body(student, usage, key))
    assert r.status_code == 409 and r.json()["code"] == "IDEMPOTENCY_KEY_SEALED"
    assert adapter.calls == []


async def test_open_rejects_mismatched_reservation(lapi, pools, adapter, student):
    _, admin = pools
    usage, key = await reserve(admin, student)
    for body in (open_body(student, usage, str(uuid.uuid4())), open_body(student, str(uuid.uuid4()), key)):
        r = await lapi.post("/internal/v1/lesson/open", json=body)
        assert r.status_code == 409 and r.json()["code"] == "IDEMPOTENCY_KEY_SEALED"
    assert adapter.calls == []


async def test_open_bad_requests(lapi, pools, student):
    _, admin = pools
    usage, key = await reserve(admin, student)
    cases = [
        (open_body(student, usage, key, subject="zhixing"), 400, "SUBJECT_NOT_SUPPORTED"),
        (open_body(student, usage, key, skill_node="zhixing:K-1"), 400, "INVALID_REQUEST"),
        (open_body(student, usage, key, lesson_ref={"lesson_id": "Bad", "lesson_version": "0.1.0"}), 400, "INVALID_REQUEST"),
        (open_body(student, usage, "not-a-uuid"), 400, "INVALID_REQUEST"),
        ({**open_body(student, usage, key), "dsl": {}}, 400, "INVALID_REQUEST"),
    ]
    for body, status, code in cases:
        r = await lapi.post("/internal/v1/lesson/open", json=body)
        assert (r.status_code, r.json()["code"]) == (status, code), body


@pytest.mark.parametrize("status,code", [(404, "CONTENT_VERSION_NOT_FOUND"), (422, "MODE_NOT_SUPPORTED")])
async def test_open_adapter_rejects_marks_failed(lapi, pools, adapter, student, status, code):
    _, admin = pools
    adapter.open_status, adapter.open_problem = status, {"code": code, "retryable": False}
    usage, key = await reserve(admin, student)
    r = await lapi.post("/internal/v1/lesson/open", json=open_body(student, usage, key))
    assert r.status_code == 422 and r.json()["code"] == "LESSON_NOT_AVAILABLE"
    rows = await _sessions(admin, student)
    assert [x["status"] for x in rows] == ["failed"]
    # 重放：确定失败，不再调适配器
    n = len(adapter.calls)
    r = await lapi.post("/internal/v1/lesson/open", json=open_body(student, usage, key))
    assert r.status_code == 422 and len(adapter.calls) == n


async def test_open_transient_failure_stays_opening_then_retry_succeeds(lapi, pools, adapter, student):
    _, admin = pools
    adapter.open_status, adapter.open_problem = 503, {"code": "UPSTREAM_UNAVAILABLE", "retryable": True}
    usage, key = await reserve(admin, student)
    r = await lapi.post("/internal/v1/lesson/open", json=open_body(student, usage, key))
    assert r.status_code == 503 and r.json()["retryable"] is True
    assert [x["status"] for x in await _sessions(admin, student)] == ["opening"]
    adapter.open_status = 200
    r = await lapi.post("/internal/v1/lesson/open", json=open_body(student, usage, key))
    assert r.status_code == 200 and r.headers["idempotent-replayed"] == "true"
    assert [x["status"] for x in await _sessions(admin, student)] == ["open"]


def _with_answers(seg):
    seg["script"]["lines"].append({"id": "a1", "role": "teacher", "text": "（合成答疑）答疑"})
    seg["script"]["answers"] = [{"step": "s1", "intent": "why_step", "line": "a1"}]


def _unknown_say(seg):
    seg["dsl"]["steps"][1]["say"] = "l9"


def _extra_line(seg):
    seg["script"]["lines"].append({"id": "l3", "role": "teacher", "text": "（合成台词）多余的一句。"})


def _wrong_role(seg):
    seg["script"]["lines"][0]["role"] = "buddy"


def _bad_dsl(seg):
    seg["dsl"]["steps"][0]["actions"].append({"type": "write_text", "id": "t9", "x": 40, "y": 64})


def _two_segments(seg):
    seg["type"] = "text"


def _old_version(seg):
    seg["dsl_version"] = "0.2.0"


def _html_in_line(seg):
    seg["script"]["lines"][0]["text"] = "<b>（合成）</b>"


@pytest.mark.parametrize("mutate", [_with_answers, _unknown_say, _extra_line, _wrong_role, _bad_dsl, _two_segments, _old_version,
                                    _html_in_line])
async def test_open_rejects_bad_lesson_content(lapi, pools, adapter, student, mutate):
    """讲解 DSL 或台词稿不合格：不下发（502），会话标 failed（BFF 释放预留）；下发的台词稿里不能有预制答疑"""
    _, admin = pools
    adapter.mutate_open = mutate
    usage, key = await reserve(admin, student)
    r = await lapi.post("/internal/v1/lesson/open", json=open_body(student, usage, key))
    assert r.status_code == 502 and r.json()["code"] == "ADAPTER_RESULT_REJECTED", mutate.__name__
    assert "合成" not in r.text
    assert [x["status"] for x in await _sessions(admin, student)] == ["failed"]


async def test_open_answer_submitted_from_attempts(lapi, pools, adapter, student):
    """answer_submitted 由平台按作答记录判定：这道题交过作答 → true（不信前端自报，请求里也没有这个字段）"""
    _, admin = pools
    async with admin.acquire() as c:
        await c.execute("""INSERT INTO platform.attempt (attempt_id, occurred_at, student_id, subject, item_id, response_evidence_id,
                                                         request_idempotency_key)
                           VALUES (gen_random_uuid(), '2026-10-08 11:30:00+00', $1::uuid, 'aya', 'SYN-UNV', gen_random_uuid(),
                                   'synthetic-lesson-attempt-key-01')""", student)
    await opened(lapi, admin, student)
    assert adapter.calls[-1]["answer_submitted"] is True
    r = await lapi.post("/internal/v1/lesson/open", json={**open_body(student, *await reserve(admin, student)), "answer_submitted": False})
    assert r.status_code == 400


# ---------- 课内提问 ----------
async def test_ask_answers_and_counts_without_charging(lapi, pools, adapter, student):
    _, admin = pools
    o = await opened(lapi, admin, student)
    k1 = str(uuid.uuid4())
    r = await lapi.post("/internal/v1/lesson/ask", json=ask_body(student, o["ticket"], k1))
    assert r.status_code == 200, r.text
    assert r.json() == {"outcome": "answered", "text": ANSWER_TEXT, "question_seq": 1, "questions_left": 19}
    req = adapter.calls[-1]
    assert req["question"] == {"lesson_ref": LESSON, "dsl_version": "0.3.0", **Q}
    assert req["context"]["usage_id"] == o["usage_id"] and req["skill_node"] == NODE and req["item_ref"] == ITEM
    assert "lesson_ref" not in req
    first_key = req["context"]["idempotency_key"]
    # 同一个提问键：重放（同一个第几问、同一个适配器幂等键），不加次数
    r = await lapi.post("/internal/v1/lesson/ask", json=ask_body(student, o["ticket"], k1))
    assert r.json()["question_seq"] == 1 and r.headers["idempotent-replayed"] == "true"
    assert adapter.calls[-1]["context"]["idempotency_key"] == first_key
    r = await lapi.post("/internal/v1/lesson/ask", json=ask_body(student, o["ticket"]))
    assert r.json()["question_seq"] == 2 and adapter.calls[-1]["context"]["idempotency_key"] != first_key
    row = await session_row(admin, o["session_id"])
    assert row["question_count"] == 2
    # 不扣次：会话那笔预留仍是一笔、数量 1，没有新的预留
    async with admin.acquire() as c:
        assert await c.fetchval("SELECT count(*) FROM platform.usage_reservation WHERE student_id = $1::uuid", student) == 1


async def test_ask_no_answer_is_not_an_error(lapi, pools, adapter, student):
    _, admin = pools
    o = await opened(lapi, admin, student)
    adapter.ask_status, adapter.ask_problem = 422, {"code": "MODE_NOT_SUPPORTED", "retryable": False}
    r = await lapi.post("/internal/v1/lesson/ask", json=ask_body(student, o["ticket"]))
    assert r.status_code == 200 and r.json() == {"outcome": "no_answer", "reason": "not_available", "question_seq": 1,
                                                 "questions_left": 19}
    adapter.ask_status, adapter.ask_problem = 422, {"code": "INVALID_REQUEST", "retryable": False}
    r = await lapi.post("/internal/v1/lesson/ask", json=ask_body(student, o["ticket"]))
    assert r.status_code == 422 and r.json()["code"] == "QUESTION_NOT_ANSWERABLE"
    adapter.ask_status, adapter.ask_problem = 503, {"code": "UPSTREAM_UNAVAILABLE", "retryable": True}
    r = await lapi.post("/internal/v1/lesson/ask", json=ask_body(student, o["ticket"]))
    assert r.status_code == 503


async def test_ask_limit_stops_calling_adapter(lapi, pools, adapter, student):
    _, admin = pools
    o = await opened(lapi, admin, student)
    for _ in range(20):
        assert (await lapi.post("/internal/v1/lesson/ask", json=ask_body(student, o["ticket"]))).json()["outcome"] == "answered"
    n = len(adapter.calls)
    r = await lapi.post("/internal/v1/lesson/ask", json=ask_body(student, o["ticket"]))
    assert r.json() == {"outcome": "no_answer", "reason": "limit", "question_seq": None, "questions_left": 0}
    assert len(adapter.calls) == n


async def test_ask_ticket_checks(lapi, pools, adapter, student):
    _, admin = pools
    o = await opened(lapi, admin, student)
    other = str(uuid.uuid4())
    head, payload, sig = o["ticket"].split(".")
    d = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    d["student"] = other
    forged = f"{head}.{base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b'=').decode()}.{sig}"
    n = len(adapter.calls)
    for body in (ask_body(other, o["ticket"]), ask_body(student, forged), ask_body(student, "lt1.x.y"), ask_body(student, "pt1." + payload + "." + sig)):
        r = await lapi.post("/internal/v1/lesson/ask", json=body)
        assert r.status_code in (404, 422), r.text
    # 另一名学生拿这张票据：学生对不上 → 422（与伪造同一个编码）；processing 闸门放不过不存在的学生 → 404
    assert len(adapter.calls) == n
    # 过期：票据按会话过期时间失效（时钟往后拨到过期之后）
    expired = lesson_ticket.LessonTicket(**{**lesson_ticket.verify(SECRET, o["ticket"], now=_now()).__dict__, "expires_at": 1_000_000_001,
                                            "issued_at": 1_000_000_000})
    r = await lapi.post("/internal/v1/lesson/ask", json=ask_body(student, lesson_ticket.issue(SECRET, expired)))
    assert r.status_code == 410 and r.json()["code"] == "TICKET_EXPIRED"


async def test_ask_bad_questions_do_not_count(lapi, pools, adapter, student):
    _, admin = pools
    o = await opened(lapi, admin, student)
    bad = [{**Q, "intent": "again"}, {**Q, "cursor": 0}, {**Q, "anchor": {"kind": "xy", "id": "s1"}}, {**Q, "text": "（合成）自由文字"},
           {"cursor": 1, "anchor": {"kind": "step", "id": "S1"}, "intent": "why_step"}]
    for q in bad:
        r = await lapi.post("/internal/v1/lesson/ask", json=ask_body(student, o["ticket"], question=q))
        assert r.status_code == 400, q
    assert (await session_row(admin, o["session_id"]))["question_count"] == 0


async def test_ask_after_reservation_released_is_sealed(lapi, pools, adapter, student):
    _, admin = pools
    o = await opened(lapi, admin, student)
    async with admin.acquire() as c:
        await c.execute("SELECT learningtest.set_reservation($1::uuid, 'released')", o["usage_id"])
    r = await lapi.post("/internal/v1/lesson/ask", json=ask_body(student, o["ticket"]))
    assert r.status_code == 409 and r.json()["code"] == "IDEMPOTENCY_KEY_SEALED"


async def test_settled_reservation_still_admits_questions(lapi, pools, adapter, student):
    """BFF 下发后结算：结算过的预留照样放行提问（提问不另扣）"""
    _, admin = pools
    o = await opened(lapi, admin, student)
    async with admin.acquire() as c:
        await c.execute("SELECT learningtest.set_reservation($1::uuid, 'settled')", o["usage_id"])
    r = await lapi.post("/internal/v1/lesson/ask", json=ask_body(student, o["ticket"]))
    assert r.json()["outcome"] == "answered"


async def test_logs_carry_no_lesson_text(lapi, pools, adapter, student, caplog):
    _, admin = pools
    caplog.set_level("INFO", logger="starbuddy.learning.lesson")
    o = await opened(lapi, admin, student)
    await lapi.post("/internal/v1/lesson/ask", json=ask_body(student, o["ticket"]))
    text = "\n".join(r.getMessage() for r in caplog.records if r.name == "starbuddy.learning.lesson")
    assert "lesson:open" in text and "lesson:ask" in text
    assert "合成" not in text and student not in text and o["ticket"] not in text


async def test_unconfigured_is_501(pools, adapter, candidate, student):
    app_pool, _ = pools
    app = create_app(settings(), pool=app_pool, transport=httpx.MockTransport(adapter.handler))
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://learning.test") as c:
            c.headers["authorization"] = f"Bearer {SESSION_CREDENTIAL}"
            r = await c.post("/internal/v1/lesson/open", json=open_body(student, str(uuid.uuid4()), str(uuid.uuid4())))
            assert r.status_code == 501 and r.json()["code"] == "LESSON_UNAVAILABLE"


async def test_write_token_is_rejected(lapi, student):
    from conftest import WRITE_AUTH
    r = await lapi.post("/internal/v1/lesson/open", json=open_body(student, str(uuid.uuid4()), str(uuid.uuid4())), headers=WRITE_AUTH)
    assert r.status_code == 401


# ---------- 纯函数与配置 ----------
def test_check_lesson_accepts_reference_and_rejects_answers():
    assert lesson_service.check_lesson(DSL, SCRIPT)
    s = copy.deepcopy(SCRIPT)
    s["answers"] = []
    assert not lesson_service.check_lesson(DSL, s)
    d = copy.deepcopy(DSL)
    d["narrated"] = False
    assert not lesson_service.check_lesson(d, SCRIPT)
    d = copy.deepcopy(DSL)
    d["steps"][1]["id"] = "s1"
    assert not lesson_service.check_lesson(d, SCRIPT)


def test_ticket_roundtrip_and_tamper():
    t = lesson_ticket.LessonTicket(student=str(uuid.uuid4()), session=str(uuid.uuid4()), subject="aya", lesson_id="syn.lesson.a",
                                   lesson_version="0.1.0", skill_node=NODE, item_ref=None, dsl_version="0.3.0",
                                   expires_at=int(_now().timestamp()) + 600, issued_at=int(_now().timestamp()))
    tok = lesson_ticket.issue(SECRET, t)
    assert lesson_ticket.verify(SECRET, tok, now=_now()) == t
    with pytest.raises(lesson_ticket.TicketError):
        lesson_ticket.verify("another-synthetic-secret-00000000000001", tok, now=_now())
    with pytest.raises(lesson_ticket.TicketError):
        lesson_ticket.verify(SECRET, "pt1." + tok[4:], now=_now())


def test_config_requires_candidate_switch():
    from learning.config import Settings
    base = {"LEARNING_ENV": "test", "LEARNING_DATABASE_URL": "postgresql://u@127.0.0.1/starbuddy_test",
            "LEARNING_SERVICE_TOKEN": "synthetic-learning-service-token-000000001", "LEARNING_LESSON_TICKET_SECRET": SECRET}
    with pytest.raises(RuntimeError, match="STARBUDDY_ALLOW_CANDIDATE_CONTRACT"):
        Settings.from_env(base)
    with pytest.raises(RuntimeError, match="32"):
        Settings.from_env({**base, "LEARNING_LESSON_TICKET_SECRET": "short", "STARBUDDY_ALLOW_CANDIDATE_CONTRACT": "1"})
    assert Settings.from_env({**base, "STARBUDDY_ALLOW_CANDIDATE_CONTRACT": "1"}).lesson_ticket_secret == SECRET


def test_internal_openapi_matches_api_keys():
    import yaml
    doc = yaml.safe_load(OPENAPI.read_text(encoding="utf-8"))
    src = (Path(__file__).resolve().parents[1] / "learning/lesson/api.py").read_text(encoding="utf-8")
    routes = re.findall(r'@app\.post\("([^"]+)"\)(.*?)(?=@app\.|\Z)', src, re.S)
    assert {p for p, _ in routes} == set(doc["paths"]) == {"/internal/v1/lesson/open", "/internal/v1/lesson/ask"}
    for path, body in routes:
        m = re.search(r"_keys\(b, (\{[^}]*\})(?:, (\{[^}]*\}))?\)", body)
        assert m, path
        req = set(eval(m.group(1)))  # noqa: S307 源码里的集合字面量
        opt = set(eval(m.group(2))) if m.group(2) else set()
        schema = doc["paths"][path]["post"]["requestBody"]["content"]["application/json"]["schema"]
        assert set(schema["required"]) == req and set(schema["properties"]) == req | opt, path
        assert schema["additionalProperties"] is False, path


async def test_internal_openapi_matches_responses(lapi, pools, adapter, student):
    import jsonschema
    import yaml
    doc = yaml.safe_load(OPENAPI.read_text(encoding="utf-8"))

    def v(name):
        return jsonschema.Draft202012Validator({"$ref": f"#/components/schemas/{name}", "components": doc["components"]})
    _, admin = pools
    o = await opened(lapi, admin, student)
    v("LessonOpened").validate(o)
    v("AskAnswered").validate((await lapi.post("/internal/v1/lesson/ask", json=ask_body(student, o["ticket"]))).json())
    adapter.ask_status, adapter.ask_problem = 422, {"code": "MODE_NOT_SUPPORTED", "retryable": False}
    v("AskNoAnswer").validate((await lapi.post("/internal/v1/lesson/ask", json=ask_body(student, o["ticket"]))).json())


# ---------- 工具 ----------
def _now():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc)


async def _sessions(admin, student: str) -> list[dict]:
    async with admin.acquire() as c:
        return [dict(r) for r in await c.fetch("SELECT status FROM platform.lesson_session WHERE student_id = $1::uuid", student)]
