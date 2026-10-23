"""LT-01 staging 运行环境（specs/launch-lt01-deploy-packaging 决定记录第 1 条）：知识服务启动校验。
dev / test 不变；staging 正反例；pilot / 生产仍拒绝。全部合成值，不连库。"""
from __future__ import annotations

import pytest

from knowledge.service.config import Settings, private_ipv4

ENV = {"KNOWLEDGE_DATABASE_URL": "postgresql://synthetic@/x", "KNOWLEDGE_TOKEN_LEARNING": "l" * 32}


def mk(env: str, **extra: str) -> dict[str, str]:
    return {**ENV, "KNOWLEDGE_ENV": env, **extra}


def test_dev_test_unchanged():
    for env in ("dev", "test"):
        assert Settings.from_env(mk(env)).host == "127.0.0.1"
        assert Settings.from_env(mk(env, KNOWLEDGE_HOST="10.1.2.3")).host == "10.1.2.3"  # 原本就收具体地址


@pytest.mark.parametrize("env", ["pilot", "prod", "production", "", "STAGING"])
def test_other_envs_refused(env):
    with pytest.raises(RuntimeError, match="KNOWLEDGE_ENV"):
        Settings.from_env(mk(env))


def test_staging_positive():
    s = Settings.from_env(mk("staging", KNOWLEDGE_HOST="172.28.0.12"))
    assert s.env == "staging" and s.host == "172.28.0.12"
    assert Settings.from_env(mk("staging")).host == "127.0.0.1"
    assert Settings.from_env(mk("staging", KNOWLEDGE_READ_CACHE="0")).read_cache is False


@pytest.mark.parametrize("host", ["0.0.0.0", "8.8.8.8", "localhost", "knowledge", "::1", "100.64.0.1"])
def test_staging_negative_hosts(host):
    with pytest.raises(RuntimeError):
        Settings.from_env(mk("staging", KNOWLEDGE_HOST=host))


def test_staging_refuses_contract_dir_and_image_mismatch():
    with pytest.raises(RuntimeError, match="KNOWLEDGE_CONTRACT_DIR"):
        Settings.from_env(mk("staging", KNOWLEDGE_CONTRACT_DIR="/tmp/x"))
    Settings.from_env(mk("test", KNOWLEDGE_CONTRACT_DIR="/tmp/x"))
    with pytest.raises(RuntimeError, match="STARBUDDY_IMAGE_ENV"):
        Settings.from_env(mk("dev", STARBUDDY_IMAGE_ENV="staging"))
    assert Settings.from_env(mk("staging", STARBUDDY_IMAGE_ENV="staging")).env == "staging"


def test_private_ipv4():
    assert private_ipv4("10.0.0.1") and not private_ipv4("10.0.0.01") and not private_ipv4("172.15.0.1")


# ---------------------------------------------------------------- LT-03：连库 TLS（决定记录（续）第 6 条）与额外允许 Host（契约审查阻断 2）


def _system_ca() -> str:
    import os
    import ssl
    for p in (ssl.get_default_verify_paths().cafile, "/etc/ssl/certs/ca-certificates.crt", "/etc/pki/tls/certs/ca-bundle.crt"):
        if p and os.path.isfile(p):
            return p
    pytest.skip("本机没有系统 CA 包")


def test_service_db_tls_in_staging(monkeypatch, tmp_path):
    from knowledge.service import config as cfg
    from knowledge.service.app import create_app
    assert cfg.RDS_CA_PATH == "/run/rds-ca/ca.pem"
    ca = _system_ca()
    monkeypatch.setattr(cfg, "RDS_CA_PATH", ca)
    cfg.check_db_tls(f"postgresql://u@pgm-syn.example.invalid:5432/x?sslmode=verify-full&sslrootcert={ca}", "staging")
    cfg.check_db_tls("postgresql://synthetic@/x", "test")
    for bad in ("postgresql://u@172.28.0.20/x", f"postgresql://u@172.28.0.20/x?sslmode=require&sslrootcert={ca}",
                "postgresql://u@172.28.0.20/x?sslmode=verify-full&sslrootcert=/etc/other.pem"):
        with pytest.raises(RuntimeError, match="verify-full"):
            cfg.check_db_tls(bad, "staging")
    with pytest.raises(RuntimeError, match="KNOWLEDGE_DATABASE_URL"):  # 服务启动：建池之前拒绝
        create_app(Settings.from_env(mk("staging")))
    monkeypatch.setattr(cfg, "RDS_CA_PATH", str(tmp_path / "missing.pem"))
    with pytest.raises(RuntimeError, match="verify-full"):
        cfg.check_db_tls(f"postgresql://u@172.28.0.20/x?sslmode=verify-full&sslrootcert={tmp_path / 'missing.pem'}", "staging")


def test_extra_host_service_ip():
    from knowledge.service.mcp_server import allowed_hosts, host_allowed, transport_security
    s = Settings.from_env(mk("staging", KNOWLEDGE_HOST="172.28.0.12", KNOWLEDGE_EXTRA_HOST="10.96.100.12"))
    assert s.extra_host == "10.96.100.12"
    hosts = allowed_hosts(s.host, s.extra_host)
    assert host_allowed("10.96.100.12:8790", hosts) and host_allowed("172.28.0.12:8790", hosts)
    assert not host_allowed("10.96.100.13:8790", hosts)
    assert "10.96.100.12:*" in transport_security(s.host, s.extra_host).allowed_hosts, "MCP 的 DNS 重绑定防护同一份列表"
    for bad in ("8.8.8.8", "knowledge", "10.96.100.12:8790", "010.96.100.12"):
        with pytest.raises(RuntimeError, match="KNOWLEDGE_EXTRA_HOST"):
            Settings.from_env(mk("staging", KNOWLEDGE_EXTRA_HOST=bad))
    with pytest.raises(RuntimeError, match="只有 staging"):
        Settings.from_env(mk("test", KNOWLEDGE_EXTRA_HOST="10.96.100.12"))
