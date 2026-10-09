"""复测与验证题的读取层（只读，不写任何表；starbuddy_learning 角色的事务，取连接有上限，同再练）。

- 作答、题目族、做过的题：直接复用再练的读取层（practice/store.load，同一套作答 + 证据 + 判分 + 打标口径，与掌握单元同粒度）；
- 开着的任务：platform.retest_task 里本模块的原因（scheduled、early、decay、verify_in_platform、verify_self_report），
  只认当前已发布地图里仍有效的单元（与再练读 hint_followup 同一做法）；
- 学生已成功的判分幂等键（platform.ingest_request）：只在有自述验证任务时读，用来认出“用本任务票据作答的题”（tasks.verify_answers）；
- 当前掌握记录的单元（只有节点与层次，不读掌握值）：只在 8 周复测点打开时读；
- 作答键的结果（replay_row）：只查不执行，不封存（宵禁时找回原结果用）。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime

from ..mastery import store as M
from ..practice import store as P
from ..practice.config import PracticeConfig
from ..practice.queue import Unit
from .tasks import RETEST_REASONS, TaskRow


@dataclass(frozen=True)
class RetestSnapshot:
    practice: P.Snapshot
    tasks: tuple[TaskRow, ...]
    keys: tuple[str, ...]                 # 学生这一科已成功的判分幂等键（只在有自述验证任务时读，否则为空）
    estimated: frozenset[Unit]            # 有当前掌握记录的单元（只在 8 周复测点打开时读）


async def load(conn, student: str, subject: str, practice_cfg: PracticeConfig, now: datetime, *, with_estimates: bool) -> RetestSnapshot:
    snap = await P.load(conn, student, subject, practice_cfg, now)
    ctx = await M.load_map(conn, subject)
    rows = await conn.fetch(
        """SELECT id::text, node_id, cognitive_level, reason, due_at FROM platform.retest_task
            WHERE student_id = $1::uuid AND subject = $2::platform.subject AND status = 'open' AND reason = ANY($3::text[])
            ORDER BY due_at, id""", student, subject, list(RETEST_REASONS))
    tasks = tuple(TaskRow(r["id"], Unit(r["node_id"], r["cognitive_level"]), r["reason"], r["due_at"]) for r in rows
                  if ctx.unit_ok(r["node_id"], r["cognitive_level"]))
    keys: tuple[str, ...] = ()
    if any(t.reason == "verify_self_report" for t in tasks):
        keys = tuple(r[0] for r in await conn.fetch(
            """SELECT idempotency_key FROM platform.ingest_request
                WHERE method = 'grade' AND student_id = $1::uuid AND subject = $2::platform.subject AND status = 'succeeded'
                ORDER BY created_at, idempotency_key""", student, subject))
    estimated: frozenset[Unit] = frozenset()
    if with_estimates:
        estimated = frozenset(
            Unit(r[0], r[1]) for r in await conn.fetch(
                """SELECT node_id, cognitive_level FROM platform.mastery_estimate
                    WHERE student_id = $1::uuid AND subject = $2::platform.subject AND is_current AND node_id IS NOT NULL
                      AND state IS NOT NULL""", student, subject)
            if ctx.unit_ok(r[0], r[1]))
    return RetestSnapshot(snap, tasks, keys, estimated)


@dataclass(frozen=True)
class KeyResult:
    usage_id: str | None
    attempt_ids: tuple[str, ...]
    summary: dict


async def replay_row(conn, student: str, key: str) -> KeyResult | None:
    """这个学生的这个判分键已经成功的结果（只读、不封存；没有、没成功、不是这个学生都为空）。"""
    r = await conn.fetchrow(
        """SELECT usage_id::text, attempt_ids::text[], summary FROM platform.ingest_request
            WHERE method = 'grade' AND idempotency_key = $1 AND student_id = $2::uuid AND status = 'succeeded'""", key, student)
    if r is None:
        return None
    summary = r[2] if isinstance(r[2], dict) else json.loads(r[2])
    return KeyResult(r[0], tuple(r[1] or ()), summary)
