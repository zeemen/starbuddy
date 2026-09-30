"""数据库侧指标（spec 设计要点 1）：连接数采样与慢查询前十。只对本机一次性实例操作（规则 9）。

- 慢查询：pg_stat_statements（随 Postgres 发行，PostgreSQL License；只在临时实例的 shared_preload_libraries 里打开，
  装在专用 schema loadtest_ext）；压测开始前清零，结束后按总耗时取前十，只保留规范化后的语句（常量已替换为 $n）；
- 连接：每 0.5 秒采样 pg_stat_activity，按登录角色记最大连接数、最大活跃数、最大锁等待数（应用侧连接池的排队看不到，
  以接口延迟间接反映；两个服务的池上限见 README）。
"""
from __future__ import annotations

import asyncio
import re
import subprocess
from dataclasses import dataclass, field

import asyncpg

APP_ROLES = ("learning_app", "bff_app")


def enable_stat_statements(pg) -> bool:
    """临时实例上打开 pg_stat_statements（需要重启）。不可用时返回 False，报告里标“未采集”。"""
    try:
        pg.psql("ALTER SYSTEM SET shared_preload_libraries = 'pg_stat_statements';")
        pg._as_pg(f"{pg.pgbin}/pg_ctl", "-D", f"{pg.work}/data", "-w", "-l", f"{pg.work}/pg.log",
                  "-o", f"-k {pg.work} -c listen_addresses='' -c fsync=off -c timezone=UTC -c max_connections=300",
                  "restart")
        pg.psql("CREATE SCHEMA IF NOT EXISTS loadtest_ext; CREATE EXTENSION IF NOT EXISTS pg_stat_statements SCHEMA loadtest_ext;")
        return True
    except subprocess.CalledProcessError:
        return False


async def reset(admin: asyncpg.Pool, enabled: bool) -> None:
    if enabled:
        await admin.execute("SELECT loadtest_ext.pg_stat_statements_reset()")


_WS = re.compile(r"\s+")


async def top_queries(admin: asyncpg.Pool, enabled: bool, limit: int = 10) -> list[dict]:
    if not enabled:
        return []
    rows = await admin.fetch(
        """SELECT r.rolname, s.query, s.calls, s.total_exec_time, s.mean_exec_time, s.max_exec_time
             FROM loadtest_ext.pg_stat_statements s JOIN pg_roles r ON r.oid = s.userid
            WHERE s.dbid = (SELECT oid FROM pg_database WHERE datname = current_database())
              AND r.rolname = ANY($1::text[])
            ORDER BY s.total_exec_time DESC LIMIT $2""", list(APP_ROLES), limit)
    return [{"role": r["rolname"], "query": _WS.sub(" ", r["query"]).strip()[:200], "calls": r["calls"],
             "total_ms": round(r["total_exec_time"], 1), "mean_ms": round(r["mean_exec_time"], 2),
             "max_ms": round(r["max_exec_time"], 1)} for r in rows]


@dataclass
class ActivitySampler:
    admin: asyncpg.Pool
    interval_s: float = 0.5
    peaks: dict[str, dict[str, int]] = field(default_factory=dict)
    samples: int = 0
    _task: asyncio.Task | None = None

    async def _loop(self) -> None:
        while True:
            try:
                rows = await self.admin.fetch(
                    """SELECT usename, count(*) AS total, count(*) FILTER (WHERE state = 'active') AS active,
                              count(*) FILTER (WHERE wait_event_type = 'Lock') AS lock_wait
                         FROM pg_stat_activity WHERE datname = current_database() AND usename = ANY($1::text[])
                        GROUP BY usename""", list(APP_ROLES))
                self.samples += 1
                for r in rows:
                    p = self.peaks.setdefault(r["usename"], {"max_connections": 0, "max_active": 0, "max_lock_wait": 0})
                    p["max_connections"] = max(p["max_connections"], r["total"])
                    p["max_active"] = max(p["max_active"], r["active"])
                    p["max_lock_wait"] = max(p["max_lock_wait"], r["lock_wait"])
            except (asyncpg.PostgresError, OSError):
                pass
            await asyncio.sleep(self.interval_s)

    def start(self) -> None:
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> dict:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        return {"samples": self.samples, "by_role": self.peaks}
