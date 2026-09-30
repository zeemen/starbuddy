"""库上测试（一次性 Postgres，执行 bootstrap 角色与全部迁移）：
- 启动断言：非超级用户、无 BYPASSRLS / CREATEDB、不与其他应用角色共存、非 pg_ 预置角色成员，否则拒绝；
- 洞察服务之外的角色不能写判断；洞察服务不能写 platform、不能写历史与模板；0028 授权够用且读不到原文；
- 合成掌握行 → 判断：状态、原因与证据写进历史（0010 守卫），到期 → 待验证，依据撤下 → 撤回；未签字只到假设；
- 可见范围过滤：本服务的读取 SQL 与 0010 的 BFF 行级策略给出同一组判断。
"""
from __future__ import annotations

from datetime import timedelta

import asyncpg
import pytest

from insight.claims import MasteryRow
from insight.store import InsightStore, RoleError, assert_insight_role, create_pool
from pginsight import (APP_ROLE, BFF_ROLE, BYPASS_ROLE, CREATEDB_ROLE, E, GA, GB, LEARNING_ROLE, M, M_OLD, M_S2, MULTI_ROLE,
                       OTHER_ROLE, PREDEF_ROLE, S1, SA1, TPL_MASTERY)
from support import NODE_A, NODE_B, NODE_C, make_policy

pytestmark = pytest.mark.anyio
UNSIGNED = make_policy()
SIGNED = make_policy(signed=True)


def reset(db) -> None:
    db.psql(f"DELETE FROM insight.claim WHERE student_id IN ('{S1}');")


async def db_now(db):
    c = await asyncpg.connect(**db.connect_kwargs("postgres"))
    try:
        return await c.fetchval("SELECT now()")
    finally:
        await c.close()


def row(i: int, node: str, gate: str, ev: tuple[int, ...], now, level: str = "apply", subject: str = "aya", conf: float = 0.5,
        retest=None, basis: str | None = None) -> MasteryRow:
    return MasteryRow(basis or M[i], S1, subject, node, level, gate, 0.6, conf, tuple(E[j] for j in ev), "kge-mastery@0.1.0.syn",
                      now, retest)


@pytest.mark.parametrize("role", [BYPASS_ROLE, MULTI_ROLE, OTHER_ROLE, PREDEF_ROLE, CREATEDB_ROLE, "postgres"])
async def test_unsafe_login_roles_refuse_to_start(insightdb, role):
    with pytest.raises((RoleError, asyncpg.PostgresError)):
        pool = await create_pool(**insightdb.connect_kwargs(role))
        await pool.close()


async def test_safe_role_starts_and_injected_pool_is_checked(insightdb):
    pool = await create_pool(**insightdb.connect_kwargs(APP_ROLE))
    try:
        async with pool.acquire() as c:
            await assert_insight_role(c)
            assert await c.fetchval("SELECT current_user") == "starbuddy_insight"
    finally:
        await pool.close()
    raw = await asyncpg.connect(**insightdb.connect_kwargs(APP_ROLE))
    try:
        with pytest.raises(RoleError):
            await assert_insight_role(raw)  # 没有 SET ROLE：当前角色不是 starbuddy_insight
    finally:
        await raw.close()


async def _as(db, login: str, role: str | None = None) -> asyncpg.Connection:
    c = await asyncpg.connect(**db.connect_kwargs(login))
    if role:
        await c.execute(f"SET ROLE {role}")
    return c


CLAIM_INSERT = f"""INSERT INTO insight.claim (student_id, subject, target_type, target_id, template_key, template_version, confidence,
  evidence_ids, basis_kind, basis_id, source_type, model_version, status, change_reason, visibility, expires_at)
  VALUES ('{S1}', 'aya', 'node', '{NODE_A}', '{TPL_MASTERY}', 1, 0.5, '{{{E[0]}}}', 'mastery_estimate', '{M[0]}', 'inferred',
          'algo:kge-mastery@0.1.0.syn', 'hypothesis', 'created', '{{agent}}', now() + interval '1 day')"""


@pytest.mark.parametrize("login, role", [(BFF_ROLE, "starbuddy_bff"), (LEARNING_ROLE, "starbuddy_learning")])
async def test_only_insight_can_write_claims(insightdb, login, role):
    c = await _as(insightdb, login, role)
    try:
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await c.execute(CLAIM_INSERT)
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await c.execute("UPDATE insight.claim SET change_reason = 'revised'")
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await c.execute("DELETE FROM insight.claim")
    finally:
        await c.close()


async def test_insight_cannot_write_platform_history_or_templates(insightdb):
    reset(insightdb)
    c = await _as(insightdb, APP_ROLE, "starbuddy_insight")
    try:
        await c.execute(CLAIM_INSERT)  # 对照：洞察服务能写判断
        for sql in (
            f"INSERT INTO platform.mastery_estimate (student_id, subject, skill_local_id, state, confidence, algorithm_version, evidence_ids) "
            f"VALUES ('{S1}', 'aya', 'SYN-X', 'execute', 0.5, 'x@1', '{{{E[0]}}}')",
            f"UPDATE platform.mastery_estimate SET confidence = 0.1 WHERE id = '{M[0]}'",
            "UPDATE platform.attempt SET hint_level_used = 0",
            "DELETE FROM platform.error_attribution",
            f"INSERT INTO insight.claim_history (claim_id, to_status, reason, visibility, value, confidence, evidence_ids, expires_at) "
            f"SELECT id, 'hypothesis', 'created', visibility, value, confidence, evidence_ids, expires_at FROM insight.claim LIMIT 1",
            "UPDATE insight.claim_history SET reason = 'created'",
            f"INSERT INTO insight.claim_template (template_key, version, target_type, max_visibility, max_ttl) VALUES ('x.y', 1, 'node', '{{agent}}', '1 day')",
            f"INSERT INTO insight.behavior_event (student_id, kind, occurred_at, idempotency_key) VALUES ('{S1}', 'session_start', now(), gen_random_uuid())",
            "DELETE FROM insight.claim",
        ):
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await c.execute(sql)
    finally:
        await c.close()
        reset(insightdb)


async def test_0028_grants_suffice_and_hide_raw_text(insightdb):
    c = await _as(insightdb, APP_ROLE, "starbuddy_insight")
    try:
        assert await c.fetchval(f"SELECT count(*) FROM platform.mastery_estimate WHERE student_id = '{S1}'") == 7
        assert await c.fetchval("SELECT count(*) FROM platform.evidence_event WHERE evidence_level = 'A'") == 12
        assert await c.fetchval("SELECT count(verdict) FROM platform.grade_result") > 0
        assert await c.fetchval("SELECT count(cross_subject_category) FROM platform.error_attribution") > 0
        assert await c.fetchval("SELECT count(hint_level_used) FROM platform.attempt") > 0
        for sql in ("SELECT payload FROM platform.evidence_event", "SELECT media_ref FROM platform.evidence_event",
                    "SELECT response_evidence_id FROM platform.attempt", "SELECT self_report FROM platform.attempt",
                    "SELECT evidence_ids FROM platform.grade_result", "SELECT * FROM platform.grade_dimension",
                    "SELECT source_request_id FROM platform.error_attribution", "SELECT * FROM platform.guardian_student_link",
                    "SELECT * FROM platform.account", "SELECT * FROM kge.item_tag"):
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await c.fetch(sql)
    finally:
        await c.close()


async def test_sync_lifecycle_in_database(insightdb):
    reset(insightdb)
    pool = await create_pool(**insightdb.connect_kwargs(APP_ROLE))
    store = InsightStore(pool)
    try:
        now = await db_now(insightdb)
        rows = [row(0, NODE_A, "execute", (0, 1), now), row(1, NODE_B, "stable", (2, 3, 4), now, conf=0.8),
                row(2, NODE_C, "transfer", (5, 6), now, conf=0.9), row(3, NODE_A, "stable", (7,), now, subject="wenli"),
                row(4, NODE_B, "understand", (8,), now, level="understand"),
                row(5, NODE_C, "execute", (9,), now, level="understand", basis=M_OLD),    # 不是当前行：不写
                row(5, NODE_A, "execute", (0,), now, level="know", basis=M_S2)]            # 别的学生的行：不写
        res = await store.sync_mastery_claims(S1, rows, SIGNED)
        assert res.skipped_rows == 2 and res.missing_templates == ()
        c = await asyncpg.connect(**insightdb.connect_kwargs("postgres"))
        try:
            claims = await c.fetch(f"SELECT * FROM insight.claim WHERE student_id = '{S1}' ORDER BY target_id, template_key, subject")
            assert len(claims) == 6  # 5 条知识掌握 + 1 条知识迁移
            established = [r for r in claims if r["status"] == "established"]
            assert established == []  # 合成阈值要求 ≥ 5 条证据：这批都不到
            wenli = [r for r in claims if str(r["subject"]) == "wenli"]
            assert [list(r["visibility"]) for r in wenli] == [["agent"]]
            assert all(list(r["visibility"]) == ["agent", "student", "guardian"] for r in claims if str(r["subject"]) == "aya")
            hist = await c.fetch("SELECT h.reason, h.from_status, h.evidence_ids FROM insight.claim_history h JOIN insight.claim c ON c.id = h.claim_id "
                                 f"WHERE c.student_id = '{S1}'")
            assert len(hist) == 6 and all(h["reason"] == "created" and h["from_status"] is None and h["evidence_ids"] for h in hist)
        finally:
            await c.close()

        # 到期：模拟时间流逝（超级用户 replica 模式改时间戳，只为夹具）→ 待验证
        insightdb.psql(f"SET session_replication_role = replica; UPDATE insight.claim SET created_at = now() - interval '40 days', "
                       f"expires_at = now() - interval '1 minute' WHERE student_id = '{S1}' AND target_id = '{NODE_A}' AND subject = 'aya';")
        # 同时：NODE_B 的行不再给出（依据撤下）→ 撤回
        now2 = await db_now(insightdb)
        rows2 = [row(0, NODE_A, "execute", (0, 1), now - timedelta(minutes=1)), row(2, NODE_C, "transfer", (5, 6), now),
                 row(3, NODE_A, "stable", (7,), now, subject="wenli")]
        res2 = await store.sync_mastery_claims(S1, rows2, UNSIGNED)
        reasons = sorted((o.unit[2], o.reason) for o in res2.ops)
        assert (NODE_A, "expired") in reasons and (NODE_B, "fact_corrected") in reasons
        c = await asyncpg.connect(**insightdb.connect_kwargs("postgres"))
        try:
            st = {(r["target_id"], str(r["subject"]), r["level"]): r["status"] for r in
                  await c.fetch(f"SELECT target_id, subject, value ->> 'level' AS level, status::text AS status FROM insight.claim WHERE student_id = '{S1}' AND template_key = '{TPL_MASTERY}'")}
            assert st[(NODE_A, "aya", "apply")] == "to_verify" and st[(NODE_B, "aya", "apply")] == "withdrawn"
            assert st[(NODE_B, "aya", "understand")] == "withdrawn" and st[(NODE_C, "aya", "apply")] == "hypothesis"
            h = await c.fetch("SELECT h.reason, h.from_status::text AS f, h.to_status::text AS t FROM insight.claim_history h "
                              f"JOIN insight.claim c ON c.id = h.claim_id WHERE c.student_id = '{S1}' AND h.from_status IS NOT NULL ORDER BY h.at")
            assert {(r["f"], r["t"], r["reason"]) for r in h} >= {("hypothesis", "to_verify", "expired"), ("hypothesis", "withdrawn", "fact_corrected")}
        finally:
            await c.close()
        assert now2 >= now
        # 再同步一次：没有任何写入（幂等）
        res3 = await store.sync_mastery_claims(S1, rows2, UNSIGNED)
        assert res3.ops == ()
    finally:
        await pool.close()
        reset(insightdb)


async def test_store_visibility_matches_bff_row_level_security(insightdb):
    reset(insightdb)
    pool = await create_pool(**insightdb.connect_kwargs(APP_ROLE))
    store = InsightStore(pool)
    try:
        now = await db_now(insightdb)
        rows = [row(0, NODE_A, "execute", (0, 1), now), row(2, NODE_C, "transfer", (5, 6), now),
                row(3, NODE_A, "stable", (7,), now, subject="wenli")]
        await store.sync_mastery_claims(S1, rows, UNSIGNED)
        # 一条只给学生看的判断（visibility_changed 单独提交）
        insightdb.psql(f"SET ROLE starbuddy_insight; UPDATE insight.claim SET visibility = '{{agent,student}}', change_reason = 'visibility_changed' "
                       f"WHERE student_id = '{S1}' AND template_key = 'syn.math.node_transfer';")
        for account, audience in ((SA1, "student"), (GA, "guardian")):
            ours = sorted(v.row.id for v in await store.visible_claims(S1, audience))
            c = await _as(insightdb, BFF_ROLE, "starbuddy_bff")
            try:
                await c.execute("SELECT set_config('app.account_id', $1, false)", account)
                theirs = sorted(str(r["id"]) for r in await c.fetch(
                    f"SELECT id FROM insight.claim WHERE student_id = '{S1}' AND $1 = ANY (visibility)", audience))
            finally:
                await c.close()
            assert ours == theirs and ours
        # 数学：NODE_A、NODE_C 两条知识掌握 + NODE_C 一条知识迁移（只给学生）；文砺一条只给 agent
        assert len(await store.visible_claims(S1, "student")) == 3 and len(await store.visible_claims(S1, "guardian")) == 2
        assert len(await store.visible_claims(S1, "agent")) == 4
        c = await _as(insightdb, BFF_ROLE, "starbuddy_bff")
        try:
            await c.execute("SELECT set_config('app.account_id', $1, false)", GB)  # 别家的家长
            assert await c.fetchval(f"SELECT count(*) FROM insight.claim WHERE student_id = '{S1}'") == 0
        finally:
            await c.close()
    finally:
        await pool.close()
        reset(insightdb)


async def test_expiry_sweep(insightdb):
    reset(insightdb)
    pool = await create_pool(**insightdb.connect_kwargs(APP_ROLE))
    store = InsightStore(pool)
    try:
        now = await db_now(insightdb)
        await store.sync_mastery_claims(S1, [row(0, NODE_A, "execute", (0, 1), now)], UNSIGNED)
        assert await store.sweep_expired() == 0
        insightdb.psql(f"SET session_replication_role = replica; UPDATE insight.claim SET created_at = now() - interval '40 days', "
                       f"expires_at = now() - interval '1 minute' WHERE student_id = '{S1}';")
        assert await store.sweep_expired() == 1
        assert insightdb.psql(f"SELECT status FROM insight.claim WHERE student_id = '{S1}'").strip() == "to_verify"
        assert await store.visible_claims(S1, "student") == []  # 已过期的判断前端看不到
    finally:
        await pool.close()
        reset(insightdb)


async def test_metric_claims_never_reach_front_even_if_marked_visible(insightdb):
    """第二道防线：即便有人登记了对学生可见的指标模板、写了对学生可见的指标判断，本服务的前端读取也不返回（spec 设计要点 5）"""
    insightdb.psql("""INSERT INTO insight.claim_template (template_key, version, target_type, max_visibility, max_ttl, text_template,
                        status, reviewed_by, reviewed_at)
                      VALUES ('syn.metric.visible_probe', 1, 'metric', '{agent,student}', '30 days', '【占位】{target_id}', 'approved',
                              'test-reviewer', now()) ON CONFLICT DO NOTHING;""")
    insightdb.psql(f"""SET ROLE starbuddy_insight;
        INSERT INTO insight.claim (student_id, target_type, target_id, template_key, template_version, value, confidence, evidence_ids,
                                   source_type, model_version, status, change_reason, visibility, expires_at)
        VALUES ('{S1}', 'metric', 'hint_dependency', 'syn.metric.visible_probe', 1, '{{"v": 0.2}}', 0.5, '{{{E[0]}}}', 'inferred',
                'algo:sie-middle-metrics@0.1.0', 'hypothesis', 'created', '{{agent,student}}', now() + interval '1 day');""")
    pool = await create_pool(**insightdb.connect_kwargs(APP_ROLE))
    try:
        store = InsightStore(pool)
        assert await store.visible_claims(S1, "student") == []
        assert [v.row.target_type for v in await store.visible_claims(S1, "agent")] == ["metric"]
    finally:
        await pool.close()
        reset(insightdb)


async def test_injected_pool_is_role_checked(insightdb):
    from insight.service.app import create_app
    from insight.store import _init_connection
    from support import settings
    pool = await asyncpg.create_pool(min_size=1, max_size=2, init=_init_connection, **insightdb.connect_kwargs(BYPASS_ROLE))
    try:
        app = create_app(settings(), pool=pool)
        with pytest.raises(RoleError):
            async with app.router.lifespan_context(app):
                pass
    finally:
        await pool.close()
