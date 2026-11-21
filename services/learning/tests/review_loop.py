"""统一复核队列的闭环（冲刺 2-G，specs/review-queue-sprint2/spec.md 设计要点 4）：审核台后端 + 两个合成 staff。

三科的端到端（阿鸦、知行、文砺）各自判分写入平台后，用 close_loop 走完“进队 → 领取 → 结案”，断言：
- 队列与详情只给学生的不透明指针，没有 student_id、没有作答原文；
- 别人领取的不能领、不能结案；结案码只收白名单；
- 结案后任务 resolved，判分不变（结案不改分）。
登录角色名带 rq_ 前缀，与 test_review_console 的角色分开；重复创建是幂等的。
"""
from __future__ import annotations

import uuid
from contextlib import asynccontextmanager

import asyncpg
import httpx

from review_console.app import create_app

STAFF_A = uuid.UUID("5fa00000-0000-4000-8000-00000000005f")
STAFF_B = uuid.UUID("5fb00000-0000-4000-8000-00000000005f")
NON_STAFF = uuid.UUID("5fc00000-0000-4000-8000-00000000005f")

ROLES_SQL = f"""
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rq_kge_app') THEN CREATE ROLE rq_kge_app LOGIN IN ROLE starbuddy_kge; END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rq_sign_app') THEN CREATE ROLE rq_sign_app LOGIN IN ROLE starbuddy_review; END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rq_sched_app') THEN CREATE ROLE rq_sched_app LOGIN IN ROLE starbuddy_scheduler; END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rq_insight_app') THEN CREATE ROLE rq_insight_app LOGIN IN ROLE starbuddy_insight; END IF;
END $$;
INSERT INTO platform.account (id, kind) VALUES ('{STAFF_A}', 'staff'), ('{STAFF_B}', 'staff'), ('{NON_STAFF}', 'guardian')
  ON CONFLICT (id) DO NOTHING;
"""


def as_staff(account: uuid.UUID) -> dict:
    return {"x-test-account": str(account)}


def _header_auth():
    # 只在测试里：按请求头选合成账号，模拟 staff 登录会话（审核台 PR 4）
    async def _a(req):
        v = req.headers.get("x-test-account")
        return uuid.UUID(v) if v else None
    return _a


@asynccontextmanager
async def console(pg):
    """审核台后端（签字连接只继承 starbuddy_review）。"""
    pg.psql(ROLES_SQL)
    kp = await asyncpg.create_pool(**pg.dsn("rq_kge_app"), min_size=1, max_size=2)
    rp = await asyncpg.create_pool(**pg.dsn("rq_sign_app"), min_size=1, max_size=2)
    try:
        app = create_app(authenticate=_header_auth(), pools=(kp, rp))
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1") as client:
                yield client
    finally:
        await kp.close()
        await rp.close()


async def find_task(client: httpx.AsyncClient, source_id: str, **params) -> dict:
    r = await client.get("/review/v0/queue", params={"limit": 200, **params}, headers=as_staff(STAFF_A))
    assert r.status_code == 200, r.text
    hits = [t for t in r.json()["items"] if t["source_id"] == source_id]
    assert len(hits) == 1, f"复核队列里应恰好有一条指向 {source_id} 的任务"
    return hits[0]


async def close_loop(client: httpx.AsyncClient, *, source_id: str, student_id: str, subject: str, expect_reasons: set[str],
                     code: str = "upheld", wrong_code: str = "dispute_forwarded") -> dict:
    """进队 → 领取 → 结案。返回结案后的详情。"""
    task = await find_task(client, source_id, subject=subject, status="open")
    assert task["subject"] == subject and expect_reasons <= set(task["reasons"])
    assert "student_id" not in task and task["student_ref"].startswith("s-") and student_id not in str(task)
    tid = task["id"]
    detail = (await client.get(f"/review/v0/queue/{tid}", headers=as_staff(STAFF_A))).json()
    assert student_id not in str(detail) and "student_id" not in detail and detail["status"] == "open"
    if detail["source_kind"] == "grade_result":
        src = detail["source"]
        assert src["dimensions"] and all("confidence" in d and d["evidence_ids"] for d in src["dimensions"])
        assert src["evidence_ids"] and src["model_version"]
        assert not ({"text", "payload", "response"} & set(src))  # 不带作答原文

    assert (await client.post(f"/review/v0/queue/{tid}/take", headers=as_staff(NON_STAFF))).status_code == 403
    assert (await client.post(f"/review/v0/queue/{tid}/resolve", json={"resolution_code": code},
                              headers=as_staff(STAFF_A))).status_code == 409  # 没领取不能结案
    assert (await client.post(f"/review/v0/queue/{tid}/take", headers=as_staff(STAFF_A))).status_code == 200
    # 放回队列再领（specs/review-console-responses：每个接口至少一次 2xx 经过响应模型）
    r = await client.post(f"/review/v0/queue/{tid}/release", headers=as_staff(STAFF_A))
    assert r.status_code == 200 and r.json() == {"status": "open"}, r.text
    assert (await client.get(f"/review/v0/queue/{tid}", headers=as_staff(STAFF_A))).json()["assigned"] is False
    assert (await client.post(f"/review/v0/queue/{tid}/take", headers=as_staff(STAFF_A))).status_code == 200
    r = await client.post(f"/review/v0/queue/{tid}/take", headers=as_staff(STAFF_B))
    assert r.status_code == 409 and r.json()["code"] == "RULE_VIOLATION"
    assert (await client.post(f"/review/v0/queue/{tid}/resolve", json={"resolution_code": code},
                              headers=as_staff(STAFF_B))).status_code == 409
    assert (await client.post(f"/review/v0/queue/{tid}/resolve", json={"resolution_code": wrong_code},
                              headers=as_staff(STAFF_A))).status_code == 422
    assert (await client.post(f"/review/v0/queue/{tid}/resolve", json={"resolution_code": "made_up_code"},
                              headers=as_staff(STAFF_A))).status_code == 422
    r = await client.post(f"/review/v0/queue/{tid}/resolve", json={"resolution_code": code}, headers=as_staff(STAFF_A))
    assert r.status_code == 200, r.text
    done = (await client.get(f"/review/v0/queue/{tid}", headers=as_staff(STAFF_A))).json()
    assert done["status"] == "resolved" and done["resolution_code"] == code and done["resolved_by_me"] and done["resolved_at"]
    if done["source_kind"] == "grade_result":
        assert done["source"]["score_total"] == detail["source"]["score_total"] and not done["source"]["superseded"]
    return done
