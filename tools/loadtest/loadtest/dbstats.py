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
from collections.abc import Callable
from dataclasses import dataclass, field

import asyncpg

APP_ROLES = ("learning_app", "bff_app")   # 登录名：连接采样（pg_stat_activity.usename）按它
# 语句统计的角色（PLT-22）：pg_stat_statements 记的是执行时的当前角色，学习服务在 learning_tx 里 SET LOCAL ROLE starbuddy_learning，
# 那些语句记在 starbuddy_learning 名下；PLT-21 及以前只按登录名过滤，看不到学习服务的大部分语句
STATEMENT_ROLES = APP_ROLES + ("starbuddy_learning",)


def enable_stat_statements(pg) -> bool:
    """临时实例上打开 pg_stat_statements（需要重启）。不可用时返回 False，报告里标“未采集”。
    PLT-22：同一次重启另打开 track_commit_timestamp（只在临时实例），压测后按事务提交时间戳算掌握重算耗时（writepath.mastery_rows）。"""
    try:
        pg.psql("ALTER SYSTEM SET shared_preload_libraries = 'pg_stat_statements';")
        pg._as_pg(f"{pg.pgbin}/pg_ctl", "-D", f"{pg.work}/data", "-w", "-l", f"{pg.work}/pg.log",
                  "-o", f"-k {pg.work} -c listen_addresses='' -c fsync=off -c timezone=UTC -c max_connections=300"
                        " -c track_commit_timestamp=on",
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
            ORDER BY s.total_exec_time DESC LIMIT $2""", list(STATEMENT_ROLES), limit)
    return [{"role": r["rolname"], "query": _WS.sub(" ", r["query"]).strip()[:200], "calls": r["calls"],
             "total_ms": round(r["total_exec_time"], 1), "mean_ms": round(r["mean_exec_time"], 2),
             "max_ms": round(r["max_exec_time"], 1)} for r in rows]


async def snapshot_statements(admin: asyncpg.Pool, enabled: bool) -> dict[tuple[str, int], dict]:
    """PLT-20：pg_stat_statements 的累计值快照（按角色 + queryid），阶段边界各取一次，相减得到各阶段的慢查询"""
    if not enabled:
        return {}
    rows = await admin.fetch(
        """SELECT r.rolname, s.queryid, s.query, s.calls, s.total_exec_time
             FROM loadtest_ext.pg_stat_statements s JOIN pg_roles r ON r.oid = s.userid
            WHERE s.dbid = (SELECT oid FROM pg_database WHERE datname = current_database())
              AND r.rolname = ANY($1::text[])""", list(STATEMENT_ROLES))
    return {(r["rolname"], int(r["queryid"] or 0)): {"query": _WS.sub(" ", r["query"]).strip()[:200], "calls": int(r["calls"]),
                                                     "total_ms": float(r["total_exec_time"])} for r in rows}


def diff_statements(before: dict, after: dict, limit: int = 3) -> list[dict]:
    """两次快照之差 → 本阶段按总耗时的前 limit 条（只含规范化语句、调用数与耗时）"""
    out = []
    for k, a in after.items():
        b = before.get(k, {"calls": 0, "total_ms": 0.0})
        calls, total = a["calls"] - b["calls"], a["total_ms"] - b["total_ms"]
        if calls > 0:
            out.append({"role": k[0], "query": a["query"], "calls": calls, "total_ms": round(total, 1), "mean_ms": round(total / calls, 2)})
    out.sort(key=lambda d: (-d["total_ms"], d["role"], d["query"]))
    return out[:limit]


def merge_peak(peaks: dict[str, dict[str, int]], role: str, total: int, active: int, lock_wait: int, waiting: int = 0) -> None:
    p = peaks.setdefault(role, {"max_connections": 0, "max_active": 0, "max_lock_wait": 0, "max_waiting": 0})
    p["max_connections"] = max(p["max_connections"], total)
    p["max_active"] = max(p["max_active"], active)
    p["max_lock_wait"] = max(p["max_lock_wait"], lock_wait)
    p["max_waiting"] = max(p.get("max_waiting", 0), waiting)


@dataclass
class ActivitySampler:
    admin: asyncpg.Pool
    interval_s: float = 0.5
    peaks: dict[str, dict[str, int]] = field(default_factory=dict)
    samples: int = 0
    _task: asyncio.Task | None = None
    # PLT-20：给了就按当前墙钟所在阶段另记一份峰值（阶段 → 角色 → 峰值）；没给时与以前相同
    stage_of: Callable[[], int | None] | None = None
    by_stage: dict[int, dict[str, dict[str, int]]] = field(default_factory=dict)

    async def _loop(self) -> None:
        while True:
            try:
                rows = await self.admin.fetch(
                    """SELECT usename, count(*) AS total, count(*) FILTER (WHERE state = 'active') AS active,
                              count(*) FILTER (WHERE wait_event_type = 'Lock') AS lock_wait,
                              count(*) FILTER (WHERE state = 'active' AND wait_event_type IN ('Lock', 'LWLock', 'IO')) AS waiting
                         FROM pg_stat_activity WHERE datname = current_database() AND usename = ANY($1::text[])
                        GROUP BY usename""", list(APP_ROLES))
                self.samples += 1
                stage = self.stage_of() if self.stage_of else None
                for r in rows:
                    p = self.peaks.setdefault(r["usename"], {"max_connections": 0, "max_active": 0, "max_lock_wait": 0})
                    p["max_connections"] = max(p["max_connections"], r["total"])
                    p["max_active"] = max(p["max_active"], r["active"])
                    p["max_lock_wait"] = max(p["max_lock_wait"], r["lock_wait"])
                    if stage is not None:
                        merge_peak(self.by_stage.setdefault(stage, {}), r["usename"], r["total"], r["active"], r["lock_wait"], r["waiting"])
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
        out: dict = {"samples": self.samples, "by_role": self.peaks}
        if self.stage_of is not None:
            out["by_stage"] = {str(k): v for k, v in sorted(self.by_stage.items())}
        return out
