"""诊断会话的库读写（迁移 0027）。每个事务 SET LOCAL ROLE starbuddy_learning（复用 learning.store.learning_tx）。

只写 0027 的两张表、已有的复核队列（过程观察，0027 策略）与证据表（过程题的书写原文，见 service.py）；
对错只读 grade_result.verdict，不另存。
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from .engine import Done, outcome_of

STEP_NS = uuid.UUID("0d1a9e5e-7a3c-4c1b-9f2e-5d0c3b6a8e27")  # 步骤幂等键、过程原文证据 ID 的命名空间


@dataclass
class SessionRow:
    id: str
    student_id: str
    subject: str
    status: str
    algorithm_version: str
    map_version: str
    usage_id: str
    time_budget_sec: int
    item_limit: int
    elapsed_sec: int
    item_count: int
    idempotency_key: str


@dataclass
class StepRow:
    id: str
    seq: int
    kind: str
    node_id: str
    item_id: str | None
    reason_code: str
    status: str
    probe_id: str | None
    grade_result_id: str | None
    elapsed_sec: int | None
    created_at: datetime
    verdict: str | None
    attempt_id: str | None

    def done(self) -> Done:
        return Done(self.kind, self.node_id, self.item_id, self.reason_code, self.status,
                    outcome_of(self.verdict) if self.status == "answered" else None, self.elapsed_sec or 0)


SESSION_COLS = """id::text, student_id::text, subject::text, status, algorithm_version::text, map_version, usage_id::text,
                  time_budget_sec, item_limit, elapsed_sec, item_count, idempotency_key::text"""


def _session(r) -> SessionRow | None:
    return None if r is None else SessionRow(*r)


async def session_by_key(conn, student: str, key: str) -> SessionRow | None:
    return _session(await conn.fetchrow(
        f"SELECT {SESSION_COLS} FROM platform.diagnosis_session WHERE student_id = $1::uuid AND idempotency_key = $2::uuid",
        student, key))


async def open_session(conn, student: str, subject: str, *, lock: bool = False) -> SessionRow | None:
    return _session(await conn.fetchrow(
        f"""SELECT {SESSION_COLS} FROM platform.diagnosis_session
             WHERE student_id = $1::uuid AND subject = $2::platform.subject AND status IN ('active', 'paused')
             {'FOR UPDATE' if lock else ''}""", student, subject))


async def lock_session(conn, session_id: str, student: str) -> SessionRow | None:
    """按学生取会话并加行锁：别的学生的会话与不存在的会话一样取不到（调用方回同一个 404）。"""
    return _session(await conn.fetchrow(
        f"SELECT {SESSION_COLS} FROM platform.diagnosis_session WHERE id = $1::uuid AND student_id = $2::uuid FOR UPDATE",
        session_id, student))


async def insert_session(conn, *, student: str, subject: str, algorithm_version: str, map_version: str, usage_id: str, key: str,
                         time_budget_sec: int, item_limit: int) -> SessionRow:
    return _session(await conn.fetchrow(
        f"""INSERT INTO platform.diagnosis_session (student_id, subject, algorithm_version, map_version, usage_id, idempotency_key,
                                                   time_budget_sec, item_limit)
            VALUES ($1::uuid, $2::platform.subject, $3, $4, $5::uuid, $6::uuid, $7, $8) RETURNING {SESSION_COLS}""",
        student, subject, algorithm_version, map_version, usage_id, key, time_budget_sec, item_limit))


async def set_status(conn, session_id: str, status: str) -> None:
    await conn.execute(
        """UPDATE platform.diagnosis_session SET status = $2,
                  finished_at = CASE WHEN $2 IN ('finished', 'abandoned') THEN now() END
            WHERE id = $1::uuid""", session_id, status)


async def add_progress(conn, session_id: str, elapsed: int) -> None:
    await conn.execute(
        "UPDATE platform.diagnosis_session SET elapsed_sec = elapsed_sec + $2, item_count = item_count + 1 WHERE id = $1::uuid",
        session_id, elapsed)


async def steps(conn, session_id: str) -> list[StepRow]:
    rows = await conn.fetch(
        """SELECT st.id::text, st.seq, st.kind, st.node_id, st.item_id::text, st.reason_code, st.status, st.probe_id::text,
                  st.grade_result_id::text, st.elapsed_sec, st.created_at, g.verdict, g.attempt_id::text
             FROM platform.diagnosis_step st LEFT JOIN platform.grade_result g ON g.id = st.grade_result_id
            WHERE st.session_id = $1::uuid ORDER BY st.seq""", session_id)
    return [StepRow(*r) for r in rows]


def step_key(session_id: str, seq: int) -> str:
    return str(uuid.uuid5(STEP_NS, f"{session_id}|step|{seq}"))


async def insert_step(conn, session_id: str, seq: int, kind: str, node_id: str, item_id: str | None, reason: str) -> None:
    status = "no_item" if reason == "no_item" else "presented"
    await conn.execute(
        """INSERT INTO platform.diagnosis_step (session_id, seq, kind, node_id, item_id, reason_code, status, idempotency_key)
           VALUES ($1::uuid, $2, $3, $4, $5, $6, $7, $8::uuid)""",
        session_id, seq, kind, node_id, item_id, reason, status, step_key(session_id, seq))


async def set_probe(conn, step_id: str, probe_id: str) -> None:
    await conn.execute("UPDATE platform.diagnosis_step SET probe_id = $2::uuid WHERE id = $1::uuid", step_id, probe_id)


async def set_partial_elapsed(conn, step_id: str, elapsed: int) -> None:
    await conn.execute(
        "UPDATE platform.diagnosis_step SET elapsed_sec = $2 WHERE id = $1::uuid AND status = 'presented' AND coalesce(elapsed_sec, 0) < $2",
        step_id, elapsed)


async def answer_step(conn, step_id: str, grade_result_id: str, elapsed: int) -> None:
    await conn.execute(
        """UPDATE platform.diagnosis_step SET status = 'answered', grade_result_id = $2::uuid, answered_at = now(), elapsed_sec = $3
            WHERE id = $1::uuid AND status = 'presented'""", step_id, grade_result_id, elapsed)


async def probe(conn, probe_id: str):
    return await conn.fetchrow(
        """SELECT student_id::text, subject::text, item_id::text, attempt_id::text, created_at
             FROM insight.confidence_probe WHERE id = $1::uuid""", probe_id)


async def student_grade(conn, student: str) -> int | None:
    return await conn.fetchval("SELECT grade FROM platform.student_profile WHERE id = $1::uuid", student)


async def item_admission(conn, subject: str, item_id: str) -> str | None:
    return await conn.fetchval("SELECT admission::text FROM platform.item WHERE subject = $1::platform.subject AND item_id = $2",
                               subject, item_id)


async def grade_requests(conn, prefix: str):
    """这一步已经发过的判分请求（学习服务的幂等表，0007）。前缀里没有 LIKE 的通配符（会话 ID 与序号）。"""
    return await conn.fetch(
        """SELECT idempotency_key, status, request_fingerprint, summary, (lease_until > now()) AS leased
             FROM platform.ingest_request WHERE method = 'grade' AND idempotency_key LIKE $1 || '%'
            ORDER BY length(idempotency_key), idempotency_key""", prefix)


async def enqueue_process_observation(conn, *, subject: str, student: str, step_id: str) -> None:
    """过程观察进统一复核队列（0027：诊断步骤只能以 process_observation 进队，且步骤须已作答）。同一步只进一次。"""
    await conn.execute(
        """INSERT INTO platform.review_task (subject, student_id, source_kind, source_id, reasons)
           VALUES ($1::platform.subject, $2::uuid, 'diagnosis_step', $3::uuid, ARRAY['process_observation']::platform.review_reason[])
           ON CONFLICT (source_kind, source_id) DO NOTHING""", subject, student, step_id)
