"""今日计划快照（PLT-15，specs/today-plan-snapshot-plt15/spec.md PR1 范围第 2 条；迁移 0055 platform.today_plan_item）。

开关 LEARNING_TODAY_SNAPSHOT=1 时由 TodayService 调用。一个事务里（learning_tx：SET LOCAL ROLE starbuddy_learning，锁等待最多
LOCK_TIMEOUT）：按（学生，日期）取咨询锁 → 学生已执行删除（0035 erased_at）就什么都不写、返回 erased → 读当天已有的行：
- 有：返回这一批（frozen）；
- 没有：这次汇总有条目、且没有任何启用的来源失败（may_freeze）时按顺序写入、读回（created）；否则不写（not_frozen：
  来源恢复或有了条目之后的读取再冻结，审查阻断 2）。
并发的两次首次读取由咨询锁串行，后一个读到前一个写的那一批；表上的唯一约束兜底。日期按数据库 now() 的北京时间取，
与表的插入守卫同一口径。推荐理由编码只存 BFF 已登记的那几个（迁移 0055 的 CHECK 同一张表），其余置空。
只写 platform.today_plan_item（学习服务是唯一写入方，规则 2、11）；快照只存编排结果，不存掌握、置信度、证据 ID、理由文本。
日志不在这里记（不记学生 ID 与条目内容，规则 8）。
"""
from __future__ import annotations

from dataclasses import replace
from typing import Sequence

import asyncpg

from ..store import learning_tx
from . import merge as M

LOCK_PREFIX = "platform.today_plan_item:"
LOCK_TIMEOUT = "2s"
# 与迁移 0055 today_plan_item_rationale_code 的 CHECK 一致（= services/bff/config/shell.json 的 content 与 about_student 两表之并）
SNAPSHOT_RATIONALE_CODES = frozenset({"high_exam_weight", "prerequisite_of_many", "retest_scheduled", "retest_due", "weak_mastery"})
CREATED, FROZEN, NOT_FROZEN, ERASED = "created", "frozen", "not_frozen", "erased"
TODAY_SQL = "SELECT (now() AT TIME ZONE 'Asia/Shanghai')::date"
ERASED_SQL = "SELECT erased_at IS NOT NULL FROM platform.student_profile WHERE id = $1::uuid"
SELECT_SQL = """SELECT id::text AS id, subject::text AS subject, kind, source, node_id, estimated_minutes, entry_ref::text AS entry_ref,
                       rationale_code
                  FROM platform.today_plan_item
                 WHERE student_id = $1::uuid AND plan_date = (now() AT TIME ZONE 'Asia/Shanghai')::date
                 ORDER BY seq"""
INSERT_SQL = """INSERT INTO platform.today_plan_item (student_id, seq, subject, kind, source, node_id, estimated_minutes, entry_ref,
                                                     rationale_code)
                VALUES ($1::uuid, $2, $3::platform.subject, $4, $5, $6, $7, $8::uuid, $9)"""


def db_source(item: M.Item) -> str:
    """快照的来源列：复测列表里的验证题单列为 verify（spec：source 取 plan / practice / retest / verify）。"""
    return "verify" if item.source == "retest" and item.kind == "verify" else item.source


def snapshot_item(item: M.Item) -> M.Item:
    """入库前：推荐理由编码不在已登记的表里就置空（BFF 对这种编码本来就不显示理由）。"""
    return item if item.rationale_code is None or item.rationale_code in SNAPSHOT_RATIONALE_CODES else replace(item, rationale_code=None)


def from_row(r) -> M.Item:
    """快照行 → 条目（与汇总结果同形状；verify 来源读回时仍是复测列表的条目，source = retest）。"""
    source = "retest" if r["source"] == "verify" else r["source"]
    return M.Item(r["subject"], r["kind"], r["node_id"], int(r["estimated_minutes"]), source, r["entry_ref"], r["rationale_code"])


def for_subjects(frozen: Sequence[tuple[str, M.Item]], subjects: Sequence[str]) -> list[tuple[str, M.Item]]:
    """冻结后的再读只给本次请求的学科（顺序仍按快照的序号）。"""
    want = set(subjects)
    return [(tid, i) for tid, i in frozen if i.subject in want]


async def freeze(pool: asyncpg.Pool, student: str, items: Sequence[M.Item], *, may_freeze: bool) -> tuple[str, list[tuple[str, M.Item]]]:
    """返回（状态, [(任务 ID, 条目)]（按序号））。状态：created / frozen（当天已有的那批）/ not_frozen（没写，列表为空）/
    erased（学生已删除，不写、列表为空）。"""
    try:
        async with learning_tx(pool) as c:
            await c.execute(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'")
            day = await c.fetchval(TODAY_SQL)
            await c.execute("SELECT pg_advisory_xact_lock(hashtextextended($1, 0))", f"{LOCK_PREFIX}{student}:{day.isoformat()}")
            # 已执行删除的学生（或档案不存在）：不再写任何个人层行（同 mastery/profile.py；插入守卫也会拒绝）
            if await c.fetchval(ERASED_SQL, student) is not False:
                return ERASED, []
            rows = await c.fetch(SELECT_SQL, student)
            if rows:
                return FROZEN, [(r["id"], from_row(r)) for r in rows]
            if not (may_freeze and items):
                return NOT_FROZEN, []
            await c.executemany(INSERT_SQL, [(student, n, i.subject, i.kind, db_source(i), i.node_id, i.estimated_minutes, i.entry_id,
                                              i.rationale_code) for n, i in enumerate(map(snapshot_item, items), start=1)])
            return CREATED, [(r["id"], from_row(r)) for r in await c.fetch(SELECT_SQL, student)]
    except asyncpg.InsufficientPrivilegeError as e:
        # 插入守卫按“已删除或不存在”拒绝（与读取 erased_at 之间被删除执行抢先）：同样不写、列表为空。
        # 别的权限错误（授权配错）照常抛出，不当作已删除
        if getattr(e, "message", None) != "not allowed":
            raise
        return ERASED, []
