"""运行器、常驻循环与命令行的测试（不连库）：子进程用假模块 tests/fake_child.py，锁用假的上下文。
- 子进程环境只含白名单变量（断言实际收到的环境）；别的身份的连接串、凭据字样的变量、PATH / PYTHONPATH / LD_PRELOAD 一律不传；
- 任务级超时、退出码映射、alert 级别、计数清洗（不含学生 ID）、取不到锁跳过、取锁失败；
- 启动核对：SCHEDULER_ENV 与身份的运行环境只收 dev / test、连接串必须本机、缺变量拒绝启动；--dry-run 不连库、只列变量名。"""
from __future__ import annotations

import asyncio
import contextlib
import io
import json
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from scheduler import __main__ as cli
from scheduler.config import IDENTITIES, Job
from scheduler.loop import Loop
from urllib.parse import parse_qs, urlsplit

from scheduler.runner import (BASE_ENV, LockHandle, RunResult, Runner, child_env, lock_key, preflight, sanitize, statement_timeout_ms,
                              stderr_tail, with_statement_timeout)
from scheduler.schedule import parse_schedule

pytestmark = pytest.mark.anyio
TESTS = Path(__file__).resolve().parent
FAKE = {"insight": (Path(sys.executable), TESTS), "learning": (Path(sys.executable), TESTS)}
SOCK = "postgresql://scheduler_app@/x?host=/tmp"
ENV = {"SCHEDULER_ENV": "test", "SCHEDULER_DATABASE_URL": SOCK,
       "INSIGHT_ENV": "test", "INSIGHT_DATABASE_URL": "postgresql://insight_app@localhost/x",
       "LEARNING_ENV": "test", "LEARNING_DATABASE_URL": "postgresql://learning_app@127.0.0.1:5432/x",
       "LEARNING_MASTERY_CONFIG_AYA": "/synthetic/aya.json", "LEARNING_MASTERY_CONFIG_SECRET": "x",
       "REVIEW_CONSOLE_ENV": "test", "REVIEW_CONSOLE_KGE_DATABASE_URL": "postgresql://kge_app@[::1]/x",
       "HOME": "/root", "PATH": "/opt/evil/bin:/usr/bin", "PYTHONPATH": "/opt/evil", "LD_PRELOAD": "/opt/evil.so",
       "SCHEDULER_EXTRA": "not-declared", "INSIGHT_POLICY": "/synthetic/claims.json", "PGPASSWORD": "synthetic",
       # 带白名单前缀但是连接串类的名字：通配也不能匹配到
       "LEARNING_MASTERY_CONFIG_DATABASE_URL": "postgresql://u@db.example.cn/x", "LEARNING_MASTERY_CONFIG_X_URL": "http://x",
       "LEARNING_MASTERY_CONFIG_X_DSN": "x", "LEARNING_ADAPTER_AYA_URL": "http://x", "INSIGHT_LEARNING_URL": "http://x",
       "REVIEW_CONSOLE_REVIEW_DATABASE_URL": "postgresql://review_app@localhost/x"}
UUID_ANY = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)
BAD_URLS = ("postgresql://u@db.example.cn/x", "postgresql://u@10.0.0.5/x", "mysql://u@localhost/x",
            "postgresql://u@/x?host=db.example.cn", "postgresql://u@127.0.0.1:5432,db.example.cn:5432/x",
            "postgresql://u@localhost/x?hostaddr=10.0.0.5", "postgresql://u@localhost/x?service=synthetic-remote",
            "postgresql://u@[2001:db8::1]:5432/x", "postgresql://u@localhost.db.example.cn/x")


def job(*args: str, identity: str = "scheduler", timeout: float = 30, extra: tuple[str, ...] = (), name: str = "fake") -> Job:
    return Job(name, parse_schedule("every 5m"), IDENTITIES[identity], "fake_child", tuple(args), extra, timeout)


class Locks:
    """假锁：记录调用；granted 决定取没取到；broken 时抛错；alive 决定探活结果"""

    def __init__(self, granted: bool = True, broken: bool = False, alive: bool = True) -> None:
        self.granted, self.broken, self.alive, self.calls, self.probes = granted, broken, alive, [], 0

    async def _probe(self) -> bool:
        self.probes += 1
        return self.alive

    @contextlib.asynccontextmanager
    async def __call__(self, j, env):
        self.calls.append((j.name, dict(env)))
        if self.broken:
            raise OSError("synthetic: cannot connect")
        yield LockHandle(self.granted, self._probe)


def runner(env=None, locks=None, out=None) -> tuple[Runner, io.StringIO, Locks]:
    o = out or io.StringIO()
    lk = locks or Locks()
    return Runner(ENV if env is None else env, out=o, lock=lk, interpreters=FAKE), o, lk


def logs(o: io.StringIO) -> list[dict]:
    return [json.loads(ln) for ln in o.getvalue().splitlines() if ln.strip()]


# ---------------------------------------------------------------- 环境白名单
async def test_child_receives_only_whitelisted_env():
    r, o, lk = runner()
    res = await r.run(job("env"))                                 # 假子进程把自己实际收到的环境变量名作为计数打印出来
    assert res.outcome == "ok", res.reason
    assert set(res.counts["env"]) == set(BASE_ENV) | {"SCHEDULER_ENV", "SCHEDULER_DATABASE_URL"}
    assert res.counts["path_is_fixed"] is True
    assert lk.calls and set(lk.calls[0][1]) == set(res.counts["env"])  # 锁也只拿到同一份环境（按该身份的连接取）
    line = o.getvalue().strip()
    assert SOCK not in line and "PGPASSWORD" not in line and "/opt/evil" not in line


@pytest.mark.parametrize("identity, want", [
    ("scheduler", {"SCHEDULER_ENV", "SCHEDULER_DATABASE_URL"}),
    ("insight", {"INSIGHT_ENV", "INSIGHT_DATABASE_URL", "INSIGHT_POLICY"}),
    ("learning", {"LEARNING_ENV", "LEARNING_DATABASE_URL", "LEARNING_MASTERY_CONFIG_AYA"}),
    ("kge", {"REVIEW_CONSOLE_ENV", "REVIEW_CONSOLE_KGE_DATABASE_URL"}),
])
async def test_child_env_per_identity(identity, want):
    extra = {"insight": ("INSIGHT_POLICY", "INSIGHT_WEEKLY_POLICY"), "learning": ("LEARNING_MASTERY_CONFIG_*",)}.get(identity, ())
    j = job("env", identity=identity, extra=extra)
    env = child_env(j, ENV)
    assert set(env) == set(BASE_ENV) | want                     # 凭据字样的变量即使匹配前缀也不传；没设的可选变量不出现
    assert all(env[k] == ENV[k] for k in want) and env["PATH"] == BASE_ENV["PATH"]
    r, o, _ = runner()
    res = await r.run(j)
    assert res.outcome == "ok" and set(res.counts["env"]) == set(BASE_ENV) | want and res.counts["path_is_fixed"] is True


async def test_lock_connection_checks_server_is_local(monkeypatch):
    """取锁的连接连上后先核对服务端地址：不是本机即拒绝（不取锁、不起子进程）"""
    import asyncpg

    from scheduler.runner import pg_advisory_lock

    class Conn:
        def __init__(self) -> None:
            self.sql: list[str] = []

        async def fetchval(self, sql, *args):
            self.sql.append(sql)
            return "10.0.0.5" if "inet_server_addr()" in sql else True

        async def close(self):
            pass
    conn = Conn()

    async def fake_connect(*_a, **_k):
        return conn
    monkeypatch.setattr(asyncpg, "connect", fake_connect)
    r = Runner(ENV, out=io.StringIO(), lock=pg_advisory_lock, interpreters=FAKE)
    res = await r.run(job("exit", "0"))
    assert (res.outcome, res.exit_code) == ("refused", 2) and "本机" in res.reason
    assert not any("pg_try_advisory_lock" in s for s in conn.sql)


@pytest.mark.parametrize("identity, extra", [
    ("scheduler", ("INSIGHT_DATABASE_URL",)), ("scheduler", ("SCHEDULER_EXTRA",)), ("scheduler", ("SCHEDULER_*",)),
    ("scheduler", ("SCHEDULER_PASSWORD",)), ("scheduler", ("PATH",)), ("scheduler", ("PYTHONPATH",)), ("scheduler", ("scheduler_x",)),
    ("kge", ("REVIEW_CONSOLE_REVIEW_DATABASE_URL",)), ("kge", ("REVIEW_CONSOLE_R*",)), ("kge", ("REVIEW_CONSOLE_*",)),
    ("learning", ("LEARNING_ADAPTER_*",)), ("learning", ("LEARNING_*",)), ("learning", ("LEARNING_MASTERY_CONFIG_AYA",)),
    ("learning", ("LEARNING_MASTERY_*",)), ("learning", ("LEARNING_DATABASE_URL",)),
    ("insight", ("INSIGHT_LEARNING_URL",)), ("insight", ("INSIGHT_*",)), ("insight", ("INSIGHT_POLICY*",)),
    ("insight", ("LEARNING_MASTERY_CONFIG_*",)),                       # 别的身份登记的前缀
])
async def test_undeclarable_extra_env_refused(identity, extra):
    lk = Locks()
    r, o, _ = runner(locks=lk)
    res = await r.run(job("env", identity=identity, extra=extra))
    assert (res.outcome, res.exit_code, res.level) == ("refused", 2, "alert") and lk.calls == []


def test_wildcard_never_matches_connection_strings():
    env = child_env(job("env", identity="learning", extra=("LEARNING_MASTERY_CONFIG_*",)), ENV)
    assert {k for k in env if k.startswith("LEARNING_MASTERY_CONFIG_")} == {"LEARNING_MASTERY_CONFIG_AYA"}
    assert "LEARNING_ADAPTER_AYA_URL" not in env and "INSIGHT_LEARNING_URL" not in env


def test_statement_timeout_appended_to_child_url():
    j = job("env", timeout=30)
    env = preflight(j, ENV, Path(sys.executable))
    q = parse_qs(urlsplit(env["SCHEDULER_DATABASE_URL"]).query)
    assert q == {"host": ["/tmp"], "statement_timeout": [str(statement_timeout_ms(30))]} and statement_timeout_ms(30) == 27000
    assert ENV["SCHEDULER_DATABASE_URL"] == SOCK                       # 调度器自己的环境不变
    assert statement_timeout_ms(600) == 540000 and statement_timeout_ms(0.5) == 1000
    again = parse_qs(urlsplit(with_statement_timeout(env["SCHEDULER_DATABASE_URL"], 5)).query)
    assert again["statement_timeout"] == ["5"]                         # 已有的被替换，不重复


async def test_lock_probe_failure_kills_child():
    lk = Locks(alive=False)
    r = Runner(ENV, out=io.StringIO(), lock=lk, interpreters=FAKE, probe_seconds=0.2)
    t0 = time.monotonic()
    res = await r.run(job("sleep", "30"))
    assert time.monotonic() - t0 < 8 and lk.probes >= 1
    assert (res.outcome, res.exit_code, res.level, res.reason) == ("lock_lost", 1, "alert", "lock_connection_lost")


async def test_lock_probed_while_child_runs():
    lk = Locks(alive=True)
    r = Runner(ENV, out=io.StringIO(), lock=lk, interpreters=FAKE, probe_seconds=0.2)
    res = await r.run(job("sleep", "1.2"))
    assert res.outcome == "ok" and lk.probes >= 3


@pytest.mark.parametrize("missing", ["SCHEDULER_ENV", "SCHEDULER_DATABASE_URL"])
async def test_missing_required_env_refused_before_lock(missing):
    lk = Locks()
    r, o, _ = runner(env={k: v for k, v in ENV.items() if k != missing}, locks=lk)
    res = await r.run(job("env"))
    assert (res.outcome, res.exit_code) == ("refused", 2) and f"缺少 {missing}" in res.reason and lk.calls == []


@pytest.mark.parametrize("value", ["pilot", "prod", "production", "", "TEST"])
async def test_identity_env_must_be_dev_or_test(value):
    lk = Locks()
    r, o, _ = runner(env={**ENV, "INSIGHT_ENV": value}, locks=lk)
    res = await r.run(job("env", identity="insight"))
    assert (res.outcome, res.exit_code) == ("refused", 2) and lk.calls == []


@pytest.mark.parametrize("url", BAD_URLS)
@pytest.mark.parametrize("identity, var", [("scheduler", "SCHEDULER_DATABASE_URL"), ("kge", "REVIEW_CONSOLE_KGE_DATABASE_URL")])
async def test_non_local_url_refused_before_lock(url, identity, var):
    lk = Locks()
    r, o, _ = runner(env={**ENV, var: url}, locks=lk)
    res = await r.run(job("env", identity=identity))
    assert (res.outcome, res.exit_code, res.level) == ("refused", 2, "alert") and lk.calls == []
    assert url not in o.getvalue()


async def test_missing_interpreter_refused():
    lk = Locks()
    r = Runner(ENV, out=io.StringIO(), lock=lk, interpreters={"insight": (TESTS / "no-such-python", TESTS)})
    res = await r.run(job("env"))
    assert res.outcome == "refused" and lk.calls == []


# ---------------------------------------------------------------- 退出码映射、超时、清洗、锁
@pytest.mark.parametrize("identity, args, outcome, code, level, child", [
    ("scheduler", ("exit", "0"), "ok", 0, "info", 0),
    ("scheduler", ("exit", "1"), "partial", 1, "alert", 1),
    ("scheduler", ("exit", "2"), "refused", 2, "alert", 2),
    ("scheduler", ("exit", "3"), "crashed", 1, "alert", 3),
    ("scheduler", ("signal",), "crashed", 1, "alert", -9),
    ("scheduler", ("garbage",), "bad_output", 1, "alert", 0),
    ("learning", ("stale",), "partial", 1, "alert", 1),
    ("learning", ("exit", "1"), "partial", 1, "alert", 1),
    # 审核台命令行以 SystemExit(说明) 拒绝，退出码 1：按“拒绝”记
    ("kge", ("exit", "1"), "refused", 2, "alert", 1),
    ("kge", ("exit", "2"), "refused", 2, "alert", 2),
    ("kge", ("exit", "3"), "crashed", 1, "alert", 3),
])
async def test_exit_code_mapping(identity, args, outcome, code, level, child):
    r, o, _ = runner()
    res = await r.run(job(*args, identity=identity))
    assert (res.outcome, res.exit_code, res.level, res.child_exit) == (outcome, code, level, child)
    (line,) = logs(o)
    assert line["outcome"] == outcome and line["level"] == level and line["exit_code"] == code and line["event"] == "job_run"


def test_stale_estimate_alerts_even_on_success():
    t = "2026-10-12T00:00:00.000+00:00"
    assert RunResult("x", "ok", 0, t, t, 1, counts={"stale_estimate": 1}).level == "alert"
    assert RunResult("x", "ok", 0, t, t, 1, counts={"stale_estimate": 0}).level == "info"
    assert RunResult("x", "ok", 0, t, t, 1, counts={"stale_estimate": True}).level == "info"
    assert RunResult("x", "skipped_locked", 0, t, t, 1).level == "info"
    assert RunResult("x", "skipped_running", 0, t, t, 1).level == "alert"


async def test_timeout_kills_child_and_alerts():
    r, o, _ = runner()
    t0 = time.monotonic()
    res = await r.run(job("sleep", "30", timeout=1))
    assert time.monotonic() - t0 < 10
    assert (res.outcome, res.exit_code, res.level) == ("timeout", 1, "alert") and res.counts is None
    assert logs(o)[0]["outcome"] == "timeout"


async def test_counts_sanitized_no_student_ids():
    r, o, _ = runner()
    res = await r.run(job("leak"))
    assert res.outcome == "ok"
    assert res.counts == {"job": "fake", "n": 3, "nested": {"ok": 1, "ids": ["aya"]}}
    assert not UUID_ANY.search(o.getvalue())
    res = await r.run(job("leakerr"))
    assert res.outcome == "refused" and "uuid" in res.reason
    assert not UUID_ANY.search(o.getvalue())


def test_stderr_tail_whitelist():
    raw = ("insight.scheduled：执行失败（CheckViolationError 23514） postgresql://scheduler_app:pw@db.example.cn:5432/x?host=/tmp "
           "user=alice c1000000-0000-4000-8000-0000000000c1 <script> $HOME\n").encode()
    tail = stderr_tail(b"first line\n" + raw)
    assert "CheckViolationError 23514" in tail and "uuid" in tail
    for leak in ("postgres", "db.example.cn", "pw", "alice", "user=", "c1000000", "<", ">", "$", "/", "执行失败", "@"):
        assert leak not in tail, leak
    assert set(tail) <= set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_ .:()-")
    assert stderr_tail(b"postgres://u@h/x") == "" and stderr_tail(b"") == "" and len(stderr_tail(b"x" * 500)) == 200


def test_sanitize_rules():
    assert sanitize({"x": "a@b"}) == {} and sanitize({"x": "aya-symbolic@0.1.0"}) == {}
    assert sanitize({"a": 1, "b": 1.5, "c": True, "d": None, "e": "aya", "f": "2026-10-05", "g": float("nan")}) == \
        {"a": 1, "b": 1.5, "c": True, "d": None, "e": "aya", "f": "2026-10-05"}
    assert sanitize({"x": "c1000000c0004000800000000000c100"}) == {}
    assert sanitize({"x": "有中文"}) == {} and sanitize({"x": "a b"}) == {}
    assert sanitize({"a": {"b": {"c": {"d": 1}}}}) == {"a": {"b": {}}}           # 超过 3 层的结构丢掉
    assert sanitize({"K": 1, "ok_key": 2}) == {"ok_key": 2}


async def test_skipped_when_lock_held_elsewhere():
    lk = Locks(granted=False)
    r, o, _ = runner(locks=lk)
    t0 = time.monotonic()
    res = await r.run(job("sleep", "5"))
    assert time.monotonic() - t0 < 3                               # 子进程没有启动
    assert (res.outcome, res.exit_code, res.level) == ("skipped_locked", 0, "info") and len(lk.calls) == 1


async def test_lock_error_is_alert_without_message():
    r, o, _ = runner(locks=Locks(broken=True))
    res = await r.run(job("exit", "0"))
    assert (res.outcome, res.exit_code, res.level, res.reason) == ("lock_error", 2, "alert", "OSError")
    assert "synthetic" not in o.getvalue()


async def test_dry_run_does_not_lock_or_spawn():
    lk = Locks()
    r, o, _ = runner(locks=lk)
    res = await r.run(job("exit", "1"), dry_run=True)
    assert (res.outcome, res.exit_code) == ("dry_run", 0) and lk.calls == []


def test_lock_key_derived_from_name():
    keys = {n: lock_key(n) for n in ("meter-expire", "meter-renew", "erasures", "freeze", "sweep", "mastery-sweep", "weekly")}
    assert len(set(keys.values())) == len(keys)
    assert all(-(1 << 63) <= k < (1 << 63) for k in keys.values()) and lock_key("sweep") == keys["sweep"]


# ---------------------------------------------------------------- 常驻循环
class FakeClock:
    def __init__(self, t: datetime) -> None:
        self.t = t

    def __call__(self) -> datetime:
        return self.t


class StubRunner(Runner):
    """不起子进程：只记下被启动的任务"""

    def __init__(self, out: io.StringIO) -> None:
        super().__init__(ENV, out=out, lock=Locks(), interpreters=FAKE)
        self.started: list[str] = []

    async def run(self, job, *, dry_run=False):
        self.started.append(job.name)
        t = "2026-10-12T00:00:00.000+00:00"
        return RunResult(job.name, "ok", 0, t, t, 0)


async def test_loop_due_no_overlap_and_no_catch_up():
    clock = FakeClock(datetime(2026, 10, 12, 2, 3, tzinfo=timezone.utc))  # 上海 10:03
    jobs = {"five": job("exit", "0", name="five"), "daily": Job("daily", parse_schedule("daily 02:30"), IDENTITIES["scheduler"],
                                                                     "fake_child", ("exit", "0"))}
    o = io.StringIO()
    r = StubRunner(o)
    lp = Loop(jobs, r, clock)
    assert lp.due(clock.t) == ([], [])
    clock.t += timedelta(minutes=2)                                  # 10:05：每 5 分钟的到期
    start, busy = lp.due(clock.t)
    assert [j.name for j in start] == ["five"] and busy == []
    assert lp.next_due["five"] == clock.t + timedelta(minutes=5)
    busy_run = asyncio.get_running_loop().create_future()            # 上一轮还没结束
    lp.running["five"] = busy_run
    clock.t += timedelta(minutes=5)
    started = lp.tick()
    await asyncio.gather(*started)
    assert r.started == [] and started == []
    (line,) = logs(o)
    assert line["outcome"] == "skipped_running" and line["level"] == "alert" and line["job"] == "five"
    busy_run.set_result(None)
    clock.t += timedelta(hours=30)                                   # 错过很多轮：只跑一次，不补跑
    start, busy = lp.due(clock.t)
    assert sorted(j.name for j in start) == ["daily", "five"]
    assert lp.due(clock.t) == ([], [])
    assert 0.05 <= lp.sleep_seconds() <= 30


async def test_loop_tick_starts_runs():
    clock = FakeClock(datetime(2026, 10, 12, 2, 4, 59, tzinfo=timezone.utc))
    r, o, _ = runner()
    lp = Loop({"five": job("exit", "0", name="five")}, r, clock)
    clock.t += timedelta(seconds=1)
    tasks = lp.tick()
    results = await asyncio.gather(*tasks)
    assert [x.outcome for x in results] == ["ok"] and logs(o)[0]["job"] == "five"


async def test_loop_catch_up_only_weekly_on_start():
    """启动时只有声明了 catch_up 的周报立即补跑一次；其他任务（包括错过了时刻的每日任务）等下一个时刻"""
    from scheduler.config import load_config
    cfg = load_config()
    assert [n for n, j in cfg.jobs.items() if j.catch_up] == ["weekly"]
    clock = FakeClock(datetime(2026, 10, 13, 1, 0, tzinfo=timezone.utc))   # 上海周二 09:00：周一 03:00 与当天 02:30 都已错过
    o = io.StringIO()
    r = StubRunner(o)
    lp = Loop(cfg.jobs, r, clock)
    await asyncio.gather(*lp.tick())
    assert r.started == ["weekly"]
    from scheduler.schedule import TZ
    assert lp.next_due["weekly"] == datetime(2026, 10, 19, 3, 0, tzinfo=TZ)            # 补跑后回到正常时间表
    await asyncio.gather(*lp.tick())
    assert r.started == ["weekly"]                                         # 只补一次


# ---------------------------------------------------------------- 命令行与启动核对
def _cli(argv, env, capsys):
    out = io.StringIO()
    rc = cli.main(argv, env, out=out)
    return rc, out.getvalue(), capsys.readouterr().err


FULL_ENV = {k: v for k, v in ENV.items()}


@pytest.mark.parametrize("value", [None, "", "pilot", "prod", "production", "Dev"])
def test_cli_refuses_other_envs(value, capsys):
    env = {k: v for k, v in FULL_ENV.items() if k != "SCHEDULER_ENV"}
    if value is not None:
        env["SCHEDULER_ENV"] = value
    rc, out, err = _cli(["--dry-run"], env, capsys)
    assert rc == 2 and out == "" and "规则 9" in err


@pytest.mark.parametrize("var", ["SCHEDULER_DATABASE_URL", "INSIGHT_DATABASE_URL", "LEARNING_DATABASE_URL", "REVIEW_CONSOLE_KGE_DATABASE_URL"])
def test_cli_refuses_non_local_db(var, capsys):
    rc, out, err = _cli(["--dry-run"], {**FULL_ENV, var: "postgresql://u@db.example.cn/x"}, capsys)
    assert rc == 2 and out == "" and "启动核对不通过" in err and "db.example.cn" not in err


def test_cli_refuses_missing_vars_and_unknown_job(capsys):
    env = {k: v for k, v in FULL_ENV.items() if not k.startswith("LEARNING_")}
    rc, out, err = _cli(["--dry-run"], env, capsys)
    assert rc == 2 and "LEARNING_ENV" in err
    rc, out, err = _cli(["--once", "meter-expire", "--dry-run"], env, capsys)   # 只核对选中的任务
    assert rc == 0 and json.loads(out)["outcome"] == "dry_run"
    rc, out, err = _cli(["--once", "no-such-job"], FULL_ENV, capsys)
    assert rc == 2 and "没有任务" in err


def test_cli_dry_run_lists_plan_without_values(capsys):
    rc, out, err = _cli(["--dry-run"], FULL_ENV, capsys)
    assert rc == 0, err
    plans = [json.loads(ln) for ln in out.splitlines()]
    assert len(plans) == 9 and all(p["event"] == "plan" and len(p["next"]) == 3 for p in plans)
    for v in ("/tmp", "localhost", "127.0.0.1", "/synthetic/", "synthetic", "/opt/evil"):
        assert v not in out
    by = {p["job"]: p for p in plans}
    assert by["mastery-sweep"]["env"] == sorted(set(BASE_ENV) | {"LEARNING_ENV", "LEARNING_DATABASE_URL", "LEARNING_MASTERY_CONFIG_AYA"})
    assert by["weekly"]["next"][0].endswith("03:00:00+08:00")


def test_cli_bad_schedule_file(tmp_path, capsys):
    p = tmp_path / "bad.yaml"
    p.write_text("schema_version: starbuddy-schedule/0.1\njobs: {}\n", encoding="utf-8")
    rc, out, err = _cli(["--schedule", str(p), "--dry-run"], FULL_ENV, capsys)
    assert rc == 2 and "时间表配置不合法" in err
    rc, out, err = _cli(["--schedule", str(tmp_path / "missing.yaml"), "--dry-run"], FULL_ENV, capsys)
    assert rc == 2
