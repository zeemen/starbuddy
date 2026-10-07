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
    resumed_at: datetime
    created_at: datetime  # 会话建立时刻：选题用的年级按这一刻推算（current_grade），整个会话不变


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
    work_evidence_id: str | None = None
    answer_key: str | None = None

    def done(self) -> Done:
        return Done(self.kind, self.node_id, self.item_id, self.reason_code, self.status,
                    outcome_of(self.verdict) if self.status == "answered" else None, self.elapsed_sec or 0)


SESSION_COLS = """id::text, student_id::text, subject::text, status, algorithm_version::text, map_version, usage_id::text,
                  time_budget_sec, item_limit, elapsed_sec, item_count, idempotency_key::text, resumed_at, created_at"""


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
                         time_budget_sec: int, item_limit: int, created_at: datetime) -> SessionRow:
    """created_at 取服务时钟（与开始时推算年级的是同一刻）：之后每一步按这一刻重算年级，跨 9 月 1 日续做选题不变。"""
    return _session(await conn.fetchrow(
        f"""INSERT INTO platform.diagnosis_session (student_id, subject, algorithm_version, map_version, usage_id, idempotency_key,
                                                   time_budget_sec, item_limit, created_at)
            VALUES ($1::uuid, $2::platform.subject, $3, $4, $5::uuid, $6::uuid, $7, $8, $9) RETURNING {SESSION_COLS}""",
        student, subject, algorithm_version, map_version, usage_id, key, time_budget_sec, item_limit, created_at))


async def set_status(conn, session_id: str, status: str) -> None:
    await conn.execute(
        """UPDATE platform.diagnosis_session SET status = $2,
                  finished_at = CASE WHEN $2 IN ('finished', 'abandoned') THEN now() END
            WHERE id = $1::uuid""", session_id, status)


async def resume(conn, session_id: str) -> None:
    """续做：恢复为进行中，并把 resumed_at 记为现在（服务端按墙钟给用时封顶，安全审查 S1；只增不减由 0027 守卫保证）。"""
    await conn.execute(
        "UPDATE platform.diagnosis_session SET status = 'active', resumed_at = greatest(resumed_at, now()) WHERE id = $1::uuid", session_id)


async def wall_clock(conn, step_id: str) -> tuple[int, bool]:
    """这一步从“出题或最近一次续做（取较晚者）”到现在的秒数（向上取整），以及续做是否晚于出题（是则暂停前的用时另计）。"""
    r = await conn.fetchrow(
        """SELECT ceil(extract(epoch FROM now() - greatest(st.created_at, s.resumed_at)))::int AS sec, s.resumed_at > st.created_at AS resumed
             FROM platform.diagnosis_step st JOIN platform.diagnosis_session s ON s.id = st.session_id WHERE st.id = $1::uuid""", step_id)
    return max(int(r["sec"] or 0), 0), bool(r["resumed"])


async def add_progress(conn, session_id: str, elapsed: int) -> None:
    await conn.execute(
        "UPDATE platform.diagnosis_session SET elapsed_sec = elapsed_sec + $2, item_count = item_count + 1 WHERE id = $1::uuid",
        session_id, elapsed)


async def steps(conn, session_id: str) -> list[StepRow]:
    rows = await conn.fetch(
        """SELECT st.id::text, st.seq, st.kind, st.node_id, st.item_id::text, st.reason_code, st.status, st.probe_id::text,
                  st.grade_result_id::text, st.elapsed_sec, st.created_at, g.verdict, g.attempt_id::text, st.work_evidence_id::text,
                  st.answer_key::text
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


async def set_answer_key(conn, step_id: str, key: str, elapsed: int) -> None:
    """记下这一步作答请求的幂等键与封顶后的用时（判分前定下，重试时判分请求的指纹不变；安全审查 S1、S5）。"""
    await conn.execute(
        """UPDATE platform.diagnosis_step SET answer_key = $2::uuid, elapsed_sec = greatest(coalesce(elapsed_sec, 0), $3)
            WHERE id = $1::uuid AND status = 'presented'""", step_id, key, elapsed)


async def set_work_evidence(conn, step_id: str, evidence_id: str) -> None:
    """过程题的书写原文证据引用（审核台按它取原文，契约审查 B1）。须在步骤记为已作答之前写（0027：已作答的步骤不能再改）。"""
    await conn.execute(
        "UPDATE platform.diagnosis_step SET work_evidence_id = $2::uuid WHERE id = $1::uuid AND status = 'presented'", step_id, evidence_id)


async def work_text(conn, evidence_id: str) -> str | None:
    return await conn.fetchval("SELECT payload ->> 'text' FROM platform.evidence_event WHERE evidence_id = $1::uuid", evidence_id)


async def answer_step(conn, step_id: str, grade_result_id: str, elapsed: int) -> None:
    await conn.execute(
        """UPDATE platform.diagnosis_step SET status = 'answered', grade_result_id = $2::uuid, answered_at = now(), elapsed_sec = $3
            WHERE id = $1::uuid AND status = 'presented'""", step_id, grade_result_id, elapsed)


async def probe(conn, probe_id: str):
    return await conn.fetchrow(
        """SELECT student_id::text, subject::text, item_id::text, attempt_id::text, created_at
             FROM insight.confidence_probe WHERE id = $1::uuid""", probe_id)


async def probe_by_key(conn, student: str, key: str) -> str | None:
    return await conn.fetchval(
        "SELECT id::text FROM insight.confidence_probe WHERE student_id = $1::uuid AND idempotency_key = $2::uuid", student, key)


async def student_grade(conn, student: str, at: datetime) -> tuple[bool, int | None]:
    """（学生档案是否存在，该时刻的年级）。年级由届别推算（迁移 0045：platform.current_grade(届别, 时刻)，9 月 1 日切学年），
    不读存下的建档年级；不在 7–9（未入学或已毕业）时为 None。时刻由调用方给（服务时钟或会话建立时刻），不用库里的 now()。"""
    r = await conn.fetchrow("SELECT platform.current_grade(exam_cohort, $2::timestamptz) AS g FROM platform.student_profile WHERE id = $1::uuid",
                            student, at)
    return (False, None) if r is None else (True, r["g"])


async def item_admission(conn, subject: str, item_id: str) -> str | None:
    return await conn.fetchval("SELECT admission::text FROM platform.item WHERE subject = $1::platform.subject AND item_id = $2",
                               subject, item_id)


async def grade_requests(conn, keys: list[str]):
    """这一步已经发过的判分请求（学习服务的幂等表，0007），按候选键的先后排序（keys 是这一步第 0、1、2… 次判分的键）。"""
    rows = await conn.fetch(
        """SELECT idempotency_key, status, request_fingerprint, summary, (lease_until > now()) AS leased
             FROM platform.ingest_request WHERE method = 'grade' AND idempotency_key = ANY($1::text[])""", keys)
    order = {k: i for i, k in enumerate(keys)}
    return sorted(rows, key=lambda r: order[r["idempotency_key"]])


async def enqueue_process_observation(conn, *, subject: str, student: str, step_id: str) -> None:
    """过程观察进统一复核队列（0027：诊断步骤只能以 process_observation 进队，且步骤须已作答）。同一步只进一次。"""
    await conn.execute(
        """INSERT INTO platform.review_task (subject, student_id, source_kind, source_id, reasons)
           VALUES ($1::platform.subject, $2::uuid, 'diagnosis_step', $3::uuid, ARRAY['process_observation']::platform.review_reason[])
           ON CONFLICT (source_kind, source_id) DO NOTHING""", subject, student, step_id)
