"""讲评课会话的存取（迁移 0064 platform.lesson_session；学习服务角色 starbuddy_learning）。

只存会话与计数，不存讲解文字、提问与回答（spec 第二节第 3、5 条）。新建时库里的守卫核对那笔讲评课预留（对不上 42501），
提问由 platform.lesson_session_ask 在同一个行锁里放行并加次数；本模块只是薄薄一层调用。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime

import asyncpg

from ..practice.store import tx

__all__ = ["SessionRow", "ask", "find", "get", "insert", "set_status", "submitted", "tx"]

COLUMNS = ("id::text, student_id::text, subject::text, lesson_id, lesson_version, usage_id::text, idempotency_key::text, status, "
           "question_count, question_limit, expires_at")


@dataclass
class SessionRow:
    id: str
    student_id: str
    subject: str
    lesson_id: str
    lesson_version: str
    usage_id: str
    idempotency_key: str
    status: str
    question_count: int
    question_limit: int
    expires_at: datetime


def _row(r) -> SessionRow | None:
    return None if r is None else SessionRow(**dict(r))


async def find(conn, student: str, key: str) -> SessionRow | None:
    """同一学生、同一预留幂等键的会话（打开讲评课的幂等重放）"""
    return _row(await conn.fetchrow(
        f"SELECT {COLUMNS} FROM platform.lesson_session WHERE student_id = $1::uuid AND idempotency_key = $2::uuid FOR UPDATE",
        student, key))


async def get(conn, session: str, student: str) -> SessionRow | None:
    return _row(await conn.fetchrow(
        f"SELECT {COLUMNS} FROM platform.lesson_session WHERE id = $1::uuid AND student_id = $2::uuid", session, student))


class NoReservation(Exception):
    """库里的守卫没找到对得上的讲评课预留（42501），或这笔预留已经开过另一个会话"""


async def insert(conn, *, student: str, subject: str, lesson_id: str, lesson_version: str, usage_id: str, key: str) -> SessionRow:
    try:
        async with conn.transaction():  # 子事务：守卫拒绝时外层事务还能继续（调用方据此回 409）
            r = await conn.fetchrow(
                f"""INSERT INTO platform.lesson_session (student_id, subject, lesson_id, lesson_version, usage_id, idempotency_key)
                    VALUES ($1::uuid, $2::platform.subject, $3, $4, $5::uuid, $6::uuid) RETURNING {COLUMNS}""",
                student, subject, lesson_id, lesson_version, usage_id, key)
    except (asyncpg.exceptions.InsufficientPrivilegeError, asyncpg.exceptions.UniqueViolationError):
        raise NoReservation() from None
    return _row(r)  # type: ignore[return-value]


async def set_status(conn, session: str, status: str) -> None:
    await conn.execute("UPDATE platform.lesson_session SET status = $2 WHERE id = $1::uuid", session, status)


async def ask(conn, session: str, student: str, ask_key: str) -> tuple[str, int | None]:
    """platform.lesson_session_ask：返回（结果编码，第几问）"""
    out = await conn.fetchval("SELECT platform.lesson_session_ask($1::uuid, $2::uuid, $3::uuid)::text", session, student, ask_key)
    d = json.loads(out)
    return d["outcome"], d["question_seq"]


async def submitted(conn, student: str, subject: str, item_id: str) -> bool:
    """这道题学生是否提交过作答（任何一次）：平台据此告诉适配器 answer_submitted，不信前端自报（lessons R1）"""
    return bool(await conn.fetchval(
        "SELECT EXISTS (SELECT 1 FROM platform.attempt WHERE student_id = $1::uuid AND subject = $2::platform.subject AND item_id = $3)",
        student, subject, item_id))
