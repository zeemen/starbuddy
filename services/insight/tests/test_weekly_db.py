"""跨科周报 v0 的库上测试（一次性 Postgres，全部迁移含 0036；合成数据）：
- 每周任务（jobs.weekly_reports）：合成掌握行 → 判断 → 学生版成长手记与家长版周报落库，每句带 claim_id、段落与模板引用，客观栏来源 objective；
  重复运行不重写；抽检进统一复核队列（默认 10%、至少 5 份，不超过总数），抽检不阻断；
- 两道检查任一不过 → withheld（只存阻断编码），学生、家长都读不到；
- 读：学生本人只读学生版、有效绑定家长只读家长版（0036 RLS），另一户读不到；
- 客观栏：会话结束时长、计划完成（先跳过后完成算完成）、各科作答用时与题数（英语不出现），只数本周（上海时区）。
"""
from __future__ import annotations

import dataclasses
import json
from datetime import datetime, timedelta

import asyncpg
import pytest

from insight import jobs
from insight.claims import MasteryRow
from insight.shared import consistency
from insight import store_weekly
from insight.store import InsightStore, create_pool
from insight.store_sie03 import Unavailable
from insight.plan_tasks import PlanRecord, plan_outcome_counts
from insight.store import load_metric_inputs
from insight.store_weekly import objective_facts
from insight.weekly import objective_column, synthetic_weekly_check_config, synthetic_weekly_policy, week_of, week_window
from pginsight import APP_ROLE, BFF_ROLE, E, GA, GB, M, M_S2, S1, S2, SA1, SA2
from support import NODE_A, NODE_B, NODE_C, make_policy

pytestmark = pytest.mark.anyio
POLICY = make_policy()
WEEKLY = synthetic_weekly_policy()
CFG = synthetic_weekly_check_config()


def reset(db) -> None:
    """清掉 S1、S2 的判断与周报（周报的守卫只放行删除请求：以超级用户在复制模式下删，只为夹具）"""
    db.psql(f"""SET session_replication_role = replica;
                DELETE FROM insight.weekly_report WHERE student_id IN ('{S1}', '{S2}');
                DELETE FROM platform.review_task WHERE source_kind = 'weekly_report';
                DELETE FROM insight.claim WHERE student_id IN ('{S1}', '{S2}');""")


async def su(db) -> asyncpg.Connection:
    return await asyncpg.connect(**db.connect_kwargs("postgres"))


def row(i: int, node: str, gate: str, ev: tuple[int, ...], now, student: str = S1, basis: str | None = None) -> MasteryRow:
    return MasteryRow(basis or M[i], student, "aya", node, "apply", gate, 0.6, 0.5, tuple(E[j] for j in ev), "kge-mastery@0.1.0.syn", now)


async def bff_reports(db, account: str) -> list[asyncpg.Record]:
    c = await asyncpg.connect(**db.connect_kwargs(BFF_ROLE))
    try:
        await c.execute("SELECT set_config('app.account_id', $1, false)", account)
        return await c.fetch("SELECT student_id::text AS s, audience, status, sentences, objective FROM insight.weekly_report ORDER BY 1, 2")
    finally:
        await c.close()


async def test_weekly_job_generates_both_versions_and_samples(insightdb):
    reset(insightdb)
    pool = await create_pool(**insightdb.connect_kwargs(APP_ROLE))
    store = InsightStore(pool)
    try:
        c = await su(insightdb)
        try:
            now = await c.fetchval("SELECT now()")
            today = await c.fetchval("SELECT (now() AT TIME ZONE 'Asia/Shanghai')::date")
        finally:
            await c.close()
        week = week_of(today)[0] - timedelta(days=7)  # 上一周（只收已结束的周）
        await store.sync_mastery_claims(S1, [row(0, NODE_A, "execute", (0, 1), now), row(1, NODE_B, "stable", (2, 3, 4), now),
                                             row(2, NODE_C, "transfer", (5, 6), now)], POLICY)
        # 迁移判断“上周新建”：以超级用户在复制模式下把新建时间挪到上周三（只为夹具）
        insightdb.psql(f"""SET session_replication_role = replica;
            UPDATE insight.claim SET created_at = ('{week}'::date + 2)::timestamp AT TIME ZONE 'Asia/Shanghai'
             WHERE student_id = '{S1}' AND template_key = '{POLICY.templates["node_transfer"].key}';""")
        run = await jobs.weekly_reports(store, week, POLICY, WEEKLY, CFG, students=[S1], env="test")
        assert [(o.audience, o.status) for o in run.outcomes] == [("student", "generated"), ("guardian", "generated")]
        assert run.sampled == 2  # 2 份 × 10% 不足 5 份：全部进队（不超过总数）

        c = await su(insightdb)
        try:
            rows = await c.fetch(f"""SELECT w.*, t.reasons::text[] AS reasons, t.source_kind, t.subject::text AS task_subject
                                       FROM insight.weekly_report w LEFT JOIN platform.review_task t ON t.id = w.spot_check_task_id
                                      WHERE w.student_id = '{S1}' ORDER BY w.audience DESC""")
            claims = {str(r["id"]): r for r in await c.fetch(f"SELECT * FROM insight.claim WHERE student_id = '{S1}'")}
        finally:
            await c.close()
        assert [r["audience"] for r in rows] == ["student", "guardian"]
        for r in rows:
            sents = json.loads(r["sentences"]) if isinstance(r["sentences"], str) else r["sentences"]
            obj = json.loads(r["objective"]) if isinstance(r["objective"], str) else r["objective"]
            assert r["status"] == "generated" and r["week_start"] == week and r["week_end"] == week + timedelta(days=6)
            assert r["algorithm_version"] == "sie-weekly@0.2.0" and r["policy_version"] == f"{POLICY.config_version}_{WEEKLY.config_version}"
            assert sents and all(s["claim_id"] in claims for s in sents)  # 每句都引用这个学生的判断
            assert all(claims[s["claim_id"]]["template_key"] == s["template_key"] for s in sents)
            # 每句的修订标记 = 写入时判断的 updated_at（安全审查 F1）
            assert all(s["claim_revision"] and claims[s["claim_id"]]["updated_at"] == datetime.fromisoformat(s["claim_revision"]) for s in sents)
            assert obj["source_type"] == "objective" and obj["self_marked"]["source_type"] == "self"  # 计划计数单独一栏，学生自述（0057）
            assert r["spot_check_task_id"] is not None and r["reasons"] == ["spot_check"] and r["source_kind"] == "weekly_report"
            assert r["task_subject"] == "aya"
        student, guardian = [json.loads(r["sentences"]) if isinstance(r["sentences"], str) else r["sentences"] for r in rows]
        assert [s["section"] for s in student] == ["lit", "working_on", "working_on"]   # 迁移判断本周新建 → 点亮；掌握判断低于迁移 → 攻克
        assert [s["section"] for s in guardian] == ["progress", "stuck", "stuck"]
        assert all("confidence_band" not in s for s in student) and all(s["confidence_band"] for s in guardian)

        # 读：学生本人只读学生版，有效绑定家长只读家长版，另一户读不到（0036 RLS）
        assert [(r["s"], r["audience"]) for r in await bff_reports(insightdb, SA1)] == [(S1, "student")]
        assert [(r["s"], r["audience"]) for r in await bff_reports(insightdb, GA)] == [(S1, "guardian")]
        assert await bff_reports(insightdb, GB) == [] and await bff_reports(insightdb, SA2) == []

        # 重复运行：不重写、不重复进队
        again = await jobs.weekly_reports(store, week, POLICY, WEEKLY, CFG, students=[S1], env="test")
        assert [o.status for o in again.outcomes] == ["exists", "exists"] and again.sampled == 0

        # 没有可见判断的学生：不写、不算不出
        empty = await jobs.weekly_reports(store, week, POLICY, WEEKLY, CFG, students=[S2], env="test")
        assert [o.status for o in empty.outcomes] == ["empty", "empty"]

        # 周：只接受已结束、35 天内的周一（本周还没结束，契约审查 L3）
        for bad in (week + timedelta(days=1), week + timedelta(days=7), week - timedelta(days=42)):
            with pytest.raises(ValueError):
                await jobs.weekly_reports(store, bad, POLICY, WEEKLY, CFG, students=[S1], env="test")
        # 周报配置未签字：test 以外一律不生成；没传 env 按非 test（安全审查 F2）
        for env in ("dev", None):
            with pytest.raises(Unavailable):
                await jobs.weekly_reports(store, week, POLICY, WEEKLY, CFG, students=[S1], env=env)
        signed = dataclasses.replace(WEEKLY, signed=True)
        assert [o.status for o in (await jobs.weekly_reports(store, week, POLICY, signed, CFG, students=[S1], env="dev")).outcomes] == ["exists"] * 2
    finally:
        await pool.close()


async def test_failed_checks_withhold_and_nobody_can_read(insightdb):
    reset(insightdb)
    pool = await create_pool(**insightdb.connect_kwargs(APP_ROLE))
    store = InsightStore(pool)
    try:
        c = await su(insightdb)
        try:
            now = await c.fetchval("SELECT now()")
            week = week_of(await c.fetchval("SELECT (now() AT TIME ZONE 'Asia/Shanghai')::date"))[0] - timedelta(days=7)
        finally:
            await c.close()
        await store.sync_mastery_claims(S2, [row(0, NODE_A, "execute", (0,), now, student=S2, basis=M_S2)], POLICY)
        bad_cfg = dataclasses.replace(CFG, connective_phrases=frozenset({consistency.normalize("【别的衔接】")}))
        run = await jobs.weekly_reports(store, week, POLICY, WEEKLY, bad_cfg, students=[S2], env="test")
        assert [(o.status, o.blocked) for o in run.outcomes] == [("withheld", ("connective_unapproved",))] * 2
        assert run.sampled == 0  # 不出的周报不抽检
        c = await su(insightdb)
        try:
            rows = await c.fetch(f"SELECT status, sentences, objective, withheld_reasons FROM insight.weekly_report WHERE student_id = '{S2}'")
        finally:
            await c.close()
        assert len(rows) == 2 and all(r["status"] == "withheld" and r["objective"] is None and list(r["withheld_reasons"]) == ["connective_unapproved"]
                                      for r in rows)
        assert await bff_reports(insightdb, SA2) == [] and await bff_reports(insightdb, GB) == []
    finally:
        await pool.close()


async def test_objective_facts_only_this_week(insightdb):
    c = await su(insightdb)
    try:
        today = await c.fetchval("SELECT (now() AT TIME ZONE 'Asia/Shanghai')::date")
        week = week_of(today)[0]
        await c.execute(f"""
          SET session_replication_role = replica;
          DELETE FROM insight.behavior_event WHERE student_id = '{S1}';
          DELETE FROM platform.attempt WHERE student_id = '{S1}';
          -- 来源按类别（0057）：会话事件 objective，计划事件 self（学生自己标记的）
          INSERT INTO insight.behavior_event (student_id, kind, payload, source_type, occurred_at, recorded_by, idempotency_key) VALUES
            ('{S1}', 'session_end', '{{"duration_sec": 1800}}', 'objective', now() - interval '1 minute', 'student', gen_random_uuid()),
            ('{S1}', 'session_end', '{{"duration_sec": 659}}', 'objective', now() - interval '2 minutes', 'student', gen_random_uuid()),
            ('{S1}', 'session_end', '{{"duration_sec": 9999}}', 'objective', now() - interval '8 days', 'student', gen_random_uuid()),
            ('{S1}', 'plan_skip', '{{"plan_task_id": "70000000-0000-4000-8000-0000000000a1"}}', 'self', now() - interval '3 minutes', 'student', gen_random_uuid()),
            ('{S1}', 'plan_done', '{{"plan_task_id": "70000000-0000-4000-8000-0000000000a1"}}', 'self', now() - interval '2 minutes', 'student', gen_random_uuid()),
            ('{S1}', 'plan_skip', '{{"plan_task_id": "70000000-0000-4000-8000-0000000000a2"}}', 'self', now() - interval '2 minutes', 'student', gen_random_uuid()),
            ('{S1}', 'plan_done', '{{"plan_task_id": "70000000-0000-4000-8000-0000000000a3"}}', 'self', now() - interval '9 days', 'student', gen_random_uuid()),
            -- PLT-15 PR2：同一任务以最后一次为准——先完成后跳过算跳过（原来“只要有完成就算完成”）；重复完成只算一个任务
            ('{S1}', 'plan_done', '{{"plan_task_id": "70000000-0000-4000-8000-0000000000a4"}}', 'self', now() - interval '4 minutes', 'student', gen_random_uuid()),
            ('{S1}', 'plan_skip', '{{"plan_task_id": "70000000-0000-4000-8000-0000000000a4"}}', 'self', now() - interval '1 minute', 'student', gen_random_uuid()),
            ('{S1}', 'plan_done', '{{"plan_task_id": "70000000-0000-4000-8000-0000000000a5"}}', 'self', now() - interval '5 minutes', 'student', gen_random_uuid()),
            ('{S1}', 'plan_done', '{{"plan_task_id": "70000000-0000-4000-8000-0000000000a5"}}', 'self', now() - interval '4 minutes', 'student', gen_random_uuid()),
            -- 同一页面内改主意（每次新键）：完成 → 跳过 → 完成，最后一条是完成
            ('{S1}', 'plan_done', '{{"plan_task_id": "70000000-0000-4000-8000-0000000000a6"}}', 'self', now() - interval '6 minutes', 'student', gen_random_uuid()),
            ('{S1}', 'plan_skip', '{{"plan_task_id": "70000000-0000-4000-8000-0000000000a6"}}', 'self', now() - interval '5 minutes', 'student', gen_random_uuid()),
            ('{S1}', 'plan_done', '{{"plan_task_id": "70000000-0000-4000-8000-0000000000a6"}}', 'self', now() - interval '30 seconds', 'student', gen_random_uuid());
          INSERT INTO platform.attempt (attempt_id, occurred_at, student_id, subject, item_id, response_evidence_id, hint_level_used,
                                        request_idempotency_key, evidence_kind, evidence_level, channel, elapsed_sec) VALUES
            (gen_random_uuid(), now() - interval '1 minute', '{S1}', 'aya', 'syn-item-001', gen_random_uuid(), 0, 'syn-weekly-att-1', 'independent', 'A', 'keypad', 300),
            (gen_random_uuid(), now() - interval '1 minute', '{S1}', 'aya', 'syn-item-002', gen_random_uuid(), 0, 'syn-weekly-att-2', 'independent', 'A', 'keypad', 330),
            (gen_random_uuid(), now() - interval '1 minute', '{S1}', 'echo', 'syn-item-003', gen_random_uuid(), 0, 'syn-weekly-att-3', 'independent', 'A', 'keypad', 600),
            (gen_random_uuid(), now() - interval '10 days', '{S1}', 'aya', 'syn-item-004', gen_random_uuid(), 0, 'syn-weekly-att-4', 'independent', 'A', 'keypad', 900);
          SET session_replication_role = origin;""")
    finally:
        await c.close()
    pool = await create_pool(**insightdb.connect_kwargs(APP_ROLE))
    try:
        async with pool.acquire() as conn:
            facts = await objective_facts(conn, S1, week)
            # 口径一致（PLT-15 PR2）：指标的读取（load_metric_inputs）取同一批计划事件、限在本周内，按同一口径算出的完成 / 跳过与客观栏相同
            now = await conn.fetchval("SELECT now()")
            inputs = await load_metric_inputs(conn, S1, now)
    finally:
        await pool.close()
    start, end = week_window(week)
    in_week = [PlanRecord(p.fact_id, p.kind, p.plan_task_id, p.occurred_at) for p in inputs.plans if start <= p.occurred_at < end]
    assert plan_outcome_counts(in_week) == (facts.plan_done, facts.plan_skipped) == (3, 2)
    # a1 先跳过后完成 → 完成；a5 完成两次 → 一个完成；a6 完成→跳过→完成 → 完成；a2 跳过；a4 先完成后跳过 → 跳过；a3 不在本周
    assert objective_column(facts) == {"source_type": "objective", "usage_minutes": 40,
                                       "subjects": [{"subject": "aya", "practice_minutes": 10, "attempts": 2}],
                                       "self_marked": {"source_type": "self", "plan_done": 3, "plan_skipped": 2}}


async def test_guard_rejection_is_recorded_and_the_run_continues(insightdb, monkeypatch):
    """某个学生某个受众被库里守卫拒收：记 failed，接着生成同一学生的另一个受众（契约审查 L4）"""
    reset(insightdb)
    pool = await create_pool(**insightdb.connect_kwargs(APP_ROLE))
    store = InsightStore(pool)
    try:
        c = await su(insightdb)
        try:
            now = await c.fetchval("SELECT now()")
            week = week_of(await c.fetchval("SELECT (now() AT TIME ZONE 'Asia/Shanghai')::date"))[0] - timedelta(days=7)
        finally:
            await c.close()
        await store.sync_mastery_claims(S1, [row(0, NODE_A, "execute", (0, 1), now)], POLICY)
        real = store_weekly.build_weekly

        def tampered(claims, audience, *args, **kw):
            res = real(claims, audience, *args, **kw)
            if audience == "student" and res.ok:  # 学生版的句子改指一条不存在的判断：守卫拒收
                bad = dict(res.report)
                bad["sections"] = [{**sec, "sentences": [{**x, "claim_id": "e7ff0000-0000-4000-8000-0000000000ff"} for x in sec["sentences"]]}
                                   for sec in res.report["sections"]]
                return dataclasses.replace(res, report=bad)
            return res

        monkeypatch.setattr(store_weekly, "build_weekly", tampered)
        run = await jobs.weekly_reports(store, week, POLICY, WEEKLY, CFG, students=[S1], env="test")
        assert [(o.audience, o.status, o.blocked) for o in run.outcomes] == [("student", "failed", ("db_check_violation",)),
                                                                            ("guardian", "generated", ())]
    finally:
        await pool.close()


class FakeProfilePages:
    """记录周报前的档案页触发（学生、学科、触发时周报是否已写）；fail = True 时每次抛出（模拟学习服务不可用）"""

    def __init__(self, db, fail: bool = False) -> None:
        self.db, self.fail, self.calls = db, fail, []

    async def ensure(self, student: str, subject: str) -> bool:
        written = self.db.psql(f"SELECT count(*) FROM insight.weekly_report WHERE student_id = '{student}'").strip()
        self.calls.append((student, subject, written))
        if self.fail:
            raise RuntimeError("learning service unavailable")
        return True


async def test_weekly_triggers_profile_page_before_generation_and_failure_does_not_block(insightdb):
    """KGE-04 第二步 PR 4（待人确认 #6 默认）：周报生成前按周报可见学科调 ensure 编译学情档案页；触发失败不阻塞周报"""
    for fail in (False, True):
        reset(insightdb)
        pool = await create_pool(**insightdb.connect_kwargs(APP_ROLE))
        store = InsightStore(pool)
        try:
            c = await su(insightdb)
            try:
                now = await c.fetchval("SELECT now()")
                today = await c.fetchval("SELECT (now() AT TIME ZONE 'Asia/Shanghai')::date")
            finally:
                await c.close()
            week = week_of(today)[0] - timedelta(days=7)
            await store.sync_mastery_claims(S1, [row(0, NODE_A, "execute", (0, 1), now)], POLICY)
            pages = FakeProfilePages(insightdb, fail=fail)
            run = await jobs.weekly_reports(store, week, POLICY, WEEKLY, CFG, students=[S1], env="test", profile_pages=pages)
            assert pages.calls == [(S1, s, "0") for s in sorted(POLICY.visible_subjects) if s != "echo"]   # 先编档案页，再写周报
            assert [(o.audience, o.status) for o in run.outcomes] == [("student", "generated"), ("guardian", "generated")]
            n = len(pages.calls)
            assert dict(run.profile_pages) == ({"ok": 0, "failed": n} if fail else {"ok": n, "failed": 0})
        finally:
            await pool.close()


async def test_learning_profile_pages_client_failures_are_counted_not_raised():
    import asyncio

    import httpx

    from insight.profile_pages import LearningProfilePages, learning_profile_pages

    seen = []

    def ok(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append((request.url.path, request.headers["authorization"], body["trigger"], body["subject"]))
        return httpx.Response(200, json={"subject": body["subject"], "outcome": "compiled", "check_status": "passed",
                                         "compiled_at": "2026-10-02T08:00:00+00:00", "compiler_version": "kge-profile@0.1.0"})

    c = LearningProfilePages("http://learning.test", "t" * 40, transport=httpx.MockTransport(ok))
    assert await c.ensure(S1, "aya") is True
    assert seen == [("/internal/v1/profile-page/ensure", "Bearer " + "t" * 40, "weekly", "aya")]
    assert await c.ensure(S1, "echo") is False and len(seen) == 1                       # 英语搁置：不调
    bad = LearningProfilePages("http://learning.test", "t" * 40, transport=httpx.MockTransport(lambda r: httpx.Response(409)))
    assert await bad.ensure(S1, "aya") is False and bad.counts == {"ok": 0, "failed": 1}

    async def slow(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(5)
        return httpx.Response(200, json={})

    timeout = LearningProfilePages("http://learning.test", "t" * 40, transport=httpx.MockTransport(slow), total_timeout=0.2)
    assert await timeout.ensure(S1, "aya") is False and timeout.counts["failed"] == 1
    assert learning_profile_pages(None) is None
    assert learning_profile_pages(("http://learning.test", "t" * 40)).base_url == "http://learning.test"
