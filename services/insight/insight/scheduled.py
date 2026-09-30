"""定时任务入口（不在契约里、不对外开放）：考前预测冻结（0034 insight.freeze_due_predictions）与到期删除执行
（0035 platform.execute_due_erasures）。两个库函数只授 starbuddy_scheduler，所以这里用调度器自己的连接，不用洞察服务的连接池：
- 登录角色只继承 starbuddy_scheduler（连接后 SET ROLE starbuddy_scheduler）；断言不是超级用户、没有 BYPASSRLS / CREATEROLE / CREATEDB、
  不继承其他应用角色（尤其是 starbuddy_insight：判断只由洞察服务写，调度器不能写）、不是 pg_ 预置角色成员，否则拒绝执行；
- 时间取数据库 now()（库函数要求 p_now 不晚于现在、最多往前 1 天）；每次调用一个事务。
调度频率：冻结在考试当天零点（上海）之后尽快；删除在冷静期满后 24 小时内（spec 待人确认 #3，靠调度频率保证）。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

import asyncpg

ROLE = "starbuddy_scheduler"
OTHER_APP_ROLES = ("starbuddy_insight", "starbuddy_bff", "starbuddy_learning", "starbuddy_review", "starbuddy_kge",
                   "starbuddy_kge_read", "starbuddy_report", "starbuddy_authz", "starbuddy_eraser")


class SchedulerRoleError(RuntimeError):
    """调度器连接的角色不安全：拒绝执行"""


async def assert_scheduler_role(conn: asyncpg.Connection) -> None:
    row = await conn.fetchrow(
        """SELECT current_user::text AS cur, r.rolsuper, r.rolbypassrls, r.rolcreaterole, r.rolcreatedb,
                  pg_has_role(session_user, $1, 'MEMBER') AS is_scheduler,
                  ARRAY(SELECT x FROM unnest($2::text[]) x WHERE pg_has_role(session_user, x, 'MEMBER')) AS others,
                  ARRAY(SELECT p.rolname::text FROM pg_roles p
                         WHERE p.rolname LIKE 'pg\\_%' AND pg_has_role(session_user, p.oid, 'MEMBER')) AS predefined
             FROM pg_roles r WHERE r.rolname = session_user""", ROLE, list(OTHER_APP_ROLES))
    if row is None or row["rolsuper"] or row["rolbypassrls"] or row["rolcreaterole"] or row["rolcreatedb"]:
        raise SchedulerRoleError("调度器的登录角色不能是超级用户，不能有 BYPASSRLS / CREATEROLE / CREATEDB")
    if row["predefined"] or row["others"] or not row["is_scheduler"] or row["cur"] != ROLE:
        raise SchedulerRoleError(f"调度器只能以 {ROLE} 执行，且不能继承其他应用角色或 pg_ 预置角色")


async def connect_scheduler(dsn: str | None = None, **connect: Any) -> asyncpg.Connection:
    conn = await asyncpg.connect(dsn, server_settings={"application_name": "starbuddy-insight-scheduler", "TimeZone": "Asia/Shanghai"},
                                 **connect)
    try:
        await conn.execute(f"SET ROLE {ROLE}")
        await assert_scheduler_role(conn)
    except BaseException:
        await conn.close()
        raise
    return conn


async def freeze_due_predictions(conn: asyncpg.Connection, now: datetime | None = None) -> int:
    """考试当天零点（上海）起冻结预测；返回冻结条数。冻结后预测不可改、不可删（库层守卫）"""
    await assert_scheduler_role(conn)
    return int(await conn.fetchval("SELECT insight.freeze_due_predictions(coalesce($1::timestamptz, now()))", now))


async def execute_due_erasures(conn: asyncpg.Connection, now: datetime | None = None) -> int:
    """冷静期满的删除请求逐个执行（一个事务删完该学生全部个人层行并写回执）；返回执行条数"""
    await assert_scheduler_role(conn)
    return int(await conn.fetchval("SELECT platform.execute_due_erasures(coalesce($1::timestamptz, now()))", now))
