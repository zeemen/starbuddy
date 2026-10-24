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


def test_jobs_staging_db_host_check(rds_tls):
    from learning import jobs
    jobs.check_staging_db_url(f"postgresql://u@172.28.0.20/x?{rds_tls}")
    for bad in (f"postgresql://u@8.8.8.8/x?{rds_tls}", f"postgresql://u@db.example.invalid/x?{rds_tls}",
                "postgresql://u@172.28.0.20/x"):  # 最后一条：内网地址但没有 TLS（决定记录（续）第 6 条）
        with pytest.raises(jobs.JobError):
            jobs.check_staging_db_url(bad)
    with pytest.raises(jobs.JobError, match="内网 IPv4"):  # 命令入口：连接前就拒绝（不读掌握配置、不连库）
        asyncio.run(jobs.run(["mastery-sweep"], {"LEARNING_ENV": "staging", "LEARNING_DATABASE_URL": f"postgresql://u@8.8.8.8/x?{rds_tls}"}))
    asyncio.run(jobs.check_staging_db_server(_Conn("172.28.0.20")))
    with pytest.raises(jobs.JobError):
        asyncio.run(jobs.check_staging_db_server(_Conn("8.8.8.8")))


class _FakePool:
    """lifespan 级核对用的假连接池（契约审查 7）：迁移存在性检查一律回真；不连任何库"""

    async def fetchval(self, _sql, *_a):
        from learning import processing
        # LT-06 处理判据：判据函数“在”，但随机学生的档案不存在，不算被拦（与真实库一致）
        return _sql != processing.BLOCKED_SQL

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
    # LT-06 内部测试运营开关：staging 能开（缺省关），只认 internal_test
    assert ReviewSettings.from_env(review_env("staging", REVIEW_CONSOLE_HOST="172.28.0.13")).internal_test is False
    assert ReviewSettings.from_env(review_env("staging", REVIEW_CONSOLE_HOST="172.28.0.13",
                                              REVIEW_CONSOLE_INTERNAL_TEST="internal_test")).internal_test is True
    with pytest.raises(RuntimeError, match="REVIEW_CONSOLE_INTERNAL_TEST"):
        ReviewSettings.from_env(review_env("staging", REVIEW_CONSOLE_HOST="172.28.0.13", REVIEW_CONSOLE_INTERNAL_TEST="tag_worklist"))
    for env in ("pilot", "production", ""):
        with pytest.raises(RuntimeError, match="REVIEW_CONSOLE_ENV"):
            ReviewSettings.from_env(review_env(env))


def test_local_only_staging_accepts_private_only(rds_tls):
    rc_main.local_only("postgresql://syn@127.0.0.1/syn")
    with pytest.raises(SystemExit):
        rc_main.local_only("postgresql://syn@172.28.0.20/syn")
    with pytest.raises(SystemExit):
        rc_main.local_only(f"postgresql://syn@172.28.0.20/syn?{rds_tls}", "dev")
    rc_main.local_only(f"postgresql://syn@172.28.0.20:5432/syn?{rds_tls}", "staging")
    for bad in (f"postgresql://syn@8.8.8.8/syn?{rds_tls}", f"postgresql://syn@db.example.invalid/syn?{rds_tls}",
                f"postgresql://syn@172.28.0.20/syn?{rds_tls}&hostaddr=8.8.8.8", f"postgresql://syn@172.28.0.20,8.8.8.8/syn?{rds_tls}",
                f"postgresql://syn@172.28.0.20/syn?{rds_tls}&host=8.8.8.8",
                "postgresql://syn@172.28.0.20:5432/syn",                      # 决定记录（续）第 6 条：staging 一律要 TLS
                f"postgresql://syn@/syn?host=/tmp/x&{rds_tls}"):              # staging 不收 Unix socket（做不了 TLS）
        with pytest.raises(SystemExit):
            rc_main.local_only(bad, "staging")


def _system_ca() -> str:
    """asyncpg 解析 verify-full 连接串时会加载 CA 文件：用本机系统 CA 包（只读，不含任何密钥）代替 /run/rds-ca/ca.pem；没有就跳过"""
    import os
    import ssl
    for p in (ssl.get_default_verify_paths().cafile, "/etc/ssl/certs/ca-certificates.crt", "/etc/pki/tls/certs/ca-bundle.crt"):
        if p and os.path.isfile(p):
            return p
    pytest.skip("本机没有系统 CA 包")


RDS_HOST = "pgm-syn0000.pg.rds.example.invalid"   # 合成的托管库主机名（保留域名；解析由测试替换，不连接）
MIXED_HOST = "pgm-mixed.pg.rds.example.invalid"   # 合成：解析结果混有公网地址
PUBLIC_HOST = "pgm-public.pg.rds.example.invalid"  # 合成：解析到公网地址
# 裸名字、大写、开头连字符也让它们“解析到内网”：拒绝只能来自主机名格式核对，不是解析失败
FAKE_DNS = {RDS_HOST: ["10.20.96.7"], MIXED_HOST: ["10.20.96.7", "47.0.0.1"], PUBLIC_HOST: ["47.0.0.1"],
            "db": ["10.20.96.7"], "PGM.EXAMPLE.INVALID": ["10.20.96.7"], "-bad.example.invalid": ["10.20.96.7"]}


@pytest.fixture
def rds_tls(monkeypatch):
    """固定 CA 路径换成本机系统 CA 包（文件存在），主机名解析换成合成表；返回连接串要带的 TLS 查询参数"""
    ca = _system_ca()
    monkeypatch.setattr(netpolicy, "RDS_CA_PATH", ca)
    monkeypatch.setattr(rc_main, "resolve", lambda host: list(FAKE_DNS.get(host, [])))
    return f"sslmode=verify-full&sslrootcert={ca}"


def test_local_only_staging_hostname_needs_verify_full(rds_tls):
    """LT-03（补 LT-01 待人确认 6；决定记录（续）第 6 条）：托管 Postgres 只给主机名——staging 下连接串必须 verify-full + 固定 CA（文件存在），
    主机名在连接前解析，全部地址都必须是内网；dev / test 一律不收；hostaddr、service 照旧拒绝；连上之后仍核对服务端地址（下一条）"""
    ca = netpolicy.RDS_CA_PATH
    ok = f"postgresql://syn@{RDS_HOST}:5432/syn?{rds_tls}"
    assert rc_main.local_only(ok, "staging") == ok
    rc_main.local_only(f"postgresql://syn@/syn?host={RDS_HOST}&{rds_tls}", "staging")
    for env in (None, "dev", "test"):
        with pytest.raises(SystemExit):
            rc_main.local_only(ok, env)
    for bad in (
        f"postgresql://syn@{RDS_HOST}/syn",                                               # 没有 TLS
        f"postgresql://syn@{RDS_HOST}/syn?sslmode=require&sslrootcert={ca}",              # 不校验主机名
        f"postgresql://syn@{RDS_HOST}/syn?sslmode=verify-ca&sslrootcert={ca}",
        f"postgresql://syn@{RDS_HOST}/syn?sslmode=verify-full",                           # 没给 CA
        f"postgresql://syn@{RDS_HOST}/syn?sslmode=verify-full&sslrootcert=/etc/other-ca.pem",  # 不是固定 CA 路径
        f"postgresql://syn@{RDS_HOST}/syn?sslmode=verify-full&sslmode=disable&sslrootcert={ca}",  # 两个 sslmode
        f"postgresql://syn@{RDS_HOST}/syn?{rds_tls}&hostaddr=8.8.8.8",
        f"postgresql://syn@{RDS_HOST}/syn?{rds_tls}&service=x",
        f"postgresql://syn@8.8.8.8/syn?{rds_tls}",                                         # 公网 IP 字面量：TLS 也不收
        f"postgresql://syn@db/syn?{rds_tls}",                                              # 裸名字
        f"postgresql://syn@{RDS_HOST},8.8.8.8/syn?{rds_tls}",                              # 多主机里混了公网
        f"postgresql://syn@{RDS_HOST}/syn?{rds_tls}&host=8.8.8.8",
        f"postgresql://syn@{PUBLIC_HOST}/syn?{rds_tls}",                                   # 主机名解析到公网：连接前就拒
        f"postgresql://syn@{MIXED_HOST}/syn?{rds_tls}",                                    # 解析结果混有公网
        f"postgresql://syn@pgm-unknown.pg.rds.example.invalid/syn?{rds_tls}",              # 解析不了
        f"postgresql://syn@-bad.example.invalid/syn?{rds_tls}",
        f"postgresql://syn@PGM.EXAMPLE.INVALID/syn?{rds_tls}",                             # 大写（判不出从严）
    ):
        with pytest.raises(SystemExit):
            rc_main.local_only(bad, "staging")


def test_local_only_staging_ca_file_must_exist(rds_tls, monkeypatch, tmp_path):
    missing = str(tmp_path / "ca.pem")
    monkeypatch.setattr(netpolicy, "RDS_CA_PATH", missing)
    with pytest.raises(SystemExit):
        rc_main.local_only(f"postgresql://syn@172.28.0.20/syn?sslmode=verify-full&sslrootcert={missing}", "staging")


@pytest.mark.parametrize("q,ok", [
    ({"sslmode": ["verify-full"], "sslrootcert": ["CA"]}, True),
    ({"sslmode": ["verify-full"], "sslrootcert": ["CA", "/x"]}, False),
    ({"sslmode": ["verify-full"]}, False),
    ({"sslmode": ["require"], "sslrootcert": ["CA"]}, False),
    ({"sslmode": ["VERIFY-FULL"], "sslrootcert": ["CA"]}, False),
    ({"sslrootcert": ["CA"]}, False),
    ({"sslmode": ["verify-full"], "sslrootcert": ["/etc/ssl/other.pem"]}, False),
])
def test_tls_verified(q, ok, rds_tls):
    q = {k: [netpolicy.RDS_CA_PATH if x == "CA" else x for x in v] for k, v in q.items()}
    assert rc_main.tls_verified(q) is ok


def test_tls_verified_other_existing_ca_or_missing_fixed_ca(rds_tls, monkeypatch, tmp_path):
    """固定 CA 路径之外的文件即使存在也不收；固定路径上的文件不存在也不收（直接核对 tls_verified，不经 asyncpg 解析）"""
    other = tmp_path / "other.pem"
    other.write_text("synthetic\n", encoding="utf-8")
    assert rc_main.tls_verified({"sslmode": ["verify-full"], "sslrootcert": [str(other)]}) is False
    missing = str(tmp_path / "missing.pem")
    monkeypatch.setattr(netpolicy, "RDS_CA_PATH", missing)
    assert rc_main.tls_verified({"sslmode": ["verify-full"], "sslrootcert": [missing]}) is False


def test_rds_ca_path_is_fixed():
    assert netpolicy.RDS_CA_PATH == "/run/rds-ca/ca.pem"


def test_check_db_tls_for_services(rds_tls, monkeypatch, tmp_path):
    """决定记录（续）第 6 条：学习服务（与审核台后端、洞察 / 知识服务同口径）staging 下拒绝不带 verify-full + 固定 CA 的连库串"""
    good = f"postgresql://u@{RDS_HOST}:5432/x?{rds_tls}"
    netpolicy.check_db_tls(good, "staging", "X")
    for env in ("dev", "test"):
        netpolicy.check_db_tls("postgresql://u@127.0.0.1/x", env, "X")
    for bad in ("postgresql://u@172.28.0.20/x", f"postgresql://u@{RDS_HOST}/x?sslmode=require&sslrootcert={netpolicy.RDS_CA_PATH}",
                f"postgresql://u@{RDS_HOST}/x?sslmode=verify-full&sslrootcert=/etc/other.pem", "not-a-url"):
        with pytest.raises(RuntimeError, match="verify-full"):
            netpolicy.check_db_tls(bad, "staging", "X")
    monkeypatch.setattr(netpolicy, "RDS_CA_PATH", str(tmp_path / "missing.pem"))
    with pytest.raises(RuntimeError, match="verify-full"):
        netpolicy.check_db_tls(f"postgresql://u@{RDS_HOST}/x?sslmode=verify-full&sslrootcert={tmp_path / 'missing.pem'}", "staging", "X")


def test_learning_create_app_refuses_plain_db_url_in_staging(rds_tls):
    from learning.app import create_app
    s = Settings.from_env(base("staging", LEARNING_HOST="172.28.0.10"))  # 连库串没有 TLS
    with pytest.raises(RuntimeError, match="LEARNING_DATABASE_URL"):
        create_app(s)
    s2 = Settings.from_env({**base("staging", LEARNING_HOST="172.28.0.10"), "LEARNING_DATABASE_URL": f"postgresql://u@{RDS_HOST}/x?{rds_tls}"})
    create_app(s2)  # 合规：只构造，不连库
    create_app(s, pool=_FakePool())  # 注入连接池（测试）不经这道核对


def test_review_console_lifespan_refuses_plain_db_url_in_staging(rds_tls):
    from review_console import app as rc_app
    s = ReviewSettings.from_env(review_env("staging", REVIEW_CONSOLE_HOST="172.28.0.13"))
    app = rc_app.create_app(s)

    async def go():
        async with app.router.lifespan_context(app):
            pass
    with pytest.raises(RuntimeError, match="REVIEW_CONSOLE_KGE_DATABASE_URL"):
        asyncio.run(go())


def test_jobs_staging_db_hostname_with_tls(rds_tls):
    """调度器的 learning.jobs 子命令（同一个 local_only）：staging 下托管库主机名 + verify-full 可用；连上之后服务端仍须是内网"""
    from learning import jobs
    jobs.check_staging_db_url(f"postgresql://u@{RDS_HOST}:5432/x?{rds_tls}")
    with pytest.raises(jobs.JobError):
        jobs.check_staging_db_url(f"postgresql://u@{RDS_HOST}:5432/x")
    asyncio.run(jobs.check_staging_db_server(_Conn("10.20.0.15")))  # 托管库在 VPC 里的私网地址
    with pytest.raises(jobs.JobError):
        asyncio.run(jobs.check_staging_db_server(_Conn("47.0.0.1")))  # 主机名解析到了公网地址


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
