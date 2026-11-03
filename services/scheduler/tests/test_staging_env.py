"""LT-01 staging（specs/launch-lt01-deploy-packaging 决定记录第 1、5 条）：调度器的启动核对。
- dev / test 不变（内网地址照旧拒绝）；
- staging：合成或未签字时间表拒绝；各身份运行环境必须也是 staging；连接串另收内网 IPv4 字面量，公网与主机名照旧拒绝；
- pilot / 生产仍拒绝。不连库：子进程用假模块，锁用假的上下文（同 test_runner.py）。"""
from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from scheduler import __main__ as cli
from scheduler.config import DEFAULT_SCHEDULE
from test_runner import ENV, Locks, job, runner

pytestmark = pytest.mark.anyio

def _system_ca() -> str:
    """LT-03 决定记录（续）第 6 条：staging 连库串必须 verify-full + 固定 CA（/run/rds-ca/ca.pem，文件存在）；测试把固定路径换成
    本机系统 CA 包（只读；asyncpg 解析连接串时会加载它）"""
    import os
    import ssl
    for p in (ssl.get_default_verify_paths().cafile, "/etc/ssl/certs/ca-certificates.crt", "/etc/pki/tls/certs/ca-bundle.crt"):
        if p and os.path.isfile(p):
            return p
    return "/nonexistent/ca.pem"


CA = _system_ca()
TLS = f"?sslmode=verify-full&sslrootcert={CA}"
STAGING = {**ENV, "SCHEDULER_ENV": "staging", "SCHEDULER_DATABASE_URL": f"postgresql://scheduler_app@172.28.0.20:5432/x{TLS}",
           "INSIGHT_ENV": "staging", "INSIGHT_DATABASE_URL": f"postgresql://insight_app@172.28.0.20:5432/x{TLS}",
           "LEARNING_ENV": "staging", "LEARNING_DATABASE_URL": f"postgresql://learning_app@172.28.0.20:5432/x{TLS}",
           "REVIEW_CONSOLE_ENV": "staging", "REVIEW_CONSOLE_KGE_DATABASE_URL": f"postgresql://kge_app@172.28.0.20/x{TLS}"}


@pytest.fixture(autouse=True)
def rds_ca(monkeypatch):
    from scheduler.runner import _local_checks
    _local_checks()  # 审核台命令行的核对（learning 包）加进 sys.path
    from learning import netpolicy
    if not CA.startswith("/nonexistent"):
        monkeypatch.setattr(netpolicy, "RDS_CA_PATH", CA)


async def test_staging_plain_db_url_refused():
    """决定记录（续）第 6 条：staging 下没有 TLS（verify-full + 固定 CA）的连接串，启动核对拒绝（不连库）"""
    lk = Locks()
    r, o, _ = runner(env={**STAGING, "SCHEDULER_DATABASE_URL": "postgresql://scheduler_app@172.28.0.20:5432/x"}, locks=lk)
    res = await r.run(job("env"))
    assert res.outcome == "refused" and not lk.calls


def signed_schedule(tmp_path: Path) -> Path:
    text = DEFAULT_SCHEDULE.read_text(encoding="utf-8")
    import yaml
    doc = yaml.safe_load(text)
    doc.update(synthetic=False, signed_by="（合成）签字人", signed_at="2026-10-19T10:00:00+08:00")
    p = tmp_path / "schedule.signed.yaml"
    p.write_text(yaml.safe_dump(doc, allow_unicode=True), encoding="utf-8")
    return p


def _cli(argv, env, capsys):
    out = io.StringIO()
    rc = cli.main(argv, env, out=out)
    return rc, out.getvalue(), capsys.readouterr().err


def test_cli_staging_refuses_synthetic_schedule(capsys):
    rc, out, err = _cli(["--dry-run"], STAGING, capsys)
    assert rc == 2 and out == "" and "签字的时间表" in err


def test_cli_staging_signed_schedule_dry_run(tmp_path, capsys):
    rc, out, err = _cli(["--dry-run", "--schedule", str(signed_schedule(tmp_path))], STAGING, capsys)
    assert rc == 0, err
    plans = [json.loads(ln) for ln in out.splitlines()]
    assert plans and all(p["event"] == "plan" for p in plans)
    assert "172.28.0.20" not in out  # 只列变量名，不列值


def test_cli_staging_refuses_public_db(tmp_path, capsys):
    env = {**STAGING, "INSIGHT_DATABASE_URL": "postgresql://u@8.8.8.8/x"}
    rc, out, err = _cli(["--dry-run", "--schedule", str(signed_schedule(tmp_path))], env, capsys)
    assert rc == 2 and "8.8.8.8" not in err


def test_cli_image_env_mismatch(capsys):
    rc, out, err = _cli(["--dry-run"], {**STAGING, "SCHEDULER_ENV": "test", "STARBUDDY_IMAGE_ENV": "staging"}, capsys)
    assert rc == 2 and "STARBUDDY_IMAGE_ENV" in err


@pytest.mark.parametrize("value", ["pilot", "production", "prod"])
def test_cli_still_refuses_pilot_production(value, capsys):
    rc, out, err = _cli(["--dry-run"], {**STAGING, "SCHEDULER_ENV": value}, capsys)
    assert rc == 2 and "规则 9" in err


async def test_staging_private_db_passes_preflight():
    lk = Locks()
    r, o, _ = runner(env=STAGING, locks=lk)
    res = await r.run(job("env"))
    assert res.outcome == "ok", res.reason
    assert lk.calls and lk.calls[0][1]["SCHEDULER_DATABASE_URL"].startswith("postgresql://scheduler_app@172.28.0.20:5432/x")


async def test_test_env_private_db_still_refused():
    lk = Locks()
    r, o, _ = runner(env={**ENV, "SCHEDULER_DATABASE_URL": "postgresql://scheduler_app@172.28.0.20/x"}, locks=lk)
    res = await r.run(job("env"))
    assert (res.outcome, res.exit_code) == ("refused", 2) and lk.calls == []


@pytest.mark.parametrize("value", ["dev", "test", "pilot"])
async def test_staging_identity_env_must_be_staging(value):
    lk = Locks()
    # 连接串用本机地址：拒绝只能来自“身份环境须与调度器一致”这一条
    r, o, _ = runner(env={**STAGING, "INSIGHT_ENV": value, "INSIGHT_DATABASE_URL": "postgresql://insight_app@localhost/x"}, locks=lk)
    res = await r.run(job("env", identity="insight"))
    assert (res.outcome, res.exit_code) == ("refused", 2) and lk.calls == []
    assert "也必须是 staging" in res.reason


@pytest.mark.parametrize("url", ["postgresql://u@8.8.8.8/x", "postgresql://u@db.example.invalid/x",
                                 "postgresql://u@172.28.0.20/x?hostaddr=8.8.8.8", "postgresql://u@172.28.0.20,8.8.8.8/x"])
async def test_staging_public_db_refused(url):
    lk = Locks()
    r, o, _ = runner(env={**STAGING, "SCHEDULER_DATABASE_URL": url}, locks=lk)
    res = await r.run(job("env"))
    assert (res.outcome, res.exit_code) == ("refused", 2) and lk.calls == []
    assert url not in o.getvalue()
