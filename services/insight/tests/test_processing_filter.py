"""LT-06 决定记录（续）第 4 条：洞察服务批量任务按库层处理判据（迁移 0059 platform.student_processing_allowed）过滤选人。
全部合成数据（规则 8）：本文件自建一户（家长、学生 X 与 Y），不碰 pginsight 的 S1–S3。

- 周报候选（students_with_claims）与点名的学生（--student / students=）都只留判据为真的：已退出内部测试、没有有效同意的学生
  不生成周报 / 成长手记、不触发档案页编译；
- 中圈指标与掌握同步的选人（jobs_cli 的 --all 与点名）同一个过滤（keep_allowed）；
- 判据不依赖调用者身份：洞察服务的登录角色直接调用。"""
from __future__ import annotations

import uuid
from datetime import timedelta

import asyncpg
import pytest

from insight import jobs, processing
from insight.claims import MasteryRow
from insight.store import InsightStore, create_pool
from insight.store_weekly import students_with_claims
from insight.weekly import synthetic_weekly_check_config, synthetic_weekly_policy, week_of
from pginsight import APP_ROLE, E, M
from support import NODE_A, NODE_B, NODE_C, make_policy

pytestmark = pytest.mark.anyio
POLICY = make_policy()


def mastery_rows(db, s: str) -> list[str]:
    """该学生的三条当前掌握行（判断同步要求依据是库里该学生的当前掌握行）；以超级用户在复制模式下写，只为夹具"""
    ids = [str(uuid.uuid4()) for _ in range(3)]
    db.psql("SET session_replication_role = replica;\n" + "\n".join(
        f"""INSERT INTO platform.mastery_estimate (id, student_id, subject, skill_local_id, state, p_mastery, confidence, algorithm_version,
              evidence_ids, is_current) VALUES ('{m}', '{s}', 'aya', 'SYN-PF-0{i}', 'stable', 0.8, 0.8, 'kge-mastery@0.1.0.syn', '{{{E[i]}}}', true);"""
        for i, m in enumerate(ids)))
    return ids


def family(db, *, consent: bool = True) -> tuple[str, str]:
    g, a, h, s = (str(uuid.uuid4()) for _ in range(4))
    db.psql(f"""
      INSERT INTO platform.account (id, kind) VALUES ('{g}', 'guardian'), ('{a}', 'student');
      INSERT INTO platform.household (id, created_by_account) VALUES ('{h}', '{g}');
      INSERT INTO platform.student_profile (id, account_id, household_id, grade, region, textbook_version, birth_year_month) VALUES
        ('{s}', '{a}', '{h}', 8, 'nanjing', 'bubian-2024', '2013-03-01');
      INSERT INTO platform.guardian_student_link (guardian_account_id, student_id, granted_via) VALUES ('{g}', '{s}', 'household_creator');""")
    if consent:
        db.psql(f"""INSERT INTO platform.consent_record (student_id, guardian_account_id, consent_type, document_version, decision)
                    SELECT '{s}', '{g}', t, replace(t, '_', '-') || '@synthetic-0', 'granted'
                      FROM unnest(ARRAY['privacy_policy', 'guardian_consent_under14']) t;""")
    return s, g


def exit_test(db, s: str, g: str) -> None:
    db.psql(f"""SET ROLE starbuddy_bff; SELECT set_config('app.account_id', '{g}', false);
                SELECT 1 FROM platform.exit_internal_test('{s}'); SELECT set_config('app.account_id', '', false); RESET ROLE;""")


async def test_weekly_candidates_and_named_students_skip_exited_and_unconsented(insightdb):
    x, gx = family(insightdb)
    y, _ = family(insightdb)
    z, _ = family(insightdb, consent=False)
    pool = await create_pool(**insightdb.connect_kwargs(APP_ROLE))
    store = InsightStore(pool)
    try:
        c = await asyncpg.connect(**insightdb.connect_kwargs("postgres"))
        try:
            now = await c.fetchval("SELECT now()")
            today = await c.fetchval("SELECT (now() AT TIME ZONE 'Asia/Shanghai')::date")
        finally:
            await c.close()
        week = week_of(today)[0] - timedelta(days=7)
        for s in (x, y, z):
            basis = mastery_rows(insightdb, s)
            r = await store.sync_mastery_claims(s, [MasteryRow(basis[i], s, "aya", n, "apply", gate, 0.6, 0.5, tuple(E[j] for j in ev),
                                                               "kge-mastery@0.1.0.syn", now)
                                                    for i, n, gate, ev in ((0, NODE_A, "execute", (0, 1)), (1, NODE_B, "stable", (2, 3, 4)),
                                                                           (2, NODE_C, "transfer", (5, 6)))], POLICY)
            assert r.skipped_rows == 0, r
        async with pool.acquire() as conn:
            assert await processing.available(conn)
            assert await processing.keep_allowed(conn, [x, y, z]) == [x, y], "没有有效同意的 z 不处理"
            assert await processing.keep_allowed(conn, [y, z, x]) == [y, x], "保持原顺序（两种顺序都试，排序的实现必错一种）"
        cands = await students_with_claims(store)
        assert x in cands and y in cands and z not in cands
        exit_test(insightdb, x, gx)
        async with pool.acquire() as conn:
            assert await processing.keep_allowed(conn, [z, y, x]) == [y], "保持原顺序，只留 y"
        cands = await students_with_claims(store)
        assert x not in cands and y in cands
        # 点名的学生同样过滤：已退出的 x 不生成周报
        run = await jobs.weekly_reports(store, week, POLICY, synthetic_weekly_policy(), synthetic_weekly_check_config(), students=[x, z], env="test")
        assert run.outcomes == () or list(run.outcomes) == []
        assert insightdb.psql(f"SELECT count(*) FROM insight.weekly_report WHERE student_id IN ('{x}', '{z}')").strip() == "0"
        ok = await jobs.weekly_reports(store, week, POLICY, synthetic_weekly_policy(), synthetic_weekly_check_config(), students=[y], env="test")
        assert {o.student_id for o in ok.outcomes} == {y}, "没退出、有同意的学生照常"
    finally:
        await pool.close()


def test_jobs_cli_named_and_all_skip_exited(insightdb):
    """命令行 metrics / sync：点名已退出的学生时学生数为 0；--all 的选人同样不含已退出的（合成掌握行让 --all 选得到他）"""
    import json

    from test_jobs_cli import _run, _url
    x, gx = family(insightdb)
    # 一条节点键当前掌握行（--all 按它选人）；复制模式下写，地图版本是随机合成 ID
    insightdb.psql(f"""SET session_replication_role = replica;
      INSERT INTO platform.mastery_estimate (id, student_id, subject, node_id, cognitive_level, map_version_id, state, p_mastery, confidence,
        algorithm_version, evidence_ids, is_current) VALUES
        ('{uuid.uuid4()}', '{x}', 'zhixing', 'SYN.PF.ZX.N1', 'apply', '{uuid.uuid4()}', 'execute', 0.6, 0.5, 'kge-mastery@0.1.0.syn',
         ARRAY[gen_random_uuid()], true);""")
    env = {"INSIGHT_ENV": "test", "INSIGHT_DATABASE_URL": _url(insightdb, APP_ROLE)}
    before = json.loads(_run("sync", "--student", x, env=env).stdout)
    assert before["students"] == 1, before
    all_before = json.loads(_run("sync", "--all", env=env).stdout)["students"]
    exit_test(insightdb, x, gx)
    for args in (("sync", "--student", x), ("metrics", "--student", x)):
        p = _run(*args, env=env)
        assert p.returncode == 0, p.stderr
        assert json.loads(p.stdout)["students"] == 0, args
    assert json.loads(_run("sync", "--all", env=env).stdout)["students"] == all_before - 1


async def test_keep_allowed_passthrough_when_function_missing():
    class Conn:
        async def fetchval(self, *_a):
            return False

        async def fetch(self, *_a):
            raise AssertionError("判据不可用时不查")
    ids = [str(uuid.uuid4()), str(uuid.uuid4())]
    assert await processing.keep_allowed(Conn(), ids) == ids
    assert await processing.keep_allowed(Conn(), []) == []
