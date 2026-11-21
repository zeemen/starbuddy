"""处理请求的数据库连接池的超时与兜底（specs/db-call-timeouts）：数值钉住；服务进程里三个处理请求的池都带上；命令行任务不带；
真实库上建出来的连接确实生效（库端 statement_timeout 10s、idle_in_transaction_session_timeout 15s）。
库端超时本身的行为（取消、断开）由 BFF 的 tests/db-timeouts.test.ts 在真实库上验过，这里只验参数落到了连接上。"""
from __future__ import annotations

import ast
from pathlib import Path

import asyncpg

from learning.db_timeouts import COMMAND_TIMEOUT_S, IDLE_IN_TRANSACTION_MS, STATEMENT_TIMEOUT_MS, request_pool_kwargs
from pgharness import LOGIN_ROLE

PKG = Path(__file__).resolve().parents[1] / "learning"


def test_values_match_bff_and_spec():
    assert (STATEMENT_TIMEOUT_MS, IDLE_IN_TRANSACTION_MS, COMMAND_TIMEOUT_S) == (10_000, 15_000, 12.0)
    kw = request_pool_kwargs(application_name="x")
    assert kw["command_timeout"] > STATEMENT_TIMEOUT_MS / 1000, "客户端兜底晚于库端取消"
    assert kw["server_settings"] == {"application_name": "x", "statement_timeout": "10000", "idle_in_transaction_session_timeout": "15000"}


def _create_pool_calls(path: Path) -> list[ast.Call]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [n for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "create_pool"]


def test_every_request_pool_in_app_has_timeouts_and_jobs_do_not():
    calls = _create_pool_calls(PKG / "app.py")
    assert len(calls) == 3, "业务池、再练加锁池、调用记录池"
    for c in calls:
        spread = [k.value for k in c.keywords if k.arg is None]
        assert any(isinstance(v, ast.Call) and getattr(v.func, "id", "") == "request_pool_kwargs" for v in spread), ast.unparse(c)
    for c in _create_pool_calls(PKG / "jobs.py"):  # 命令行任务有长查询：不加
        assert "request_pool_kwargs" not in ast.unparse(c)


async def test_settings_reach_the_connection(pg):
    pool = await asyncpg.create_pool(**pg.dsn(LOGIN_ROLE), min_size=1, max_size=1, **request_pool_kwargs())
    try:
        async with pool.acquire() as c:
            assert await c.fetchval("SHOW statement_timeout") == "10s"
            assert await c.fetchval("SHOW idle_in_transaction_session_timeout") == "15s"
    finally:
        await pool.close()


def test_review_console_pools_have_timeouts():
    """审核台后端（同一代码目录、单独进程）：知识层池与审核池带同一组超时；登录专用池保留原有的 5 秒语句超时"""
    calls = _create_pool_calls(Path(__file__).resolve().parents[1] / "review_console" / "app.py")
    assert len(calls) == 3
    with_timeouts = [c for c in calls if "request_pool_kwargs()" in ast.unparse(c)]
    assert len(with_timeouts) == 2, [ast.unparse(c) for c in calls]
    login = [c for c in calls if c not in with_timeouts]
    assert "'statement_timeout': '5000'" in ast.unparse(login[0])
