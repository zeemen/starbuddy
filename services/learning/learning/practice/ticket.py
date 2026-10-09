"""再练票据（spec 第 2 节第 5 步、第 3 节）：HMAC-SHA256 签名，不落库，密钥来自环境变量。

内容：学生、学科、单元（节点、层次）、条目锚点（第一道错题的 attempt_id 或 hint_followup 任务 ID）、题目、轮次、签发时间。
格式 pt1.<载荷 base64url>.<签名 base64url>；签名覆盖 "pt1." + 载荷段。验签先比签名（常量时间），再看过期：
伪造、篡改、格式不对一律 TICKET_INVALID；签名对但过了 ticket_ttl_minutes 才是 TICKET_EXPIRED。
签发时间晚于现在超过容差（时钟回拨或伪造）也按无效处理。票据里没有作答、答案与任何推断。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone

PREFIX = "pt1"
MAX_LEN = 1024
MIN_SECRET = 32
CLOCK_SKEW = timedelta(minutes=5)
FIELDS = ("student", "subject", "node_id", "cognitive_level", "anchor", "item_id", "round", "issued_at")
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
B64_RE = re.compile(r"^[A-Za-z0-9_-]+$")


class TicketError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code            # TICKET_INVALID / TICKET_EXPIRED


@dataclass(frozen=True)
class Ticket:
    student: str
    subject: str
    node_id: str
    cognitive_level: str
    anchor: str
    item_id: str
    round: int
    issued_at: int                   # Unix 秒（服务器时间）


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _unb64(s: str) -> bytes:
    if not B64_RE.fullmatch(s):
        raise ValueError("not base64url")
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _key(secret: str) -> bytes:
    if not isinstance(secret, str) or len(secret) < MIN_SECRET:
        raise ValueError("ticket secret must be at least 32 characters")
    return secret.encode()


def _sign(secret: str, head: str) -> str:
    return _b64(hmac.new(_key(secret), head.encode(), hashlib.sha256).digest())


def issue(secret: str, t: Ticket, *, prefix: str = PREFIX) -> str:
    """prefix 区分票据用途（再练 pt1、复测与验证题 rt1，KGE-05）：前缀在签名覆盖的范围内，一种用途的票据改不成另一种。"""
    payload = json.dumps(asdict(t), sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    head = f"{prefix}.{_b64(payload.encode())}"
    return f"{head}.{_sign(secret, head)}"


def issued(now: datetime) -> int:
    return int(now.astimezone(timezone.utc).timestamp())


def _valid_payload(d: object) -> bool:
    if not isinstance(d, dict) or set(d) != set(FIELDS):
        return False
    strs = ("student", "subject", "node_id", "cognitive_level", "anchor", "item_id")
    return (all(isinstance(d[k], str) and 0 < len(d[k]) <= 128 for k in strs) and UUID_RE.fullmatch(d["student"]) is not None
            and isinstance(d["round"], int) and not isinstance(d["round"], bool) and 1 <= d["round"] <= 100
            and isinstance(d["issued_at"], int) and not isinstance(d["issued_at"], bool) and d["issued_at"] > 0)


def verify(secret: str, token: object, *, now: datetime, ttl_minutes: int, prefix: str = PREFIX) -> Ticket:
    if not isinstance(token, str) or len(token) > MAX_LEN:
        raise TicketError("TICKET_INVALID")
    parts = token.split(".")
    if len(parts) != 3 or parts[0] != prefix:
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
    t = Ticket(**d)
    at = datetime.fromtimestamp(t.issued_at, tz=timezone.utc)
    if at > now + CLOCK_SKEW:
        raise TicketError("TICKET_INVALID")
    if now > at + timedelta(minutes=ttl_minutes):
        raise TicketError("TICKET_EXPIRED")
    return t


def expires_at(t: Ticket, ttl_minutes: int) -> datetime:
    return datetime.fromtimestamp(t.issued_at, tz=timezone.utc) + timedelta(minutes=ttl_minutes)
