"""模考限时状态机与会话票据（PLT-18 spec 范围第 2 条）。纯函数：时间一律由调用方注入（带时区的 datetime），不读系统时钟。

状态：created → in_progress → submitted | expired。
- 截止时间只在 start 时由服务端算：开始时间 + 限时 + 配置宽限；之后任何输入（含客户端时间）都不改它；
- 作答：now < 截止才收（截止时刻本身即拒绝）；同题重复作答以最后一次（截止前）为准；交卷后、过期后一律拒绝；
- 到截止未交卷：advance / submit 把会话推进为 expired（结束时刻记为截止时刻），已作答部分照常交给汇总计分；
- 时钟回拨：任何操作的 now 早于会话最后一次被处理的时刻 → CLOCK_ROLLBACK，会话不动（防止用回拨的时间续命）。
作答只记调用方给的不透明作答引用（answer_ref，如作答 ID），不记作答原文、答案与判分。

票据：会话快照序列化为 me1.<kid>.<载荷 base64url>.<签名 base64url>，HMAC-SHA256 签名覆盖 "me1.<kid>." + 载荷段（沿用再练 pt1 的写法）。
密钥由调用方注入（SigningKeys：当前一把 + 可选的上一把，按 kid 选，用于轮换；模考密钥单独配置，不与 BFF、再练复用）。
验签先比签名（常量时间），再校验载荷形状与时序一致性，再核对绑定（卷 ID、卷版本、学科、学生），最后看过期：
伪造、篡改、未知 kid、格式不对、时序不一致、换卷、换学生一律 TICKET_INVALID；签名对但过了 ticket_ttl_minutes 才是 TICKET_EXPIRED。
密钥本身不合规（太短、kid 不合规或重复）是部署配置错误 SigningKeyError（不是票据错误），路由层应映射为 500。
**只签名不加密**：载荷可被读到，里面有学生内部 ID、会话 ID、各题 answer_ref 与作答时刻。第二步之前票据不得交给客户端，
也不得进 URL、日志与分析事件；第二步改为 AEAD 封装（沿用 BFF sbp1）或只给客户端服务端会话 ID。

**重放（第二步上线的前置条件，第一步不防）**：票据是自包含快照，纯函数无法知道它是否已被用过。已知两种攻击：
1. 重复开始：保存 created 状态的票据，开考后再拿它 start 一次，得到新的开始时间与更晚的截止（重置截止）；
2. 回滚重答：交卷后拿交卷前 in_progress 状态的旧票据继续作答、再次交卷（覆盖已交的答卷、重复交卷）。
第二步建会话表时解决：会话表唯一约束（每个会话只能开始一次、只能结束一次）+ 状态版本号（票据带版本，只认最新版本，只能单调前进）。
测试里两条 xfail(strict=True) 用例记录这两种攻击（tests/test_mock_exam_session.py）。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone

from .config import MockExamConfig, MockExamConfigError
from .manifest import Paper

STATES = ("created", "in_progress", "submitted", "expired")
PREFIX = "me1"
MAX_LEN = 16384
MIN_KEY = 32
CLOCK_SKEW_MS = 5 * 60 * 1000
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
KID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
B64_RE = re.compile(r"^[A-Za-z0-9_-]+$")
FIELDS = ("session_id", "paper_id", "paper_version", "subject", "student", "state", "created_ms", "started_ms", "deadline_ms",
          "ended_ms", "seen_ms", "answers", "issued_ms")


class SessionError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code   # NOT_STARTED / ALREADY_STARTED / ALREADY_SUBMITTED / EXPIRED / DEADLINE_PASSED / ITEM_NOT_IN_PAPER /
                           # CLOCK_ROLLBACK / INVALID_TIME / INVALID_INPUT / PAPER_MISMATCH


class TicketError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code   # TICKET_INVALID / TICKET_EXPIRED


class SigningKeyError(MockExamConfigError):
    """签名密钥配置不合规（太短、kid 不合规或重复）：部署配置错误，路由层应映射为 500，不按票据无效（4xx）处理。"""


@dataclass(frozen=True)
class SigningKeys:
    """模考票据密钥：当前一把用于签发与验签，上一把（可选）只用于验签，供轮换期间已签发的票据。单独配置，不与 BFF、再练复用。"""
    current_kid: str
    current: bytes
    previous_kid: str | None = None
    previous: bytes | None = None

    def __post_init__(self) -> None:
        pairs = [(self.current_kid, self.current)]
        if self.previous_kid is not None or self.previous is not None:
            pairs.append((self.previous_kid, self.previous))
        for kid, key in pairs:
            if not isinstance(kid, str) or KID_RE.fullmatch(kid) is None:
                raise SigningKeyError("kid 不合规")
            if not isinstance(key, (bytes, bytearray)) or len(key) < MIN_KEY:
                raise SigningKeyError("签名密钥至少 32 字节")
        if len(pairs) == 2 and (self.previous_kid == self.current_kid or bytes(self.previous) == bytes(self.current)):
            raise SigningKeyError("上一把密钥与当前密钥的 kid 或内容相同")

    def lookup(self, kid: str) -> bytes | None:
        if kid == self.current_kid:
            return bytes(self.current)
        if self.previous_kid is not None and kid == self.previous_kid:
            return bytes(self.previous)  # type: ignore[arg-type]
        return None


@dataclass(frozen=True)
class Answer:
    item_id: str
    answer_ref: str
    at_ms: int


@dataclass(frozen=True)
class Session:
    session_id: str
    paper_id: str
    paper_version: str
    subject: str
    student: str
    state: str
    created_ms: int
    started_ms: int | None
    deadline_ms: int | None
    ended_ms: int | None
    seen_ms: int                  # 最后一次被处理的时刻（时钟回拨判断用）
    answers: tuple[Answer, ...]   # 每题最多一条（最后一次），按 item_id 排序

    @property
    def timed_out(self) -> bool:
        return self.state == "expired"

    def answer_of(self, item_id: str) -> Answer | None:
        for a in self.answers:
            if a.item_id == item_id:
                return a
        return None


def ms(now: datetime) -> int:
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise SessionError("INVALID_TIME")
    return (now - EPOCH) // timedelta(milliseconds=1)


def _seen(s: Session, now: datetime) -> int:
    t = ms(now)
    if t < s.seen_ms:
        raise SessionError("CLOCK_ROLLBACK")
    return t


def create(paper: Paper, *, session_id: str, student: str, now: datetime) -> Session:
    if not isinstance(session_id, str) or UUID_RE.fullmatch(session_id) is None or not isinstance(student, str) \
            or UUID_RE.fullmatch(student) is None:
        raise SessionError("INVALID_INPUT")
    t = ms(now)
    return Session(session_id, paper.paper_id, paper.version, paper.subject, student, "created", t, None, None, None, t, ())


def _check_paper(s: Session, paper: Paper) -> None:
    if (s.paper_id, s.paper_version, s.subject) != (paper.paper_id, paper.version, paper.subject):
        raise SessionError("PAPER_MISMATCH")


def deadline_for(started_ms: int, paper: Paper, cfg: MockExamConfig) -> int:
    return started_ms + paper.duration_minutes * 60_000 + cfg.grace_seconds * 1000


def start(s: Session, paper: Paper, cfg: MockExamConfig, *, now: datetime) -> Session:
    _check_paper(s, paper)
    t = _seen(s, now)
    if s.state != "created":
        raise SessionError("ALREADY_STARTED")
    return replace(s, state="in_progress", started_ms=t, deadline_ms=deadline_for(t, paper, cfg), seen_ms=t)


def advance(s: Session, *, now: datetime) -> Session:
    """到截止未交卷 → expired（结束时刻 = 截止时刻）；其余状态原样返回（seen_ms 前移）。"""
    t = _seen(s, now)
    if s.state == "in_progress" and s.deadline_ms is not None and t >= s.deadline_ms:
        return replace(s, state="expired", ended_ms=s.deadline_ms, seen_ms=t)
    return replace(s, seen_ms=t)


def _require_open(s: Session, t: int) -> None:
    if s.state == "created":
        raise SessionError("NOT_STARTED")
    if s.state == "submitted":
        raise SessionError("ALREADY_SUBMITTED")
    if s.state == "expired":
        raise SessionError("EXPIRED")
    if s.deadline_ms is None or t >= s.deadline_ms:
        raise SessionError("DEADLINE_PASSED")


def answer(s: Session, paper: Paper, *, item_id: str, answer_ref: str, now: datetime) -> Session:
    _check_paper(s, paper)
    t = _seen(s, now)
    _require_open(s, t)
    if paper.item(item_id) is None:
        raise SessionError("ITEM_NOT_IN_PAPER")
    if not isinstance(answer_ref, str) or REF_RE.fullmatch(answer_ref) is None:
        raise SessionError("INVALID_INPUT")
    kept = [a for a in s.answers if a.item_id != item_id]
    kept.append(Answer(item_id, answer_ref, t))
    return replace(s, answers=tuple(sorted(kept, key=lambda a: a.item_id)), seen_ms=t)


def submit(s: Session, *, now: datetime) -> Session:
    """截止前交卷 → submitted；截止及之后交卷 → expired（不报错，已作答部分照常计分）。"""
    t = _seen(s, now)
    if s.state == "in_progress" and s.deadline_ms is not None and t >= s.deadline_ms:
        return replace(s, state="expired", ended_ms=s.deadline_ms, seen_ms=t)
    _require_open(s, t)
    return replace(s, state="submitted", ended_ms=t, seen_ms=t)


# ---------- 票据 ----------
def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _unb64(x: str) -> bytes:
    if not B64_RE.fullmatch(x):
        raise ValueError("not base64url")
    return base64.urlsafe_b64decode(x + "=" * (-len(x) % 4))


def _sign(key: bytes, head: str) -> str:
    return _b64(hmac.new(key, head.encode(), hashlib.sha256).digest())


def _payload(s: Session, issued_ms: int) -> dict:
    return {"session_id": s.session_id, "paper_id": s.paper_id, "paper_version": s.paper_version, "subject": s.subject,
            "student": s.student, "state": s.state, "created_ms": s.created_ms, "started_ms": s.started_ms,
            "deadline_ms": s.deadline_ms, "ended_ms": s.ended_ms, "seen_ms": s.seen_ms,
            "answers": [[a.item_id, a.answer_ref, a.at_ms] for a in s.answers], "issued_ms": issued_ms}


def seal(keys: SigningKeys, s: Session, *, now: datetime) -> str:
    if not isinstance(keys, SigningKeys):
        raise SigningKeyError("签名密钥必须是 SigningKeys")
    payload = json.dumps(_payload(s, ms(now)), sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    head = f"{PREFIX}.{keys.current_kid}.{_b64(payload.encode())}"
    return f"{head}.{_sign(bytes(keys.current), head)}"


def _int(v: object) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and v >= 0


def _opt_int(v: object) -> bool:
    return v is None or _int(v)


def _timeline_ok(s: Session) -> bool:
    """时序一致性：created ≤ started ≤ deadline；started ≤ 各题作答时刻 < deadline；交卷 started ≤ ended < deadline，
    过期 ended = deadline；seen 不早于以上任何时刻。"""
    marks = [s.created_ms]
    if s.started_ms is not None and s.deadline_ms is not None:
        if not s.created_ms <= s.started_ms <= s.deadline_ms:
            return False
        for a in s.answers:
            if not s.started_ms <= a.at_ms < s.deadline_ms:
                return False
            marks.append(a.at_ms)
        marks.append(s.started_ms)
        if s.state == "submitted" and not (s.ended_ms is not None and s.started_ms <= s.ended_ms < s.deadline_ms):
            return False
        if s.state == "expired" and s.ended_ms != s.deadline_ms:
            return False
        if s.ended_ms is not None:
            marks.append(s.ended_ms)
    return all(s.seen_ms >= m for m in marks)


def _from_payload(d: object) -> tuple[Session, int] | None:
    if not isinstance(d, dict) or set(d) != set(FIELDS):
        return None
    strs = ("session_id", "paper_id", "paper_version", "subject", "student")
    if not all(isinstance(d[k], str) and 0 < len(d[k]) <= 128 for k in strs):
        return None
    if UUID_RE.fullmatch(d["student"]) is None or UUID_RE.fullmatch(d["session_id"]) is None or d["state"] not in STATES:
        return None
    if not (_int(d["created_ms"]) and _int(d["seen_ms"]) and _int(d["issued_ms"])
            and _opt_int(d["started_ms"]) and _opt_int(d["deadline_ms"]) and _opt_int(d["ended_ms"])):
        return None
    st = d["state"]
    started = st != "created"
    if started != (d["started_ms"] is not None) or started != (d["deadline_ms"] is not None):
        return None
    if (st in ("submitted", "expired")) != (d["ended_ms"] is not None):
        return None
    if not isinstance(d["answers"], list):
        return None
    answers: list[Answer] = []
    for a in d["answers"]:
        if not (isinstance(a, list) and len(a) == 3 and isinstance(a[0], str) and isinstance(a[1], str) and _int(a[2])
                and REF_RE.fullmatch(a[0]) and REF_RE.fullmatch(a[1])):
            return None
        answers.append(Answer(a[0], a[1], a[2]))
    if len({a.item_id for a in answers}) != len(answers) or (answers and not started):
        return None
    s = Session(d["session_id"], d["paper_id"], d["paper_version"], d["subject"], d["student"], st, d["created_ms"],
                d["started_ms"], d["deadline_ms"], d["ended_ms"], d["seen_ms"], tuple(sorted(answers, key=lambda a: a.item_id)))
    if not _timeline_ok(s):
        return None
    return s, d["issued_ms"]


def unseal(keys: SigningKeys, ticket: object, *, paper: Paper, student: str, cfg: MockExamConfig, now: datetime) -> Session:
    if not isinstance(keys, SigningKeys):
        raise SigningKeyError("签名密钥必须是 SigningKeys")
    if not isinstance(ticket, str) or len(ticket) > MAX_LEN:
        raise TicketError("TICKET_INVALID")
    parts = ticket.split(".")
    if len(parts) != 4 or parts[0] != PREFIX:
        raise TicketError("TICKET_INVALID")
    key = keys.lookup(parts[1])
    if key is None:
        raise TicketError("TICKET_INVALID")
    head = f"{parts[0]}.{parts[1]}.{parts[2]}"
    if not hmac.compare_digest(_sign(key, head).encode(), parts[3].encode()):
        raise TicketError("TICKET_INVALID")
    try:
        got = _from_payload(json.loads(_unb64(parts[2])))
    except ValueError:
        raise TicketError("TICKET_INVALID") from None
    if got is None:
        raise TicketError("TICKET_INVALID")
    s, issued = got
    # 绑定：换卷（卷 ID、版本、学科）、换学生一律无效，与伪造同一个码
    if (s.paper_id, s.paper_version, s.subject) != (paper.paper_id, paper.version, paper.subject) or s.student != student \
            or any(paper.item(a.item_id) is None for a in s.answers):
        raise TicketError("TICKET_INVALID")
    t = ms(now)
    if issued > t + CLOCK_SKEW_MS:
        raise TicketError("TICKET_INVALID")
    if t > issued + cfg.ticket_ttl_minutes * 60_000:
        raise TicketError("TICKET_EXPIRED")
    return s
