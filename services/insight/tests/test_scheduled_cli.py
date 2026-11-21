"""PLT-13 第一步（specs/scheduler-plt13）补的命令行入口：python -m insight.scheduled freeze|erasures|meter-expire|meter-renew|spot-check
（调度器身份）与 python -m insight.jobs weekly（洞察服务身份）。库函数与授权都不改，只调用。
- 只收 SCHEDULER_ENV=dev / test；连接前核对全部主机、连上后核对服务端地址；登录角色只继承 starbuddy_scheduler（connect_scheduler 断言）；
- 计量小批量循环与 BFF jobs.ts 同语义（一批不满即停；到了批次上限仍可能有到期 → more_due、退出码 1）；
- 抽检按学科逐个调用、比例由调用方给，同一窗口重复运行不重复进队；
- weekly 缺省上一个已结束的周，重复运行不重写；输出一行 JSON，只有计数，不含学生或判断 ID。全部合成数据（规则 8）。"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from insight import jobs_cli, scheduled
from pginsight import APP_ROLE, S3

SVC = Path(__file__).resolve().parents[1]
UUID_ANY = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)
SCHED_ROLE = "sched_cli_app"   # 只继承 starbuddy_scheduler（与 sie03_seed 的 scheduler_app 分开，免得夹具顺序互相影响）
BAD_URLS = ("postgresql://u@db.example.cn/x", "postgresql://u@10.0.0.5/x", "mysql://u@localhost/x",
            "postgresql://u@/x?host=db.example.cn", "postgresql://u@127.0.0.1:5432,db.example.cn:5432/x",
            "postgresql://u@localhost/x?hostaddr=10.0.0.5", "postgresql://u@localhost/x?service=synthetic-remote",
            "postgresql://u@[2001:db8::1]:5432/x")
GOOD = {"SCHEDULER_ENV": "test", "SCHEDULER_DATABASE_URL": "postgresql://u@localhost/x"}


def _url(db, role: str) -> str:
    kw = db.connect_kwargs(role)
    return f"postgresql://{kw['user']}@/{kw['database']}?host={kw['host']}"


def _run(module: str, *args: str, env: dict[str, str]) -> subprocess.CompletedProcess:
    base = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "PYTHONDONTWRITEBYTECODE": "1"}
    return subprocess.run([sys.executable, "-m", module, *args], cwd=SVC, capture_output=True, text=True, env={**base, **env}, timeout=120)


@pytest.fixture
def no_connect(monkeypatch):
    async def never(*_a, **_k):
        raise AssertionError("不应连库")
    monkeypatch.setattr(scheduled, "connect_scheduler", never)


# ---------------------------------------------------------------- 不连库：环境、参数、本机核对
@pytest.mark.parametrize("value", ["", "pilot", "prod", "production", "TEST"])
def test_env_must_be_dev_or_test(value, no_connect, capsys):
    assert scheduled.main(["freeze"], {**GOOD, "SCHEDULER_ENV": value}) == 2
    assert "不连接 pilot 或生产环境" in capsys.readouterr().err


@pytest.mark.parametrize("url", BAD_URLS)
def test_non_local_url_refused_before_connect(url, no_connect, capsys):
    assert scheduled.main(["meter-expire"], {**GOOD, "SCHEDULER_DATABASE_URL": url}) == 2
    err = capsys.readouterr().err
    assert ("调度器定时任务只连本机数据库" in err or "postgresql://" in err) and "时政" not in err and "Traceback" not in err


@pytest.mark.parametrize("argv", [["meter-expire", "--batch", "0"], ["meter-renew", "--batch", "1001"], ["meter-expire", "--max-batches", "0"],
                                  ["spot-check", "--subject", "aya", "--ratio", "0.1", "--lookback-hours", "0"],
                                  ["spot-check", "--subject", "aya", "--ratio", "0.1", "--lookback-hours", "745"],
                                  ["spot-check", "--subject", "aya", "--ratio", "0.1", "--since", "2026-10-01T00:00:00"],
                                  ["spot-check", "--subject", "aya", "--ratio", "0.1", "--since", "not-a-time"],
                                  ["model-call-purge", "--batch", "0"], ["model-call-purge", "--batch", "10001"],
                                  ["model-call-purge", "--max-batches", "0"]])
def test_bad_arguments(argv, no_connect, capsys):
    assert scheduled.main(argv, GOOD) == 2
    err = capsys.readouterr().err
    assert "执行失败" not in err and ("--batch" in err or "--lookback-hours" in err or "--since" in err), err  # 参数核对拦下，没有走到连库


def test_missing_url_and_required_arguments(no_connect):
    assert scheduled.main(["freeze"], {"SCHEDULER_ENV": "test"}) == 2
    for argv in (["spot-check", "--subject", "aya", "--lookback-hours", "26"],             # 比例没有代码缺省值
                 ["spot-check", "--ratio", "0.1", "--lookback-hours", "26"],
                 ["spot-check", "--subject", "aya", "--ratio", "0.1"],
                 ["spot-check", "--subject", "aya", "--ratio", "0.1", "--lookback-hours", "26", "--since", "2026-10-01T00:00:00+08:00"],
                 ["spot-check", "--subject", "math", "--ratio", "0.1", "--lookback-hours", "26"], ["sync"]):
        with pytest.raises(SystemExit):
            scheduled.main(argv, GOOD)


class FakeConn:
    """计量循环的假连接：角色核对按 role_ok 返回；每次调用依次返回 counts 里的条数，用完返回 0"""

    def __init__(self, counts, role_ok: bool = True) -> None:
        self.counts, self.role_ok, self.calls = list(counts), role_ok, []

    async def fetchrow(self, sql, *args):
        return {"cur": scheduled.ROLE, "rolsuper": not self.role_ok, "rolbypassrls": False, "rolcreaterole": False, "rolcreatedb": False,
                "is_scheduler": True, "others": [], "predefined": []}

    async def fetchval(self, sql, *args):
        self.calls.append((sql, args))
        return self.counts.pop(0) if self.counts else 0


@pytest.mark.anyio
async def test_meter_loop_semantics():
    c = FakeConn([50, 50, 3])
    assert await scheduled.meter_expire_due(c, 50) == (103, 3, False)                # 一批不满即停
    assert all("platform.meter_expire_due" in sql and args == (50,) for sql, args in c.calls)
    c = FakeConn([10, 10, 10, 10])
    assert await scheduled.meter_renew_due(c, 10, max_batches=2) == (20, 2, True)    # 到了上限仍可能有到期
    assert all("platform.meter_renew_due" in sql for sql, _ in c.calls)
    c = FakeConn([0])
    assert await scheduled.meter_expire_due(c, 1) == (0, 1, False)
    with pytest.raises(scheduled.SchedulerRoleError):
        await scheduled.meter_expire_due(FakeConn([5], role_ok=False))
    with pytest.raises(ValueError):
        await scheduled.meter_expire_due(FakeConn([]), 0)
    with pytest.raises(scheduled.SchedulerRoleError):
        await scheduled.sample_spot_checks(FakeConn([1], role_ok=False), "aya", 0.1, lookback_hours=26)
    c = FakeConn([7])
    assert await scheduled.sample_spot_checks(c, "wenli", 0.25, lookback_hours=26) == 7
    assert c.calls[0][1] == ("wenli", None, 26, 0.25)


@pytest.mark.anyio
async def test_model_call_purge_loop_semantics():
    """调用记录留存删除（0060，specs/plt-model-call-log）：时钟取数据库 now()、批量由调用方给，一批不满即停；到了上限 more_due"""
    c = FakeConn([1000, 1000, 5])
    assert await scheduled.purge_model_calls(c) == (2005, 3, False)
    assert all(sql == "SELECT platform.model_call_log_purge(now(), $1::integer)" and args == (1000,) for sql, args in c.calls)
    c = FakeConn([3, 3, 3])
    assert await scheduled.purge_model_calls(c, 3, max_batches=2) == (6, 2, True)
    assert await scheduled.purge_model_calls(FakeConn([0]), 10) == (0, 1, False)
    with pytest.raises(scheduled.SchedulerRoleError):
        await scheduled.purge_model_calls(FakeConn([5], role_ok=False))
    for bad in (0, 10_001):
        with pytest.raises(ValueError):
            await scheduled.purge_model_calls(FakeConn([]), bad)


def test_model_call_purge_more_due_exit_code(monkeypatch, capsys):
    async def fake_run(a, url, env=None):
        assert a.cmd == "model-call-purge" and a.batch == 1000 and a.max_batches == 10_000
        return {"job": "model-call-purge", "purged": 2000, "batches": 2, "more_due": True}
    monkeypatch.setattr(scheduled, "_run", fake_run)
    assert scheduled.main(["model-call-purge"], GOOD) == 1
    assert json.loads(capsys.readouterr().out)["purged"] == 2000


def test_more_due_exit_code(monkeypatch, capsys):
    async def fake_run(a, url, env=None):  # LT-01：_run 另收运行环境（staging 的服务端地址口径）
        return {"job": "meter-expire", "expired": 100, "batches": 2, "more_due": True}
    monkeypatch.setattr(scheduled, "_run", fake_run)
    assert scheduled.main(["meter-expire"], GOOD) == 1
    assert json.loads(capsys.readouterr().out)["more_due"] is True


# ---------------------------------------------------------------- 库上
@pytest.fixture
def scheddb(insightdb):
    if insightdb.psql(f"SELECT count(*) FROM pg_roles WHERE rolname = '{SCHED_ROLE}'").strip() == "0":
        insightdb.psql(f"CREATE ROLE {SCHED_ROLE} LOGIN IN ROLE starbuddy_scheduler;")
    return insightdb


def test_scheduled_cli_on_local_db(scheddb):
    db = scheddb
    env = {"SCHEDULER_ENV": "test", "SCHEDULER_DATABASE_URL": _url(db, SCHED_ROLE)}
    for args, want in ((("freeze",), {"job": "freeze", "frozen": 0}), (("erasures",), {"job": "erasures", "executed": 0}),
                       (("meter-expire",), {"job": "meter-expire", "expired": 0, "batches": 1, "more_due": False}),
                       (("meter-renew", "--batch", "10"), {"job": "meter-renew", "renewed": 0, "batches": 1, "more_due": False})):
        p = _run("insight.scheduled", *args, env=env)
        assert p.returncode == 0, p.stderr
        assert json.loads(p.stdout) == want and len(p.stdout.splitlines()) == 1
    # 抽检：补 40 条知行判分（合成），比例 0.5 → 按判分 ID 哈希确定性抽样；重复运行不重复进队
    db.psql(f"""SET session_replication_role = replica;
        INSERT INTO platform.grade_result (student_id, subject, attempt_id, attempt_occurred_at, verdict, rubric_version, score_total, max_total,
          calibrated, confidence, source_version, model_version, human_review_required, semantics, evidence_ids, source_request_id)
          SELECT '{S3}', 'zhixing', gen_random_uuid(), now() - interval '1 hour', 'correct', 'zhixing-rubric@0.1.0', 1, 1, true, 1,
                 'rubric:zhixing-rubric@0.1.0', 'algo:zhixing-rubric@0.1.0', false, 'formal', ARRAY[gen_random_uuid()], gen_random_uuid()
            FROM generate_series(1, 40);""")
    try:
        want = int(db.psql("SELECT count(*) FROM platform.grade_result g WHERE g.subject = 'zhixing' AND g.superseded_by IS NULL "
                           "AND ('x' || substr(md5(g.id::text), 1, 8))::bit(32)::bigint < floor(0.5 * 4294967296)").strip())
        assert want >= 1
        # 多学科：每个学科各调一次（重复的学科只算一次）；没有判分的学科记 0
        p = _run("insight.scheduled", "spot-check", "--subject", "wenli", "--subject", "zhixing", "--subject", "zhixing", "--ratio", "0.5",
                 "--lookback-hours", "26", env=env)
        assert p.returncode == 0, p.stderr
        assert json.loads(p.stdout) == {"job": "spot-check", "enqueued": {"wenli": 0, "zhixing": want}} and not UUID_ANY.search(p.stdout)
        assert db.psql("SELECT count(*) FROM platform.review_task WHERE subject = 'zhixing' AND source_kind = 'grade_result'").strip() == str(want)
        p = _run("insight.scheduled", "spot-check", "--subject", "zhixing", "--ratio", "0.5", "--since",
                 db.psql("SELECT (now() - interval '1 day')::text").strip().replace(" ", "T"), env=env)
        assert p.returncode == 0 and json.loads(p.stdout)["enqueued"] == {"zhixing": 0}, p.stderr
        # 库函数拒绝（比例超出 (0, 0.5]、since 超过 31 天）：退出码 2，只报错误类别与 SQLSTATE
        for extra in (("--ratio", "0.9", "--lookback-hours", "26"), ("--ratio", "0.1", "--since", "2020-01-01T00:00:00+08:00")):
            p = _run("insight.scheduled", "spot-check", "--subject", "zhixing", *extra, env=env)
            assert p.returncode == 2 and "22023" in p.stderr and "Traceback" not in p.stderr
    finally:
        db.psql("""SET session_replication_role = replica;
            DELETE FROM platform.review_task WHERE subject = 'zhixing' AND source_kind = 'grade_result';
            DELETE FROM platform.grade_result WHERE subject = 'zhixing';""")
    # 登录角色不是只继承 starbuddy_scheduler：拒绝执行，不带消息原文
    bad = _run("insight.scheduled", "meter-expire", env={**env, "SCHEDULER_DATABASE_URL": _url(db, APP_ROLE)})
    assert bad.returncode == 2 and "执行失败" in bad.stderr and "Traceback" not in bad.stderr


MCL_ROW = ("INSERT INTO platform.model_call_log (occurred_at, env, task, data_class, source_version, outcome, attempt_count, fallback,"
           " attempt_statuses, input_tokens, output_tokens, elapsed_ms, model_mismatch, usage_ref, idempotency_ref)"
           " VALUES (now() - interval '{age}', 'test', 'T2', 'L1', 'invalid', 'rejected', 0, false, '{{}}', 0, 0, 1, false,"
           " left(md5('{ref}'), 16), left(md5('idem-{ref}'), 16));")


def test_model_call_purge_on_local_db(scheddb):
    """调用记录留存删除（0060）：只删 180 天前的行、按批；到了批次上限退出码 1、下一轮接着删；输出只有计数"""
    db = scheddb
    env = {"SCHEDULER_ENV": "test", "SCHEDULER_DATABASE_URL": _url(db, SCHED_ROLE)}
    db.psql("SELECT platform.ensure_monthly_partitions('platform.model_call_log', (now() - interval '200 days')::date, 8);"
            + MCL_ROW.format(age="200 days", ref="syn-purge-old-1") + MCL_ROW.format(age="190 days", ref="syn-purge-old-2")
            + MCL_ROW.format(age="179 days", ref="syn-purge-keep-1") + MCL_ROW.format(age="1 hour", ref="syn-purge-keep-2"))
    try:
        p = _run("insight.scheduled", "model-call-purge", "--batch", "1", "--max-batches", "1", env=env)
        assert p.returncode == 1, p.stderr
        assert json.loads(p.stdout) == {"job": "model-call-purge", "purged": 1, "batches": 1, "more_due": True}
        p = _run("insight.scheduled", "model-call-purge", "--batch", "1", env=env)
        assert p.returncode == 0, p.stderr
        assert json.loads(p.stdout) == {"job": "model-call-purge", "purged": 1, "batches": 2, "more_due": False}
        assert not UUID_ANY.search(p.stdout) and len(p.stdout.splitlines()) == 1
        left = db.psql("SELECT string_agg(usage_ref, ',' ORDER BY occurred_at) FROM platform.model_call_log"
                       " WHERE usage_ref IN (left(md5('syn-purge-keep-1'), 16), left(md5('syn-purge-keep-2'), 16),"
                       " left(md5('syn-purge-old-1'), 16), left(md5('syn-purge-old-2'), 16))").strip()
        assert left == db.psql("SELECT left(md5('syn-purge-keep-1'), 16) || ',' || left(md5('syn-purge-keep-2'), 16)").strip()
        p = _run("insight.scheduled", "model-call-purge", env=env)
        assert p.returncode == 0 and json.loads(p.stdout)["purged"] == 0, p.stderr
    finally:
        db.psql("DELETE FROM platform.model_call_log WHERE usage_ref IN (left(md5('syn-purge-keep-1'), 16), left(md5('syn-purge-keep-2'), 16),"
                " left(md5('syn-purge-old-1'), 16), left(md5('syn-purge-old-2'), 16));")


# ---------------------------------------------------------------- insight.jobs weekly
@pytest.mark.parametrize("week", ["2026-10-06", "not-a-date", "2026-13-01"])
def test_weekly_week_start_checked(week, monkeypatch, capsys):
    monkeypatch.setenv("INSIGHT_ENV", "test")
    monkeypatch.setenv("INSIGHT_DATABASE_URL", "postgresql://u@localhost/x")

    async def never(*_a, **_k):
        raise AssertionError("不应连库")
    import insight.store
    monkeypatch.setattr(insight.store, "create_pool", never)
    assert jobs_cli.main(["weekly", "--week-start", week]) == 2
    assert "--week-start" in capsys.readouterr().err


def test_weekly_failed_outcomes_exit_1(monkeypatch, capsys):
    async def fake_run(*a, **k):
        return {"job": "weekly", "week_start": "2026-10-05", "students": 1,
                "outcomes": {"generated": 1, "withheld": 0, "empty": 0, "exists": 0, "failed": 1}, "sampled": 0,
                "profile_pages": {"ok": 0, "failed": 0}}
    monkeypatch.setattr(jobs_cli, "_run", fake_run)
    assert jobs_cli.main(["weekly"]) == 1
    fake = {"job": "weekly", "outcomes": {"failed": 0}}

    async def ok_run(*a, **k):
        return fake
    monkeypatch.setattr(jobs_cli, "_run", ok_run)
    assert jobs_cli.main(["weekly"]) == 0
    capsys.readouterr()


def _signed_copy(tmp_path, name: str, doc: dict) -> str:
    """只在测试里模拟“已签字”的配置（数值仍是合成占位）"""
    doc = {**doc, "synthetic": False, "signed_by": "test-signer", "signed_at": "2026-09-30"}
    p = tmp_path / name
    p.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    return str(p)


def test_weekly_synthetic_config_only_in_test(tmp_path, insightdb):
    """dev 环境：判断策略用“已签字”的副本，周报配置或一致性词表任一仍是合成占位 → 拒绝，不连库写任何东西"""
    from insight.policy import CONFIG_DIR
    from support import policy_doc
    pol = _signed_copy(tmp_path, "claims.signed.json", policy_doc())
    weekly = _signed_copy(tmp_path, "weekly.signed.json", json.loads((CONFIG_DIR / "weekly.synthetic.json").read_text(encoding="utf-8")))
    check = _signed_copy(tmp_path, "weekly-check.signed.json",
                         json.loads((CONFIG_DIR / "weekly-check.synthetic.json").read_text(encoding="utf-8")))
    env = {"INSIGHT_ENV": "dev", "INSIGHT_DATABASE_URL": _url(insightdb, APP_ROLE), "INSIGHT_POLICY": pol}
    for extra in ({}, {"INSIGHT_WEEKLY_CHECK": check}, {"INSIGHT_WEEKLY_POLICY": weekly}):
        p = _run("insight.jobs", "weekly", env={**env, **extra})
        assert p.returncode == 2 and "周报配置不可用" in p.stderr, (extra, p.stderr)


class ServerConn:
    def __init__(self, addr) -> None:
        self.addr = addr
        self.closed = False

    async def fetchval(self, sql, *args):
        assert "inet_server_addr()" in sql
        return self.addr

    async def close(self):
        self.closed = True


@pytest.mark.anyio
@pytest.mark.parametrize("addr", ["10.0.0.5", "192.0.2.7", "2001:db8::1"])
async def test_connected_server_must_be_local(addr):
    for ok in (None, "127.0.0.1", "::1"):
        await scheduled.check_server(ServerConn(ok))
    with pytest.raises(scheduled.CliError, match="连上的服务端不是本机"):
        await scheduled.check_server(ServerConn(addr))


def test_cli_checks_server_after_connect(monkeypatch, capsys):
    conn = ServerConn("10.0.0.5")

    async def fake_connect(*_a, **_k):
        return conn
    monkeypatch.setattr(scheduled, "connect_scheduler", fake_connect)
    assert scheduled.main(["freeze"], GOOD) == 2
    assert "连上的服务端不是本机" in capsys.readouterr().err and conn.closed


def test_weekly_other_value_errors_not_reported_as_week(insightdb, monkeypatch, capsys):
    """只有周参数的核对转成“周参数错”；生成过程中的 ValueError 按原样归类为执行失败"""
    from insight import jobs

    async def boom(*_a, **_k):
        raise ValueError("synthetic failure inside generation")
    monkeypatch.setattr(jobs, "weekly_reports", boom)
    monkeypatch.setenv("INSIGHT_ENV", "test")
    monkeypatch.setenv("INSIGHT_DATABASE_URL", _url(insightdb, APP_ROLE))
    assert jobs_cli.main(["weekly"]) == 2
    err = capsys.readouterr().err
    assert "执行失败（ValueError）" in err and "已结束" not in err and "synthetic" not in err


def test_weekly_on_local_db(insightdb):
    env = {"INSIGHT_ENV": "test", "INSIGHT_DATABASE_URL": _url(insightdb, APP_ROLE)}
    p = _run("insight.jobs", "weekly", env=env)
    assert p.returncode == 0, p.stderr
    out = json.loads(p.stdout)
    last_monday = insightdb.psql("SELECT (date_trunc('week', (now() AT TIME ZONE 'Asia/Shanghai')::date) - interval '7 days')::date").strip()
    assert out["job"] == "weekly" and out["week_start"] == last_monday
    assert set(out["outcomes"]) == {"generated", "withheld", "empty", "exists", "failed"} and out["outcomes"]["failed"] == 0
    assert out["profile_pages"] == {"ok": 0, "failed": 0} and not UUID_ANY.search(p.stdout) and len(p.stdout.splitlines()) == 1
    again = json.loads(_run("insight.jobs", "weekly", "--week-start", last_monday, env=env).stdout)
    assert again["outcomes"]["generated"] == 0 and again["outcomes"]["exists"] == out["outcomes"]["generated"] + out["outcomes"]["exists"]
    # 本周还没结束：拒绝
    this_monday = insightdb.psql("SELECT date_trunc('week', (now() AT TIME ZONE 'Asia/Shanghai')::date)::date").strip()
    p = _run("insight.jobs", "weekly", "--week-start", this_monday, env=env)
    assert p.returncode == 2 and "已结束" in p.stderr


class FakeHorizonConn(FakeConn):
    """分区续建的假连接：fetchval 返回新建分区数，fetch 返回水位（表 → 剩余月数）"""

    def __init__(self, created, ahead, role_ok: bool = True) -> None:
        super().__init__([created], role_ok)
        self.ahead = ahead

    async def fetch(self, sql, *args):
        self.calls.append((sql, args))
        return [{"parent": k, "ahead": v} for k, v in sorted(self.ahead.items())]


@pytest.mark.anyio
async def test_partition_ensure_semantics():
    """分区续建（0062，PLT-23）：时钟取数据库 now()；续建后任何一张表剩余不足 3 个月（或缺表）就告警"""
    full = {"attempt": 13, "evidence_event": 13, "model_call_log": 13, "usage_ledger": 13}
    c = FakeHorizonConn(0, full)
    r = await scheduled.ensure_partitions(c)
    assert r == {"created": 0, "months_ahead": full, "short": []}
    assert c.calls[0][0] == "SELECT platform.ensure_partitions_ahead(now())" and "platform.partition_horizon()" in c.calls[1][0]
    r = await scheduled.ensure_partitions(FakeHorizonConn(4, {**full, "usage_ledger": 2}))
    assert r["created"] == 4 and r["short"] == ["usage_ledger"]
    r = await scheduled.ensure_partitions(FakeHorizonConn(0, {k: v for k, v in full.items() if k != "attempt"}))
    assert r["short"] == ["attempt"]                                  # 水位里少了一张表也算不足
    r = await scheduled.ensure_partitions(FakeHorizonConn(0, {**full, "attempt": None}))
    assert r["short"] == ["attempt"]                                  # 一个分区都没有
    with pytest.raises(scheduled.SchedulerRoleError):
        await scheduled.ensure_partitions(FakeHorizonConn(0, full, role_ok=False))


@pytest.mark.anyio
async def test_retention_purge_loop_semantics():
    """保留期清理（0063，PLT-23）：五张表依次、每张表一批不满即停；任何一张表到了批次上限 more_due"""
    c = FakeConn([1000, 5, 0, 3, 0, 1000, 7])
    per, batches, more = await scheduled.purge_retention(c)
    assert per == {"ingest_request": 1005, "auth_otp_challenge": 0, "auth_session": 3, "auth_rate_counter": 0, "behavior_event": 1007}
    assert batches == 7 and more is False
    funcs = [sql.split("(")[0].removeprefix("SELECT ") for sql, _ in c.calls]
    assert funcs == ["platform.retention_purge_ingest_request"] * 2 + ["platform.retention_purge_auth_otp", "platform.retention_purge_auth_session",
                     "platform.retention_purge_auth_rate_counter"] + ["insight.retention_purge_behavior_event"] * 2
    assert all(sql.endswith("(now(), $1::integer)") and args == (1000,) for sql, args in c.calls)
    per, batches, more = await scheduled.purge_retention(FakeConn([3, 3, 0, 0, 0, 0]), 3, max_batches=2)
    assert per["ingest_request"] == 6 and more is True                # 第一张表到了上限，其余照常清理
    with pytest.raises(scheduled.SchedulerRoleError):
        await scheduled.purge_retention(FakeConn([5], role_ok=False))
    for bad in (0, 10_001):
        with pytest.raises(ValueError):
            await scheduled.purge_retention(FakeConn([]), bad)


def test_partition_alert_and_retention_more_due_exit_code(monkeypatch, capsys):
    async def fake_run(a, url, env=None):
        if a.cmd == "partition-ensure":
            return {"job": a.cmd, "created": 0, "months_ahead": {}, "short": ["usage_ledger"], "alert": True}
        assert a.cmd == "retention-purge" and a.batch == 1000 and a.max_batches == 10_000
        return {"job": a.cmd, "purged": {"behavior_event": 1}, "batches": 6, "more_due": False}
    monkeypatch.setattr(scheduled, "_run", fake_run)
    assert scheduled.main(["partition-ensure"], GOOD) == 1
    assert json.loads(capsys.readouterr().out)["short"] == ["usage_ledger"]
    assert scheduled.main(["retention-purge"], GOOD) == 0
    for argv in (["retention-purge", "--batch", "0"], ["retention-purge", "--batch", "10001"], ["retention-purge", "--max-batches", "0"]):
        assert scheduled.main(argv, GOOD) == 2


def test_data_maintenance_on_local_db(scheddb):
    """分区续建与保留期清理（0062、0063）在一次性库上：水位至少到当月 + 6；只删超过保留期的过程数据；输出只有计数"""
    db = scheddb
    env = {"SCHEDULER_ENV": "test", "SCHEDULER_DATABASE_URL": _url(db, SCHED_ROLE)}
    p = _run("insight.scheduled", "partition-ensure", env=env)
    assert p.returncode == 0, p.stderr
    out = json.loads(p.stdout)
    assert out["job"] == "partition-ensure" and out["short"] == [] and out["alert"] is False
    assert sorted(out["months_ahead"]) == ["attempt", "evidence_event", "model_call_log", "usage_ledger"]
    assert all(v >= 6 for v in out["months_ahead"].values())
    db.psql("INSERT INTO platform.auth_rate_counter (bucket, key_digest, window_start, hits) VALUES"
            " ('syn.dm.day', sha256('syn-dm-old'), now() - interval '3 days', 1), ('syn.dm.day', sha256('syn-dm-new'), now() - interval '1 hour', 1);")
    try:
        p = _run("insight.scheduled", "retention-purge", "--batch", "1", env=env)
        assert p.returncode == 0, p.stderr
        out = json.loads(p.stdout)
        assert out["job"] == "retention-purge" and out["purged"]["auth_rate_counter"] >= 1 and out["more_due"] is False
        assert sorted(out["purged"]) == ["auth_otp_challenge", "auth_rate_counter", "auth_session", "behavior_event", "ingest_request"]
        assert not UUID_ANY.search(p.stdout) and len(p.stdout.splitlines()) == 1
        left = db.psql("SELECT count(*) FILTER (WHERE key_digest = sha256('syn-dm-old')) || ',' || count(*) FILTER (WHERE key_digest = sha256('syn-dm-new'))"
                       " FROM platform.auth_rate_counter WHERE bucket = 'syn.dm.day'").strip()
        assert left == "0,1"
    finally:
        db.psql("DELETE FROM platform.auth_rate_counter WHERE bucket = 'syn.dm.day';")
