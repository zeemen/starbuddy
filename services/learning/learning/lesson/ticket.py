"""讲评课票据：HMAC-SHA256 签名，不落库，密钥来自环境变量 LEARNING_LESSON_TICKET_SECRET（与再练票据同一套写法与编码，前缀不同）。

内容：学生、会话、学科、讲评课引用（lesson_id、lesson_version）、打开时的节点与题（skill_node、item_ref，课内提问原样带给适配器；
会话表不存它们）、下发的讲解 DSL 版本、会话过期时间、签发时间。
格式 lt1.<载荷 base64url>.<签名 base64url>；签名覆盖 "lt1." + 载荷段（前缀在签名范围内，改不成再练或复测票据）。
验签先比签名（常量时间），再看过期：伪造、篡改、格式不对一律 TICKET_INVALID；签名对但过了会话过期时间才是 TICKET_EXPIRED。
票据只是“这名学生打开过这节课”的凭据：放行与否仍以库里的会话为准（platform.lesson_session_ask 核对状态、有效期、预留与上限）。
票据里没有讲解文字、提问与回答。
"""
from __future__ import annotations

import hmac
import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone

from ..practice.ticket import CLOCK_SKEW, MAX_LEN, TicketError, _b64, _sign, _unb64

PREFIX = "lt1"
FIELDS = ("student", "session", "subject", "lesson_id", "lesson_version", "skill_node", "item_ref", "dsl_version", "expires_at",
          "issued_at")
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
LESSON_ID_RE = re.compile(r"^[a-z][a-z0-9_.\-]{0,63}$")
SEMVER_RE = re.compile(r"^\d{1,3}\.\d{1,3}\.\d{1,4}$")
REF_RE = re.compile(r"^[a-z]+:[A-Za-z0-9][A-Za-z0-9_.\-]{0,63}$")
SUBJECTS = frozenset({"aya", "zhixing", "wenli"})

__all__ = ["LessonTicket", "TicketError", "issue", "verify"]


@dataclass(frozen=True)
class LessonTicket:
    student: str
    session: str
    subject: str
    lesson_id: str
    lesson_version: str
    skill_node: str
    item_ref: str | None
    dsl_version: str
    expires_at: int                  # Unix 秒：会话过期时间（即那笔预留的过期时间）
    issued_at: int                   # Unix 秒（服务器时间）


def issue(secret: str, t: LessonTicket) -> str:
    payload = json.dumps(asdict(t), sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    head = f"{PREFIX}.{_b64(payload.encode())}"
    return f"{head}.{_sign(secret, head)}"


def _int(v: object) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and v > 0


def _valid_payload(d: object) -> bool:
    if not isinstance(d, dict) or set(d) != set(FIELDS):
        return False
    return (isinstance(d["student"], str) and UUID_RE.fullmatch(d["student"]) is not None
            and isinstance(d["session"], str) and UUID_RE.fullmatch(d["session"]) is not None
            and d["subject"] in SUBJECTS
            and isinstance(d["lesson_id"], str) and LESSON_ID_RE.fullmatch(d["lesson_id"]) is not None
            and isinstance(d["lesson_version"], str) and SEMVER_RE.fullmatch(d["lesson_version"]) is not None
            and isinstance(d["skill_node"], str) and REF_RE.fullmatch(d["skill_node"]) is not None
            and (d["item_ref"] is None or (isinstance(d["item_ref"], str) and REF_RE.fullmatch(d["item_ref"]) is not None))
            and isinstance(d["dsl_version"], str) and SEMVER_RE.fullmatch(d["dsl_version"]) is not None
            and _int(d["expires_at"]) and _int(d["issued_at"]) and d["expires_at"] > d["issued_at"])


def verify(secret: str, token: object, *, now: datetime) -> LessonTicket:
    if not isinstance(token, str) or len(token) > MAX_LEN:
        raise TicketError("TICKET_INVALID")
    parts = token.split(".")
    if len(parts) != 3 or parts[0] != PREFIX:
        raise TicketError("TICKET_INVALID")
    head = f"{parts[0]}.{parts[1]}"
    if not hmac.compare_digest(_sign(secret, head).encode(), parts[2].encode()):
        raise TicketError("TICKET_INVALID")
    try:
        d = json.loads(_unb64(parts[1]))
    except ValueError:
        raise TicketError("TICKET_INVALID") from None
    if not _valid_payload(d):
        raise TicketError("TICKET_INVALID")
    t = LessonTicket(**d)
    if datetime.fromtimestamp(t.issued_at, tz=timezone.utc) > now + CLOCK_SKEW:
        raise TicketError("TICKET_INVALID")
    if now.timestamp() >= t.expires_at:
        raise TicketError("TICKET_EXPIRED")
    return t
