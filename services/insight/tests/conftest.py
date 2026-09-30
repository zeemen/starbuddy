"""共享夹具：一次性 Postgres（会话级）、异步后端。全部是合成数据（规则 8）。"""
from __future__ import annotations

import os

import pytest


@pytest.fixture(scope="session")
def insightdb():
    """会话级一次性 Postgres：bootstrap 角色、全部迁移、合成数据（tests/pginsight.py）。
    没有 Postgres 时非严格模式跳过；严格模式（make ... CONTRACT_STRICT=1 → INSIGHT_TEST_STRICT=1）直接失败"""
    from pginsight import EphemeralPG
    try:
        db = EphemeralPG()
    except FileNotFoundError as e:
        if os.environ.get("INSIGHT_TEST_STRICT") == "1":
            raise
        pytest.skip(str(e))
    db.start()
    yield db
    db.stop()


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
