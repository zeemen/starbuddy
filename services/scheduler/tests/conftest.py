"""共享夹具。库上测试用洞察服务测试的一次性 Postgres（services/insight/tests/pginsight.py：bootstrap 角色、全部迁移、合成数据），
再补调度器测试自己的合成夹具（tests/seed.py）。全部是合成数据（规则 8），只连本机 Unix socket（规则 9）。"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

SVC = Path(__file__).resolve().parents[1]
ROOT = SVC.parents[1]
for p in (ROOT / "services" / "insight" / "tests", ROOT / "services" / "insight"):
    if str(p) not in sys.path:
        sys.path.append(str(p))


@pytest.fixture(scope="session")
def schedulerdb():
    """会话级一次性 Postgres。没有 Postgres 时非严格模式跳过；严格模式（CONTRACT_STRICT=1 → SCHEDULER_TEST_STRICT=1）直接失败"""
    from pginsight import EphemeralPG

    from seed import SEED_SQL
    try:
        db = EphemeralPG()
    except FileNotFoundError as e:
        if os.environ.get("SCHEDULER_TEST_STRICT") == "1":
            raise
        pytest.skip(str(e))
    db.db = "scheduler_test"
    db.start()
    db.psql(SEED_SQL)
    yield db
    db.stop()


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
