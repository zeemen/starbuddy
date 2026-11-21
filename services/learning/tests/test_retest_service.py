"""KGE-05 复测与验证题交付：学习服务库上测试（specs/retest-delivery-kge05/spec.md 验收）。一次性 Postgres，全部合成数据。

沿用错题再练 PR 2 的一次性库、合成地图与合成题（SYNP-*）、假知识服务与假适配器（tests/test_practice_service.py）；
掌握重算用合成配置，只把当日复测间隔改成 0（rung0 在到达“执行”的同一刻到期），这样作答的服务器记录时间（数据库 now()）
晚于到期时间，复测轮能被真实地判通过或失败——不手工插 scheduled 行（再练 lessons 经验）。
覆盖：到期复测与验证任务能列出、取题、作答，作答经 grade 落库（purpose 由服务端按 tasks.PURPOSE 写：签字前衰减 retest、其余 practice）并触发重算，
复测轮按 KGE-04 规则关闭；有无本模块时掌握记录与复测任务逐字段一致（对拍）；票据防伪造、防过期、防跨学生、跨学科、
再练与复测票据不能互换；同 key 重放不写新行；只出没做过的题目族；hint_followup 不列；8 周复测点默认关、打开后可用；
鉴权只认 BFF 令牌；开关（配置）关闭时接口 501；服务目录 OpenAPI 与实现一致；返回内容没有掌握、置信度、证据 ID。
"""
from __future__ import annotations

import json
import re
import uuid
from datetime import timedelta
from pathlib import Path

import httpx
import pytest

from conftest import WRITE_AUTH, SESSION_CREDENTIAL, ADAPTER_CREDENTIAL, CREDENTIAL, grade_body, meter_row, to_v1_body
from learning import meter as METER
from learning.app import create_app
from learning.mastery import parse_config
from learning.mastery import recompute as R
from learning.practice import ticket as T
from learning.retest import config as RC
from learning.retest import tasks as K
from test_mastery_db import _doc
from test_practice_service import (CV, INSIGHT, KNOWLEDGE, N1, N2, N3, OFFSET, ORCH, SECRET, Fakes, candidate_contract, clock,  # noqa: F401
                                   counts, new_student, no_forbidden, plock, ppg, ppools, practice_settings, reset_clock)

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")

OPENAPI = Path(__file__).resolve().parents[1] / "openapi/retest-internal.v0.yaml"


def _zero_gap(subject: str = "aya"):
    d = _doc(subject)
    d["retest"]["rung0_min_gap_seconds"] = 0          # 合成：当日复测在到达“执行”的同一刻到期（只在本测试的内存配置里）
    return parse_config(d, env="test")


ZERO = {"aya": _zero_gap()}
RSVCS: list = []


@pytest.fixture(autouse=True)
def capture_retest(monkeypatch):
    """取到 app 里的 RetestService（看计数用）：lifespan 里按名字导入，替换模块里的类即可。"""
    import learning.retest.service as RSmod
    orig = RSmod.RetestService

    class Captured(orig):
        def __init__(self, *a, **kw) -> None:
            super().__init__(*a, **kw)
            RSVCS.append(self)
    monkeypatch.setattr(RSmod, "RetestService", Captured)
    yield
    RSVCS.clear()


def engine(app_pool, **kw) -> R.MasteryEngine:
    return R.MasteryEngine(app_pool, ZERO, **kw)


class RApi:
    def __init__(self, client: httpx.AsyncClient, fakes: Fakes, eng: R.MasteryEngine | None) -> None:
        self.c, self.fakes, self.engine = client, fakes, eng

    @property
    def svc(self):
        return RSVCS[-1]

    async def queue(self, student, subject="aya"):
        return await self.c.post("/internal/v1/retest/queue", json={"student_ref": student, "subject": subject})

    async def entries(self, student):
        r = await self.queue(student)
        assert r.status_code == 200, r.text
        return r.json()["entries"]

    async def next(self, student, entry_id, subject="aya"):
        return await self.c.post("/internal/v1/retest/next", json={"student_ref": student, "subject": subject, "entry_id": entry_id})

    async def answer(self, student, ticket, text="42", *, key=None, usage=None, subject="aya", extra=None, answer=None):
        body = {"student_ref": student, "subject": subject, "ticket": ticket, "answer": answer or {"text": text},
                "idempotency_key": key or str(uuid.uuid4()), "usage_id": usage or str(uuid.uuid4())}
        body.update(extra or {})
        return await self.c.post("/internal/v1/retest/answer", json=body)

    async def check(self, student, ticket, subject="aya", extra=None):
        return await self.c.post("/internal/v1/retest/check", json={"student_ref": student, "subject": subject, "ticket": ticket, **(extra or {})})

    async def replay(self, student, ticket, key, usage, subject="aya"):
        return await self.c.post("/internal/v1/retest/replay", json={"student_ref": student, "subject": subject, "ticket": ticket,
                                                                     "idempotency_key": key, "usage_id": usage})

    async def practice_answer(self, student, ticket, text="42"):
        return await self.c.post("/internal/v1/practice/answer", json={
            "student_ref": student, "subject": "aya", "ticket": ticket, "answer": {"text": text},
            "idempotency_key": str(uuid.uuid4()), "usage_id": str(uuid.uuid4())})

    async def grade(self, student, item, text, hint=0, purpose=None):
        """平台作答（不经本模块）：learning-internal v1 的 grade；purpose 给了就按它写（对拍用）。
        LT-04 起直接经 HTTP 的写入用途只能是 practice：非 practice 用途改由进程内的同一个写入服务（可信调用方、按自练计次核对计量，
        与复测模块自己的作答路径相同）写，HTTP 只用于 practice。"""
        b = to_v1_body(grade_body(student, item=item, text=text))
        b["context"]["content_version"] = dict(CV)
        b["attempt"]["hint_level_used"] = hint
        b["attempt"]["occurred_at"] = clock().isoformat()
        ingest = {"channel": "keypad", **({"purpose": purpose} if purpose else {})}
        if purpose not in (None, "practice"):
            summary, _ = await self.svc.practice.ingest.ingest("grade", {"request": b, "ingest": ingest}, "1.0.0", meter=METER.COUNTED)
            return summary
        r = await self.c.post("/internal/v1/grade", json={"request": b, "ingest": ingest}, headers=WRITE_AUTH)
        assert r.status_code == 200, r.text
        return r.json()


def retest_settings(**over):
    return practice_settings(**{"retest_config": "synthetic", **over})


async def open_api(app_pool, lock_pool, *, eng=None, **over):
    fakes = Fakes()
    app = create_app(retest_settings(**over), pool=app_pool, transport=httpx.MockTransport(fakes.handler), mastery=eng,
                     practice_clock=clock, practice_lock_pool=lock_pool)
    return app, fakes


@pytest.fixture
async def api(ppools, plock):
    app_pool, _ = ppools
    eng = engine(app_pool)
    app, fakes = await open_api(app_pool, plock, eng=eng)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://learning.test") as c:
            c.headers["authorization"] = f"Bearer {SESSION_CREDENTIAL}"
            yield RApi(c, fakes, eng)


async def tasks(admin, student, *, open_only=False) -> list[dict]:
    async with admin.acquire() as c:
        rows = await c.fetch(f"""SELECT id::text, node_id, cognitive_level, rung, reason, status, closing_attempt_id::text AS closing
                                   FROM platform.retest_task WHERE student_id = $1::uuid {"AND status = 'open'" if open_only else ""}
                                  ORDER BY created_at, id""", student)
    return [dict(r) for r in rows]


async def reach_execute(api: RApi, student: str) -> None:
    """两个不同题目族独立做对 → KGE-04 到“执行”，排当日复测 rung0（合成配置：间隔 0，立即到期）。"""
    await api.grade(student, "SYNP-0003", "42")
    await api.grade(student, "SYNP-0004", "42")


async def one_entry(api: RApi, student: str, node: str = N1, kind: str | None = None) -> dict:
    """一个单元一类的那一条。做错之后 KGE-04 往往会另排衰减（decay，置信度低于下限即到期），它也是复测条目；按类别挑。"""
    es = [e for e in await api.entries(student) if e["node_id"] == node and (kind is None or e["kind"] == kind)]
    assert len(es) == 1, es
    return es[0]


# ---------- 到期复测：列出 → 取题 → 作答（purpose = retest）→ KGE-04 关闭复测轮 ----------
async def test_scheduled_retest_listed_answered_and_closed_by_kge(api, ppools, student):
    _, admin = ppools
    await reach_execute(api, student)
    ts = await tasks(admin, student, open_only=True)
    assert [(t["reason"], t["rung"]) for t in ts] == [("scheduled", "rung0")]
    e = await one_entry(api, student)
    assert e == {"entry_id": e["entry_id"], "node_id": N1, "cognitive_level": "apply", "kind": "retest", "status": "due", "empty_reason": None}
    no_forbidden(e)
    before = await counts(admin, student)
    r = await api.next(student, e["entry_id"])
    assert r.status_code == 200, r.text
    n = r.json()
    # 参照题是最近一次作答的 SYNP-0004；做过的族（fam-b、fam-c）不出；按题号第一道没做过的族是 SYNP-0001（fam-a）
    assert (n["status"], n["kind"], n["item_ref"]) == ("ready", "retest", "aya:SYNP-0001") and n["ticket"].startswith("rt1.")
    assert set(n) == {"status", "entry_id", "kind", "item_ref", "item", "ticket", "expires_at"}
    no_forbidden(n)
    req = api.fakes.variation_requests[-1]
    assert req["source_item_ref"] == "aya:SYNP-0004" and {"aya:SYNP-0003", "aya:SYNP-0004"} <= set(req["exclude_item_refs"])
    assert {f["item_ref"] for f in req["item_family"]} == {"aya:SYNP-0001", "aya:SYNP-0002", "aya:SYNP-0005", "aya:SYNP-0011"}
    assert "subject_error_category" not in req and "usage_id" not in req["context"]
    assert await counts(admin, student) == before                        # queue、next 不写任何表
    a = await api.answer(student, n["ticket"], "42")
    assert a.status_code == 200, a.text
    assert a.headers["idempotent-replayed"] == "false"
    assert a.json() == {"verdict": "correct", "entry": {"entry_id": e["entry_id"], "status": "done"}}
    no_forbidden(a.json())
    async with admin.acquire() as c:
        row = await c.fetchrow("""SELECT attempt_id::text, channel, hint_level_used, evidence_kind, evidence_level, item_id::text
                                    FROM platform.attempt WHERE student_id = $1::uuid ORDER BY recorded_at DESC LIMIT 1""", student)
    # 服务端写入：键盘通道、签字前复测轮按练习用途（→ independent，不写 delayed_retest / transfer）、无提示、A 级
    assert tuple(row)[1:] == ("keypad", 0, "independent", "A", "SYNP-0001")
    after = await tasks(admin, student)
    closed = next(t for t in after if t["id"] == ts[0]["id"])
    assert (closed["status"], closed["closing"]) == ("passed", row["attempt_id"])  # 由 KGE-04 重算关闭（本模块不写任务）
    assert await api.entries(student) == []
    r = await api.next(student, e["entry_id"])
    assert r.status_code == 404 and r.json()["code"] == "ENTRY_NOT_FOUND"


async def test_failed_retest_is_rescheduled_by_kge(api, ppools, student):
    """复测做错：KGE-04 记失败并从失败时刻重排本轮（新任务、新条目）；旧票据换个键再答 409。"""
    _, admin = ppools
    await reach_execute(api, student)
    e = await one_entry(api, student)
    n = (await api.next(student, e["entry_id"])).json()
    a = (await api.answer(student, n["ticket"], "41")).json()
    assert a == {"verdict": "incorrect", "entry": {"entry_id": e["entry_id"], "status": "done"}}
    ts = [t for t in await tasks(admin, student) if t["reason"] == "scheduled"]
    assert [t["status"] for t in ts] == ["failed", "open"]
    new_id = K.entry_id(student, "aya", K.anchor_of("scheduled", ts[1]["id"]))
    listed = {x["entry_id"]: x for x in await api.entries(student)}
    assert e["entry_id"] not in listed and listed[new_id]["status"] == "due"
    r = await api.answer(student, n["ticket"], "42")
    assert r.status_code == 409 and r.json()["code"] == "RETEST_NOT_DUE"


# ---------- “我其实会”的自述验证：两道验证题（签字前 purpose = practice），只数用本任务票据作答的题，任务本身不由本模块关 ----------
async def test_self_report_verification_two_items(api, ppools, student):
    _, admin = ppools
    await api.grade(student, "SYNP-0001", "41")
    await R.schedule_verification(api.engine, student, "aya", N1, "apply", "verify_self_report", str(uuid.uuid4()))
    e = await one_entry(api, student, kind="verify")
    assert e["status"] == "due"
    n1 = (await api.next(student, e["entry_id"])).json()
    assert n1["kind"] == "verify" and n1["item_ref"] == "aya:SYNP-0003"
    a1 = (await api.answer(student, n1["ticket"], "42")).json()
    assert a1["entry"] == {"entry_id": e["entry_id"], "status": "due"}  # 做了 1 道，还差 1 道
    # 不经本任务票据的同单元作答（直接经 grade）不算验证题（审查意见 4）
    await api.grade(student, "SYNP-0005", "42")
    assert (await one_entry(api, student, kind="verify"))["status"] == "due"
    n2 = (await api.next(student, e["entry_id"])).json()
    assert n2["item_ref"] == "aya:SYNP-0004"                             # 第二道：又一个没做过的族（变式）
    a2 = (await api.answer(student, n2["ticket"], "41")).json()
    assert a2 == {"verdict": "incorrect", "entry": {"entry_id": e["entry_id"], "status": "done"}}
    async with admin.acquire() as c:
        kinds = [r[0] for r in await c.fetch("""SELECT evidence_kind FROM platform.attempt WHERE student_id = $1::uuid
                                                  ORDER BY recorded_at""", student)]
    assert kinds == ["independent"] * 4                                      # 没有 transfer 情境（审查阻断 1）
    assert [x for x in await api.entries(student) if x["kind"] == "verify"] == []
    r = await api.next(student, e["entry_id"])
    assert r.status_code == 409 and r.json()["code"] == "RETEST_NOT_DUE"
    # 做满之后，即使拿着一张签名有效、题没做过的票据（测试用同一密钥签发），作答也被拒：条目已不是到期未做完
    t2 = T.verify(SECRET, n2["ticket"], now=clock(), ttl_minutes=60, prefix="rt1")
    fresh = T.issue(SECRET, T.Ticket(**{**t2.__dict__, "item_id": "SYNP-0005", "issued_at": T.issued(clock())}), prefix="rt1")
    r = await api.answer(student, fresh, "42")
    assert r.status_code == 409 and r.json()["code"] == "RETEST_NOT_DUE", r.text
    # 自述验证任务不经重放（KGE-04 不关它），本模块也不写：仍开着
    assert [t["status"] for t in await tasks(admin, student) if t["reason"] == "verify_self_report"] == ["open"]


# ---------- 选题：只出没做过的题目族；没有参照题；hint_followup 不列 ----------
async def test_only_new_family_or_unavailable(api, student):
    """N2 只有一个题目族（fam-e）：学生做过之后没有没做过的族，这一轮不可用（no_new_family），不调适配器、不出同族题。"""
    await api.grade(student, "SYNP-0006", "41")
    await R.schedule_verification(api.engine, student, "aya", N2, "apply", "verify_self_report", str(uuid.uuid4()))
    e = await one_entry(api, student, N2, "verify")
    assert (e["status"], e["empty_reason"]) == ("unavailable", "no_new_family")
    r = await api.next(student, e["entry_id"])
    assert r.status_code == 200 and r.json() == {"status": "unavailable", "empty_reason": "no_new_family"}
    assert api.fakes.calls.get("generate_variation", 0) == 0


async def test_no_source_item_is_unavailable(api, student):
    await R.schedule_verification(api.engine, student, "aya", N3, "apply", "verify_self_report", str(uuid.uuid4()))
    e = await one_entry(api, student, N3)
    assert (e["status"], e["empty_reason"]) == ("unavailable", "no_source_item")
    assert (await api.next(student, e["entry_id"])).json() == {"status": "unavailable", "empty_reason": "no_source_item"}
    assert api.fakes.calls == {}


async def test_hint_followup_not_listed(api, ppools, student):
    _, admin = ppools
    await api.grade(student, "SYNP-0001", "42", hint=3)
    assert [t["reason"] for t in await tasks(admin, student, open_only=True)] == ["hint_followup"]
    assert await api.entries(student) == []                              # 由错题再练提供，这里不重复


# ---------- 对拍：有无本模块时掌握记录与复测任务逐字段一致 ----------
async def snap(admin, sid):
    async with admin.acquire() as c:
        est = await c.fetch("""SELECT node_id, cognitive_level, state::text, p_mastery, confidence, next_retest_at IS NULL AS no_retest,
                                      cardinality(evidence_ids) AS n, algorithm_version::text, map_version_id::text, is_current
                                 FROM platform.mastery_estimate WHERE student_id = $1::uuid ORDER BY computed_at, id""", sid)
        ts = await c.fetch("""SELECT node_id, cognitive_level, rung, reason, status, closing_attempt_id IS NULL AS no_closing
                                FROM platform.retest_task WHERE student_id = $1::uuid ORDER BY created_at, id""", sid)
        att = await c.fetch("""SELECT item_id::text, channel, evidence_kind, evidence_level, hint_level_used FROM platform.attempt
                                WHERE student_id = $1::uuid ORDER BY recorded_at""", sid)
        ev = await c.fetch("""SELECT kind, item_id::text, admission::text, source_method, evidence_kind, evidence_level,
                                     map_version_id::text FROM platform.evidence_event WHERE student_id = $1::uuid
                                ORDER BY recorded_at, kind""", sid)
        gr = await c.fetch("""SELECT verdict, score_total, max_total, calibrated, confidence, human_review_required, review_reasons::text[],
                                     semantics::text, cardinality(evidence_ids) AS n, superseded_by IS NULL AS current
                                FROM platform.grade_result WHERE student_id = $1::uuid ORDER BY created_at""", sid)
    return [dict(r) for r in est], [dict(r) for r in ts], [dict(r) for r in att], [dict(r) for r in ev], [dict(r) for r in gr]


async def platform_counts(admin) -> dict[str, int]:
    """platform.* 每张表（含分区父表）的行数快照：读列表、出题前后必须一致（本模块只读）。"""
    async with admin.acquire() as c:
        names = [r[0] for r in await c.fetch("""SELECT relname FROM pg_class WHERE relnamespace = 'platform'::regnamespace
                                                   AND relkind IN ('r', 'p') ORDER BY relname""")]
        return {n: await c.fetchval(f'SELECT count(*) FROM platform."{n}"') for n in names}


async def entry_for(admin, student: str, reason: str) -> str:
    """按任务原因找到这个学生开着的那条任务的条目 ID（与 tasks.anchor_of 同一口径）。"""
    async with admin.acquire() as c:
        tid = await c.fetchval("""SELECT id::text FROM platform.retest_task WHERE student_id = $1::uuid AND reason = $2 AND status = 'open'""",
                               student, reason)
    assert tid is not None, reason
    return K.entry_id(student, "aya", K.anchor_of(reason, tid))


# 按任务类参数化：（任务原因, 依次作答, 直接写入时用的用途 = tasks.PURPOSE 的签字前口径）
CASES = [("scheduled", ("42",)), ("scheduled", ("41", "42")), ("verify_self_report", ("42", "41")), ("decay", ("42",))]


@pytest.mark.parametrize("reason,answers", CASES)
async def test_mastery_and_tasks_identical_with_and_without_retest(api, ppools, student, reason, answers):
    """对拍：同一组合成作答，A 经本模块作答，B 直接经 grade 写入同一道题、同一用途（tasks.PURPOSE）。两人的掌握记录、复测任务、作答、
    证据与判分结果逐字段一致（本模块只读、只经 grade 写）。反复读列表、出题前后 platform.* 全表行数不变。"""
    _, admin = ppools
    b = await new_student(admin)
    for s in (student, b):
        if reason == "scheduled":
            await reach_execute(api, s)
        else:
            await api.grade(s, "SYNP-0001", "41")                        # 做错：KGE-04 排衰减（置信度低于下限即到期）
            if reason == "verify_self_report":
                await R.schedule_verification(api.engine, s, "aya", N1, "apply", "verify_self_report", str(uuid.uuid4()))
    for text in answers:
        eid = await entry_for(admin, student, reason)
        before = await platform_counts(admin)
        for _ in range(2):
            await api.queue(b)
            await api.queue(student)
            await api.next(student, eid)
        n = (await api.next(student, eid)).json()
        assert n["status"] == "ready", n
        assert await platform_counts(admin) == before                       # 读列表、出题不写任何表
        assert (await api.answer(student, n["ticket"], text)).status_code == 200
        await api.grade(b, n["item_ref"].split(":", 1)[1], text, purpose=K.PURPOSE[reason])
    sa, sb = await snap(admin, student), await snap(admin, b)
    assert sa[0] and sa[0] == sb[0]
    assert sa[1] and sa[1] == sb[1]
    assert sa[2] == sb[2] and sa[3] == sb[3] and sa[4] == sb[4]


# ---------- 票据 ----------
async def test_ticket_forged_expired_cross_student_subject_and_purpose(api, ppools, student):
    _, admin = ppools
    await reach_execute(api, student)
    e = await one_entry(api, student)
    tok = (await api.next(student, e["entry_id"])).json()["ticket"]
    t = T.verify(SECRET, tok, now=clock(), ttl_minutes=60, prefix="rt1")
    before = await counts(admin, student)
    head, payload, sig = tok.split(".")
    other = await new_student(admin)
    as_practice = T.issue(SECRET, t)                                     # 同一内容签成再练票据
    relabeled = "pt1." + tok.split(".", 1)[1]
    other_purpose = T.issue(SECRET, T.Ticket(**{**t.__dict__, "anchor": "retest:" + t.anchor.split(":", 1)[1]}), prefix="rt1")
    bad_anchor = T.issue(SECRET, T.Ticket(**{**t.__dict__, "anchor": "transfer_check:" + t.anchor.split(":", 1)[1]}), prefix="rt1")
    cases = [
        (await api.answer(student, f"{head}.{payload}.{sig[::-1]}"), 403, "TICKET_INVALID"),
        (await api.answer(student, T.issue("another-secret-another-secret-0000000001", t, prefix="rt1")), 403, "TICKET_INVALID"),
        (await api.answer(student, as_practice), 403, "TICKET_INVALID"),        # 再练票据拿来复测
        (await api.answer(student, bad_anchor), 403, "TICKET_INVALID"),          # 锚点里的用途不认识
        (await api.answer(other, tok), 403, "TICKET_INVALID"),                   # 跨学生
        (await api.answer(student, tok, subject="zhixing"), 403, "TICKET_INVALID"),  # 跨学科
        (await api.answer(student, other_purpose), 409, "RETEST_NOT_DUE"),       # 改了用途（签名有效）：对不上任何到期条目
        (await api.practice_answer(student, tok), 403, "TICKET_INVALID"),        # 复测票据拿去再练
        (await api.practice_answer(student, relabeled), 403, "TICKET_INVALID"),
    ]
    for r, status, code in cases:
        assert r.status_code == status and r.json()["code"] == code, r.text
    OFFSET[0] = timedelta(minutes=61)
    r = await api.answer(student, tok)
    assert r.status_code == 410 and r.json()["code"] == "TICKET_EXPIRED"
    assert await counts(admin, student) == before                         # 一行不写
    assert (api.svc.stats["ticket_invalid"], api.svc.stats["ticket_expired"]) == (6, 1)


async def test_request_validation(api, student):
    await reach_execute(api, student)
    e = await one_entry(api, student)
    tok = (await api.next(student, e["entry_id"])).json()["ticket"]
    for extra in ({"purpose": "practice"}, {"purpose": "retest"}, {"channel": "photo"}, {"hint_level_used": 3}, {"evidence_level": "A"}):
        r = await api.answer(student, tok, extra=extra)
        assert r.status_code == 400 and r.json()["code"] == "INVALID_REQUEST", extra
    assert (await api.answer(student, tok, key="not-a-uuid")).status_code == 400
    assert (await api.answer(student, tok, key=str(uuid.uuid4()).upper())).status_code == 400
    assert (await api.answer(student, tok, usage="x")).status_code == 400
    r = await api.answer(student, tok, answer={"choice": ["A"]})
    assert r.status_code == 422 and r.json()["code"] == "INVALID_ANSWER"
    assert (await api.queue(student, "wenli")).json()["code"] == "SUBJECT_NOT_SUPPORTED"
    assert (await api.queue(student.upper())).status_code == 400
    r = await api.c.post("/internal/v1/retest/queue", content=b'{"student_ref": "a", "student_ref": "b", "subject": "aya"}')
    assert r.status_code == 400
    assert (await api.c.post("/internal/v1/retest/queue", content=b"x" * (65 * 1024))).status_code == 413
    assert (await api.next(student, str(uuid.uuid4()))).json()["code"] == "ENTRY_NOT_FOUND"
    assert (await api.next(str(uuid.uuid4()), e["entry_id"])).json()["code"] == "ENTRY_NOT_FOUND"   # 别的学生拿这个条目 ID


# ---------- 幂等 ----------
async def test_idempotent_replay(api, ppools, student):
    _, admin = ppools
    await reach_execute(api, student)
    e = await one_entry(api, student)
    tok = (await api.next(student, e["entry_id"])).json()["ticket"]
    key, usage = str(uuid.uuid4()), str(uuid.uuid4())
    first = await api.answer(student, tok, "42", key=key, usage=usage)
    assert first.status_code == 200 and first.headers["idempotent-replayed"] == "false"
    c1 = await counts(admin, student)
    grades = api.fakes.calls["grade"]
    again = await api.answer(student, tok, "42", key=key, usage=usage)
    assert again.status_code == 200 and again.headers["idempotent-replayed"] == "true" and again.json() == first.json()
    assert await counts(admin, student) == c1 and api.fakes.calls["grade"] == grades     # 不写新行、不再判分
    OFFSET[0] = timedelta(minutes=90)                                                   # 过期后同键重试仍拿回原结果
    late = await api.answer(student, tok, "42", key=key, usage=usage)
    assert late.status_code == 200 and late.headers["idempotent-replayed"] == "true"
    OFFSET[0] = timedelta(0)
    conflict = await api.answer(student, tok, "41", key=key, usage=usage)
    assert conflict.status_code == 409 and conflict.json()["code"] == "IDEMPOTENCY_CONFLICT"
    r = await api.answer(student, tok, "42")                                            # 同一张票据换一个键：任务已关
    assert r.status_code == 409 and r.json()["code"] == "RETEST_NOT_DUE"
    assert await counts(admin, student) == c1
    rp = await api.c.get(f"/internal/v1/replay/grade/{key}", params={"student_ref": student, "usage_id": usage}, headers=WRITE_AUTH)
    assert rp.status_code == 200 and rp.json()["verdict"] == "correct"


async def test_same_ticket_new_key_rejected_after_pending_review(api, ppools, student):
    """判分待复核：复测轮不判（KGE-04 不计入待复核），任务仍开着；同一张票据换键再答 409 TICKET_USED（审查 B1 同再练）。"""
    _, admin = ppools
    await reach_execute(api, student)
    e = await one_entry(api, student)
    tok = (await api.next(student, e["entry_id"])).json()["ticket"]
    out = (await api.answer(student, tok, "review")).json()
    assert out == {"verdict": "incorrect", "pending_review": True, "entry": {"entry_id": e["entry_id"], "status": "due"}}
    c1 = await counts(admin, student)
    r = await api.answer(student, tok, "42")
    assert r.status_code == 409 and r.json()["code"] == "TICKET_USED"
    assert await counts(admin, student) == c1 and api.svc.stats["ticket_used"] == 1


async def test_check_and_replay_are_read_only(api, ppools, student):
    """审查意见 2：check 是计次前的只读预检（条目仍到期、票据未用、未过期、属于这个学生）；replay 只找回原结果（不执行、不封存），
    要求键是用这张票据作答的、usage_id 对得上。两者都不写任何表。"""
    _, admin = ppools
    await reach_execute(api, student)
    e = await one_entry(api, student)
    tok = (await api.next(student, e["entry_id"])).json()["ticket"]
    other = await new_student(admin)
    before = await platform_counts(admin)
    r = await api.check(student, tok)
    assert r.status_code == 200 and r.json() == {"status": "ok"}, r.text
    assert (await api.check(str(uuid.uuid4()), tok)).json()["code"] == "TICKET_INVALID"
    assert (await api.check(student, tok, extra={"answer": {"text": "1"}})).status_code == 400
    key, usage = str(uuid.uuid4()), str(uuid.uuid4())
    nf = await api.replay(student, tok, key, usage)                       # 还没作答：找不到，也不封存这个键
    assert nf.status_code == 404 and nf.json()["code"] == "REPLAY_NOT_FOUND"
    assert await platform_counts(admin) == before                         # check、replay 不写任何表（含 replay_seal）
    first = await api.answer(student, tok, "42", key=key, usage=usage)
    assert first.status_code == 200, first.text                           # 没被 replay 封存：照常作答
    r = await api.check(student, tok)
    assert r.status_code == 409 and r.json()["code"] == "RETEST_NOT_DUE"  # 任务已关：预检不通过，BFF 不计次
    c1 = await platform_counts(admin)
    rp = await api.replay(student, tok, key, usage)
    assert rp.status_code == 200 and rp.headers["idempotent-replayed"] == "true" and rp.json() == first.json(), rp.text
    for bad in ((key, str(uuid.uuid4())), (str(uuid.uuid4()), usage)):    # usage_id 不对、键不是这张票据的
        assert (await api.replay(student, tok, *bad)).status_code == 404
    assert (await api.replay(other, tok, key, usage)).json()["code"] == "TICKET_INVALID"
    OFFSET[0] = timedelta(minutes=90)                                     # 票据过期后照样找回（签名仍要对）
    assert (await api.replay(student, tok, key, usage)).status_code == 200
    assert (await api.check(student, tok)).json()["code"] == "TICKET_EXPIRED"
    OFFSET[0] = timedelta(0)
    assert await platform_counts(admin) == c1
    # 另一条任务的票据（签名有效、同一学生、同一键）：作答 ID 由键与锚点派生，对不上就找不到
    t = T.verify(SECRET, tok, now=clock(), ttl_minutes=60, prefix="rt1")
    tok2 = T.issue(SECRET, T.Ticket(**{**t.__dict__, "anchor": f"practice:{uuid.uuid4()}"}), prefix="rt1")
    assert (await api.replay(student, tok2, key, usage)).status_code == 404


async def test_key_reread_after_taking_lock(api, monkeypatch, student):
    """拿锁之后再读一次键（同再练 S1）：锁外读到“没提交过”、实际同键已成功时，交给幂等处理拿回原结果，而不是按条目 409。"""
    from learning.practice import answer as PA
    await reach_execute(api, student)
    e = await one_entry(api, student)
    tok = (await api.next(student, e["entry_id"])).json()["ticket"]
    key, usage = str(uuid.uuid4()), str(uuid.uuid4())
    first = await api.answer(student, tok, "42", key=key, usage=usage)
    assert first.status_code == 200 and first.json()["entry"]["status"] == "done"
    real, calls = PA._key_state, []

    async def stale(svc, k):
        calls.append(k)
        return None if len(calls) == 1 else await real(svc, k)
    monkeypatch.setattr(PA, "_key_state", stale)
    r = await api.answer(student, tok, "42", key=key, usage=usage)
    assert r.status_code == 200 and r.headers["idempotent-replayed"] == "true" and r.json() == first.json(), r.text
    assert len(calls) == 2


async def test_concurrent_same_ticket_only_one_succeeds(api, ppools, student):
    """两个不同键并发用同一张票据：与再练同一把单元锁，只有一个写入（另一个 503 BUSY 或 409）。"""
    import asyncio
    _, admin = ppools
    await reach_execute(api, student)
    e = await one_entry(api, student)
    tok = (await api.next(student, e["entry_id"])).json()["ticket"]
    c0 = await counts(admin, student)
    api.fakes.grade_delay = 0.5
    rs = await asyncio.wait_for(asyncio.gather(api.answer(student, tok, "42"), api.answer(student, tok, "42")), timeout=60)
    codes = sorted(r.status_code for r in rs)
    assert codes[0] == 200 and codes[1] in (409, 503), [r.text for r in rs]
    c1 = await counts(admin, student)
    assert c1["attempt"] == c0["attempt"] + 1 and c1["grade_result"] == c0["grade_result"] + 1


# ---------- 8 周复测点（默认关；配置文件打开） ----------
async def test_followup_off_by_default_and_on_by_config(ppools, plock, student, tmp_path):
    app_pool, admin = ppools
    eng = engine(app_pool)
    on = tmp_path / "retest-followup.json"
    on.write_text(json.dumps({**RC.SYNTHETIC, "followup_enabled": True, "followup_after_days": 56}), encoding="utf-8")
    results = {}
    for name, spec in (("off", "synthetic"), ("on", str(on))):
        app, fakes = await open_api(app_pool, plock, eng=eng, retest_config=spec)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://learning.test") as c:
                c.headers["authorization"] = f"Bearer {SESSION_CREDENTIAL}"
                a = RApi(c, fakes, eng)
                if name == "off":
                    await a.grade(student, "SYNP-0003", "42")          # 一道独立做对：到“理解”，有当前掌握记录
                    # 合成前提：这个单元没有开着的任务（只有一条证据时 KGE-04 会立即排衰减；这里撤掉，只为构造“久未作答、无任务”的单元）
                    async with admin.acquire() as conn:
                        await conn.execute("UPDATE platform.retest_task SET status = 'cancelled' WHERE student_id = $1::uuid AND status = 'open'",
                                           student)
                OFFSET[0] = timedelta(days=57)
                results[name] = await a.entries(student)
                if name == "on":
                    assert await tasks(admin, student, open_only=True) == []    # 8 周复测点不建任务
                    e = results[name][0]
                    n = (await a.next(student, e["entry_id"])).json()
                    out = (await a.answer(student, n["ticket"], "42")).json()
                    assert out["entry"] == {"entry_id": e["entry_id"], "status": "done"}
                    async with admin.acquire() as conn:
                        kind = await conn.fetchval("""SELECT evidence_kind FROM platform.attempt WHERE student_id = $1::uuid
                                                       ORDER BY recorded_at DESC LIMIT 1""", student)
                    assert kind == "independent"                         # 8 周复测点签字前按练习用途
                    # 刚作答过：8 周复测点不再满天数（之后出现的只可能是 KGE-04 自己排的任务）
                    assert e["entry_id"] not in {x["entry_id"] for x in await a.entries(student)}
                OFFSET[0] = timedelta(0)
    assert results["off"] == []
    assert [(e["kind"], e["status"], e["node_id"]) for e in results["on"]] == [("retest", "due", N1)]


# ---------- 鉴权与开关 ----------
async def test_only_bff_token(api, student):
    paths = [("/internal/v1/retest/queue", {"student_ref": student, "subject": "aya"}),
             ("/internal/v1/retest/next", {"student_ref": student, "subject": "aya", "entry_id": str(uuid.uuid4())}),
             ("/internal/v1/retest/answer", {"student_ref": student, "subject": "aya", "ticket": "x", "answer": {"text": "1"},
                                             "idempotency_key": str(uuid.uuid4()), "usage_id": str(uuid.uuid4())})]
    for path, body in paths:
        for header in ("", f"Bearer {ORCH}", f"Bearer {INSIGHT}", f"Bearer {ADAPTER_CREDENTIAL}", f"Bearer {KNOWLEDGE.credential}",
                       f"Bearer {SECRET}", f"Bearer {SESSION_CREDENTIAL}x", SESSION_CREDENTIAL, f"Bearer {CREDENTIAL}"):
            r = await api.c.post(path, json=body, headers={"authorization": header})
            assert r.status_code == 401 and r.json()["code"] == "UNAUTHENTICATED", (path, header)
    assert (await api.queue(student)).status_code == 200


async def test_not_configured(ppools, plock, student):
    """开关关闭（不设 LEARNING_RETEST_CONFIG）：三个接口一律 501，再练照常；没有题面提供者：列表照常、出题与作答 501。"""
    app_pool, _ = ppools
    for over, codes, practice_ok in (({"retest_config": None}, (501, 501, 501), True),
                                     ({"practice_presenter": None}, (200, 501, 501), True)):
        app, fakes = await open_api(app_pool, plock, **over)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://learning.test") as c:
                c.headers["authorization"] = f"Bearer {SESSION_CREDENTIAL}"
                a = RApi(c, fakes, None)
                got = ((await a.queue(student)).status_code, (await a.next(student, str(uuid.uuid4()))).status_code,
                       (await a.answer(student, "x")).status_code)
                assert got == codes, over
                if codes[0] == 501:
                    assert (await a.queue(student)).json()["code"] == "RETEST_UNAVAILABLE"
                r = await c.post("/internal/v1/practice/queue", json={"student_ref": student, "subject": "aya"})
                assert (r.status_code == 200) is practice_ok


def test_settings_need_practice_config():
    from learning.config import Settings
    base = {"LEARNING_ENV": "test", "LEARNING_SERVICE_TOKEN": CREDENTIAL, "LEARNING_DATABASE_URL": "postgresql://unused",
            "LEARNING_PRACTICE_CONFIG": "synthetic", "LEARNING_PRACTICE_TICKET_SECRET": SECRET, "LEARNING_RETEST_CONFIG": "synthetic"}
    assert Settings.from_env(base).retest_config == "synthetic"
    assert Settings.from_env({k: v for k, v in base.items() if k != "LEARNING_RETEST_CONFIG"}).retest_config is None
    with pytest.raises(RuntimeError):
        Settings.from_env({k: v for k, v in base.items() if k not in ("LEARNING_PRACTICE_CONFIG", "LEARNING_PRACTICE_TICKET_SECRET")})


# ---------- 服务目录 OpenAPI 一致性 ----------
def test_internal_openapi_matches_api_keys():
    import yaml
    doc = yaml.safe_load(OPENAPI.read_text(encoding="utf-8"))
    src = (Path(__file__).resolve().parents[1] / "learning/retest/api.py").read_text(encoding="utf-8")
    routes = re.findall(r'@app\.post\("([^"]+)"\)(.*?)(?=@app\.|\Z)', src, re.S)
    assert {p for p, _ in routes} == set(doc["paths"]) == {"/internal/v1/retest/queue", "/internal/v1/retest/next",
                                                           "/internal/v1/retest/check", "/internal/v1/retest/replay",
                                                           "/internal/v1/retest/answer"}
    for path, body in routes:
        m = re.search(r"_keys\(b, (\{[^}]*\})\)", body)
        assert m, path
        req = set(eval(m.group(1)))  # noqa: S307 源码里的集合字面量
        schema = doc["paths"][path]["post"]["requestBody"]["content"]["application/json"]["schema"]
        assert set(schema["required"]) == req == set(schema["properties"]), path
        assert schema["additionalProperties"] is False, path
    assert "/internal/v1/replay/grade/" in doc["info"]["description"]


def _validator(name: str):
    import jsonschema
    import yaml
    doc = yaml.safe_load(OPENAPI.read_text(encoding="utf-8"))
    return jsonschema.Draft202012Validator({"$ref": f"#/components/schemas/{name}", "components": doc["components"]})


async def test_internal_openapi_matches_responses(api, student):
    await reach_execute(api, student)
    await api.grade(student, "SYNP-0006", "41")
    await R.schedule_verification(api.engine, student, "aya", N2, "apply", "verify_self_report", str(uuid.uuid4()))
    es = await api.entries(student)
    assert len(es) >= 2
    for e in es:
        _validator("Entry").validate(e)
    n = (await api.next(student, (await one_entry(api, student, N1, "retest"))["entry_id"])).json()
    _validator("NextReady").validate(n)
    _validator("NextUnavailable").validate((await api.next(student, (await one_entry(api, student, N2, "verify"))["entry_id"])).json())
    _validator("AnswerResult").validate((await api.answer(student, n["ticket"], "41")).json())


# ---------- 计量核对（LT-04 决定记录（续）第 2 条：复测作答只认题库自练计次 counted） ----------
@pytest.mark.parametrize("state,ok", [("counted", True), ("reserved", False), ("settled", False), (None, False)])
async def test_answer_meter_check_accepts_counted_only(api, ppools, student, meter_provisioned, state, ok):
    """复测作答同再练：usage_id + 幂等键 + 学生须对上题库自练计次；对上预留或什么都没有 409 IDEMPOTENCY_KEY_SEALED，
    不判分、不落库、复测轮不关闭。准备数据照常代建，作答前关掉代建。"""
    _, admin = ppools
    await reach_execute(api, student)
    e = await one_entry(api, student)
    tok = (await api.next(student, e["entry_id"])).json()["ticket"]
    meter_provisioned.on = False
    key, usage = str(uuid.uuid4()), str(uuid.uuid4())
    if state is not None:
        await meter_row(admin, usage_id=usage, key=key, student=student, state=state)
    before, grades = await counts(admin, student), api.fakes.calls["grade"]
    r = await api.answer(student, tok, "42", key=key, usage=usage)
    if ok:
        assert r.status_code == 200 and r.json()["entry"]["status"] == "done", r.text
        assert api.fakes.calls["grade"] == grades + 1
    else:
        assert r.status_code == 409 and r.json()["code"] == "IDEMPOTENCY_KEY_SEALED", r.text
        assert api.fakes.calls["grade"] == grades
        after = await counts(admin, student)
        assert {k: v for k, v in after.items() if k != "ingest_request"} == {k: v for k, v in before.items() if k != "ingest_request"}
        assert (await one_entry(api, student))["status"] == "due", "没有落库：复测轮仍开着"


@pytest.fixture
async def student(ppools) -> str:
    _, admin = ppools
    return await new_student(admin)
