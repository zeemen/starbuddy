"""处理请求的数据库连接池的超时与兜底（specs/db-call-timeouts）：服务进程建池时带语句与事务闲置超时；编译器整图读取（DbAtomSource）不带。"""
from __future__ import annotations

from pathlib import Path

import pytest

from knowledge.db_timeouts import COMMAND_TIMEOUT_S, IDLE_IN_TRANSACTION_MS, STATEMENT_TIMEOUT_MS
from knowledge.service.reader import create_reader_pool
from pgkge import APP_ROLE

pytestmark = pytest.mark.anyio
PKG = Path(__file__).resolve().parents[1] / "knowledge"


def test_values_and_call_sites():
    assert (STATEMENT_TIMEOUT_MS, IDLE_IN_TRANSACTION_MS, COMMAND_TIMEOUT_S) == (10_000, 15_000, 12.0)
    assert "create_reader_pool(settings.database_url, request_timeouts=True)" in (PKG / "service/app.py").read_text(encoding="utf-8")
    assert "pool = await create_reader_pool(self.dsn)\n" in (PKG / "service/reader.py").read_text(encoding="utf-8"), "整图读取不加"


async def test_service_pool_has_timeouts_compiler_pool_does_not(kgedb):
    svc = await create_reader_pool(**kgedb.connect_kwargs(APP_ROLE), request_timeouts=True)
    plain = await create_reader_pool(**kgedb.connect_kwargs(APP_ROLE))
    try:
        async with svc.acquire() as c:
            assert await c.fetchval("SHOW statement_timeout") == "10s"
            assert await c.fetchval("SHOW idle_in_transaction_session_timeout") == "15s"
            assert await c.fetchval("SHOW default_transaction_read_only") == "on", "原有的只读设置照常带上"
        async with plain.acquire() as c:
            assert await c.fetchval("SHOW statement_timeout") == "0"
            assert await c.fetchval("SHOW default_transaction_read_only") == "on"
    finally:
        await svc.close()
        await plain.close()
