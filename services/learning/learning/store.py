"""数据库读写（asyncpg）。每个事务 SET LOCAL ROLE starbuddy_learning（spec 范围第 7 条）。

参数一律以文本传入再在 SQL 里转换类型，避免驱动对枚举、域类型的编码差异。
数据库报错可能带出整行数据（例如约束报错的 “Failing row contains ...”），所以这里只把
SQLSTATE 与约束名转成 IngestError，原文不进日志、不进响应。
"""
from __future__ import annotations

import json
import math
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass

import asyncpg

from .checks import local_id
from .errors import IngestError
from .fingerprint import sha256_hex


@asynccontextmanager
async def learning_tx(pool: asyncpg.Pool):
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SET LOCAL ROLE starbuddy_learning")
            yield conn


def db_error(e: asyncpg.PostgresError) -> IngestError:
    constraint = getattr(e, "constraint_name", None) or ""
    return IngestError(
        502,
        "ADAPTER_RESULT_REJECTED",
        "result violates platform constraints",
        detail=f"sqlstate {e.sqlstate}" + (f" constraint {constraint}" if constraint else ""),
    )


# ---------- 请求级幂等（0007 ingest_request） ----------
@dataclass
class Claim:
    kind: str  # new | takeover | done | conflict | busy
    attempt_ids: list[str]
    lease_token: str | None = None
    summary: dict | None = None


async def claim(pool, *, method, key, subject, student_id, fingerprint, attempt_ids, lease_seconds) -> Claim:
    token = str(uuid.uuid4())
    try:
        async with learning_tx(pool) as conn:
            row = await conn.fetchrow(
                """INSERT INTO platform.ingest_request (method, idempotency_key, subject, student_id, request_fingerprint,
                     attempt_ids, lease_token, lease_until)
                   VALUES ($1, $2, $3::platform.subject, $4::uuid, $5, $6::text[]::uuid[], $7::uuid,
                           now() + make_interval(secs => $8::float8))
                   ON CONFLICT (method, idempotency_key) DO NOTHING
                   RETURNING attempt_ids::text[]""",
                method, key, subject, student_id, fingerprint, attempt_ids, token, lease_seconds,
            )
            if row:
                return Claim("new", list(row[0]), token)
            row = await conn.fetchrow(
                """SELECT request_fingerprint, status, lease_until > now() AS leased, summary, attempt_ids::text[]
                     FROM platform.ingest_request WHERE method = $1 AND idempotency_key = $2 FOR UPDATE""",
                method, key,
            )
            if row["request_fingerprint"] != fingerprint:
                return Claim("conflict", [])
            if row["status"] == "succeeded":
                return Claim("done", list(row["attempt_ids"]), summary=json.loads(row["summary"]))
            if row["status"] == "pending" and row["leased"]:
                return Claim("busy", list(row["attempt_ids"]))
            # 失败过，或者持有者的租约已过期（例如进程中途崩溃）：接管，沿用原 attempt_id
            await conn.execute(
                """UPDATE platform.ingest_request
                      SET status = 'pending', lease_token = $3::uuid, error_code = NULL,
                          lease_until = now() + make_interval(secs => $4::float8)
                    WHERE method = $1 AND idempotency_key = $2""",
                method, key, token, lease_seconds,
            )
            return Claim("takeover", list(row["attempt_ids"]), token)
    except asyncpg.ForeignKeyViolationError:
        raise IngestError(404, "STUDENT_NOT_FOUND", "student_ref is not a registered student") from None


async def mark_failed(pool, *, method, key, lease_token, error_code) -> None:
    """尽力而为：只有租约仍归自己时才标记失败，之后同 key 重试可以立即接管。"""
    try:
        async with learning_tx(pool) as conn:
            await conn.execute(
                """UPDATE platform.ingest_request
                      SET status = 'failed', error_code = $4, lease_token = NULL, lease_until = NULL
                    WHERE method = $1 AND idempotency_key = $2 AND status = 'pending' AND lease_token = $3::uuid""",
                method, key, lease_token, error_code,
            )
    except asyncpg.PostgresError:
        pass  # 标记失败本身失败：租约到期后照样能被接管


# ---------- 登记查询 ----------
async def registered(pool, subject: str, item_refs: set[str], node_refs: set[str]) -> tuple[dict[str, str], set[str]]:
    async with learning_tx(pool) as conn:
        items = await conn.fetch(
            "SELECT item_id::text, admission::text FROM platform.item WHERE subject = $1::platform.subject AND item_id = ANY($2::text[])",
            subject, [local_id(r) for r in item_refs],
        )
        nodes = await conn.fetch(
            "SELECT local_id::text FROM platform.skill_node WHERE subject = $1::platform.subject AND local_id = ANY($2::text[])",
            subject, [local_id(r) for r in node_refs],
        )
    return {r[0]: r[1] for r in items}, {r[0] for r in nodes}


# ---------- 落库 ----------
class LeaseLost(Exception):
    """落库前发现租约已被别人接管：放弃本次写入，回到幂等检查。"""


def _num(v) -> str | None:
    if v is None:
        return None
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        raise IngestError(502, "ADAPTER_RESULT_REJECTED", "adapter result rejected by platform checks", detail="non-finite number")
    return repr(v)


async def _insert_evidence(conn, e: dict, *, student_id, subject, method, ctx) -> None:
    stored = {
        "student_id": student_id,
        "subject": subject,
        "kind": e["kind"],
        "item_id": local_id(e["item_ref"]) if "item_ref" in e else None,
        "skill_local_id": local_id(e["skill_node"]) if "skill_node" in e else None,
        "admission": e["admission"],
        "payload": e["payload"],
        "media_ref": e.get("media_ref"),
        "occurred_at": e["occurred_at"],
    }
    digest = sha256_hex(stored)
    inserted = await conn.fetchval(
        """INSERT INTO platform.evidence_registry (evidence_id, occurred_at, student_id, content_digest, request_idempotency_key)
           VALUES ($1::uuid, $2::text::timestamptz, $3::uuid, $4, $5)
           ON CONFLICT (evidence_id) DO NOTHING RETURNING true""",
        e["evidence_id"], e["occurred_at"], student_id, digest, ctx["idempotency_key"],
    )
    if not inserted:
        existing = await conn.fetchval(
            "SELECT content_digest FROM platform.evidence_registry WHERE evidence_id = $1::uuid", e["evidence_id"]
        )
        if existing != digest:
            raise IngestError(502, "ADAPTER_RESULT_REJECTED", "adapter result rejected by platform checks",
                              detail="evidence_id already recorded with different content")
        return  # 同一证据重试：跳过
    await conn.execute(
        """INSERT INTO platform.evidence_event (evidence_id, occurred_at, student_id, subject, kind, item_id, skill_local_id,
             admission, payload, media_ref, source_method, request_id, request_idempotency_key)
           VALUES ($1::uuid, $2::text::timestamptz, $3::uuid, $4::platform.subject, $5, $6, $7, $8::platform.admission,
                   $9::jsonb, $10, $11, $12::uuid, $13)""",
        e["evidence_id"], e["occurred_at"], student_id, subject, e["kind"], stored["item_id"], stored["skill_local_id"],
        e["admission"], json.dumps(e["payload"], ensure_ascii=False), e.get("media_ref"), method, ctx["request_id"],
        ctx["idempotency_key"],
    )


async def _insert_attempt(conn, a: dict, attempt_id: str, response_evidence_id: str, *, student_id, subject, ctx) -> None:
    await conn.execute(
        """INSERT INTO platform.attempt (attempt_id, occurred_at, student_id, subject, item_id, response_evidence_id,
             hint_level_used, elapsed_sec, self_report, request_idempotency_key)
           VALUES ($1::uuid, $2::text::timestamptz, $3::uuid, $4::platform.subject, $5, $6::uuid,
                   $7::text::smallint, $8::text::integer, $9, $10)""",
        attempt_id, a["occurred_at"], student_id, subject, local_id(a["item_ref"]), response_evidence_id,
        None if a.get("hint_level_used") is None else str(a["hint_level_used"]),
        None if a.get("elapsed_sec") is None else str(a["elapsed_sec"]),
        a.get("self_report"), ctx["idempotency_key"],
    )


async def _insert_attribution(conn, a: dict, *, student_id, subject, ctx) -> None:
    await conn.execute(
        """INSERT INTO platform.error_attribution (student_id, subject, skill_local_id, subject_category, cross_subject_category,
             confidence, algorithm_version, evidence_ids, source_request_id)
           VALUES ($1::uuid, $2::platform.subject, $3, $4, $5::platform.cross_subject_error, $6::text::numeric, $7,
                   $8::text[]::uuid[], $9::uuid)""",
        student_id, subject, local_id(a["skill_node"]) if "skill_node" in a else None, a["subject_category"],
        a["cross_subject_category"], _num(a["confidence"]), a["algorithm_version"], a["evidence_ids"], ctx["request_id"],
    )


async def _insert_observation(conn, o: dict, *, student_id, subject, ctx) -> None:
    await conn.execute(
        """INSERT INTO platform.skill_observation (student_id, subject, skill_local_id, outcome, local_level, confidence,
             algorithm_version, evidence_ids, source_request_id)
           VALUES ($1::uuid, $2::platform.subject, $3, $4, $5, $6::text::numeric, $7, $8::text[]::uuid[], $9::uuid)""",
        student_id, subject, local_id(o["skill_node"]), o["outcome"], o.get("local_level"), _num(o["confidence"]),
        o["algorithm_version"], o["evidence_ids"], ctx["request_id"],
    )


async def _insert_grade(conn, response: dict, verified, attempt: dict, attempt_id: str, *, student_id, subject, ctx) -> str:
    r = response["result"]
    verifier = r.get("verifier") or {}
    grade_id = await conn.fetchval(
        """INSERT INTO platform.grade_result (student_id, subject, attempt_id, attempt_occurred_at, verdict, rubric_version,
             score_total, max_total, calibrated, confidence, source_version, model_version, verifier_id, verifier_version,
             verifier_reason_codes, human_review_required, review_reasons, semantics, evidence_ids, source_request_id)
           VALUES ($1::uuid, $2::platform.subject, $3::uuid, $4::text::timestamptz, $5, $6, $7::text::numeric, $8::text::numeric, $9::bool,
                   $10::text::numeric, $11, $12, $13, $14, $15::text[], $16::bool, $17::text[]::platform.review_reason[],
                   $18::platform.semantics, $19::text[]::uuid[], $20::uuid)
           RETURNING id::text""",
        student_id, subject, attempt_id, attempt["occurred_at"], r["verdict"], r["rubric_version"],
        _num(r["score"]["total"]), _num(r["score"]["max_total"]), r["score"]["calibrated"], _num(response["confidence"]),
        response["source_version"], response["model_version"], verifier.get("verifier_id"), verifier.get("version"),
        verifier.get("reason_codes", []), verified.human_review_required, verified.review_reasons, verified.semantics,
        response["evidence_ids"], ctx["request_id"],
    )
    for d in r["dimensions"]:
        await conn.execute(
            """INSERT INTO platform.grade_dimension (grade_result_id, dimension_id, score, max_score, judged_by, subjective,
                 rule_ref, confidence, evidence_ids)
               VALUES ($1::uuid, $2, $3::text::numeric, $4::text::numeric, $5, $6::bool, $7, $8::text::numeric, $9::text[]::uuid[])""",
            grade_id, d["dimension_id"], _num(d["score"]), _num(d["max_score"]), d["judged_by"], d["subjective"],
            d.get("rule_ref"), _num(d["confidence"]), d["evidence_ids"],
        )
    return grade_id


async def persist(pool, *, method, key, lease_token, request, response, verified, attempt_ids, summary_base) -> dict:
    """一个事务写完全部行，并把幂等记录推进到 succeeded。返回结果摘要。"""
    ctx = request["context"]
    student_id, subject = ctx["student_ref"], ctx["subject"]
    attempts = [request["attempt"]] if method == "grade" else request["attempts"]
    try:
        async with learning_tx(pool) as conn:
            holder = await conn.fetchval(
                """SELECT lease_token::text FROM platform.ingest_request
                    WHERE method = $1 AND idempotency_key = $2 AND status = 'pending' FOR UPDATE""",
                method, key,
            )
            if holder != lease_token:
                raise LeaseLost()
            for e in verified.evidence:
                await _insert_evidence(conn, e, student_id=student_id, subject=subject, method=method, ctx=ctx)
            for a, attempt_id, ev_id in zip(attempts, attempt_ids, verified.response_evidence_ids):
                await _insert_attempt(conn, a, attempt_id, ev_id, student_id=student_id, subject=subject, ctx=ctx)
            summary = dict(summary_base)
            if method == "grade":
                summary["grade_result_id"] = await _insert_grade(
                    conn, response, verified, attempts[0], attempt_ids[0], student_id=student_id, subject=subject, ctx=ctx
                )
            for o in response["result"].get("observations", []):
                await _insert_observation(conn, o, student_id=student_id, subject=subject, ctx=ctx)
            for a in response["result"].get("error_attributions", []):
                await _insert_attribution(conn, a, student_id=student_id, subject=subject, ctx=ctx)
            await conn.execute(
                """UPDATE platform.ingest_request
                      SET status = 'succeeded', summary = $3::jsonb, lease_token = NULL, lease_until = NULL
                    WHERE method = $1 AND idempotency_key = $2""",
                method, key, json.dumps(summary),
            )
            return summary
    except asyncpg.PostgresError as e:
        raise db_error(e) from None
