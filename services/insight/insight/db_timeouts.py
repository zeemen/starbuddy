"""处理请求的数据库连接池的超时与兜底（specs/db-call-timeouts，与 BFF services/bff/src/http/db-pool.ts 同一组数值）。

数据库卡住（锁等待、慢查询）时请求不能一直挂着：
  - 单条语句 10 秒（库端 statement_timeout，取消时错误码 57014，走已有的 500 路径）；客户端另设 12 秒兜底（command_timeout）；
  - 事务开着却不发语句超过 15 秒，库端断开连接（idle_in_transaction_session_timeout），锁与连接都放出来。
只用于服务进程里处理请求的池（insight/service/app.py 建的池）；命令行任务（insight/jobs_cli.py，含留存清理、周报批量生成）不用它（有长查询）。
"""
from __future__ import annotations

from typing import Any

STATEMENT_TIMEOUT_MS = 10_000
IDLE_IN_TRANSACTION_MS = 15_000
COMMAND_TIMEOUT_S = 12.0


def request_pool_kwargs(**server_settings: str) -> dict[str, Any]:
    """asyncpg.create_pool 的超时参数；server_settings 里另加的键（如 application_name）一并带上"""
    return {"command_timeout": COMMAND_TIMEOUT_S,
            "server_settings": {**server_settings, "statement_timeout": str(STATEMENT_TIMEOUT_MS),
                                "idle_in_transaction_session_timeout": str(IDLE_IN_TRANSACTION_MS)}}
