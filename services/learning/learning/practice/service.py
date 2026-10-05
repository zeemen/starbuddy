"""再练服务：列表（queue）与出题（next）（spec 第 1、2、4 节）；作答见 answer.py。

- 不写任何表：条目与再练状态每次读时算出；票据不落库。对 BFF 只回单元、状态、轮次、时间与题面，
  不回掌握、置信度、证据 ID、错因（规则 3、11）。
- 选题：知识服务 item.similar（必要时 item.search 补足）→ 平台过滤（同节点同层次、未做过、未受限、已登记已准入）→
  客观题（验证器或确定性 Rubric 判分）→ 不同题目族优先 → generate_variation（插件 API v1.0，非消耗类，候选版开关签字前只在测试里开）→ 核对每道题都在交给它的那一档里、
  已登记、已准入，否则这一轮不可用（unavailable，不外露）→ 签发票据。
- 日志与计数只有状态、原因编码与耗时，不记学生 ID、作答与题面（规则 8）。
"""
from __future__ import annotations

import json
import logging
import re
import uuid
from collections import Counter
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Callable

import asyncpg
import httpx

from ..adapter_client import call_adapter
from ..config import AdapterTarget
from ..contract import UnsupportedContractVersion, plugin_contract
from ..diagnosis.source import SourceError
from ..errors import IngestError
from ..service import IngestService
from . import queue as Q
from . import select as S
from . import store
from . import ticket as T
from .config import PracticeConfig
from .presenter import PracticePresenter
from .sources import KnowledgeCandidates, RequestMismatch

log = logging.getLogger("starbuddy.learning.practice")
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
CONTRACT = "1.0.0"
VARIATION_TIMEOUT = 5.0


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def err(status: int, code: str, title: str, *, retryable: bool = False) -> IngestError:
    return IngestError(status, code, title, retryable=retryable)


def iso(t: datetime | None) -> str | None:
    return None if t is None else t.astimezone(timezone.utc).isoformat()


@dataclass
class PracticeService:
    pool: asyncpg.Pool
    ingest: IngestService
    client: httpx.AsyncClient
    cfg: PracticeConfig
    adapters: dict[str, AdapterTarget]
    ticket_secret: str
    presenter: PracticePresenter | None = None
    knowledge: KnowledgeCandidates | None = None
    clock: Callable[[], datetime] = utcnow
    stats: Counter = None  # type: ignore[assignment]
    # 作答串行锁（会话级咨询锁）专用的小连接池（审查 B2）：锁连接要在整个判分期间握着，放在业务池里会和 ingest 争连接，
    # 并发作答数达到业务池上限时互相等死。None = 与业务池相同（只供不并发的单测用；app 一律给独立的锁池）
    lock_pool: asyncpg.Pool | None = None

    def __post_init__(self) -> None:
        if self.stats is None:
            self.stats = Counter()
        if self.lock_pool is None:
            self.lock_pool = self.pool

    def objective(self, subject: str) -> Callable[[str], bool] | None:
        """选题只留客观题（审查 S7）：题面提供者登记的判分方式是验证器或确定性 Rubric。没有题面提供者时不按判分方式过滤。"""
        p = self.presenter
        if p is None:
            return None
        return lambda item_id: p.grading(subject, item_id) in S.OBJECTIVE_GRADING

    # ---------- 工具 ----------
    def check_subject(self, student: str, subject: str) -> None:
        if not isinstance(student, str) or not UUID_RE.fullmatch(student):
            raise err(400, "INVALID_REQUEST", "student_ref must be a lowercase UUID")
        if subject not in self.cfg.subjects_enabled:
            raise err(400, "SUBJECT_NOT_SUPPORTED", "practice is not enabled for this subject")

    async def snapshot(self, student: str, subject: str, now: datetime) -> store.Snapshot:
        async with store.tx(self.pool) as conn:
            return await store.load(conn, student, subject, self.cfg, now)

    def entries(self, snap: store.Snapshot, now: datetime, *, capped: bool = True) -> list[Q.Entry]:
        return Q.build(snap.attempts, snap.hint_tasks, snap.retest_due, self.cfg, now, capped=capped)

    def view(self, student: str, subject: str, e: Q.Entry) -> dict:
        """对 BFF 的条目视图：只有单元、来源、状态、轮次、时间与原题引用。"""
        return {"entry_id": Q.entry_id(student, subject, e.unit), "node_id": e.unit.node_id, "cognitive_level": e.unit.cognitive_level,
                "source": e.source, "status": e.status, "round": e.round, "max_rounds": self.cfg.max_rounds,
                "source_item_ref": f"{subject}:{e.source_item_id}" if e.source_item_id else None,
                "next_round_at": iso(e.next_round_at), "retest_due": e.retest_due, "same_family_only": e.same_family_only,
                "empty_reason": e.empty_reason}

    # ---------- 列表 ----------
    async def queue(self, student: str, subject: str) -> dict:
        self.check_subject(student, subject)
        now = self.clock()
        async with store.tx(self.pool) as conn:
            snap = await store.load(conn, student, subject, self.cfg, now)
            entries = self.entries(snap, now)
            due = [e for e in entries if e.status == "due"]
            cands, admission = await store.unit_candidates(conn, subject, snap.map_id, {e.unit for e in due})
        objective = self.objective(subject)
        out = []
        for e in entries:
            if e.status == "due" and e.source_item_id is None:
                # hint_followup 找不到提示后做对的那道题：不调适配器，这一轮不可用（审查 S5）
                e = replace(e, status="unavailable", empty_reason=S.NO_SOURCE_ITEM)
            elif e.status == "due":
                f = S.filter_candidates(cands, e.unit, source_item_id=e.source_item_id, exclude_item_ids=snap.recent_items,
                                        admission=admission, objective=objective)
                if f.empty_reason is not None:
                    e = replace(e, status="unavailable", empty_reason=f.empty_reason)
                else:
                    e = replace(e, same_family_only=S.tier(f.kept, snap.used_families(e.unit),
                                                           prefer_new_family=self.cfg.prefer_new_family).same_family_only)
            out.append(e)
        self.stats.update(f"queue_{e.status}" for e in out)
        log.info(json.dumps({"event": "practice_queue", "subject": subject, "counts": dict(Q.counts(out))}, ensure_ascii=False))
        return {"subject": subject, "entries": [self.view(student, subject, e) for e in out]}

    # ---------- 出题 ----------
    def _unavailable(self, reason: str) -> dict:
        self.stats[f"unavailable_{reason}"] += 1
        log.info(json.dumps({"event": "practice_unavailable", "reason": reason}, ensure_ascii=False))
        return {"status": "unavailable", "empty_reason": reason}

    def require_presenter(self) -> PracticePresenter:
        if self.presenter is None:
            raise err(501, "PRACTICE_UNAVAILABLE", "no item presenter is configured (present_item is not signed off yet)")
        return self.presenter

    async def next(self, student: str, subject: str, eid: str) -> dict:
        self.check_subject(student, subject)
        presenter = self.require_presenter()
        if self.knowledge is None:
            raise err(501, "PRACTICE_UNAVAILABLE", "knowledge service is not configured")
        target = self.adapters.get(subject)
        if target is None:
            raise err(501, "PRACTICE_UNAVAILABLE", "no adapter is configured for this subject")
        if not isinstance(eid, str) or not UUID_RE.fullmatch(eid):
            raise err(404, "ENTRY_NOT_FOUND", "practice entry not found")
        now = self.clock()
        snap = await self.snapshot(student, subject, now)
        entry = Q.find(self.entries(snap, now, capped=False), student, subject, eid)
        if entry is None:
            raise err(404, "ENTRY_NOT_FOUND", "practice entry not found")
        if entry.status != "due":
            raise err(409, "PRACTICE_NOT_DUE", "this practice entry is not open for a new round")
        unit = entry.unit
        used = snap.used_families(unit)
        source_item_id = entry.source_item_id
        if source_item_id is None:
            # hint_followup 找不到提示后做对的那道题：不调知识服务与适配器，这一轮不可用（审查 S5）
            return self._unavailable(S.NO_SOURCE_ITEM)
        objective = self.objective(subject)

        async def admitted(cs: list[S.Candidate]) -> dict[str, str]:
            async with store.tx(self.pool) as conn:
                return await store.admissions(conn, subject, (c.item_id for c in cs))

        def filtered(cs: list[S.Candidate], adm: dict[str, str]) -> S.Filtered:
            return S.filter_candidates(cs, unit, source_item_id=source_item_id, exclude_item_ids=snap.recent_items, admission=adm,
                                       objective=objective)
        try:
            cands = await self.knowledge.similar(subject, source_item_id, snap.map_version)
            admission = await admitted(cands)
            # 过滤后（未做过、已准入、不受限、客观题）的不同题目族不够时，再用 item.search 补足
            if S.needs_search(filtered(cands, admission).kept, unit, used, self.cfg.variants_per_round):
                cands = S.merge(cands, await self.knowledge.search(subject, unit, snap.map_version))
                admission = await admitted(cands)
        except SourceError:
            raise err(503, "KNOWLEDGE_UNAVAILABLE", "knowledge service is unavailable", retryable=True) from None
        except RequestMismatch:
            raise err(500, "INTERNAL", "assembled knowledge request does not match the contract", retryable=True) from None
        category = None
        if entry.source == "wrong":
            async with store.tx(self.pool) as conn:
                code = await store.error_category(conn, student, subject, entry.anchor)
            category = self._error_category(subject, code)
        f = filtered(cands, admission)
        if f.empty_reason is not None:
            return self._unavailable(f.empty_reason)
        tier = S.tier(f.kept, used, prefer_new_family=self.cfg.prefer_new_family)
        picks = await self._variation(target, student, subject, entry, tier, snap.recent_items, category, admission)
        if isinstance(picks, str):
            return self._unavailable(picks)
        chosen = next(((c, v) for c in picks if (v := presenter.present(subject, c.item_id)) is not None), None)
        if chosen is None or presenter.rubric_version(subject, chosen[0].item_id) is None:
            raise err(501, "PRESENTER_UNAVAILABLE", "no item view for the selected item")
        c, item_view = chosen
        t = T.Ticket(student=student, subject=subject, node_id=unit.node_id, cognitive_level=unit.cognitive_level, anchor=entry.anchor,
                     item_id=c.item_id, round=entry.round, issued_at=T.issued(now))
        self.stats["ticket_issued"] += 1
        return {"status": "ready", "entry_id": eid, "round": entry.round, "item_ref": c.item_ref, "item": dict(item_view),
                "ticket": T.issue(self.ticket_secret, t), "expires_at": iso(T.expires_at(t, self.cfg.ticket_ttl_minutes)),
                "same_family_only": tier.same_family_only}

    def _error_category(self, subject: str, code: str | None) -> str | None:
        """错因编码只在存在、属于该科、且不属于敏感类时传给适配器。错因分类表读不到（文件缺失、格式坏）时不传、计数，
        不让出题失败（审查 S4：错因只是选题偏好，不是必需输入）。"""
        if code is None:
            return None
        try:
            codes = S.taxonomy_codes()
        except Exception as e:  # noqa: BLE001 分类表读不到就不传错因
            self.stats["taxonomy_unavailable"] += 1
            log.warning(json.dumps({"event": "practice_taxonomy_unavailable", "error_class": type(e).__name__[:48]}, ensure_ascii=False))
            return None
        return S.error_category_for_request(subject, code, codes)

    async def _variation(self, target: AdapterTarget, student: str, subject: str, entry: Q.Entry, tier: S.Tier, recent: frozenset[str],
                         category: str | None, admission: dict[str, str]) -> list[S.Candidate] | str:
        """调 generate_variation 并核对；返回合格的题（按适配器给的顺序），或不可用原因。"""
        try:
            contract = plugin_contract(CONTRACT)
        except UnsupportedContractVersion:
            raise err(501, "PRACTICE_UNAVAILABLE", "plugin contract 1.0.0 is not enabled on this service") from None
        ctx = {"request_id": str(uuid.uuid4()), "contract_version": CONTRACT, "subject": subject, "student_ref": student,
               "data_class": "L2", "idempotency_key": str(uuid.uuid4())}
        req = S.variation_request(context=ctx, subject=subject, source_item_id=entry.source_item_id, unit=entry.unit,
                                  offered=tier.offered, exclude_item_ids=recent, count=self.cfg.variants_per_round,
                                  error_category=category)
        if contract.validate("GenerateVariationRequest", req):
            raise err(500, "INTERNAL", "assembled variation request does not match the contract", retryable=True)
        try:
            data = await call_adapter(self.client, target, "generate_variation", req, VARIATION_TIMEOUT)
        except IngestError as e:
            # 超时、连不上、适配器自己说可重试：结果未知，503 让 BFF 稍后再试；其余（没实现、拒绝请求）这一轮不可用
            if e.retryable:
                raise err(503, "UPSTREAM_UNAVAILABLE", "adapter is unavailable", retryable=True) from None
            return S.ADAPTER_UNAVAILABLE
        if data.get("contract_version") != CONTRACT or contract.validate("GenerateVariationResponse", data):
            return S.ADAPTER_REJECTED
        result = data["result"]
        if not result["candidates"]:
            reason = result.get("empty_reason")
            return reason if reason in S.ADAPTER_EMPTY else S.ADAPTER_REJECTED
        picks = S.check_picks(result["candidates"], tier.offered, admission)
        if picks is None or not picks:
            return S.ADAPTER_REJECTED
        return picks

    def ticket_of(self, token: object, now: datetime, *, allow_expired: bool = False) -> T.Ticket:
        try:
            return T.verify(self.ticket_secret, token, now=now, ttl_minutes=self.cfg.ticket_ttl_minutes)
        except T.TicketError as e:
            if e.code == "TICKET_EXPIRED" and allow_expired:
                return T.verify(self.ticket_secret, token, now=now, ttl_minutes=10 ** 7)
            self.stats[e.code.lower()] += 1                     # ticket_invalid / ticket_expired（审查 S11）
            if e.code == "TICKET_EXPIRED":
                raise err(410, "TICKET_EXPIRED", "practice ticket has expired") from None
            raise err(403, "TICKET_INVALID", "practice ticket is not valid") from None
