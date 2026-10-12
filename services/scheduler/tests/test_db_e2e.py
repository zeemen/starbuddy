"""库上端到端（一次性 Postgres，全部迁移 + 合成数据）：每个任务经 `python -m scheduler --once <任务>` 的同一入口（cli.main）
以真实子进程调用所属服务已有的命令行，产生预期效果；重复运行幂等（第二轮计数为 0 或“已存在”）。
每个测试自己造数据（tests/seed.py 的 seed_*，随机 ID），断言只看自己的数据，与测试顺序无关（反序跑一遍同样通过）。
互斥：两个运行器并发同一任务只有一个执行（真实的会话级咨询锁）；另一实例持锁时 --once 跳过、不产生效果；锁连接被断开时立即杀子进程。
全部合成数据（规则 8），只连本机 Unix socket（规则 9）。"""
from __future__ import annotations

import asyncio
import io
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import asyncpg
import pytest

import seed as SD
from scheduler import __main__ as cli
from scheduler.config import IDENTITIES, Job
from scheduler.runner import Runner, lock_key
from scheduler.schedule import parse_schedule

SVC = Path(__file__).resolve().parents[1]
ROOT = SVC.parents[1]
TESTS = Path(__file__).resolve().parent
UUID_ANY = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)
PICKED = "('x' || substr(md5(g.id::text), 1, 8))::bit(32)::bigint < floor({ratio} * 4294967296)"


def _url(db, role: str) -> str:
    kw = db.connect_kwargs(role)
    return f"postgresql://{kw['user']}@/{kw['database']}?host={kw['host']}"


def environ(db) -> dict[str, str]:
    from pginsight import APP_ROLE, LEARNING_ROLE
    return {"SCHEDULER_ENV": "test", "SCHEDULER_DATABASE_URL": _url(db, SD.SCHEDULER_ROLE),
            "INSIGHT_ENV": "test", "INSIGHT_DATABASE_URL": _url(db, APP_ROLE),
            "LEARNING_ENV": "test", "LEARNING_DATABASE_URL": _url(db, LEARNING_ROLE),
            "LEARNING_MASTERY_CONFIG_AYA": str(ROOT / "services/learning/config/mastery/synthetic-test.json"),
            "REVIEW_CONSOLE_ENV": "test", "REVIEW_CONSOLE_KGE_DATABASE_URL": _url(db, SD.KGE_ROLE)}


def once(db, job: str, env: dict[str, str] | None = None) -> tuple[int, dict]:
    out = io.StringIO()
    e = env or environ(db)
    rc = cli.main(["--once", job], e, out=out)
    lines = out.getvalue().splitlines()
    assert len(lines) == 1, lines
    log = json.loads(lines[0])
    assert log["job"] == job and log["event"] == "job_run" and not UUID_ANY.search(lines[0])
    for v in e.values():
        assert v not in lines[0]
    return rc, log


def q(db, sql: str) -> str:
    return db.psql(sql).strip()


def _need_children() -> None:
    for svc in ("insight", "learning"):
        if not (ROOT / "services" / svc / ".venv/bin/python").exists():
            if os.environ.get("SCHEDULER_TEST_STRICT") == "1":
                raise AssertionError(f"缺 services/{svc}/.venv")
            pytest.skip(f"没有 services/{svc}/.venv（子进程解释器）")


@pytest.fixture
def db(schedulerdb):
    _need_children()
    return schedulerdb


def test_meter_expire(db):
    s = SD.seed_expired_reservation(db)
    rc, log = once(db, "meter-expire")
    assert (rc, log["outcome"], log["level"]) == (0, "ok", "info"), log
    c = log["counts"]
    assert c["job"] == "meter-expire" and c["expired"] >= 1 and c["more_due"] is False
    assert q(db, f"SELECT state FROM platform.usage_reservation WHERE usage_id = '{s['usage']}'") == "expired"
    assert q(db, f"SELECT reserved FROM platform.entitlement WHERE id = '{s['ent']}'") == "0"
    assert q(db, f"SELECT count(*) FROM platform.usage_ledger WHERE usage_id = '{s['usage']}' AND kind = 'expire' AND actor = 'system'") == "1"
    rc, log = once(db, "meter-expire")
    assert rc == 0 and log["counts"]["expired"] == 0
    assert q(db, f"SELECT count(*) FROM platform.usage_ledger WHERE usage_id = '{s['usage']}' AND kind = 'expire'") == "1"


def test_meter_renew(db):
    s = SD.seed_due_trial(db)
    rc, log = once(db, "meter-renew")
    assert (rc, log["outcome"]) == (0, "ok"), log
    assert log["counts"]["job"] == "meter-renew" and log["counts"]["renewed"] >= 1
    assert q(db, f"SELECT status FROM platform.subscription WHERE id = '{s['sub']}'") == "ended"         # 试用到期结束
    assert q(db, f"SELECT status FROM platform.subscription WHERE id = '{s['live_sub']}'") == "active"   # 没到期的不动
    rc, log = once(db, "meter-renew")
    assert rc == 0 and log["counts"]["renewed"] == 0


def test_erasures(db):
    s = SD.seed_due_erasure(db)
    rc, log = once(db, "erasures")
    assert (rc, log["outcome"]) == (0, "ok") and log["counts"]["executed"] >= 1, log
    assert q(db, f"SELECT status FROM platform.erasure_request WHERE id = '{s['erase']}'") == "executed"
    assert q(db, f"SELECT status FROM platform.account WHERE id = '{s['account']}'") == "disabled"
    rc, log = once(db, "erasures")
    assert rc == 0 and log["counts"]["executed"] == 0


def test_freeze(db):
    s = SD.seed_due_prediction(db)
    rc, log = once(db, "freeze")
    assert (rc, log["outcome"]) == (0, "ok") and log["counts"]["frozen"] >= 1, log
    frozen_at = q(db, f"SELECT frozen_at FROM insight.exam_prediction WHERE id = '{s['pred']}'")
    assert frozen_at != ""
    rc, log = once(db, "freeze")
    assert rc == 0 and log["counts"]["frozen"] == 0
    assert q(db, f"SELECT frozen_at FROM insight.exam_prediction WHERE id = '{s['pred']}'") == frozen_at


def test_spot_check(db):
    s = SD.seed_grades(db, "wenli", 80)
    own = f"FROM platform.grade_result g WHERE g.student_id = '{s['student']}' AND {PICKED.format(ratio=0.1)}"
    want = int(q(db, f"SELECT count(*) {own}"))
    assert want >= 1
    rc, log = once(db, "spot-check")
    assert (rc, log["outcome"]) == (0, "ok"), log
    enq = log["counts"]["enqueued"]
    assert set(enq) == {"aya", "zhixing", "wenli"} and enq["wenli"] >= want
    assert q(db, f"SELECT count(*) {own} AND EXISTS (SELECT 1 FROM platform.review_task t WHERE t.source_kind = 'grade_result' "
                 f"AND t.source_id = g.id AND 'spot_check' = ANY (t.reasons::text[]))") == str(want)
    assert q(db, f"SELECT count(*) FROM platform.review_task WHERE student_id = '{s['student']}'") == str(want)  # 没抽中的不进队
    rc, log = once(db, "spot-check")
    assert rc == 0 and log["counts"]["enqueued"] == {"aya": 0, "zhixing": 0, "wenli": 0}


def test_expire_affairs(db):
    a = SD.seed_affairs(db)
    rc, log = once(db, "expire-affairs")
    assert (rc, log["outcome"]) == (0, "ok"), log
    assert log["counts"]["retired"] >= 1 and log["counts"]["job"] == "expire-affairs"
    assert q(db, f"SELECT review_status FROM kge.current_affair WHERE id = '{a['old']}'") == "retired"
    assert q(db, f"SELECT review_status FROM kge.current_affair WHERE id = '{a['new']}'") == "published"
    assert q(db, f"SELECT basis_code FROM kge.content_review WHERE object_id = '{a['old']}'") == "expired_auto"
    rc, log = once(db, "expire-affairs")
    assert rc == 0 and log["counts"]["retired"] == 0
    assert q(db, f"SELECT count(*) FROM kge.content_review WHERE object_id = '{a['old']}'") == "1"


def _sync_claims(db, student: str, rows_spec) -> None:
    from insight.claims import MasteryRow
    from insight.store import InsightStore, create_pool
    from pginsight import APP_ROLE, E
    from support import make_policy

    async def go():
        pool = await create_pool(**db.connect_kwargs(APP_ROLE))
        try:
            async with pool.acquire() as c:
                now = await c.fetchval("SELECT now()")
            rows = [MasteryRow(basis, student, "aya", node, "apply", gate, 0.6, 0.5, tuple(E[j] for j in ev), "kge-mastery@0.1.0.syn", now)
                    for basis, node, gate, ev in rows_spec]
            await InsightStore(pool).sync_mastery_claims(student, rows, make_policy())
        finally:
            await pool.close()
    asyncio.run(go())


def test_weekly(db):
    from pginsight import M, S1
    from support import NODE_A, NODE_B, NODE_C
    _sync_claims(db, S1, [(M[0], NODE_A, "execute", (0, 1)), (M[1], NODE_B, "stable", (2, 3, 4)), (M[2], NODE_C, "transfer", (5, 6))])
    db.psql(f"""SET session_replication_role = replica;
        UPDATE insight.claim SET created_at = date_trunc('week', (now() AT TIME ZONE 'Asia/Shanghai') - interval '7 days') AT TIME ZONE 'Asia/Shanghai'
                                              + interval '2 days'
         WHERE student_id = '{S1}' AND template_key = 'syn.math.node_transfer';""")
    rc, log = once(db, "weekly")
    assert (rc, log["outcome"]) == (0, "ok"), log
    c = log["counts"]
    last_monday = q(db, "SELECT (date_trunc('week', (now() AT TIME ZONE 'Asia/Shanghai')::date) - interval '7 days')::date")
    assert c["job"] == "weekly" and c["week_start"] == last_monday and c["outcomes"]["failed"] == 0
    assert q(db, f"SELECT string_agg(audience || ':' || status, ',' ORDER BY audience) FROM insight.weekly_report "
                 f"WHERE student_id = '{S1}' AND week_start = '{last_monday}'") == "guardian:generated,student:generated"
    rc, log = once(db, "weekly")
    again = log["counts"]["outcomes"]
    assert rc == 0 and again["generated"] == 0 and again["withheld"] == 0 and again["exists"] >= 2 and log["counts"]["sampled"] == 0
    assert q(db, f"SELECT count(*) FROM insight.weekly_report WHERE student_id = '{S1}'") == "2"


def test_sweep(db):
    from pginsight import M_S2, S2
    from support import NODE_A
    _sync_claims(db, S2, [(M_S2, NODE_A, "execute", (0,))])
    db.psql(f"SET session_replication_role = replica; UPDATE insight.claim SET created_at = now() - interval '2 days', "
            f"updated_at = now() - interval '2 days', expires_at = now() - interval '1 minute' "
            f"WHERE student_id = '{S2}' AND status IN ('hypothesis', 'established');")
    own = int(q(db, f"SELECT count(*) FROM insight.claim WHERE student_id = '{S2}' AND status IN ('hypothesis', 'established') "
                    f"AND expires_at <= now()"))
    assert own >= 1
    rc, log = once(db, "sweep")
    assert (rc, log["outcome"]) == (0, "ok") and log["counts"]["to_verify"] >= own, log
    assert q(db, f"SELECT count(*) FROM insight.claim WHERE student_id = '{S2}' AND status IN ('hypothesis', 'established') "
                 f"AND expires_at <= now()") == "0"
    rc, log = once(db, "sweep")
    assert rc == 0 and log["counts"]["to_verify"] == 0


def test_mastery_sweep(db):
    """掌握对账以学习服务身份运行（LEARNING_MASTERY_CONFIG_* 按通配传入）：一次已判分、已打标的作答还没有掌握记录 →
    第一轮写出该节点 × 层次的掌握记录（未签字的合成配置最高到“稳定”）；第二轮不再新增记录"""
    s = SD.seed_answer(db)
    rows = f"FROM platform.mastery_estimate WHERE student_id = '{s['student']}' AND node_id = '{SD.NODE}'"
    assert q(db, f"SELECT count(*) {rows}") == "0"
    rc, log = once(db, "mastery-sweep")
    assert (rc, log["outcome"], log["level"]) == (0, "ok", "info"), log
    c = log["counts"]
    assert c["job"] == "mastery-sweep" and c["failed_units"] == 0 and c["stale_estimate"] == 0 and c["subjects"]["aya"]["students"] >= 1
    first = int(q(db, f"SELECT count(*) {rows}"))
    assert first >= 1 and q(db, f"SELECT count(*) {rows} AND is_current") == "1"
    assert q(db, f"SELECT cognitive_level {rows} AND is_current") == SD.LEVEL
    assert q(db, f"SELECT '{s['evidence']}' = ANY (evidence_ids::text[]) {rows} AND is_current") == "t"
    tasks = q(db, f"SELECT count(*) FROM platform.retest_task WHERE student_id = '{s['student']}'")
    rc, again = once(db, "mastery-sweep")
    assert rc == 0 and again["counts"]["failed_units"] == 0
    assert int(q(db, f"SELECT count(*) {rows}")) == first                                  # 第二轮没有新记录
    assert q(db, f"SELECT count(*) FROM platform.retest_task WHERE student_id = '{s['student']}'") == tasks


# ---------------------------------------------------------------- 退出码映射（真实子进程）
def test_refused_identity_mismatch_and_wrong_role(db):
    """连接串的登录角色不是该身份：子进程的角色断言拒绝。insight.scheduled 退出码 2；审核台命令行以 SystemExit / 异常退出（退出码 1），
    在调度器里同样按“拒绝”记"""
    from pginsight import APP_ROLE
    rc, log = once(db, "freeze", {**environ(db), "SCHEDULER_DATABASE_URL": _url(db, APP_ROLE)})
    assert (rc, log["outcome"], log["level"], log["child_exit"]) == (2, "refused", "alert", 2)
    assert log["reason"] == "insight.scheduled InsufficientPrivilegeError 42501"   # SET ROLE 被拒：错误类别 + SQLSTATE，别的都不带
    rc, log = once(db, "expire-affairs", {**environ(db), "REVIEW_CONSOLE_KGE_DATABASE_URL": _url(db, APP_ROLE)})
    assert (rc, log["outcome"], log["level"], log["child_exit"]) == (2, "refused", "alert", 1)


# ---------------------------------------------------------------- 互斥与锁
FAKE = {"insight": (Path(sys.executable), TESTS)}


def _fake_job(name: str, *args: str) -> Job:
    return Job(name, parse_schedule("every 5m"), IDENTITIES["scheduler"], "fake_child", args, (), 60)


def test_two_instances_same_job_only_one_runs(db):
    """两个运行器（两个实例）并发同一任务：真实咨询锁，只有一个执行，另一个记 skipped_locked；结束后锁释放"""
    j = _fake_job("lock-probe", "sleep", "2")

    async def go():
        a = Runner(environ(db), out=io.StringIO(), interpreters=FAKE)
        b = Runner(environ(db), out=io.StringIO(), interpreters=FAKE)
        both = await asyncio.gather(a.run(j), b.run(j))
        after = await a.run(_fake_job("lock-probe", "exit", "0"))
        other = await b.run(_fake_job("other-job", "exit", "0"))
        return both, after, other
    both, after, other = asyncio.run(go())
    assert sorted(r.outcome for r in both) == ["ok", "skipped_locked"]
    assert after.outcome == "ok" and other.outcome == "ok"


def test_lock_connection_lost_kills_child(db):
    """子进程运行中锁连接被断开（锁随之释放）：探活发现后立即杀掉子进程组，记 lock_lost（alert），不等任务超时"""
    j = _fake_job("lock-lost-probe", "sleep", "30")

    async def go():
        r = Runner(environ(db), out=io.StringIO(), interpreters=FAKE, probe_seconds=0.3)
        task = asyncio.ensure_future(r.run(j))
        admin = await asyncpg.connect(**db.connect_kwargs("postgres"))
        try:
            for _ in range(50):
                await asyncio.sleep(0.2)
                n = await admin.fetchval("SELECT count(*) FROM pg_locks l JOIN pg_stat_activity a ON a.pid = l.pid "
                                         "WHERE l.locktype = 'advisory' AND a.application_name = $1", f"starbuddy-scheduler:{j.name}")
                if n:
                    break
            killed = await admin.fetchval("SELECT count(*) FROM (SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                                          "WHERE application_name = $1) x", f"starbuddy-scheduler:{j.name}")
        finally:
            await admin.close()
        t0 = asyncio.get_running_loop().time()
        res = await task
        return killed, res, asyncio.get_running_loop().time() - t0
    killed, res, waited = asyncio.run(go())
    assert killed == 1
    assert (res.outcome, res.exit_code, res.level) == ("lock_lost", 1, "alert") and waited < 10


def test_child_statement_timeout_applied(db):
    """传给子进程的连接串带 statement_timeout（任务超时的 90%）：asyncpg 把它当服务端设置，连上即生效"""
    from scheduler.config import load_config
    from scheduler.runner import preflight
    job = load_config().jobs["freeze"]
    env = preflight(job, environ(db), Runner(environ(db)).interpreter(job)[0])

    async def go():
        c = await asyncpg.connect(env["SCHEDULER_DATABASE_URL"])
        try:
            return await c.fetchval("SHOW statement_timeout")
        finally:
            await c.close()
    assert asyncio.run(go()) == "9min"                                     # 600 秒 × 90% = 540 秒


def test_once_skips_while_another_instance_holds_the_lock(db):
    """另一实例正持有 meter-expire 的锁（以调度器身份的连接持锁）：--once 跳过本轮，不产生效果；锁释放后再跑才处理"""
    s = SD.seed_expired_reservation(db)
    env = {**environ(db), "PATH": "/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1"}

    async def go():
        holder = await asyncpg.connect(**db.connect_kwargs(SD.SCHEDULER_ROLE))
        try:
            assert await holder.fetchval("SELECT pg_try_advisory_lock($1::bigint)", lock_key("meter-expire"))
            p = await asyncio.create_subprocess_exec(sys.executable, "-m", "scheduler", "--once", "meter-expire", cwd=str(SVC), env=env,
                                                     stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            out, err = await asyncio.wait_for(p.communicate(), 120)
            return p.returncode, out.decode(), err.decode()
        finally:
            await holder.close()
    rc, out, err = asyncio.run(go())
    assert rc == 0, err
    assert json.loads(out)["outcome"] == "skipped_locked"
    assert q(db, f"SELECT state FROM platform.usage_reservation WHERE usage_id = '{s['usage']}'") == "reserved"
    p = subprocess.run([sys.executable, "-m", "scheduler", "--once", "meter-expire"], cwd=SVC, env=env, capture_output=True, text=True,
                       timeout=120)
    assert p.returncode == 0, p.stderr
    assert json.loads(p.stdout)["counts"]["expired"] >= 1
    assert q(db, f"SELECT state FROM platform.usage_reservation WHERE usage_id = '{s['usage']}'") == "expired"
