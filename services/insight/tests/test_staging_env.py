"""LT-01 staging 运行环境（specs/launch-lt01-deploy-packaging 决定记录第 1、5 条）：洞察服务与两个调度器子命令的启动核对。
dev / test 不变；staging 正反例；pilot / 生产仍拒绝。全部合成值，不连库。"""
from __future__ import annotations

import asyncio

import pytest

from insight import jobs_cli, scheduled
from insight.policy import CONFIG_DIR, PolicyError, load_policy
from insight.service.config import Settings, private_ipv4
from support import CREDS

ENV = {"INSIGHT_DATABASE_URL": "postgresql://unused", "INSIGHT_TOKEN_BFF": CREDS["bff"]}
LEARN = "l" * 40


def mk(env: str, **extra: str) -> dict[str, str]:
    return {**ENV, "INSIGHT_ENV": env, **extra}


def test_dev_test_unchanged():
    for env in ("dev", "test"):
        s = Settings.from_env(mk(env, INSIGHT_HOST="10.1.2.3"))  # dev / test 原本就收具体地址
        assert s.host == "10.1.2.3"
        with pytest.raises(RuntimeError, match="INSIGHT_LEARNING_URL"):
            Settings.from_env(mk(env, INSIGHT_LEARNING_URL="http://172.28.0.10:8788", INSIGHT_LEARNING_TOKEN=LEARN))


@pytest.mark.parametrize("env", ["pilot", "prod", "production", "", "Staging"])
def test_other_envs_refused(env):
    with pytest.raises(RuntimeError, match="INSIGHT_ENV"):
        Settings.from_env(mk(env))


def test_staging_positive():
    s = Settings.from_env(mk("staging", INSIGHT_HOST="172.28.0.14", INSIGHT_LEARNING_URL="http://172.28.0.10:8788",
                             INSIGHT_LEARNING_TOKEN=LEARN))
    assert s.env == "staging" and s.host == "172.28.0.14" and s.learning == ("http://172.28.0.10:8788", LEARN)
    assert Settings.from_env(mk("staging")).host == "127.0.0.1"


@pytest.mark.parametrize("extra", [
    {"INSIGHT_HOST": "0.0.0.0"}, {"INSIGHT_HOST": "8.8.8.8"}, {"INSIGHT_HOST": "localhost"}, {"INSIGHT_HOST": "insight"},
    {"INSIGHT_LEARNING_URL": "http://8.8.8.8:8788", "INSIGHT_LEARNING_TOKEN": LEARN},
    {"INSIGHT_LEARNING_URL": "http://learning:8788", "INSIGHT_LEARNING_TOKEN": LEARN},
    {"INSIGHT_LEARNING_URL": "http://172.28.0.10:8788/x", "INSIGHT_LEARNING_TOKEN": LEARN},
])
def test_staging_negative(extra):
    with pytest.raises(RuntimeError):
        Settings.from_env(mk("staging", **extra))


def test_staging_refuses_contract_dir_and_image_mismatch():
    with pytest.raises(RuntimeError, match="INSIGHT_CONTRACT_DIR"):
        Settings.from_env(mk("staging", INSIGHT_CONTRACT_DIR="/tmp/x"))
    Settings.from_env(mk("test", INSIGHT_CONTRACT_DIR="/tmp/x"))  # test 照旧可换
    with pytest.raises(RuntimeError, match="STARBUDDY_IMAGE_ENV"):
        Settings.from_env(mk("test", STARBUDDY_IMAGE_ENV="staging"))
    with pytest.raises(RuntimeError, match="STARBUDDY_IMAGE_ENV"):
        Settings.from_env(mk("staging", STARBUDDY_IMAGE_ENV="test"))
    assert Settings.from_env(mk("staging", STARBUDDY_IMAGE_ENV="staging")).env == "staging"


def test_staging_refuses_synthetic_policy():
    """合成占位策略（服务缺省）在 staging 照旧拒绝加载：服务启动（create_app）即失败"""
    with pytest.raises(PolicyError):
        load_policy(CONFIG_DIR / "claims.synthetic.json", env="staging")


def test_monthly_switch():
    """决定记录第 9 条：staging 不设 INSIGHT_MONTHLY_PLAN = 月度计划关（不加载配置与模板库）；设了 = 开；dev / test 恒开"""
    assert Settings.from_env(mk("staging")).monthly_enabled is False
    assert Settings.from_env(mk("staging", INSIGHT_MONTHLY_PLAN="/run/config/monthly.signed.json")).monthly_enabled is True
    for env in ("dev", "test"):
        assert Settings.from_env(mk(env)).monthly_enabled is True


def test_monthly_off_skips_loading(tmp_path):
    import dataclasses

    from insight.service.app import create_app
    missing = tmp_path / "missing.json"
    s = dataclasses.replace(Settings.from_env(mk("test")), monthly_path=missing, monthly_templates_path=missing)
    create_app(dataclasses.replace(s, monthly_enabled=False))  # 关：不读配置，构造不失败（生成接口由 state["monthly"] 为空回 501）
    with pytest.raises((PolicyError, OSError)):
        create_app(s)  # 开：启动即读并校验


def test_private_ipv4():
    assert private_ipv4("192.168.0.1") and not private_ipv4("192.168.000.1") and not private_ipv4("11.0.0.1")


def test_scheduled_cli_staging_db_hosts():
    ok = {"SCHEDULER_ENV": "staging", "SCHEDULER_DATABASE_URL": "postgresql://u@172.28.0.20:5432/x"}
    assert scheduled.check_env(ok) == ok["SCHEDULER_DATABASE_URL"]
    with pytest.raises(scheduled.CliError):
        scheduled.check_env({**ok, "SCHEDULER_ENV": "test"})  # test 下内网地址照旧拒绝
    for bad in ("postgresql://u@8.8.8.8/x", "postgresql://u@db.example.invalid/x", "postgresql://u@172.28.0.20/x?hostaddr=8.8.8.8"):
        with pytest.raises(scheduled.CliError):
            scheduled.check_env({**ok, "SCHEDULER_DATABASE_URL": bad})
    for env in ("pilot", "production"):
        with pytest.raises(scheduled.CliError, match="SCHEDULER_ENV"):
            scheduled.check_env({**ok, "SCHEDULER_ENV": env})


class _Conn:
    def __init__(self, addr):
        self.addr = addr

    async def fetchval(self, _sql):
        return self.addr


def test_server_checks_staging():
    asyncio.run(scheduled.check_server(_Conn("172.28.0.20"), "staging"))
    asyncio.run(jobs_cli.check_server(_Conn("172.28.0.20"), "staging"))
    for f in (scheduled.check_server, jobs_cli.check_server):
        with pytest.raises((scheduled.CliError, jobs_cli.JobError)):
            asyncio.run(f(_Conn("172.28.0.20")))
        with pytest.raises((scheduled.CliError, jobs_cli.JobError)):
            asyncio.run(f(_Conn("8.8.8.8"), "staging"))


def test_jobs_cli_staging_env(monkeypatch):
    monkeypatch.setenv("INSIGHT_ENV", "staging")
    monkeypatch.setenv("INSIGHT_DATABASE_URL", "postgresql://u@172.28.0.20/x")
    assert jobs_cli._env() == ("staging", "postgresql://u@172.28.0.20/x")
    monkeypatch.setenv("INSIGHT_ENV", "test")
    with pytest.raises(jobs_cli.JobError):
        jobs_cli._env()
    monkeypatch.setenv("INSIGHT_ENV", "pilot")
    with pytest.raises(jobs_cli.JobError, match="INSIGHT_ENV"):
        jobs_cli._env()
