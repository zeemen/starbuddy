"""复测与验证题服务：列表（queue）、出题（next）、作答（answer）。KGE-05 spec 范围第 1、2、5 条。

- 不写任何表：条目每次读时算出（retest_task 只读）；票据不落库。作答只经 IngestService.ingest("grade", …)（与再练同一条路径：
  幂等、封存、核对、落库与同步重算），用途 purpose 由服务端按任务原因写入（tasks.PURPOSE；签字前衰减 retest、其余 practice），请求里给了按 400 处理。
- 计次前的只读预检（check）与宵禁时只找回原结果（replay，不执行、不封存）供 BFF 用（审查意见 2）。
- 选题复用再练（PracticeService.pick）：参照题是这个单元最近一次作答的题，排除原题与 exclude_window_days 内做过的题，
  只出学生在这个单元没做过的题目族（同族重做 KGE-04 记 repeat_family，既不计入也不判复测轮，见 mastery/gates.py），
  没有就这一轮不可用（no_new_family），不出同族题、不生成新题。
- 票据：再练票据的同一套签发与验签（ticket.py，同一密钥），前缀 rt1（签名覆盖前缀，再练票据拿来复测作答、复测票据拿去再练都验不过）；
  条目锚点里带用途（"<用途>:<任务>"，用途为 practice 或 retest），签名覆盖，客户端改不了用途。
- 作答与再练共用同一把单元串行锁（同一学生同一单元的再练与复测作答互相串行），并沿用再练的“已提交键放行过期票据”（审查 H1）
  与“票据签发后这道题已作答过即 409 TICKET_USED”（审查 B1）。
- 对 BFF 只回单元、条目类别（复测 / 验证）、状态、题面与判分结论；不回掌握、置信度、证据 ID、错因、任务原因与轮次（规则 3、11）。
- 日志与计数只有状态、原因编码，不记学生 ID、作答与题面（规则 8）。
"""
from __future__ import annotations

import json
import logging
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable

from ..errors import IngestError
from ..present_item import present_view
from ..practice import answer as PA
from ..practice import queue as Q
from ..practice import select as S
from ..practice import store as P
from ..practice import ticket as T
from ..practice.service import UUID_RE, PracticeService, err, iso, utcnow
from . import store as RS
from . import tasks as K
from .config import RetestConfig

log = logging.getLogger("starbuddy.learning.retest")
PREFIX = "rt1"                         # 复测票据前缀（再练是 pt1）
INGEST_CHANNEL = "keypad"


@dataclass
class RetestService:
    practice: PracticeService
    cfg: RetestConfig
    clock: Callable[[], datetime] = utcnow
    stats: Counter = field(default_factory=Counter)

    # ---------- 工具 ----------
    def check_subject(self, student: str, subject: str) -> None:
        self.practice.check_subject(student, subject)
        if subject not in self.cfg.subjects_enabled:
            raise err(400, "SUBJECT_NOT_SUPPORTED", "retest is not enabled for this subject")

    async def snapshot(self, student: str, subject: str, now: datetime) -> RS.RetestSnapshot:
        async with P.tx(self.practice.pool) as conn:
            return await RS.load(conn, student, subject, self.practice.cfg, now, with_estimates=self.cfg.followup_enabled)

    def entries(self, rs: RS.RetestSnapshot, now: datetime, *, capped: bool = True) -> list[K.Entry]:
        return K.build(rs.tasks, rs.practice.attempts, rs.keys, rs.estimated, self.cfg, now, capped=capped)

    def view(self, student: str, subject: str, e: K.Entry, status: str, empty_reason: str | None) -> dict:
        """对 BFF 的条目视图：只有条目 ID、单元、类别（复测 / 验证）、状态与空原因编码。"""
        return {"entry_id": K.entry_id(student, subject, e.anchor), "node_id": e.unit.node_id, "cognitive_level": e.unit.cognitive_level,
                "kind": e.kind, "status": status, "empty_reason": empty_reason}

    # ---------- 列表 ----------
    async def queue(self, student: str, subject: str) -> dict:
        self.check_subject(student, subject)
        now = self.clock()
        async with P.tx(self.practice.pool) as conn:
            rs = await RS.load(conn, student, subject, self.practice.cfg, now, with_estimates=self.cfg.followup_enabled)
            entries = self.entries(rs, now)
            cands, admission = await P.unit_candidates(conn, subject, rs.practice.map_id, {e.unit for e in entries})
        objective = self.practice.objective(subject)
        out = []
        for e in entries:
            status, reason = "due", None
            if e.source_item_id is None:
                status, reason = "unavailable", S.NO_SOURCE_ITEM
            else:
                f = S.filter_candidates(cands, e.unit, source_item_id=e.source_item_id, exclude_item_ids=rs.practice.recent_items,
                                        admission=admission, objective=objective)
                if f.empty_reason is not None:
                    status, reason = "unavailable", f.empty_reason
                elif S.tier(f.kept, rs.practice.used_families(e.unit), prefer_new_family=True).same_family_only:
                    status, reason = "unavailable", S.NO_NEW_FAMILY
            out.append(self.view(student, subject, e, status, reason))
        self.stats.update(f"queue_{v['kind']}_{v['status']}" for v in out)
        log.info(json.dumps({"event": "retest_queue", "subject": subject, "counts": dict(Counter(f"{v['kind']}_{v['status']}" for v in out))},
                            ensure_ascii=False))
        return {"subject": subject, "entries": out}

    # ---------- 出题 ----------
    async def next(self, student: str, subject: str, eid: str) -> dict:
        self.check_subject(student, subject)
        presenter = self.practice.require_presenter()
        if self.practice.knowledge is None:
            raise err(501, "RETEST_UNAVAILABLE", "knowledge service is not configured")
        target = self.practice.adapters.get(subject)
        if target is None:
            raise err(501, "RETEST_UNAVAILABLE", "no adapter is configured for this subject")
        if not isinstance(eid, str) or not UUID_RE.fullmatch(eid):
            raise err(404, "ENTRY_NOT_FOUND", "retest entry not found")
        now = self.clock()
        rs = await self.snapshot(student, subject, now)
        e = K.find(self.entries(rs, now, capped=False), student, subject, eid)
        if e is None:
            raise err(404, "ENTRY_NOT_FOUND", "retest entry not found")
        if e.status != "due":
            raise err(409, "RETEST_NOT_DUE", "this retest entry is not open")
        # 交给再练选题的条目：来源不是 wrong（不传错因），参照题是这个单元最近一次作答的题
        qe = Q.Entry(e.unit, "retest", "due", 1, 0, e.anchor, e.source_item_id, None, e.due_at)
        picked = await self.practice.pick(student, subject, rs.practice, qe, presenter, target, require_new_family=True)
        if isinstance(picked, dict):
            self.stats[f"unavailable_{picked.get('empty_reason')}"] += 1
            return picked
        c, item_view, _tier = picked
        t = T.Ticket(student=student, subject=subject, node_id=e.unit.node_id, cognitive_level=e.unit.cognitive_level, anchor=e.anchor,
                     item_id=c.item_id, round=1, issued_at=T.issued(now))
        self.stats["ticket_issued"] += 1
        return {"status": "ready", "entry_id": eid, "kind": e.kind, "item_ref": c.item_ref, "item": item_view,
                "ticket": T.issue(self.practice.ticket_secret, t, prefix=PREFIX),
                "expires_at": iso(T.expires_at(t, self.cfg.ticket_ttl_minutes))}

    def ticket_of(self, token: object, now: datetime, *, allow_expired: bool = False) -> T.Ticket:
        secret = self.practice.ticket_secret
        try:
            t = T.verify(secret, token, now=now, ttl_minutes=self.cfg.ticket_ttl_minutes, prefix=PREFIX)
        except T.TicketError as e:
            if e.code == "TICKET_EXPIRED" and allow_expired:
                t = T.verify(secret, token, now=now, ttl_minutes=10 ** 7, prefix=PREFIX)
            else:
                self.stats[e.code.lower()] += 1                 # ticket_invalid / ticket_expired
                if e.code == "TICKET_EXPIRED":
                    raise err(410, "TICKET_EXPIRED", "retest ticket has expired") from None
                raise err(403, "TICKET_INVALID", "retest ticket is not valid") from None
        if K.purpose_of_anchor(t.anchor) is None:
            self.stats["ticket_invalid"] += 1
            raise err(403, "TICKET_INVALID", "retest ticket is not valid")
        return t

    # ---------- 作答 ----------
    async def _check_open(self, t: T.Ticket, student: str, subject: str, unit: Q.Unit, now: datetime) -> None:
        """（锁内、键未提交过）票据的条目仍是到期未做完的同一条，且这张票据的题签发后还没有被作答过（审查 B1）。"""
        rs = await self.snapshot(student, subject, now)
        cur = next((e for e in self.entries(rs, now, capped=False) if e.anchor == t.anchor), None)
        if cur is None or cur.status != "due" or cur.unit != unit:
            self.stats["answer_not_due"] += 1
            raise err(409, "RETEST_NOT_DUE", "this retest entry is no longer open")
        since = datetime.fromtimestamp(t.issued_at, tz=timezone.utc) - T.CLOCK_SKEW
        if any(a.item_id == t.item_id and a.recorded_at >= since for a in rs.practice.attempts):
            self.stats["ticket_used"] += 1
            raise err(409, "TICKET_USED", "this retest ticket has already been answered")

    async def answer(self, *, student: str, subject: str, ticket: object, answer: object, key: object,
                     usage_id: object) -> tuple[dict, bool]:
        """返回（摘要, 是否为重放）。"""
        self.check_subject(student, subject)
        if not isinstance(key, str) or not PA.UUID4_RE.fullmatch(key):
            raise err(400, "INVALID_REQUEST", "idempotency_key must be a lowercase UUIDv4")
        if not isinstance(usage_id, str) or not UUID_RE.fullmatch(usage_id):
            raise err(400, "INVALID_REQUEST", "usage_id must be a lowercase UUID")
        svc = self.practice
        presenter = svc.require_presenter()
        now = self.clock()
        # 同一学生的键已成功或正在执行（租约未过期）：放过过期票据，交给 ingest 的幂等处理拿回原结果（同再练，审查 H1）
        settled = PA._settled(await PA._key_state(svc, key), student)
        t = self.ticket_of(ticket, now, allow_expired=settled)
        if t.student != student or t.subject != subject:
            self.stats["ticket_invalid"] += 1
            raise err(403, "TICKET_INVALID", "retest ticket is not valid")
        purpose = K.purpose_of_anchor(t.anchor)
        view = await present_view(presenter, subject, t.item_id)
        rubric = presenter.rubric_version(subject, t.item_id)
        if view is None or rubric is None:
            raise err(501, "PRESENTER_UNAVAILABLE", "no item view for the ticket item")
        answer = PA.validate_answer(dict(view), answer)
        unit = Q.Unit(t.node_id, t.cognitive_level)
        # 与再练同一把单元锁（同一个锁名）：同一单元的再练与复测作答互相串行
        async with PA.unit_lock(svc, f"{student}|{subject}|{unit.node_id}|{unit.cognitive_level}"):
            if not PA._settled(await PA._key_state(svc, key), student):
                if settled:
                    self.ticket_of(ticket, self.clock())          # 原先放过了过期票据，但那次已失败或租约已过期：按常规验票
                await self._check_open(t, student, subject, unit, now)
            async with P.tx(svc.pool) as conn:
                cv = await P.content_version(conn, subject, t.item_id)
            if cv is None:
                raise err(422, "CONTENT_NOT_REGISTERED", "item has no registered content version")
            body = PA.grade_envelope(student=student, subject=subject, item_id=t.item_id, answer=answer,
                                     occurred_at=datetime.fromtimestamp(t.issued_at, tz=timezone.utc), key=key, usage_id=usage_id,
                                     rubric_version=rubric, content_version=cv)
            # 用途由服务端按票据锚点（签名覆盖）写入，映射只在 tasks.PURPOSE 一处（签字前：衰减 → retest，其余 → practice）
            body["ingest"] = {"channel": INGEST_CHANNEL, "purpose": purpose}
            # 作答 ID 由幂等键与锚点派生：读时据此认出“用本任务票据作答的题”（自述验证题道数），重试不变
            body["request"]["attempt"]["attempt_id"] = K.attempt_id_for(key, t.anchor)
            summary, replayed = await svc.ingest.ingest("grade", body, PA.GRADE_VERSION)
        out = await self._result(summary, student, subject, t)
        self.stats[f"answer_{out['verdict']}"] += 1
        if "pending_review" in out:
            self.stats["answer_pending_review"] += 1
        log.info(json.dumps({"event": "retest_answer", "subject": subject, "purpose": purpose, "verdict": out["verdict"],
                             "status": out["entry"]["status"], "pending_review": "pending_review" in out, "replayed": replayed},
                            ensure_ascii=False))
        return out, replayed

    async def _result(self, summary: dict, student: str, subject: str, t: T.Ticket) -> dict:
        """对 BFF 的作答结果：判分结论（grade 摘要的 verdict）、待复核标记、条目新状态（due / done）。没有掌握、置信度、证据 ID。"""
        after = self.clock()
        rs = await self.snapshot(student, subject, after)
        still = any(e.anchor == t.anchor and e.status == "due" for e in self.entries(rs, after, capped=False))
        entry = {"entry_id": K.entry_id(student, subject, t.anchor), "status": "due" if still else "done"}
        verdict = summary.get("verdict")
        if not isinstance(verdict, str):
            raise IngestError(500, "INTERNAL", "grade summary without verdict", retryable=True)
        out: dict = {"verdict": verdict, "entry": entry}
        if summary.get("human_review_required") is True:
            out["pending_review"] = True
        return out

    # ---------- 计次前的只读预检、宵禁时只找回原结果（审查意见 2） ----------
    async def check(self, *, student: str, subject: str, ticket: object) -> dict:
        """BFF 计次之前的只读预检：票据有效（未过期、属于这个学生与学科）、条目仍到期未做完、这张票据的题签发后没作答过。
        不写任何表、不调适配器；不通过时与作答同一组错误码（403 / 410 / 409），BFF 据此不计次。"""
        self.check_subject(student, subject)
        now = self.clock()
        t = self.ticket_of(ticket, now)
        if t.student != student or t.subject != subject:
            self.stats["ticket_invalid"] += 1
            raise err(403, "TICKET_INVALID", "retest ticket is not valid")
        await self._check_open(t, student, subject, Q.Unit(t.node_id, t.cognitive_level), now)
        self.stats["check_ok"] += 1
        return {"status": "ok"}

    async def replay(self, *, student: str, subject: str, ticket: object, key: object, usage_id: object) -> dict:
        """只找回原结果、不执行（宵禁时 BFF 用）：这张票据（可已过期，签名仍要对）的这个键已成功，就回与作答相同形状的结果；
        否则 404 REPLAY_NOT_FOUND。不写任何表、不封存（与 /internal/v1/replay 不同：之后白天同一张票据仍可正常作答）。
        键必须是用这张票据作答的（作答 ID 由键与锚点派生，核对一致），usage_id 也要对上。"""
        self.check_subject(student, subject)
        if not isinstance(key, str) or not PA.UUID4_RE.fullmatch(key):
            raise err(400, "INVALID_REQUEST", "idempotency_key must be a lowercase UUIDv4")
        if not isinstance(usage_id, str) or not UUID_RE.fullmatch(usage_id):
            raise err(400, "INVALID_REQUEST", "usage_id must be a lowercase UUID")
        t = self.ticket_of(ticket, self.clock(), allow_expired=True)
        if t.student != student or t.subject != subject:
            self.stats["ticket_invalid"] += 1
            raise err(403, "TICKET_INVALID", "retest ticket is not valid")
        async with P.tx(self.practice.pool) as conn:
            row = await RS.replay_row(conn, student, key)
        if row is None or row.usage_id not in (None, usage_id) or K.attempt_id_for(key, t.anchor) not in row.attempt_ids:
            raise err(404, "REPLAY_NOT_FOUND", "no recorded result for this ticket and key")
        self.stats["replay_found"] += 1
        return await self._result(row.summary, student, subject, t)
