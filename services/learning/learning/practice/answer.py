"""再练作答（spec 第 3 节）：验票 → 重新确认条目仍开着这一轮 → 经现有 grade 写入路径落库 → 只回判分结论与条目新状态。

- 票据必须有效（伪造、篡改 403 TICKET_INVALID；过期 410 TICKET_EXPIRED），且属于请求里的学生与学科（跨学生、跨学科 403）。
- 条目必须仍是 due，且轮次、条目锚点与票据一致，否则 409 PRACTICE_NOT_DUE；票据的题在签发之后已经被作答过（不论判分结果，
  含待复核），409 TICKET_USED（审查 B1：同一张票据换个键不能再答一次）。
- 组装 learning-internal v1 的 GradeIngest 信封，在进程内调 IngestService.ingest("grade", …, "1.0.0")：复用幂等、封存、核对、
  落库与同步重算（重算失败只记计数，不影响作答）。ingest.channel = keypad、ingest.purpose = practice 由服务端写入，
  请求里出现这两个键按 400 处理（未知键）；hint_level_used = 0（本任务不提供提示，如实上报）。
- 判分请求的键就是 BFF 给的 idempotency_key（小写 UUIDv4），attempt_id 由它确定地派生；occurred_at 取票据签发时间（出题时间），
  同一请求重试时请求指纹不变。同一学生的键已经成功、或正在执行且租约未过期时，放过过期票据、不再看条目状态，直接交给 ingest 的
  幂等处理：同键同内容回原结果（响应头 idempotent-replayed: true），同键异内容 409；失败过或租约已过期的键照常验票、照常确认条目
  （审查 H1）。结果未知（5xx）BFF 用同一个键重试，或用既有的 GET /internal/v1/replay/grade/{key} 只查不执行。
- 同一单元的作答串行（会话级咨询锁，拿不到即 503 BUSY，可重试）：防止两个不同键并发用同一张票据各写一道。锁连接取自独立的小
  连接池，不占业务池；业务池取连接都有上限，超时 503 BUSY（审查 B2）。拿到锁之后再读一次键的状态（审查 S1）。
- 返回只有判分结论（grade 摘要的 verdict）、条目新状态与下一轮开放时间；判分待人工复核时另加 pending_review: true（verdict 原样，
  审查 S6）。没有掌握、置信度、证据 ID、错因与解释文字（规则 3、11）。
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from ..errors import IngestError
from . import queue as Q
from . import store
from . import ticket as T
from .service import UUID_RE, PracticeService, err, iso

log = logging.getLogger("starbuddy.learning.practice")
UUID4_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
GRADE_VERSION = "1.0.0"
INGEST = {"channel": "keypad", "purpose": "practice"}
ATTEMPT_NS = uuid.UUID("3e9a1c7d-2b4f-4d6a-8e1c-5f7a9b0c2d4e")
MAX_FILL = 200


def v4_shaped(name: str) -> str:
    b = bytearray(uuid.uuid5(ATTEMPT_NS, name).bytes)
    b[6] = (b[6] & 0x0F) | 0x40
    b[8] = (b[8] & 0x3F) | 0x80
    return str(uuid.UUID(bytes=bytes(b)))


def validate_answer(view: dict, answer: object) -> dict:
    """按题型核对作答形状（与诊断作答同一口径）；不合格 422，不送判分。"""
    fmt = view["format"]
    keys = [o["key"] for o in view.get("options", [])]
    if not isinstance(answer, dict) or len(answer) != 1:
        raise err(422, "INVALID_ANSWER", "answer must be {text} or {choice}")
    if fmt == "fill":
        t = answer.get("text")
        if not isinstance(t, str) or not t.strip() or len(t) > MAX_FILL:
            raise err(422, "INVALID_ANSWER", "fill item expects answer.text")
    else:
        c = answer.get("choice")
        if (not isinstance(c, list) or not c or len(set(c)) != len(c) or not all(isinstance(k, str) and k in keys for k in c)
                or (fmt == "single" and len(c) != 1)):
            raise err(422, "INVALID_ANSWER", "choice item expects answer.choice with known option keys")
    return answer


def grade_envelope(*, student: str, subject: str, item_id: str, answer: dict, occurred_at: datetime, key: str, usage_id: str,
                   rubric_version: str, content_version: dict) -> dict:
    request = {
        "context": {"request_id": str(uuid.uuid4()), "contract_version": GRADE_VERSION, "subject": subject, "student_ref": student,
                    "data_class": "L2", "usage_id": usage_id, "idempotency_key": key, "content_version": dict(content_version)},
        "attempt": {"attempt_id": v4_shaped(f"{key}|attempt"), "item_ref": f"{subject}:{item_id}",
                    "occurred_at": occurred_at.astimezone(timezone.utc).isoformat(), "response": answer, "hint_level_used": 0},
        "rubric_version": rubric_version,
    }
    return {"request": request, "ingest": dict(INGEST)}


async def _lock(conn, name: str) -> bool:
    return bool(await conn.fetchval("SELECT pg_try_advisory_lock(hashtextextended('platform.practice:' || $1, 0))", name))


async def _unlock(conn, name: str) -> None:
    await conn.execute("SELECT pg_advisory_unlock(hashtextextended('platform.practice:' || $1, 0))", name)


@asynccontextmanager
async def unit_lock(svc: PracticeService, name: str):
    """同一单元的作答串行：会话级咨询锁，连接取自独立的锁池（不占业务池，审查 B2）。取不到连接或锁被别人拿着：503 BUSY。"""
    try:
        conn = await svc.lock_pool.acquire(timeout=store.ACQUIRE_TIMEOUT)
    except asyncio.TimeoutError:
        svc.stats["busy"] += 1
        raise store.busy() from None
    try:
        if not await _lock(conn, name):
            svc.stats["busy"] += 1
            raise err(503, "BUSY", "another answer for this practice entry is being graded", retryable=True)
        try:
            yield
        finally:
            await _unlock(conn, name)
    finally:
        await svc.lock_pool.release(conn)


async def _key_state(svc: PracticeService, key: str) -> store.KeyState | None:
    async with store.tx(svc.pool) as conn:
        return await store.grade_key_student(conn, key)


def _settled(ks: store.KeyState | None, student: str) -> bool:
    return ks is not None and ks.settled_for(student)


async def _check_round_open(svc: PracticeService, t, student: str, subject: str, unit: Q.Unit, now: datetime) -> None:
    """（锁内、键未提交过）条目仍开着票据那一轮；且这张票据的题在签发之后还没有被作答过（审查 B1：判分待复核等情况下
    条目状态不变，只看条目会让同一张票据换个键再答一次）。"""
    snap = await svc.snapshot(student, subject, now)
    cur = next((e for e in svc.entries(snap, now, capped=False) if e.unit == unit), None)
    if cur is None or cur.status != "due" or cur.round != t.round or cur.anchor != t.anchor:
        svc.stats["answer_not_due"] += 1
        raise err(409, "PRACTICE_NOT_DUE", "this practice round is no longer open")
    # 签发时间按秒取整，再留出时钟容差；签发前做过的题不会被选中（排除窗口），所以容差不会误伤
    since = datetime.fromtimestamp(t.issued_at, tz=timezone.utc) - T.CLOCK_SKEW
    if any(a.item_id == t.item_id and a.recorded_at >= since for a in snap.attempts):
        svc.stats["ticket_used"] += 1
        raise err(409, "TICKET_USED", "this practice ticket has already been answered")


async def answer(svc: PracticeService, *, student: str, subject: str, ticket: object, answer: object, key: object,
                 usage_id: object) -> tuple[dict, bool]:
    """返回（摘要, 是否为重放）。"""
    svc.check_subject(student, subject)
    if not isinstance(key, str) or not UUID4_RE.fullmatch(key):
        raise err(400, "INVALID_REQUEST", "idempotency_key must be a lowercase UUIDv4")
    if not isinstance(usage_id, str) or not UUID_RE.fullmatch(usage_id):
        raise err(400, "INVALID_REQUEST", "usage_id must be a lowercase UUID")
    presenter = svc.require_presenter()
    now = svc.clock()
    # 同一学生的键已经成功或正在执行（租约未过期）：过期的票据也让它走到 ingest 的幂等处理（同键重试拿回原结果，不因票据过期变成 410）；
    # 失败过、租约已过期的照常验票（审查 H1）
    settled = _settled(await _key_state(svc, key), student)
    t = svc.ticket_of(ticket, now, allow_expired=settled)
    if t.student != student or t.subject != subject:
        svc.stats["ticket_invalid"] += 1
        raise err(403, "TICKET_INVALID", "practice ticket is not valid")
    view = presenter.present(subject, t.item_id)
    rubric = presenter.rubric_version(subject, t.item_id)
    if view is None or rubric is None:
        raise err(501, "PRESENTER_UNAVAILABLE", "no item view for the ticket item")
    answer = validate_answer(dict(view), answer)
    unit = Q.Unit(t.node_id, t.cognitive_level)
    async with unit_lock(svc, f"{student}|{subject}|{unit.node_id}|{unit.cognitive_level}"):
        # 拿到锁之后再读一次键（审查 S1）：等锁期间同键的另一请求可能已经提交或失败
        if not _settled(await _key_state(svc, key), student):
            if settled:
                svc.ticket_of(ticket, svc.clock())         # 原先放过了过期票据，但那次执行已失败或租约已过期：按常规验票
            await _check_round_open(svc, t, student, subject, unit, now)
        async with store.tx(svc.pool) as conn:
            cv = await store.content_version(conn, subject, t.item_id)
        if cv is None:
            raise err(422, "CONTENT_NOT_REGISTERED", "item has no registered content version")
        body = grade_envelope(student=student, subject=subject, item_id=t.item_id, answer=answer,
                              occurred_at=datetime.fromtimestamp(t.issued_at, tz=timezone.utc), key=key, usage_id=usage_id,
                              rubric_version=rubric, content_version=cv)
        summary, replayed = await svc.ingest.ingest("grade", body, GRADE_VERSION)
    after = svc.clock()
    snap = await svc.snapshot(student, subject, after)
    cur = next((e for e in svc.entries(snap, after, capped=False) if e.unit == unit), None)
    entry = {"entry_id": Q.entry_id(student, subject, unit), "status": cur.status if cur is not None else "closed",
             "round": cur.round if cur is not None else t.round, "next_round_at": iso(cur.next_round_at) if cur is not None else None}
    verdict = summary.get("verdict")
    if not isinstance(verdict, str):
        raise IngestError(500, "INTERNAL", "grade summary without verdict", retryable=True)
    svc.stats[f"answer_{verdict}"] += 1
    out: dict = {"verdict": verdict, "entry": entry}
    if summary.get("human_review_required") is True:
        # 判分待人工复核（审查 S6）：verdict 原样保留（规则 3），但标明待复核；这次作答既不算错题也不能闭环（queue.py）
        out["pending_review"] = True
        svc.stats["answer_pending_review"] += 1
    log.info(json.dumps({"event": "practice_answer", "subject": subject, "verdict": verdict, "status": entry["status"],
                         "pending_review": "pending_review" in out, "replayed": replayed}, ensure_ascii=False))
    return out, replayed
