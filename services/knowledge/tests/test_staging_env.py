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
