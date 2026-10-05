"""命令行 python -m insight.jobs sync|sweep|metrics（SIE-02b 第二步，spec“第二步补充”默认方案第 5 条，待人确认 S2-4）：
只连本机数据库（连接前核对全部主机、连上后核对服务端地址）、只以 starbuddy_insight 执行、INSIGHT_ENV 只收 dev / test；
输出一行 JSON，只有计数，不含学生或判断 ID。全部合成数据（规则 8）。"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from insight import jobs_cli
from pginsight import APP_ROLE, LEARNING_ROLE, S1, S3
from test_db import reset

SVC = Path(__file__).resolve().parents[1]
UUID_ANY = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)
BAD_URLS = ("postgresql://u@db.example.cn/x", "postgresql://u@10.0.0.5/x", "mysql://u@localhost/x",
            "postgresql://u@/x?host=db.example.cn", "postgresql://u@127.0.0.1:5432,db.example.cn:5432/x",
            "postgresql://u@localhost/x?hostaddr=10.0.0.5", "postgresql://u@localhost/x?service=synthetic-remote",
            "postgresql://u@[2001:db8::1]:5432/x", "postgresql://u@localhost.db.example.cn/x")


def _url(db, role: str) -> str:
    kw = db.connect_kwargs(role)
    return f"postgresql://{kw['user']}@/{kw['database']}?host={kw['host']}"


def _run(*args: str, env: dict[str, str]) -> subprocess.CompletedProcess:
    base = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "PYTHONDONTWRITEBYTECODE": "1"}
    return subprocess.run([sys.executable, "-m", "insight.jobs", *args], cwd=SVC, capture_output=True, text=True,
                          env={**base, **env}, timeout=120)


@pytest.mark.parametrize("url", BAD_URLS)
def test_non_local_url_refused_before_connect(url, monkeypatch, capsys):
    monkeypatch.setenv("INSIGHT_ENV", "test")
    monkeypatch.setenv("INSIGHT_DATABASE_URL", url)

    async def never(*_a, **_k):
        raise AssertionError("不应连库")
    import insight.store
    monkeypatch.setattr(insight.store, "create_pool", never)
    assert jobs_cli.main(["sweep"]) == 2
    err = capsys.readouterr().err
    assert ("洞察服务定时任务只连本机数据库" in err or "postgresql://" in err) and "时政" not in err and "Traceback" not in err


def test_env_and_arguments_checked(monkeypatch, capsys):
    monkeypatch.setenv("INSIGHT_DATABASE_URL", "postgresql://u@localhost/x")
    for env in ("pilot", "prod", ""):
        monkeypatch.setenv("INSIGHT_ENV", env)
        assert jobs_cli.main(["sweep"]) == 2
    assert "不连接 pilot 或生产环境" in capsys.readouterr().err
    monkeypatch.setenv("INSIGHT_ENV", "test")
    assert jobs_cli.main(["sync", "--student", "not-a-uuid"]) == 2
    assert jobs_cli.main(["sweep", "--limit", "0"]) == 2
    monkeypatch.delenv("INSIGHT_DATABASE_URL")
    assert jobs_cli.main(["sweep"]) == 2
    with pytest.raises(SystemExit):
        jobs_cli.main(["sync"])  # --student 与 --all 必须给一个（argparse）
    with pytest.raises(SystemExit):
        jobs_cli.main(["sync", "--all", "--student", S1])


@pytest.mark.anyio
@pytest.mark.parametrize("addr", ["10.0.0.5", "192.0.2.7", "2001:db8::1"])
async def test_connected_server_must_be_local(addr):
    class FakeConn:
        def __init__(self, a):
            self.a = a

        async def fetchval(self, sql):
            assert "inet_server_addr()" in sql
            return self.a
    for ok in (None, "127.0.0.1", "::1"):
        await jobs_cli.check_server(FakeConn(ok))
    with pytest.raises(jobs_cli.JobError, match="连上的服务端不是本机"):
        await jobs_cli.check_server(FakeConn(addr))


def test_jobs_run_on_local_db(insightdb):
    env = {"INSIGHT_ENV": "test", "INSIGHT_DATABASE_URL": _url(insightdb, APP_ROLE)}
    try:
        p = _run("sync", "--student", S1, env=env)
        assert p.returncode == 0, p.stderr
        out = json.loads(p.stdout)
        assert out["job"] == "sync" and out["students"] == 1 and isinstance(out["writes"], dict) and "skipped_rows" in out
        p = _run("sync", "--all", env=env)
        assert p.returncode == 0, p.stderr
        assert json.loads(p.stdout)["job"] == "sync"
        p = _run("sweep", "--limit", "10", env=env)
        assert p.returncode == 0, p.stderr
        assert set(json.loads(p.stdout)) == {"job", "to_verify"}
        p = _run("metrics", "--student", S3, env=env)
        assert p.returncode == 0, p.stderr
        m = json.loads(p.stdout)
        assert m["job"] == "metrics" and m["students"] == 1 and m["metric_results"] >= 1
        # 输出只有计数：不含任何学生、判断或证据 ID
        for args in (("sync", "--student", S1), ("metrics", "--student", S3), ("sweep",)):
            q = _run(*args, env=env)
            assert q.returncode == 0 and not UUID_ANY.search(q.stdout) and len(q.stdout.splitlines()) == 1
        # 登录角色不是只继承 starbuddy_insight：拒绝执行，不带消息原文
        bad = _run("sweep", env={**env, "INSIGHT_DATABASE_URL": _url(insightdb, LEARNING_ROLE)})
        assert bad.returncode == 2 and "执行失败" in bad.stderr and "Traceback" not in bad.stderr
        # 合成占位的判断策略只允许 test 环境
        dev = _run("sync", "--student", S1, env={**env, "INSIGHT_ENV": "dev"})
        assert dev.returncode == 2 and "判断策略配置不可用" in dev.stderr
    finally:
        reset(insightdb)
