"""统一复核队列（冲刺 2-G）：学习服务写判分时同一事务进队、申诉进队、抽检与画像异议入队、审核台闭环。全部合成数据。"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import asyncpg

from conftest import grade_body
from review_loop import STAFF_A, as_staff, close_loop, console, find_task


async def post_grade(api, body) -> dict:
    r = await api.post("/internal/v0/grade", json=body)
    assert r.status_code == 200, r.text
    return r.json()


async def tasks_for(admin, student) -> list[dict]:
    async with admin.acquire() as c:
        rows = await c.fetch("""SELECT source_kind, source_id::text, reasons::text[] AS reasons, status, subject::text
                                  FROM platform.review_task WHERE student_id = $1::uuid""", student)
    return [dict(r) for r in rows]


async def test_review_required_grade_enters_queue_once(api, pools, student):
    _, admin = pools
    body = grade_body(student)
    s = await post_grade(api, body)
    assert s["human_review_required"]
    await post_grade(api, body)  # 重放：同一幂等键
    t = await tasks_for(admin, student)
    assert t == [{"source_kind": "grade_result", "source_id": s["grade_result_id"], "reasons": ["content_not_admitted"],
                  "status": "open", "subject": "aya"}]


async def test_no_review_no_task(api, pools, student, fake_adapter):
    _, admin = pools
    fake_adapter.admission = "admitted"
    s = await post_grade(api, grade_body(student, item="SYN-ADM", text="42"))
    assert not s["human_review_required"]
    assert await tasks_for(admin, student) == []


async def test_appeal_enters_queue(api, pools, student, fake_adapter):
    _, admin = pools
    fake_adapter.admission = "admitted"
    s = await post_grade(api, grade_body(student, item="SYN-ADM", text="42", appeal=True))
    t = await tasks_for(admin, student)
    assert len(t) == 1 and t[0]["source_id"] == s["grade_result_id"] and "student_appeal" in t[0]["reasons"]


async def test_adapter_declared_spot_check_enters_queue(api, pools, student, fake_adapter):
    """契约里 spot_check 是合法的复核原因：适配器声明了，平台照常进队，不能比适配器更松（契约审查 A1）。"""
    _, admin = pools
    fake_adapter.admission = "admitted"

    def mutate(out):
        out["human_review_required"], out["review_reasons"] = True, ["spot_check"]
        return out
    fake_adapter.mutate = mutate
    s = await post_grade(api, grade_body(student, item="SYN-ADM", text="42"))
    t = await tasks_for(admin, student)
    assert len(t) == 1 and t[0]["source_id"] == s["grade_result_id"] and t[0]["reasons"] == ["spot_check"]


async def test_console_reason_list_matches_db_enum(pools):
    """审核台后端的原因白名单与库里的 platform.review_reason 一致（契约审查 B2：防漂移）。"""
    from review_console.app import QUEUE_REASONS
    _, admin = pools
    async with admin.acquire() as c:
        db = await c.fetchval("SELECT enum_range(NULL::platform.review_reason)::text[]")
    assert list(QUEUE_REASONS) == list(db)


async def test_console_closed_loop_aya(api, pools, pg, student):
    s = await post_grade(api, grade_body(student))
    async with console(pg) as client:
        await close_loop(client, source_id=s["grade_result_id"], student_id=student, subject="aya",
                         expect_reasons={"content_not_admitted"}, code="content_issue")
        r = await client.get("/review/v0/queue", params={"status": "closed"}, headers=as_staff(STAFF_A))
        assert r.status_code == 422
        r = await client.get("/review/v0/queue", params={"limit": 500}, headers=as_staff(STAFF_A))
        assert r.status_code == 422
        r = await client.get("/review/v0/queue")
        assert r.status_code == 401
        codes = (await client.get("/review/v0/queue/resolution-codes", headers=as_staff(STAFF_A))).json()["items"]
        assert {c["code"] for c in codes} == {"upheld", "regrade_required", "content_issue", "dispute_forwarded", "not_actionable",
                                                  "process_noted", "weekly_report_ok", "weekly_report_issue"}  # 0036 周报抽检
        r = await client.get(f"/review/v0/queue/{uuid.uuid4()}", headers=as_staff(STAFF_A))
        assert r.status_code == 404
        # 翻页：游标是 (创建时间, id)；不带时区的时间与单独的 before_id 都拒绝
        p1 = (await client.get("/review/v0/queue", params={"limit": 1}, headers=as_staff(STAFF_A))).json()
        assert len(p1["items"]) == 1 and p1["next"] and set(p1["next"]) == {"before", "before_id"}
        p2 = (await client.get("/review/v0/queue", params={"limit": 1, **p1["next"]}, headers=as_staff(STAFF_A))).json()
        assert p2["items"] and p2["items"][0]["id"] != p1["items"][0]["id"]
        assert (p2["items"][0]["created_at"], p2["items"][0]["id"]) < (p1["items"][0]["created_at"], p1["items"][0]["id"])
        r = await client.get("/review/v0/queue", params={"before": "2026-10-01T00:00:00"}, headers=as_staff(STAFF_A))
        assert r.status_code == 422
        r = await client.get("/review/v0/queue", params={"before_id": str(uuid.uuid4())}, headers=as_staff(STAFF_A))
        assert r.status_code == 422


async def test_spot_check_and_profile_dispute(api, pools, pg, student, fake_adapter):
    _, admin = pools
    async with console(pg) as client:  # 建好调度与洞察服务的登录角色
        fake_adapter.admission = "admitted"
        ids = [(await post_grade(api, grade_body(student, item="SYN-ADM", text="42")))["grade_result_id"] for _ in range(30)]
        sched = await asyncpg.connect(**pg.dsn("rq_sched_app"))
        try:
            since = datetime.now(timezone.utc) - timedelta(hours=1)
            n = await sched.fetchval("SELECT platform.review_sample_spot_checks('aya', $1, 0.5)", since)
            assert await sched.fetchval("SELECT platform.review_sample_spot_checks('aya', $1, 0.5)", since) == 0
        finally:
            await sched.close()
        sampled = [t for t in await tasks_for(admin, student) if t["reasons"] == ["spot_check"]]
        async with admin.acquire() as c:  # 测试方独立按同一公式算这个学生应抽中的条数
            expected = await c.fetchval(
                """SELECT count(*) FROM unnest($1::text[]) i
                    WHERE ('x' || substr(md5(i), 1, 8))::bit(32)::bigint < floor(0.5 * 4294967296)""", ids)
        assert len(sampled) == expected <= n and {t["source_id"] for t in sampled} <= set(ids)
        assert 0 < expected < 30  # 30 条抽一半：极小概率全中或全不中（判分 ID 随机，概率约 2e-9）
        await close_loop(client, source_id=sampled[0]["source_id"], student_id=student, subject="aya", expect_reasons={"spot_check"})

        # 画像异议：洞察服务把一条判断放进同一队列（判断由超级用户以合成模板写入）
        claim = str(uuid.uuid4())
        async with admin.acquire() as c:
            await c.execute("""INSERT INTO insight.claim_template (template_key, version, target_type, max_visibility, max_ttl,
                                 text_template, status, reviewed_by, reviewed_at)
                               VALUES ('syn.queue.node', 1, 'node', '{agent}', '30 days', NULL, 'approved', 'test-reviewer', now())
                               ON CONFLICT DO NOTHING""")
            await c.execute("""INSERT INTO insight.claim (id, student_id, subject, target_type, target_id, template_key, template_version,
                                 confidence, source_type, status, change_reason, visibility, expires_at)
                               VALUES ($1::uuid, $2::uuid, 'aya', 'node', 'K-SYN-0001', 'syn.queue.node', 1, 0.5, 'self',
                                       'hypothesis', 'created', '{agent}', now() + interval '10 days')""", claim, student)
        ins = await asyncpg.connect(**pg.dsn("rq_insight_app"))
        try:
            tid = await ins.fetchval("SELECT platform.enqueue_claim_dispute($1)::text", claim)
            assert await ins.fetchval("SELECT platform.enqueue_claim_dispute($1)::text", claim) == tid
        finally:
            await ins.close()
        t = await find_task(client, claim, reason="profile_dispute")
        assert t["id"] == tid and t["source_kind"] == "claim"
        done = await close_loop(client, source_id=claim, student_id=student, subject="aya", expect_reasons={"profile_dispute"},
                                code="dispute_forwarded", wrong_code="regrade_required")
        assert done["source"]["template_key"] == "syn.queue.node" and "value" not in done["source"]
        async with admin.acquire() as c:
            assert await c.fetchval("SELECT status::text FROM insight.claim WHERE id = $1::uuid", claim) == "hypothesis"
