"""再练的读取层（spec 第 1、2 节）。只读，不写任何表；每次读都在 starbuddy_learning 角色的事务里（learning.store.learning_tx）。

- 作答：复用掌握读取层（mastery/store.load_rows、load_tags、map_row）：同一套作答 + 作答证据 + 当前判分 + 准入 + 打标口径，
  按作答时的地图版本解析打标，再经 ID 迁移映射到当前已发布版本（与掌握单元同粒度）；另读作答的输入通道（platform.attempt.channel）；
- 题目族：当前已发布版本里的 kge.item_tag.family_id → kge.item_family.code（知识服务 ItemTagView.family_code 同一口径）；
- 任务：开着的 retest_task（hint_followup 读成条目；scheduled / early 已到期的只给 retest_due 标记）；只读，不写，不关；
- 候选（列表页的可用性）：当前已发布版本里同主节点、同层次、打标审核通过的题，连同平台题目表的准入（与知识服务 item.search 同口径）；
- 题目的内容版本（判分请求要带）、错题的学科错因编码（generate_variation 用）、某个作答键是否已经提交过（含状态与租约）。
再练模块的取连接一律带上限（tx，超时 503 BUSY）；作答的串行锁用独立的小连接池，不占业务池（审查 B2）。
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Iterable

from ..errors import IngestError
from ..mastery import store as M
from .config import PracticeConfig
from .queue import AttemptFact, HintTask, Unit
from .select import Candidate, family_key

ACQUIRE_TIMEOUT = 5.0     # 取连接的上限（秒）：池满时不无限等，回 503 BUSY（可重试；审查 B2）


def busy() -> IngestError:
    return IngestError(503, "BUSY", "the practice service is busy; retry later", retryable=True)


@asynccontextmanager
async def tx(pool, timeout: float = ACQUIRE_TIMEOUT):
    """同 learning.store.learning_tx（starbuddy_learning 角色的事务），但取连接有上限：超时 503 BUSY。"""
    try:
        conn = await pool.acquire(timeout=timeout)
    except asyncio.TimeoutError:
        raise busy() from None
    try:
        async with conn.transaction():
            await conn.execute("SET LOCAL ROLE starbuddy_learning")
            yield conn
    finally:
        await pool.release(conn)


@dataclass(frozen=True)
class Snapshot:
    map_id: str | None
    map_version: str | None                # 当前已发布版本号（知识服务请求的 context.map_version）
    attempts: tuple[AttemptFact, ...]
    families: dict[str, str | None]        # item_id → 当前版本的题目族编码
    hint_tasks: tuple[HintTask, ...]
    retest_due: frozenset[Unit]
    recent_items: frozenset[str]           # exclude_window_days 内做过的题（本学科全部单元）

    def used_families(self, unit: Unit) -> set[str]:
        """学生在这个单元做过的题所在的族（含原题；没有族的题按单题自成一族）。"""
        return {family_key(a.item_id, self.families.get(a.item_id)) for a in self.attempts if a.unit == unit}


async def load(conn, student: str, subject: str, cfg: PracticeConfig, now: datetime) -> Snapshot:
    ctx = await M.load_map(conn, subject)
    version = await conn.fetchval("SELECT version FROM kge.map_version WHERE id = $1::uuid", ctx.current_id) if ctx.current_id else None
    rows = await M.load_rows(conn, student, subject)
    tags = await M.load_tags(conn, subject, (r.item_id for r in rows))
    channels = {r[0]: r[1] for r in await conn.fetch(
        "SELECT attempt_id::text, channel FROM platform.attempt WHERE student_id = $1::uuid AND subject = $2::platform.subject",
        student, subject)}
    attempts = []
    for r in rows:
        m = M.map_row(r, tags, ctx)
        attempts.append(AttemptFact(
            attempt_id=r.attempt_id, item_id=r.item_id, recorded_at=r.recorded_at, verdict=r.verdict,
            human_review_required=r.human_review_required, evidence_level=r.evidence_level, channel=channels.get(r.attempt_id),
            node_id=m.main, cognitive_level=m.level, hint_level=r.hint_level, self_report=r.self_report,
            review_reasons=r.review_reasons, in_diagnosis=r.source_method == "diagnose" or r.in_diagnosis, has_grade=r.grade_id is not None))
    families: dict[str, str | None] = {}
    if ctx.current_id is not None and rows:
        families = {r[0]: r[1] for r in await conn.fetch(
            """SELECT t.item_id::text, f.code FROM kge.item_tag t
                 LEFT JOIN kge.item_family f ON f.map_version_id = t.map_version_id AND f.id = t.family_id
                WHERE t.map_version_id = $1::uuid AND t.subject = $2::platform.subject AND t.item_id = ANY($3::text[])""",
            ctx.current_id, subject, sorted({r.item_id for r in rows}))}
    tasks = await conn.fetch(
        """SELECT id::text, node_id, cognitive_level, reason, due_at FROM platform.retest_task
            WHERE student_id = $1::uuid AND subject = $2::platform.subject AND status = 'open'
              AND reason IN ('hint_followup', 'scheduled', 'early')
            ORDER BY due_at, id""", student, subject)
    hint = tuple(HintTask(t["id"], Unit(t["node_id"], t["cognitive_level"]), t["due_at"]) for t in tasks if t["reason"] == "hint_followup"
                 if ctx.unit_ok(t["node_id"], t["cognitive_level"]))
    due = frozenset(Unit(t["node_id"], t["cognitive_level"]) for t in tasks if t["reason"] != "hint_followup" and t["due_at"] <= now)
    since = now - timedelta(days=cfg.exclude_window_days)
    recent = frozenset(r.item_id for r in rows if r.recorded_at >= since)
    return Snapshot(ctx.current_id, version, tuple(attempts), families, hint, due, recent)


async def unit_candidates(conn, subject: str, map_id: str | None, units: Iterable[Unit]) -> tuple[list[Candidate], dict[str, str]]:
    """列表页判断“有没有可练的题”：当前已发布版本里这些单元的已审打标题目（同 item.search 的口径），以及它们的平台准入。"""
    wanted = set(units)
    if map_id is None or not wanted:
        return [], {}
    rows = await conn.fetch(
        """SELECT t.item_id::text, t.main_node_id::text, t.cognitive_level, f.code, t.license_state, i.admission::text
             FROM kge.item_tag t
             JOIN platform.item i ON i.subject = t.subject AND i.item_id = t.item_id
             LEFT JOIN kge.item_family f ON f.map_version_id = t.map_version_id AND f.id = t.family_id
            WHERE t.map_version_id = $1::uuid AND t.subject = $2::platform.subject AND t.tag_review_status = 'approved'
              AND t.main_node_id = ANY($3::text[])
            ORDER BY t.item_id""", map_id, subject, sorted({u.node_id for u in wanted}))
    cands, admission = [], {}
    for r in rows:
        if Unit(r[1], r[2]) not in wanted:
            continue
        view = {"item_ref": f"{subject}:{r[0]}", "main_node_id": r[1], "cognitive_level": r[2], "license_state": r[4]}
        if r[3]:
            view["family_code"] = r[3]
        cands.append(Candidate(view["item_ref"], r[0], r[1], r[2], r[3], r[4], view))
        admission[r[0]] = r[5]
    return cands, admission


async def admissions(conn, subject: str, item_ids: Iterable[str]) -> dict[str, str]:
    """平台题目表的准入（唯一真相源）；不在结果里 = 未登记。"""
    ids = sorted(set(item_ids))
    if not ids:
        return {}
    rows = await conn.fetch("SELECT item_id::text, admission::text FROM platform.item WHERE subject = $1::platform.subject AND item_id = ANY($2::text[])",
                            subject, ids)
    return {r[0]: r[1] for r in rows}


async def error_category(conn, student: str, subject: str, attempt_id: str) -> str | None:
    """这道错题（条目锚点那次作答）的学科错因编码（当前、未被取代的那条归因）；没有就是空。"""
    return await conn.fetchval(
        """SELECT x.subject_category FROM platform.attempt a
             JOIN platform.error_attribution x ON x.student_id = a.student_id AND x.subject = a.subject AND x.superseded_by IS NULL
                                              AND a.response_evidence_id = ANY (x.evidence_ids)
            WHERE a.student_id = $1::uuid AND a.subject = $2::platform.subject AND a.attempt_id = $3::uuid
            ORDER BY x.created_at DESC, x.id LIMIT 1""", student, subject, attempt_id)


async def content_version(conn, subject: str, item_id: str) -> dict | None:
    """题目登记时的内容版本（六段；判分请求的 context.content_version 必填）。"""
    r = await conn.fetchrow(
        """SELECT c.region, c.exam_year, c.grade, c.textbook_version, c.subject::text, c.content_version
             FROM platform.item i JOIN platform.curriculum_version c ON c.id = i.curriculum_version_id
            WHERE i.subject = $1::platform.subject AND i.item_id = $2""", subject, item_id)
    if r is None:
        return None
    return {"region": r[0], "exam_year": int(r[1]), "grade": int(r[2]), "textbook_version": r[3], "subject": r[4],
            "content_version": r[5]}


@dataclass(frozen=True)
class KeyState:
    """一个作答键在幂等表里的状态：学生、status（pending / succeeded / failed）、pending 的租约是否未过期。"""
    student: str
    status: str
    leased: bool

    def settled_for(self, student: str) -> bool:
        """同一学生的这个键已经成功，或正在执行（租约未过期）：交给 ingest 的幂等处理（同键重试拿回原结果），
        放过过期票据、不再看条目状态。失败过或租约已过期的照常验票、照常确认条目仍开着（审查 H1）。"""
        return self.student == student and (self.status == "succeeded" or (self.status == "pending" and self.leased))


async def grade_key_student(conn, key: str) -> KeyState | None:
    """这个作答键是否已经提交过 grade；返回当时的学生 ID 与状态（没有记录为空）。"""
    r = await conn.fetchrow(
        """SELECT student_id::text, status, coalesce(lease_until > now(), false) FROM platform.ingest_request
            WHERE method = 'grade' AND idempotency_key = $1""", key)
    return None if r is None else KeyState(r[0], r[1], bool(r[2]))
