"""计量调用与零泄漏核对（只读断言，不改规则；规则 4）。

调用：与 BFF（services/bff/src/metering/meter.ts）同一组函数、同一数据库角色（starbuddy_bff 的登录角色），在事务里先
set_config('app.account_id') 再调函数；本工具不改任何计量函数、额度或扣次规则。
零泄漏（spec 验收“压测后计量零泄漏”，复用计量的对账口径 = services/bff/tests/harness.ts 的 ledgerConsistent）：
  1. 对账视图 platform.entitlement_reconciliation 与 platform.reservation_ledger_mismatch 都为零行；
  2. 快进计量时钟过预留有效期并以定时任务角色跑完过期后，本轮合成学生没有停在 reserved 的预留；
  3. 每一笔预留恰好一个终态，终态流水与预留流水对得上：结算 ≤ 预留（部分结算，余量随结算退回额度桶）、
     释放 = 预留、过期 = 预留，且没有第二种终态流水；每个额度桶 reserved = 0。
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

import asyncpg


def fingerprint(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


async def call(pool: asyncpg.Pool, account: str, sql: str, *args: Any) -> dict:
    async with pool.acquire() as c, c.transaction():
        await c.execute("SELECT set_config('app.account_id', $1, true)", account)
        raw = await c.fetchval(sql, *args)
    return json.loads(raw) if isinstance(raw, str) else raw


async def reserve(pool, account: str, student: str, resource: str, key: str, fp: str, qty: int, content_fp: str | None,
                  method: str | None) -> dict:
    return await call(pool, account, "SELECT platform.meter_reserve($1::uuid, $2, $3, $4, $5::numeric, $6, $7)",
                      student, resource, key, fp, qty, content_fp, method)


async def settle(pool, account: str, usage_id: str, qty: int) -> dict:
    return await call(pool, account, "SELECT platform.meter_settle($1::uuid, $2::numeric)", usage_id, qty)


async def release(pool, account: str, usage_id: str, reason: str) -> dict:
    return await call(pool, account, "SELECT platform.meter_release($1::uuid, $2)", usage_id, reason)


async def expire_all(scheduler: asyncpg.Pool) -> int:
    n, total = 1, 0
    while n:
        n = await scheduler.fetchval("SELECT platform.meter_expire_due(200)")
        total += n
    return total


LEAK_SQL = """
WITH r AS (
  SELECT r.usage_id, r.state::text AS state, r.reserved_qty,
         coalesce(sum(l.quantity) FILTER (WHERE l.kind = 'reserve'), 0) AS q_reserve,
         coalesce(sum(l.quantity) FILTER (WHERE l.kind = 'settle'), 0) AS q_settle,
         coalesce(sum(l.quantity) FILTER (WHERE l.kind = 'release'), 0) AS q_release,
         coalesce(sum(l.quantity) FILTER (WHERE l.kind = 'expire'), 0) AS q_expire
    FROM platform.usage_reservation r
    LEFT JOIN platform.usage_ledger l ON l.usage_id = r.usage_id
   WHERE r.student_id = ANY($1::uuid[])
   GROUP BY r.usage_id, r.state, r.reserved_qty)
SELECT count(*) AS reservations,
       count(*) FILTER (WHERE state = 'reserved') AS still_reserved,
       count(*) FILTER (WHERE state = 'settled') AS settled,
       count(*) FILTER (WHERE state = 'released') AS released,
       count(*) FILTER (WHERE state = 'expired') AS expired,
       count(*) FILTER (WHERE NOT CASE state
                           WHEN 'settled' THEN q_settle <= q_reserve AND q_release = 0 AND q_expire = 0
                           WHEN 'released' THEN q_release = q_reserve AND q_settle = 0 AND q_expire = 0
                           WHEN 'expired' THEN q_expire = q_reserve AND q_settle = 0 AND q_release = 0
                           ELSE true END) AS unbalanced,
       coalesce(sum(q_settle), 0) AS settled_qty
  FROM r
"""


async def leak_report(admin: asyncpg.Pool, students: list[str]) -> dict:
    """在快进与过期之后调用；返回只含计数的汇总与 zero_leak 结论"""
    async with admin.acquire() as c:
        row = dict(await c.fetchrow(LEAK_SQL, students))
        views = await c.fetchval("""SELECT (SELECT count(*) FROM platform.entitlement_reconciliation)
                                         + (SELECT count(*) FROM platform.reservation_ledger_mismatch)""")
        bucket_reserved = await c.fetchval(
            "SELECT coalesce(sum(reserved), 0) FROM platform.entitlement WHERE student_id = ANY($1::uuid[])", students)
    out = {k: int(v) for k, v in row.items()}
    out["reconciliation_rows"] = int(views)
    out["bucket_reserved"] = int(bucket_reserved)
    out["zero_leak"] = out["still_reserved"] == 0 and out["unbalanced"] == 0 and out["reconciliation_rows"] == 0 \
        and out["bucket_reserved"] == 0
    return out
