"""LT-01 staging 运行环境（specs/launch-lt01-deploy-packaging 决定记录第 1、5 条）：学习服务与审核台后端的启动校验。

- dev / test 行为不变（监听地址只能是 127.0.0.1，服务间地址只收 https 或回环）；
- staging 正例：显式配置内网 IPv4 的监听地址与服务间地址；
- staging 反例：合成配置（synthetic）、合成夹具、合成题面、候选契约开关、未签字掌握配置、公网地址、主机名、通配地址；
- pilot / 生产仍一律拒绝。
全部合成值，不连库。
"""
from __future__ import annotations

import asyncio

import pytest

from learning import netpolicy
from learning.config import Settings
from learning.mastery import config as mastery_config
from review_console import __main__ as rc_main
from review_console.config import ReviewSettings

CRED = "s" * 40
ADAPTER_CRED = "a" * 40
KNOW_CRED = "k" * 40
TICKET = "t" * 40


def base(env: str, **extra: str) -> dict[str, str]:
    e = {"LEARNING_ENV": env, "LEARNING_SERVICE_TOKEN": CRED, "LEARNING_DATABASE_URL": "postgresql://syn@127.0.0.1/syn"}
    e.update(extra)
    return e


# ---------------------------------------------------------------- netpolicy


@pytest.mark.parametrize("host,ok", [
    ("10.0.0.5", True), ("172.16.0.1", True), ("172.31.255.254", True), ("192.168.1.10", True),
    ("172.32.0.1", False), ("8.8.8.8", False), ("127.0.0.1", False), ("0.0.0.0", False), ("010.0.0.5", False),
    ("10.0.0", False), ("learning", False), ("::1", False), ("169.254.1.1", False), ("100.64.0.1", False),
])
def test_private_ipv4(host, ok):
    assert netpolicy.private_ipv4(host) is ok


@pytest.mark.parametrize("url,env,ok", [
    ("https://svc.example.invalid", "dev", True),
    ("http://127.0.0.1:8787", "dev", True),
    ("http://172.28.0.10:8787", "dev", False),
    ("http://172.28.0.10:8787", "test", False),
    ("http://172.28.0.10:8787", "staging", True),
    ("http://172.28.0.10", "staging", True),
    ("http://8.8.8.8:8787", "staging", False),
    ("http://learning:8787", "staging", False),
    ("http://user:pw@172.28.0.10:8787", "staging", False),
    ("http://172.28.0.10.example.invalid:8787", "staging", False),
    ("http://172.28.0.10:8787", "pilot", False),
])
def test_service_url_ok(url, env, ok):
    assert netpolicy.service_url_ok(url, env) is ok


def test_listen_host():
    assert netpolicy.listen_host(None, "dev", "X") == "127.0.0.1"
    assert netpolicy.listen_host("127.0.0.1", "test", "X") == "127.0.0.1"
    assert netpolicy.listen_host("172.28.0.10", "staging", "X") == "172.28.0.10"
    for env in ("dev", "test"):
        with pytest.raises(RuntimeError, match="只有 staging"):
            netpolicy.listen_host("172.28.0.10", env, "X")
    for bad in ("0.0.0.0", "::", "8.8.8.8", "learning", "localhost"):
        with pytest.raises(RuntimeError):
            netpolicy.listen_host(bad, "staging", "X")


# ---------------------------------------------------------------- 学习服务


def test_dev_test_unchanged():
    for env in ("dev", "test"):
        s = Settings.from_env(base(env))
        assert s.host == "127.0.0.1"
        with pytest.raises(RuntimeError):
            Settings.from_env(base(env, LEARNING_ADAPTER_AYA_URL="http://172.28.0.11:8787", LEARNING_ADAPTER_AYA_TOKEN=ADAPTER_CRED))
        with pytest.raises(RuntimeError, match="只有 staging"):
            Settings.from_env(base(env, LEARNING_HOST="172.28.0.10"))
    # dev 照旧可以用合成占位配置
    s = Settings.from_env(base("dev", LEARNING_PRACTICE_CONFIG="synthetic", LEARNING_PRACTICE_TICKET_SECRET=TICKET))
    assert s.practice_config == "synthetic"


@pytest.mark.parametrize("env", ["pilot", "production", "prod", "", "STAGING"])
def test_other_envs_still_refused(env):
    with pytest.raises(RuntimeError, match="LEARNING_ENV"):
        Settings.from_env(base(env))


def test_staging_positive():
    s = Settings.from_env(base(
        "staging", LEARNING_HOST="172.28.0.10",
        LEARNING_ADAPTER_AYA_URL="http://127.0.0.1:8781", LEARNING_ADAPTER_AYA_TOKEN=ADAPTER_CRED,
        LEARNING_KNOWLEDGE_URL="http://172.28.0.12:8790", LEARNING_KNOWLEDGE_TOKEN=KNOW_CRED,
        LEARNING_PRACTICE_CONFIG="/run/config/practice.signed.json", LEARNING_PRACTICE_TICKET_SECRET=TICKET))
    assert s.env == "staging" and s.host == "172.28.0.10"
    assert s.knowledge is not None and s.knowledge.base_url == "http://172.28.0.12:8790"
    assert s.adapters["aya"].base_url == "http://127.0.0.1:8781"


@pytest.mark.parametrize("extra,msg", [
    ({"LEARNING_PRACTICE_CONFIG": "synthetic", "LEARNING_PRACTICE_TICKET_SECRET": TICKET}, "LEARNING_PRACTICE_CONFIG"),
    ({"LEARNING_PRACTICE_CONFIG": "/x.json", "LEARNING_PRACTICE_TICKET_SECRET": TICKET, "LEARNING_RETEST_CONFIG": "synthetic"},
     "LEARNING_RETEST_CONFIG"),
    ({"LEARNING_TODAY_CONFIG": "synthetic"}, "LEARNING_TODAY_CONFIG"),
    ({"STARBUDDY_ALLOW_CANDIDATE_CONTRACT": "1"}, "STARBUDDY_ALLOW_CANDIDATE_CONTRACT"),
    ({"STARBUDDY_ALLOW_CANDIDATE_CONTRACT": "0"}, "STARBUDDY_ALLOW_CANDIDATE_CONTRACT"),
    ({"LEARNING_DIAGNOSIS_SOURCE": "fixture"}, "fixture"),
    ({"LEARNING_DIAGNOSIS_PRESENTER": "synthetic", "LEARNING_DIAGNOSIS_SOURCE": "knowledge"}, "合成题面"),
    ({"LEARNING_PRACTICE_PRESENTER": "synthetic"}, "合成题面"),
    ({"STARBUDDY_IMAGE_ENV": "test"}, "STARBUDDY_IMAGE_ENV"),
    ({"LEARNING_HOST": "0.0.0.0"}, "LEARNING_HOST"),
    ({"LEARNING_HOST": "8.8.8.8"}, "LEARNING_HOST"),
    ({"LEARNING_ADAPTER_AYA_URL": "http://8.8.8.8:8787", "LEARNING_ADAPTER_AYA_TOKEN": ADAPTER_CRED}, "LEARNING_ADAPTER_AYA_URL"),
    ({"LEARNING_ADAPTER_AYA_URL": "http://aya:8787", "LEARNING_ADAPTER_AYA_TOKEN": ADAPTER_CRED}, "LEARNING_ADAPTER_AYA_URL"),
])
def test_staging_refuses_synthetic_and_dev_switches(extra, msg):
    with pytest.raises(RuntimeError, match=msg):
        Settings.from_env(base("staging", **extra))


def test_image_env_and_diagnosis_source_default():
    for env in ("dev", "test"):
        with pytest.raises(RuntimeError, match="STARBUDDY_IMAGE_ENV"):
            Settings.from_env(base(env, STARBUDDY_IMAGE_ENV="staging"))
        assert Settings.from_env(base(env)).diagnosis_source == "fixture"
    s = Settings.from_env(base("staging", STARBUDDY_IMAGE_ENV="staging"))
    assert s.diagnosis_source == "knowledge"  # staging 缺省经知识服务，不用合成夹具


def test_internal_v1_switch_is_gone():
    """LT-04：learning-internal v1 签字后开关 LEARNING_INTERNAL_V1 已删除——各环境都不再读它（设了也不影响启动与配置），
    源码里也没有它。"""
    from pathlib import Path
    for env in ("dev", "test"):
        assert Settings.from_env(base(env, LEARNING_INTERNAL_V1="1")) == Settings.from_env(base(env))
    assert Settings.from_env(base("staging", STARBUDDY_IMAGE_ENV="staging", LEARNING_INTERNAL_V1="1")) == \
        Settings.from_env(base("staging", STARBUDDY_IMAGE_ENV="staging"))
    src = Path(__file__).resolve().parents[1] / "learning"
    assert not [p for p in src.rglob("*.py") if "LEARNING_INTERNAL_V1\"" in p.read_text(encoding="utf-8")
                or "internal_v1_enabled" in p.read_text(encoding="utf-8")]


def test_jobs_staging_db_host_check():
    from learning import jobs
    jobs.check_staging_db_url("postgresql://u@172.28.0.20/x")
    for bad in ("postgresql://u@8.8.8.8/x", "postgresql://u@db.example.invalid/x"):
        with pytest.raises(jobs.JobError):
            jobs.check_staging_db_url(bad)
    with pytest.raises(jobs.JobError, match="内网 IPv4"):  # 命令入口：连接前就拒绝（不读掌握配置、不连库）
        asyncio.run(jobs.run(["mastery-sweep"], {"LEARNING_ENV": "staging", "LEARNING_DATABASE_URL": "postgresql://u@8.8.8.8/x"}))
    asyncio.run(jobs.check_staging_db_server(_Conn("172.28.0.20")))
    with pytest.raises(jobs.JobError):
        asyncio.run(jobs.check_staging_db_server(_Conn("8.8.8.8")))


class _FakePool:
    """lifespan 级核对用的假连接池（契约审查 7）：迁移存在性检查一律回真；不连任何库"""

    async def fetchval(self, _sql, *_a):
        return True

    async def close(self):
        return None


def test_staging_lifespan_with_matrix_flags_starts_and_modules_are_501(tmp_path):
    """开关矩阵在 staging 的实际效果：再练、复测、今日编排配了（教研签字前只有未签字配置）也能启动整个服务，
    对应接口回 501；诊断没有题面提供者（合成题面 staging 拒绝），不启动"""
    import json
    import uuid

    import httpx

    from learning.app import create_app
    from learning.practice import config as PC
    from learning.retest import config as RC
    from learning.today import config as TC
    paths = {}
    for name, mod in (("practice", PC), ("retest", RC), ("today", TC)):
        doc = {**mod.SYNTHETIC, "synthetic": False}
        p = tmp_path / f"{name}.unsigned.json"
        p.write_text(json.dumps(doc), encoding="utf-8")
        paths[name] = str(p)
    s = Settings.from_env(base("staging", LEARNING_HOST="172.28.0.10", LEARNING_PRACTICE_CONFIG=paths["practice"],
                               LEARNING_PRACTICE_TICKET_SECRET=TICKET, LEARNING_RETEST_CONFIG=paths["retest"],
                               LEARNING_TODAY_CONFIG=paths["today"],
                               LEARNING_ADAPTER_AYA_URL="http://127.0.0.1:8781", LEARNING_ADAPTER_AYA_TOKEN=ADAPTER_CRED))
    app = create_app(s, pool=_FakePool(), practice_lock_pool=_FakePool())

    async def go():
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://learning.test") as c:
                c.headers["authorization"] = f"Bearer {CRED}"
                body = {"request_id": str(uuid.uuid4()), "student_ref": str(uuid.uuid4()), "subjects": ["aya"]}
                r = await c.post("/internal/v1/today", json=body)
                assert r.status_code == 501 and r.json()["code"] == "TODAY_UNAVAILABLE", r.text
                r = await c.post("/internal/v1/practice/queue", json={"student_ref": str(uuid.uuid4()), "subject": "aya"})
                assert r.status_code == 501 and r.json()["code"] == "PRACTICE_UNAVAILABLE", r.text
                r = await c.post("/internal/v1/retest/queue", json={"student_ref": str(uuid.uuid4()), "subject": "aya"})
                assert r.status_code == 501 and r.json()["code"] == "RETEST_UNAVAILABLE", r.text
    asyncio.run(go())


def test_staging_mastery_config_loads_only_signed(tmp_path):
    import json
    from pathlib import Path
    src = Path(__file__).resolve().parents[1] / "config" / "mastery" / "synthetic-test.json"
    doc = json.loads(src.read_text(encoding="utf-8"))
    p = tmp_path / "m.json"
    p.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(mastery_config.ConfigError):
        mastery_config.load_config_from_env(p, {"LEARNING_ENV": "staging"})
    for env in ("pilot", "production", ""):
        with pytest.raises(mastery_config.ConfigError, match="没有设置或不认识"):
            mastery_config.load_config_from_env(p, {"LEARNING_ENV": env})


# ---------------------------------------------------------------- 审核台后端


def review_env(env: str, **extra: str) -> dict[str, str]:
    e = {"REVIEW_CONSOLE_ENV": env, "REVIEW_CONSOLE_KGE_DATABASE_URL": "postgresql://kge_syn@127.0.0.1/syn",
         "REVIEW_CONSOLE_REVIEW_DATABASE_URL": "postgresql://review_syn@127.0.0.1/syn"}
    e.update(extra)
    return e


def test_review_console_envs():
    assert ReviewSettings.from_env(review_env("dev")).host == "127.0.0.1"
    assert ReviewSettings.from_env(review_env("staging", REVIEW_CONSOLE_HOST="172.28.0.13")).host == "172.28.0.13"
    with pytest.raises(RuntimeError, match="只有 staging"):
        ReviewSettings.from_env(review_env("test", REVIEW_CONSOLE_HOST="172.28.0.13"))
    with pytest.raises(RuntimeError, match="REVIEW_CONSOLE_HOST"):
        ReviewSettings.from_env(review_env("staging", REVIEW_CONSOLE_HOST="0.0.0.0"))
    # 开发开关在 staging 仍拒绝
    with pytest.raises(RuntimeError, match="REVIEW_CONSOLE_FEATURES"):
        ReviewSettings.from_env(review_env("staging", REVIEW_CONSOLE_FEATURES="tag_worklist"))
    for env in ("pilot", "production", ""):
        with pytest.raises(RuntimeError, match="REVIEW_CONSOLE_ENV"):
            ReviewSettings.from_env(review_env(env))


def test_local_only_staging_accepts_private_only():
    rc_main.local_only("postgresql://syn@127.0.0.1/syn")
    with pytest.raises(SystemExit):
        rc_main.local_only("postgresql://syn@172.28.0.20/syn")
    with pytest.raises(SystemExit):
        rc_main.local_only("postgresql://syn@172.28.0.20/syn", "dev")
    rc_main.local_only("postgresql://syn@172.28.0.20:5432/syn", "staging")
    for bad in ("postgresql://syn@8.8.8.8/syn", "postgresql://syn@db.example.invalid/syn",
                "postgresql://syn@172.28.0.20/syn?hostaddr=8.8.8.8", "postgresql://syn@172.28.0.20,8.8.8.8/syn",
                "postgresql://syn@172.28.0.20/syn?host=8.8.8.8"):
        with pytest.raises(SystemExit):
            rc_main.local_only(bad, "staging")


class _Conn:
    def __init__(self, addr):
        self.addr = addr

    async def fetchval(self, _sql):
        return self.addr


def test_check_server_local_staging():
    asyncio.run(rc_main.check_server_local(_Conn(None)))
    asyncio.run(rc_main.check_server_local(_Conn("127.0.0.1")))
    with pytest.raises(SystemExit):
        asyncio.run(rc_main.check_server_local(_Conn("172.28.0.20")))
    asyncio.run(rc_main.check_server_local(_Conn("172.28.0.20"), "staging"))
    with pytest.raises(SystemExit):
        asyncio.run(rc_main.check_server_local(_Conn("8.8.8.8"), "staging"))
