"""处理请求的数据库连接池的超时与兜底（specs/db-call-timeouts）：服务进程建池时带语句与事务闲置超时；命令行任务（jobs_cli）不带。"""
from __future__ import annotations

from pathlib import Path

import pytest

from insight.db_timeouts import COMMAND_TIMEOUT_S, IDLE_IN_TRANSACTION_MS, STATEMENT_TIMEOUT_MS
from insight.store import create_pool
from pginsight import APP_ROLE

pytestmark = pytest.mark.anyio
PKG = Path(__file__).resolve().parents[1] / "insight"


def test_values_and_call_sites():
    assert (STATEMENT_TIMEOUT_MS, IDLE_IN_TRANSACTION_MS, COMMAND_TIMEOUT_S) == (10_000, 15_000, 12.0)
    assert "create_pool(settings.database_url, request_timeouts=True)" in (PKG / "service/app.py").read_text(encoding="utf-8")
    assert "request_timeouts" not in (PKG / "jobs_cli.py").read_text(encoding="utf-8"), "命令行任务有长查询：不加"


async def test_service_pool_has_timeouts_cli_pool_does_not(insightdb):
    svc = await create_pool(**insightdb.connect_kwargs(APP_ROLE), request_timeouts=True)
    cli = await create_pool(**insightdb.connect_kwargs(APP_ROLE))
    try:
        async with svc.acquire() as c:
            assert await c.fetchval("SHOW statement_timeout") == "10s"
            assert await c.fetchval("SHOW idle_in_transaction_session_timeout") == "15s"
            assert await c.fetchval("SHOW application_name") == "starbuddy-insight", "原有的 server_settings 照常带上"
        async with cli.acquire() as c:
            assert await c.fetchval("SHOW statement_timeout") == "0"
            assert await c.fetchval("SHOW application_name") == "starbuddy-insight"
    finally:
        await svc.close()
        await cli.close()
