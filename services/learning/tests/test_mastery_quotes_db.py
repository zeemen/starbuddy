"""引语原文核对的库上测试（specs/quote-verbatim-guard 第二节第 4、5、7 条）：learning.mastery.quotes.verify 以 starbuddy_learning 身份
查证据原文。一次性 Postgres 与合成地图、题目沿用 test_mastery_db；判断以洞察服务身份写入（只在测试库）。全部合成数据（规则 8）。

- 通过：该生、键盘输入、在所引判断依据里、原文逐字包含；
- not_found 同一个编码：别的学生的证据、别的学生的判断、OCR / 语音 / 拍照通道、不在判断依据里、判断待验证以外读不到（已过期）、
  ID 不是 UUID、证据不存在；
- disputed：所引判断有未结的复核任务（数据有误）；结案后恢复；
- no_match：改一个字；只读：核对前后各表行数不变；超过上限拒绝。
"""
from __future__ import annotations

import json
import uuid

import pytest

from learning.mastery import consistency as C
from learning.mastery import quotes as Q
from test_mastery_db import V1, invariants, item, mpg, mpools, student  # noqa: F401

NODE = "MATH.SYN.T1.N1"
TEXT = "（合成作答）先移项，再把 x 的系数化为 1。"


@pytest.fixture(scope="module")
async def template(mpools):
    _, admin = mpools
    async with admin.acquire() as c:
        await c.execute(
            """INSERT INTO insight.claim_template (template_key, version, target_type, max_visibility, max_ttl, text_template, status,
                                                   reviewed_by, reviewed_at)
               VALUES ('syn.quote.node', 1, 'node', '{agent,student,guardian}', '60 days', '（合成模板）{node_id}', 'approved',
                       'test-reviewer', now()) ON CONFLICT DO NOTHING""")
    return "syn.quote.node"


async def evidence(admin, student: str, text: str = TEXT, *, kind: str = "response_text", channel: str | None = "keypad") -> str:
    iid = await item(admin, "aya", V1, NODE)
    ev = str(uuid.uuid4())
    payload = {"text": text} if kind in ("response_text", "ocr_transcription", "asr_transcription") else {}
    async with admin.acquire() as c:
        await c.execute(
            """INSERT INTO platform.evidence_event (evidence_id, occurred_at, student_id, subject, kind, item_id, admission, payload,
                 source_method, request_id, request_idempotency_key, evidence_kind, evidence_level, channel, map_version_id)
               VALUES ($1::uuid, now(), $2::uuid, 'aya', $3, $4, 'admitted', $5::jsonb, 'grade', $6::uuid, $7, 'independent', $10, $8,
                       $9::uuid)""",
            ev, student, kind, iid, json.dumps(payload), str(uuid.uuid4()), f"synthetic-quote-key-{uuid.uuid4().hex}", channel, V1,
            "A" if channel in ("keypad", None) else "B")   # 拍照、语音、手写板的证据库里最高 B 级
    return ev


async def claim(admin, student: str, evidence_ids: list[str], *, expires: str = "30 days") -> str:
    cid = str(uuid.uuid4())
    async with admin.acquire() as c:
        async with c.transaction():
            await c.execute("SET LOCAL ROLE starbuddy_insight")
            await c.execute(
                """INSERT INTO insight.claim (id, student_id, subject, target_type, target_id, template_key, template_version, value,
                                              confidence, source_type, status, change_reason, visibility, expires_at, evidence_ids)
                   VALUES ($1::uuid, $2::uuid, 'aya', 'node', $3, 'syn.quote.node', 1, '{"level": "apply", "gate": "stable"}'::jsonb,
                           0.8, 'objective', 'established', 'created', '{agent,student,guardian}', now() + $4::text::interval, $5::uuid[])""",
                cid, student, NODE, expires, evidence_ids)
    return cid


async def verify(mpools, student, *checks):
    app_pool, _ = mpools
    async with app_pool.acquire() as conn:
        return await Q.verify(conn, student, [Q.QuoteCheck(f"q{i}", *c) for i, c in enumerate(checks)])


async def test_verified_and_no_match(mpools, student, template):
    _, admin = mpools
    ev = await evidence(admin, student)
    cid = await claim(admin, student, [ev])
    out = await verify(mpools, student, (cid, ev, "先移项，再把x的系数"), (cid, ev, "ｘ 的系数化为 1。"), (cid, ev, "先移项，再把y的系数"),
                       (cid, ev, "先移项。"))
    assert out == {"q0": "ok", "q1": "ok", "q2": "no_match", "q3": "no_match"}


async def test_not_found_is_uniform(mpools, student, template):
    _, admin = mpools
    mine = await evidence(admin, student)
    cid = await claim(admin, student, [mine])
    ocr = await evidence(admin, student, kind="ocr_transcription", channel="photo")
    asr = await evidence(admin, student, kind="asr_transcription", channel="voice")
    photo = await evidence(admin, student, channel="photo")
    canvas = await evidence(admin, student, channel="canvas")
    nochan = await evidence(admin, student, channel=None)
    # OCR / 语音标成键盘通道的证据库里写不进（0017 evidence_event_keypad_kind），种类条件是第二道
    outside = await evidence(admin, student)          # 不在判断依据里
    cid_all = await claim(admin, student, [ocr, asr, photo, canvas, nochan])
    expired = await claim(admin, student, [mine])
    async with admin.acquire() as c:   # 让判断过期（只在测试库，绕开触发器）：学习服务读不到过期判断
        async with c.transaction():
            await c.execute("SET LOCAL session_replication_role = replica")
            await c.execute("""UPDATE insight.claim SET created_at = now() - interval '2 days', expires_at = now() - interval '1 day'
                                WHERE id = $1::uuid""", expired)
    q = "先移项"
    out = await verify(mpools, student, (cid_all, ocr, q), (cid_all, asr, q), (cid_all, photo, q), (cid_all, canvas, q),
                       (cid_all, nochan, q), (cid, outside, q), (expired, mine, q), (cid, str(uuid.uuid4()), q))
    assert set(out.values()) == {"not_found"}, out
    assert await verify(mpools, str(uuid.uuid4()), (cid, mine, q)) == {"q0": "not_found"}     # 不是该生
    assert await verify(mpools, student, ("not-a-uuid", mine, q), (cid, "x", q)) == {"q0": "not_found", "q1": "not_found"}
    assert await verify(mpools, "nope", (cid, mine, q)) == {"q0": "not_found"}


async def test_other_students_evidence_not_found(mpools, student, template):
    _, admin = mpools
    ev = await evidence(admin, student)
    cid = await claim(admin, student, [ev])
    others = await admin.fetch("SELECT id::text FROM platform.student_profile WHERE id <> $1::uuid AND erased_at IS NULL LIMIT 1", student)
    if not others:
        pytest.skip("测试库里没有第二名合成学生")
    other = others[0]["id"]
    assert await verify(mpools, other, (cid, ev, "先移项")) == {"q0": "not_found"}
    # 别的学生的证据被写进本生判断的依据里：仍不认（证据也必须属于该生）
    theirs = await evidence(admin, other)
    mixed = await claim(admin, student, [theirs])
    assert await verify(mpools, student, (mixed, theirs, "先移项")) == {"q0": "not_found"}
    # 本生的证据被写进别的学生的判断：判断也必须属于该生
    foreign = await claim(admin, other, [ev])
    assert await verify(mpools, student, (foreign, ev, "先移项")) == {"q0": "not_found"}


async def test_disputed_until_resolved(mpools, student, template):
    _, admin = mpools
    ev = await evidence(admin, student)
    cid = await claim(admin, student, [ev])
    task = await admin.fetchval(
        """INSERT INTO platform.review_task (subject, student_id, source_kind, source_id, reasons)
           VALUES ('aya', $1::uuid, 'claim', $2::uuid, ARRAY['profile_dispute']::platform.review_reason[]) RETURNING id::text""",
        student, cid)
    assert await verify(mpools, student, (cid, ev, "先移项")) == {"q0": "disputed"}
    acct = await admin.fetchval("SELECT id FROM platform.account LIMIT 1")
    if acct is None:
        pytest.skip("测试库里没有可作结案人的合成账号")
    async with admin.acquire() as c:
        async with c.transaction():
            await c.execute("SET LOCAL session_replication_role = replica")
            await c.execute("""UPDATE platform.review_task SET status = 'resolved', resolved_at = now(), resolution_code = 'syn',
                                      resolved_by_account = $2 WHERE id = $1::uuid""", task, acct)
    assert await verify(mpools, student, (cid, ev, "先移项")) == {"q0": "ok"}


async def test_read_only_and_limits(mpools, student, template):
    _, admin = mpools
    ev = await evidence(admin, student)
    cid = await claim(admin, student, [ev])
    tables = ("platform.evidence_event", "insight.claim", "platform.review_task")
    before = [await admin.fetchval(f"SELECT count(*) FROM {t}") for t in tables]
    await verify(mpools, student, *[(cid, ev, "先移项")] * Q.VERIFY_MAX_QUOTES)
    assert [await admin.fetchval(f"SELECT count(*) FROM {t}") for t in tables] == before
    with pytest.raises(Q.TooManyQuotes):
        await verify(mpools, student, *[(cid, ev, "先移项")] * (Q.VERIFY_MAX_QUOTES + 1))


async def test_page_verdicts_feed_check_page(mpools, student, template):
    _, admin = mpools
    ev = await evidence(admin, student)
    cid = await claim(admin, student, [ev])
    secs = [C.Section("working_on", "接下来{q:a}", (C.Statement(cid, "合成模板渲染文字"),), (C.Quote("a", cid, ev, "再把 x 的系数"),)),
            C.Section("next", "另外{q:a}", (C.Statement(cid, "合成模板渲染文字"),), (C.Quote("a", cid, ev, "再把 y 的系数"),))]
    app_pool, _ = mpools
    async with app_pool.acquire() as conn:
        v = await Q.page_verdicts(conn, student, secs)
        assert v == {("working_on", "a"): "ok", ("next", "a"): "no_match"}
        assert await Q.page_verdicts(conn, student, [C.Section("x", "接下来")]) == {}
        many = [C.Section(f"s{i}", "接下来{q:a}", (), (C.Quote("a", cid, ev, "先移项"),)) for i in range(Q.VERIFY_MAX_QUOTES + 1)]
        assert set((await Q.page_verdicts(conn, student, many)).values()) == {"not_found"}
