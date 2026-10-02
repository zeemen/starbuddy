"""KGE-04 第二步 PR 4 集成测试：学情档案页（specs/kge-04-mastery-step2/spec.md 第 5 节；第一步 spec 第 14 节与档案页四项验收）。

一次性 Postgres（本模块独占，执行全部迁移 0001–0037；合成地图、题目与配置同 test_mastery_db）。判断由测试夹具以 starbuddy_insight
身份写入（只在测试库；学习服务没有这个身份），学习服务代码只以 starbuddy_learning 身份读判断、写档案页。
- 引用：陈述只引用成立 / 假设；所引判断被推翻、撤回、过期后读取结果里消失；学生、家长视图（0037 陈述视图）里没有待验证判断；
- 拦截程度升级：阻断后页为纯模板版本（库里 check_status = blocked_fallback、没有衔接语）并有一条阻断记录；
- 惰性编译（作答与重算不触发编译由 test_mastery_profile.py 静态检查）：判断未变 → 不重编；判断变化后 → 重编一次；周报调用 → 编一次；超出时间预算 → template_only、不写库；
- 边界：默认不调模型（model_version 为空）；学习服务对 insight.claim 的 INSERT / UPDATE 被拒；已执行删除的学生不写档案页；
  超时取消后连接池仍可用；
- 接口（learning-internal 1.1.0 草稿内追加）：ensure 只认编排层（会话开始）与洞察服务（周报），read 只认编排层，响应符合契约。
全部合成数据（规则 8）。
"""
from __future__ import annotations

import asyncio
import json
import uuid
from datetime import timedelta

import asyncpg
import httpx
import pytest

from conftest import CREDENTIAL, settings
from learning.app import create_app
from learning.contract import internal_contract
from learning.mastery import profile as PF
from test_mastery_db import (V1, engine, invariants, mpg, mpools, normal_path, student)  # noqa: F401

INSIGHT_TOKEN = "synthetic-insight-to-learning-credential-0001"
ORCH_TOKEN = "synthetic-orchestrator-to-learning-credential-01"
TEXT = "（合成模板）{node_id}｜{gate}"
TEMPLATES = ("syn.mastery.stable", "syn.mastery.unstable", "syn.mastery.weak", "syn.progress.recent", "syn.mastery.node")


@pytest.fixture(scope="module")
async def templates(mpools):
    _, admin = mpools
    async with admin.acquire() as c:
        for key in TEMPLATES:
            await c.execute(
                """INSERT INTO insight.claim_template (template_key, version, target_type, max_visibility, max_ttl, text_template, status,
                                                       reviewed_by, reviewed_at)
                   VALUES ($1, 1, 'node', '{agent,student,guardian}', '60 days', $2, 'approved', 'test-reviewer', now())
                   ON CONFLICT DO NOTHING""", key, TEXT)
    return TEMPLATES


@pytest.fixture
async def pages(mpools, templates):
    app_pool, _ = mpools
    return PF.ProfilePages(app_pool, PF.synthetic_profile_config())


async def put_claim(admin, student: str, key: str = "syn.mastery.stable", status: str = "established", *, gate: str = "stable",
                    node: str = "MATH.SYN.T1.N1", visibility: tuple[str, ...] = ("agent", "student", "guardian"), subject: str = "aya") -> str:
    """以洞察服务身份写一条合成判断（只在测试库）；待验证经“成立 → 待验证（异议）”流转"""
    cid = str(uuid.uuid4())
    first = "hypothesis" if status == "hypothesis" else "established"
    async with admin.acquire() as c:
        async with c.transaction():
            await c.execute("SET LOCAL ROLE starbuddy_insight")
            await c.execute(
                """INSERT INTO insight.claim (id, student_id, subject, target_type, target_id, template_key, template_version, value, confidence,
                                              source_type, status, change_reason, visibility, expires_at)
                   VALUES ($1::uuid, $2::uuid, $3::platform.subject, 'node', $4, $5, 1, $6::jsonb, 0.8, 'objective', $7::insight.claim_status,
                           'created', $8::text[], now() + interval '30 days')""",
                cid, student, subject, node, key, json.dumps({"level": "apply", "gate": gate}), first, list(visibility))
            if status == "to_verify":
                await c.execute("UPDATE insight.claim SET status = 'to_verify', change_reason = 'dispute' WHERE id = $1::uuid", cid)
    return cid


async def change(admin, cid: str, status: str, reason: str) -> None:
    async with admin.acquire() as c:
        async with c.transaction():
            await c.execute("SET LOCAL ROLE starbuddy_insight")
            await c.execute("UPDATE insight.claim SET status = $2::insight.claim_status, change_reason = $3 WHERE id = $1::uuid",
                            cid, status, reason)


async def page_row(admin, student: str):
    async with admin.acquire() as c:
        return await c.fetchrow("""SELECT id::text, sections, check_status, model_version, claims_digest, compiler_version::text AS cv
                                     FROM platform.profile_page WHERE student_id = $1::uuid AND subject = 'aya'""", student)


async def checks(admin, student: str) -> list:
    async with admin.acquire() as c:
        return await c.fetch("SELECT violations::text AS v, profile_page_id::text AS page FROM platform.profile_page_check WHERE student_id = $1::uuid",
                             student)


def ids(section) -> list[str]:
    return [x["claim_id"] for x in section["statements"]]


def sec(page: dict, key: str) -> dict:
    return next(s for s in page["sections"] if s["key"] == key)


# ---------- 惰性编译 ----------
async def test_lazy_compile_only_when_claims_change(mpools, pages, student):
    _, admin = mpools
    assert (await pages.ensure(student, "aya", "session_start")).outcome == "empty"          # 没有判断：不写
    assert await page_row(admin, student) is None
    c1 = await put_claim(admin, student)
    r = await pages.ensure(student, "aya", "session_start")
    assert r.outcome == "compiled" and r.check_status == "passed" and pages.stats["compiled"] == 1
    row = await page_row(admin, student)
    assert row["model_version"] is None and row["cv"] == PF.COMPILER_VERSION                 # 不调模型
    for _ in range(3):                                                                        # 判断未变：会话开始不重编
        assert (await pages.ensure(student, "aya", "session_start")).outcome == "unchanged"
    assert pages.stats["compiled"] == 1
    await put_claim(admin, student, "syn.mastery.unstable", "hypothesis", gate="execute", node="MATH.SYN.T1.N2")
    assert (await pages.ensure(student, "aya", "session_start")).outcome == "compiled"       # 判断变化：重编一次
    assert (await pages.ensure(student, "aya", "session_start")).outcome == "unchanged"
    assert pages.stats["compiled"] == 2
    await change(admin, c1, "to_verify", "dispute")                                           # 状态变化也重编
    assert (await pages.ensure(student, "aya", "session_start")).outcome == "compiled"
    assert pages.stats["compiled"] == 3
    async with admin.acquire() as c:
        assert await c.fetchval("SELECT count(*) FROM platform.profile_page WHERE student_id = $1::uuid", student) == 1   # 原地重编


async def test_weekly_trigger_compiles_once(mpools, pages, student):
    _, admin = mpools
    await put_claim(admin, student)
    assert (await pages.ensure(student, "aya", "weekly")).outcome == "compiled"
    assert (await pages.ensure(student, "aya", "weekly")).outcome == "unchanged"
    assert pages.stats["compiled"] == 1


# “作答与重算不触发编译”由 tests/test_mastery_profile.py 的静态检查保证（写入、重算、对账、掌握读取的模块不引用档案页模块）。


async def test_erased_student_gets_no_page(mpools, pages, student):
    """已执行删除（0035 student_profile.erased_at）的学生：ensure 不写档案页与阻断记录"""
    _, admin = mpools
    await put_claim(admin, student)
    async with admin.acquire() as c:
        await c.execute("UPDATE platform.student_profile SET erased_at = now() WHERE id = $1::uuid", student)
    r = await pages.ensure(student, "aya", "weekly")
    assert r.outcome == "empty" and pages.stats["erased"] == 1 and pages.stats["compiled"] == 0
    assert await page_row(admin, student) is None and await checks(admin, student) == []


async def test_budget_exceeded_returns_template_only_without_writing(mpools, pages, student):
    _, admin = mpools
    await put_claim(admin, student)
    async with admin.acquire() as c:                                                          # 另一个会话占着同一把档案页锁
        await c.execute("SELECT pg_advisory_lock(hashtextextended($1, 0))", f"platform.profile_page:{student}:aya")
        try:
            r = await pages.ensure(student, "aya", "session_start", budget=0.3)
        finally:
            await c.execute("SELECT pg_advisory_unlock(hashtextextended($1, 0))", f"platform.profile_page:{student}:aya")
    assert r.outcome == "template_only" and pages.stats["timeout"] == 1 and pages.stats["compiled"] == 0
    assert await page_row(admin, student) is None
    page = await pages.read(student, "aya")                                                   # 读取：纯模板版本，不阻塞
    assert page["variant"] == "template_only" and all(s["connective"] == "" for s in page["sections"])
    assert len(ids(sec(page, "mastered"))) == 1
    # 超时取消后连接池仍可用（被取消的事务已回滚、连接已归还）：锁释放后再 ensure 照常编译
    for _ in range(3):
        r = await pages.ensure(student, "aya", "session_start")
    assert r.outcome == "unchanged" and pages.stats["compiled"] == 1 and await page_row(admin, student) is not None


# ---------- 引用与读取时重核 ----------
async def test_read_follows_claim_state(mpools, pages, student):
    _, admin = mpools
    c1 = await put_claim(admin, student)
    c2 = await put_claim(admin, student, node="MATH.SYN.T1.N2")
    c3 = await put_claim(admin, student, "syn.mastery.unstable", "hypothesis", gate="execute", node="MATH.SYN.T1.N3")
    c4 = await put_claim(admin, student, "syn.mastery.unstable", "to_verify", gate="execute", node="MATH.SYN.T1.N4")
    await pages.ensure(student, "aya", "session_start")
    page = await pages.read(student, "aya")
    assert page["variant"] == "compiled" and internal_contract("1.0.0").validate("ProfilePage", page) == []
    assert ids(sec(page, "mastered")) == sorted([c1, c2], key=lambda x: {c1: 1, c2: 2}[x]) and sec(page, "mastered")["connective"] == "另外"
    assert ids(sec(page, "working_on")) == [c3]
    assert [x["claim_id"] for x in sec(page, "working_on")["todo"]] == [c4]                   # 待验证只作待办
    assert sec(page, "mastered")["statements"][0]["text"] == "（合成模板）MATH.SYN.T1.N1｜stable"
    row = await page_row(admin, student)
    assert "合成模板" not in row["sections"]                                                   # 陈述文字不落库
    # 推翻（成立 → 待验证 → 推翻）、撤回：读取结果里消失（判断变了还没重编 → 纯模板版本）
    await change(admin, c1, "to_verify", "dispute")
    await change(admin, c1, "refuted", "verification_refuted")
    await change(admin, c3, "withdrawn", "fact_corrected")
    page = await pages.read(student, "aya")
    assert page["variant"] == "template_only" and ids(sec(page, "mastered")) == [c2] and ids(sec(page, "working_on")) == []
    assert all(s["connective"] == "" for s in page["sections"])
    await pages.ensure(student, "aya", "session_start")
    page = await pages.read(student, "aya")
    assert page["variant"] == "compiled" and ids(sec(page, "mastered")) == [c2] and ids(sec(page, "working_on")) == []
    # 过期：读不到即消失
    async with admin.acquire() as c:
        await c.execute("SET session_replication_role = replica")                             # 跳过守卫，模拟到期（只在测试库）
        await c.execute("UPDATE insight.claim SET expires_at = now() - interval '1 second', created_at = now() - interval '2 days' WHERE id = $1::uuid", c2)
        await c.execute("SET session_replication_role = origin")
    page = await pages.read(student, "aya")
    assert all(s["statements"] == [] for s in page["sections"])
    assert all(c not in json.dumps(page) for c in (c1, c2, c3))


async def test_student_and_guardian_views_have_no_to_verify(mpools, pages, student):
    _, admin = mpools
    c1 = await put_claim(admin, student)
    c4 = await put_claim(admin, student, "syn.mastery.unstable", "to_verify", gate="execute", node="MATH.SYN.T1.N4")
    await pages.ensure(student, "aya", "session_start")
    async with admin.acquire() as c:
        acct, guardian = await c.fetchrow(
            """SELECT s.account_id::text, h.created_by_account::text FROM platform.student_profile s
                 JOIN platform.household h ON h.id = s.household_id WHERE s.id = $1::uuid""", student)
        await c.execute("INSERT INTO platform.guardian_student_link (guardian_account_id, student_id, granted_via) VALUES ($1::uuid, $2::uuid, 'household_creator')",
                        guardian, student)
        seen = {}
        for who in (acct, guardian):
            async with c.transaction():
                await c.execute("SET LOCAL ROLE starbuddy_bff")
                await c.execute("SELECT set_config('app.account_id', $1, true)", who)
                seen[who] = [r[0] for r in await c.fetch(
                    "SELECT claim_id::text FROM platform.profile_page_statement_v WHERE student_id = $1::uuid", student)]
    assert seen[acct] == [c1] and seen[guardian] == [c1]
    assert all(c4 not in v for v in seen.values())


# ---------- 一致性检查：阻断后纯模板版本 + 阻断记录 ----------
@pytest.mark.parametrize("bad,code", [("这部分薄弱", "degree_escalation"), ("已经掌握得很扎实", "new_conclusion"),
                                      ("这周做对了3题", "number_in_connective"), ("你很聪明", "forbidden_inference"),
                                      ("阿鸦陪你", "persona_language")])
async def test_blocked_compile_falls_back_and_records(mpools, pages, student, bad, code):
    _, admin = mpools
    await put_claim(admin, student, "syn.mastery.unstable", "hypothesis", gate="execute")
    r = await pages.ensure(student, "aya", "session_start", connective=lambda k, s: bad)
    assert r.outcome == "blocked_fallback"
    row = await page_row(admin, student)
    secs = json.loads(row["sections"])
    assert row["check_status"] == "blocked_fallback" and all(s["connective"] == "" for s in secs)
    assert sum(len(s["statements"]) for s in secs) == 1                                       # 纯模板版本：陈述保留
    recs = await checks(admin, student)
    assert len(recs) == 1 and recs[0]["page"] == row["id"]
    vs = json.loads(recs[0]["v"])
    assert code in {v["code"] for v in vs}
    assert all(v["fragment"] == "" for v in vs if v["code"] in ("forbidden_inference", "number_in_connective"))
    page = await pages.read(student, "aya")
    assert page["variant"] == "compiled" and page["check_status"] == "blocked_fallback" and all(s["connective"] == "" for s in page["sections"])


async def test_compliant_compile_passes_without_records(mpools, pages, student):
    _, admin = mpools
    await put_claim(admin, student)
    await put_claim(admin, student, "syn.mastery.weak", gate="understand", node="MATH.SYN.T1.N2")
    await put_claim(admin, student, "syn.progress.recent", node="MATH.SYN.T1.N3")
    assert (await pages.ensure(student, "aya", "session_start")).outcome == "compiled"
    assert await checks(admin, student) == []


# ---------- 边界 ----------
async def test_learning_role_cannot_write_claims(mpools, student, templates):
    app_pool, admin = mpools
    c1 = await put_claim(admin, student)
    async with app_pool.acquire() as c:
        for sql in ("""INSERT INTO insight.claim (student_id, subject, target_type, target_id, template_key, template_version, confidence,
                                                   source_type, status, change_reason, visibility, expires_at)
                       VALUES ($1::uuid, 'aya', 'node', 'MATH.SYN.T1.N1', 'syn.mastery.stable', 1, 0.8, 'objective', 'established', 'created',
                               '{agent}', now() + interval '1 day')""",
                    "UPDATE insight.claim SET status = 'withdrawn', change_reason = 'fact_corrected' WHERE student_id = $1::uuid"):
            async with c.transaction():
                await c.execute("SET LOCAL ROLE starbuddy_learning")
                with pytest.raises(asyncpg.InsufficientPrivilegeError):
                    await c.execute(sql, student)
    async with admin.acquire() as c:
        assert await c.fetchval("SELECT status::text FROM insight.claim WHERE id = $1::uuid", c1) == "established"


async def test_other_subject_claims_never_enter_page(mpools, pages, student):
    _, admin = mpools
    c1 = await put_claim(admin, student)
    cw = await put_claim(admin, student, subject="wenli", node="CHN.SYN.T1.N1")
    assert (await pages.ensure(student, "aya", "session_start")).outcome == "compiled"
    page = await pages.read(student, "aya")
    assert ids(sec(page, "mastered")) == [c1] and cw not in json.dumps(page)


async def test_unconfigured_subject_is_unavailable(pages, student):
    with pytest.raises(PF.ProfileUnavailable):
        await pages.ensure(student, "wenli", "session_start")
    with pytest.raises(PF.ProfileUnavailable):
        await pages.read(student, "wenli")


# ---------- 接口（learning-internal 1.1.0 草稿内追加） ----------
@pytest.fixture
async def client(mpools, pages):
    app_pool, _ = mpools
    app = create_app(settings(insight_client_credential=INSIGHT_TOKEN, orchestrator_client_credential=ORCH_TOKEN), pool=app_pool,
                     transport=httpx.MockTransport(lambda r: httpx.Response(500)), profile=pages)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://learning.test") as cl:
            yield cl


def _ensure(student, trigger="session_start", subject="aya"):
    return {"request_id": str(uuid.uuid4()), "student_ref": student, "subject": subject, "trigger": trigger}


def _post(cl, path, body, token):
    return cl.post(path, json=body, headers={"authorization": f"Bearer {token}"})


async def test_ensure_and_read_endpoints(client, mpools, pages, student):
    _, admin = mpools
    c1 = await put_claim(admin, student)
    c = internal_contract("1.0.0")
    r = await _post(client, "/internal/v1/profile-page/ensure", _ensure(student), ORCH_TOKEN)
    assert r.status_code == 200, r.text
    assert c.validate("ProfilePageEnsureResult", r.json()) == [] and r.json()["outcome"] == "compiled"
    assert c1 not in r.text                                                                     # ensure 响应没有判断
    r = await _post(client, "/internal/v1/profile-page/ensure", _ensure(student, "weekly"), INSIGHT_TOKEN)
    assert r.status_code == 200 and r.json()["outcome"] == "unchanged"
    # 令牌与触发点必须一致；BFF 令牌一律 401
    assert (await _post(client, "/internal/v1/profile-page/ensure", _ensure(student, "weekly"), ORCH_TOKEN)).status_code == 403
    assert (await _post(client, "/internal/v1/profile-page/ensure", _ensure(student), INSIGHT_TOKEN)).status_code == 403
    assert (await _post(client, "/internal/v1/profile-page/ensure", _ensure(student), CREDENTIAL)).status_code == 401
    rd = {"request_id": str(uuid.uuid4()), "student_ref": student, "subject": "aya"}
    r = await _post(client, "/internal/v1/profile-page/read", rd, ORCH_TOKEN)
    assert r.status_code == 200 and c.validate("ProfilePage", r.json()) == [] and c1 in r.text
    assert (await _post(client, "/internal/v1/profile-page/read", rd, INSIGHT_TOKEN)).status_code == 401   # Agent 版只给编排层
    assert (await _post(client, "/internal/v1/profile-page/read", rd, CREDENTIAL)).status_code == 401
    # 契约外的请求、未配置的学科
    bad = _ensure(student) | {"trigger": "attempt"}
    assert (await _post(client, "/internal/v1/profile-page/ensure", bad, ORCH_TOKEN)).status_code == 400
    r = await _post(client, "/internal/v1/profile-page/ensure", _ensure(student, subject="wenli"), ORCH_TOKEN)
    assert r.status_code == 409 and r.json()["code"] == "PROFILE_UNAVAILABLE"


async def test_endpoints_unavailable_without_config(mpools, student):
    app_pool, _ = mpools
    app = create_app(settings(orchestrator_client_credential=ORCH_TOKEN), pool=app_pool,
                     transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://learning.test") as cl:
            r = await _post(cl, "/internal/v1/profile-page/ensure", _ensure(student), ORCH_TOKEN)
    assert r.status_code == 409 and r.json()["code"] == "PROFILE_UNAVAILABLE"
