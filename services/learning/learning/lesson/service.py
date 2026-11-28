"""讲评课会话服务（specs/stage-lesson-session 第二节第 1、4、5 条，PR ③b-4）。

打开讲评课（open）：
- 讲评课的节点与题由这里按讲评课目录补全（catalog.py，适配器 list_lessons；specs/lesson-catalog PR B）：目录里没有这节课 →
  422 LESSON_NOT_AVAILABLE，不建会话、不调 teach；调用方只给讲评课引用（过渡期的可选节点与题已删，带了是 400 请求不合格）；
- BFF 已按学生身份预留一次 lesson_whiteboard（teach，数量 1），把 usage_id 与预留幂等键交给这里；
- 建会话：库里的守卫（0064）核对这笔预留（usage_id、幂等键、学生、teach、lesson_whiteboard、未过期），对不上 → 409
  IDEMPOTENCY_KEY_SEALED（不调适配器）；同一个预留键再来按幂等重放处理（同一个会话）；
- 调适配器 teach(mode: lesson, lesson_ref)（插件契约 1.1.0 候选版），取回讲解 DSL 与台词稿；
- 校验（不合格不下发）：响应按契约 TeachResponse 校验；讲解 DSL 与台词稿按 packages/stage 冻结的 0.3.0 schema 校验，再做配对核对
  （每个 say 有台词、每句台词都被 say 引用、角色与讲话人一致、不带预制答疑）；前端播放前另用 packages/stage 的 migrateStageDsl /
  migrateStageScript 再读一次（③b-5）。学习服务是 Python，读不了 TS 的入口，所以这里是同一份 schema 加同一组配对规则，见 lessons；
- 合格：会话改为 open，签发讲评课票据（ticket.py），返回讲解 DSL、台词稿、票据与过期时间；BFF 结算这一次；
- 适配器确定拒绝（404 / 422 / 400）或结果不合格：会话改为 failed，回确定的失败，BFF 释放预留；暂时失败：会话留在 opening，回 503，
  BFF 用同一个键重试。
课内提问（ask）：
- 票据验签（学生一致、未过期）→ 库里 platform.lesson_session_ask 放行（会话已下发、未过期、预留仍有效、没到上限；
  同一个提问键再来是重放）→ 调适配器 teach(question)，usage_id 用会话的那一笔，幂等键按（会话，第几问）派生，重放拿同一结果；
- 超过上限、适配器 422 MODE_NOT_SUPPORTED（没有这条答疑，lessons Q7）都按“不答”返回 200（outcome = no_answer），不扣次；
- 每问都不扣次，只累计会话里的提问次数。
不作证据（spec 第二节第 5 条）：提问与回答都不进证据、掌握与判断，不落库、不进日志。answer_submitted 由平台按作答记录判定，不信前端自报。
"""
from __future__ import annotations

import functools
import json
import re
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

import asyncpg
import httpx
from jsonschema import Draft202012Validator

from ..adapter_client import call_adapter
from ..config import AdapterTarget
from ..contract import plugin_contract
from ..errors import IngestError
from . import store
from . import ticket as T
from .catalog import LessonCatalog

CONTRACT = "1.1.0"
DSL_VERSION = "0.3.0"
TIMEOUT = 10.0
# 只有阿鸦有讲评课（规格第三节：道法、语文另起）；人设随学科，由平台指定（人设变更是必停项）
SUBJECTS = {"aya": {"persona_id": "aya", "prompt_version": "1.1.0"}}
# 预制答疑记 1 级（specs/stage-point-to-ask-answer lessons Q5）：平台给的上限是 1
ALLOWED_HINT_LEVEL = 1
INTENTS = frozenset({"why_step", "what_is", "where_from"})
ANCHOR_KINDS = frozenset({"element", "slot", "step"})
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
UUID4_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
REF_RE = re.compile(r"^([a-z]+):([A-Za-z0-9][A-Za-z0-9_.\-]{0,63})$")
STAGE_ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
KEY_NS = uuid.UUID("7c3f5e1a-0b64-4b64-8b64-000000000064")
SCHEMAS = Path(__file__).resolve().parents[4] / "packages" / "stage" / "schema"
# 适配器的这些拒绝是确定的（这节课打不开 / 这一问没有回答），其余按暂时失败
DEFINITE = frozenset({400, 404, 409, 422})


def err(status: int, code: str, title: str, *, retryable: bool = False) -> IngestError:
    return IngestError(status, code, title, retryable=retryable)


def derived_key(name: str) -> str:
    """由名字确定地派生一个 UUIDv4 形态的键（契约要求幂等键是小写 UUIDv4）"""
    b = bytearray(uuid.uuid5(KEY_NS, name).bytes)
    b[6] = (b[6] & 0x0F) | 0x40
    b[8] = (b[8] & 0x3F) | 0x80
    return str(uuid.UUID(bytes=bytes(b)))


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


@functools.lru_cache(maxsize=2)
def _schema(name: str) -> Draft202012Validator:
    doc = json.loads((SCHEMAS / f"{name}.schema.json").read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(doc)
    return Draft202012Validator(doc)


def check_lesson(dsl: object, script: object) -> bool:
    """讲解 DSL 与台词稿：packages/stage 的 0.3.0 schema，加上 validateStagePair 的配对规则（不带预制答疑）。"""
    if not isinstance(dsl, dict) or not isinstance(script, dict):
        return False
    if next(_schema(f"stage-dsl-{DSL_VERSION}").iter_errors(dsl), None) is not None:
        return False
    if next(_schema(f"stage-script-{DSL_VERSION}").iter_errors(script), None) is not None:
        return False
    if dsl.get("dsl_version") != DSL_VERSION or script.get("dsl_version") != DSL_VERSION or "answers" in script:
        return False
    steps = dsl.get("steps") or []
    if len({s["id"] for s in steps}) != len(steps):
        return False
    speakers: dict[str, str] = {}
    speaker = "none"
    for s in steps:
        for a in s.get("actions") or []:
            if a.get("type") == "speaker":
                speaker = a.get("role")
        if "say" in s:
            if s["say"] in speakers:
                return False
            speakers[s["say"]] = speaker
    lines = script.get("lines") or []
    seen: set[str] = set()
    for line in lines:
        lid = line.get("id")
        if lid in seen or lid not in speakers or speakers[lid] != line.get("role"):
            return False
        seen.add(lid)
    return seen == set(speakers) and (not lines or dsl.get("narrated") is True)


def _ref(v: object, subject: str) -> str | None:
    """'aya:XXX' → 'XXX'（学科前缀必须与请求学科一致）"""
    m = REF_RE.fullmatch(v) if isinstance(v, str) else None
    return m.group(2) if m and m.group(1) == subject else None


def check_question(q: object) -> dict:
    """提问对象（前端第一阶段的结构化提问，不含讲评课引用与 DSL 版本：由票据给出）"""
    if not isinstance(q, dict) or set(q) != {"cursor", "anchor", "intent"}:
        raise err(400, "INVALID_REQUEST", "question must have exactly cursor, anchor and intent")
    c, a, i = q["cursor"], q["anchor"], q["intent"]
    if not (isinstance(c, int) and not isinstance(c, bool) and 1 <= c <= 100):
        raise err(400, "INVALID_REQUEST", "bad cursor")
    if not (isinstance(a, dict) and set(a) == {"kind", "id"} and a["kind"] in ANCHOR_KINDS
            and isinstance(a["id"], str) and STAGE_ID_RE.fullmatch(a["id"])):
        raise err(400, "INVALID_REQUEST", "bad anchor")
    if i not in INTENTS:
        raise err(400, "INVALID_REQUEST", "bad intent")
    return {"cursor": c, "anchor": {"kind": a["kind"], "id": a["id"]}, "intent": i}


class LessonService:
    def __init__(self, pool: asyncpg.Pool, client: httpx.AsyncClient, adapters: dict[str, AdapterTarget], ticket_secret: str,
                 clock: Callable[[], datetime] = utcnow, timeout: float = TIMEOUT, catalog: LessonCatalog | None = None) -> None:
        plugin_contract(CONTRACT)  # 候选开关没开时启动即失败（与 present_item 同一口径）
        if len(ticket_secret) < 32:
            raise ValueError("lesson ticket secret must be at least 32 characters")
        self.pool = pool
        self.client = client
        self.adapters = adapters
        self.secret = ticket_secret
        self.clock = clock
        self.timeout = timeout
        self.catalog = catalog if catalog is not None else LessonCatalog(client, adapters, timeout=timeout)

    def _target(self, subject: str) -> AdapterTarget:
        target = self.adapters.get(subject)
        if target is None:
            raise err(501, "LESSON_UNAVAILABLE", "no adapter configured for this subject")
        return target

    def _request(self, *, subject: str, student: str, usage_id: str, key: str, request_id: str, skill_node: str,
                 item_ref: str | None, submitted: bool, extra: dict) -> dict:
        req = {"context": {"request_id": request_id, "contract_version": CONTRACT, "subject": subject, "student_ref": student,
                           "data_class": "L2", "usage_id": usage_id, "idempotency_key": key},
               "mode": "lesson", "skill_node": skill_node, "allowed_hint_level": ALLOWED_HINT_LEVEL,
               "persona": dict(SUBJECTS[subject]), "answer_submitted": submitted, **extra}
        if item_ref is not None:
            req["item_ref"] = item_ref
        if plugin_contract(CONTRACT).validate("TeachRequest", req):
            raise err(400, "INVALID_REQUEST", "lesson request does not match the plugin contract")
        return req

    async def _submitted(self, conn, student: str, subject: str, item_ref: str | None) -> bool:
        item = _ref(item_ref, subject) if item_ref is not None else None
        return False if item is None else await store.submitted(conn, student, subject, item)

    async def _fail(self, session: str) -> None:
        async with store.tx(self.pool) as conn:
            row = await conn.fetchrow("SELECT status FROM platform.lesson_session WHERE id = $1::uuid FOR UPDATE", session)
            if row is not None and row["status"] == "opening":
                await store.set_status(conn, session, "failed")

    # ---------- 打开讲评课 ----------
    async def open(self, *, student: str, subject: str, lesson_id: str, lesson_version: str, usage_id: str, key: str) -> tuple[dict, bool]:
        """返回（响应体，是否幂等重放）。调用方只给讲评课引用；节点与题由目录补全（specs/lesson-catalog）"""
        if subject not in SUBJECTS:
            raise err(400, "SUBJECT_NOT_SUPPORTED", "lessons are only available for aya in the pilot")
        if not (UUID_RE.fullmatch(student) and UUID_RE.fullmatch(usage_id) and UUID4_RE.fullmatch(key)
                and T.LESSON_ID_RE.fullmatch(lesson_id) and T.SEMVER_RE.fullmatch(lesson_version)):
            raise err(400, "INVALID_REQUEST", "bad field values")
        entry = await self.catalog.find(subject, lesson_id, lesson_version)  # 目录暂时取不到 → 503（BFF 用同一个键重试）
        if entry is None:
            raise err(422, "LESSON_NOT_AVAILABLE", "this lesson could not be opened")
        skill_node, item_ref = entry.skill_node, entry.item_ref
        lesson_ref = {"lesson_id": lesson_id, "lesson_version": lesson_version}
        # 建会话之前先按契约核一遍请求（键用占位）：不合格的请求不留下 opening 的会话
        self._request(subject=subject, student=student, usage_id=usage_id, key=derived_key("probe"), request_id=str(uuid.uuid4()),
                      skill_node=skill_node, item_ref=item_ref, submitted=False, extra={"lesson_ref": lesson_ref})
        async with store.tx(self.pool) as conn:
            s = await store.find(conn, student, key)
            replayed = s is not None
            if s is None:
                try:
                    s = await store.insert(conn, student=student, subject=subject, lesson_id=lesson_id, lesson_version=lesson_version,
                                           usage_id=usage_id, key=key)
                except store.NoReservation:
                    raise err(409, "IDEMPOTENCY_KEY_SEALED", "idempotency_key has no valid lesson reservation") from None
            elif (s.subject, s.lesson_id, s.lesson_version, s.usage_id) != (subject, lesson_id, lesson_version, usage_id):
                raise err(409, "IDEMPOTENCY_CONFLICT", "idempotency_key was used to open another lesson")
            if s.status == "failed":
                raise err(422, "LESSON_NOT_AVAILABLE", "this lesson could not be opened")
            submitted = await self._submitted(conn, student, subject, item_ref)
        target = self._target(subject)
        req = self._request(subject=subject, student=student, usage_id=s.usage_id, key=derived_key(f"open:{s.id}:{int(submitted)}"),
                            request_id=derived_key(f"open-request:{s.id}:{int(submitted)}"), skill_node=skill_node,
                            item_ref=item_ref, submitted=submitted,
                            extra={"lesson_ref": lesson_ref})
        try:
            data = await call_adapter(self.client, target, "teach", req, self.timeout)
        except IngestError as e:
            if e.status in DEFINITE and not e.retryable:
                await self._fail(s.id)
                raise err(422, "LESSON_NOT_AVAILABLE", "this lesson could not be opened") from None
            raise err(503, "UPSTREAM_UNAVAILABLE", "lesson service is temporarily unavailable", retryable=True) from None
        seg = self._lesson_segment(data)
        if seg is None:
            await self._fail(s.id)
            raise err(502, "ADAPTER_RESULT_REJECTED", "adapter lesson does not match the contract or the stage schema")
        async with store.tx(self.pool) as conn:
            row = await store.get(conn, s.id, student)
            if row is None:
                raise err(404, "SESSION_NOT_FOUND", "lesson session not found")
            if row.status == "opening":
                if self.clock() >= row.expires_at:
                    await store.set_status(conn, s.id, "failed")
                    row.status = "failed"
                else:
                    await store.set_status(conn, s.id, "open")
                    row.status = "open"
        if row.status != "open":
            raise err(422, "LESSON_NOT_AVAILABLE", "this lesson could not be opened")
        now = self.clock()
        tk = T.LessonTicket(student=student, session=s.id, subject=subject, lesson_id=lesson_id, lesson_version=lesson_version,
                            skill_node=skill_node, item_ref=item_ref, dsl_version=DSL_VERSION, expires_at=int(row.expires_at.timestamp()), issued_at=int(now.timestamp()))
        out = {"session_id": s.id, "usage_id": s.usage_id, "dsl_version": DSL_VERSION, "dsl": seg["dsl"], "script": seg["script"],
               "ticket": T.issue(self.secret, tk), "expires_at": row.expires_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
               "question_limit": row.question_limit, "question_count": row.question_count}
        return out, replayed

    @staticmethod
    def _lesson_segment(data: dict) -> dict | None:
        contract = plugin_contract(CONTRACT)
        if contract.validate("TeachResponse", data) or data.get("contract_version") != CONTRACT:
            return None
        segs = (data.get("result") or {}).get("segments") or []
        if len(segs) != 1:
            return None
        seg = segs[0]
        if seg.get("type") != "whiteboard_dsl" or seg.get("dsl_version") != DSL_VERSION:
            return None
        if not check_lesson(seg.get("dsl"), seg.get("script")):
            return None
        return {"dsl": seg["dsl"], "script": seg["script"]}

    # ---------- 课内提问 ----------
    async def ask(self, *, student: str, ticket: object, question: object, ask_key: str) -> tuple[dict, bool]:
        """返回（响应体，是否幂等重放）"""
        if not (UUID_RE.fullmatch(student) and UUID4_RE.fullmatch(ask_key)):
            raise err(400, "INVALID_REQUEST", "bad field values")
        try:
            t = T.verify(self.secret, ticket, now=self.clock())
        except T.TicketError as e:
            if e.code == "TICKET_EXPIRED":
                raise err(410, "TICKET_EXPIRED", "the lesson has expired; open it again") from None
            raise err(422, "TICKET_INVALID", "lesson ticket is invalid") from None
        if t.student != student:
            raise err(422, "TICKET_INVALID", "lesson ticket is invalid")
        q = check_question(question)
        lesson_ref = {"lesson_id": t.lesson_id, "lesson_version": t.lesson_version}
        question_body = {"lesson_ref": lesson_ref, "dsl_version": t.dsl_version, **q}
        async with store.tx(self.pool) as conn:
            s = await store.get(conn, t.session, student)
            if s is None:
                raise err(404, "SESSION_NOT_FOUND", "lesson session not found")
            skill_node, item_ref = t.skill_node, t.item_ref
            # 先按契约核一遍请求（键用占位），不合格不占提问次数
            self._request(subject=s.subject, student=student, usage_id=s.usage_id, key=derived_key("probe"), request_id=str(uuid.uuid4()),
                          skill_node=skill_node, item_ref=item_ref, submitted=False, extra={"question": question_body})
            outcome, seq = await store.ask(conn, s.id, student, ask_key)
            submitted = await self._submitted(conn, student, s.subject, item_ref)
            after = await store.get(conn, s.id, student)
        remaining = max(0, after.question_limit - after.question_count) if after else 0
        if outcome == "none":
            raise err(404, "SESSION_NOT_FOUND", "lesson session not found")
        if outcome == "not_open":
            raise err(409, "LESSON_NOT_OPEN", "this lesson is not open")
        if outcome == "expired":
            raise err(410, "TICKET_EXPIRED", "the lesson has expired; open it again")
        if outcome == "unpaid":
            raise err(409, "IDEMPOTENCY_KEY_SEALED", "the lesson reservation is no longer valid")
        if outcome == "limit":
            return {"outcome": "no_answer", "reason": "limit", "question_seq": None, "questions_left": 0}, False
        replayed = outcome == "replay"
        req = self._request(subject=s.subject, student=student, usage_id=s.usage_id, key=derived_key(f"ask:{s.id}:{seq}"),
                            request_id=derived_key(f"ask-request:{s.id}:{seq}"), skill_node=skill_node, item_ref=item_ref,
                            submitted=submitted, extra={"question": question_body})
        try:
            data = await call_adapter(self.client, self._target(s.subject), "teach", req, self.timeout)
        except IngestError as e:
            if e.status == 422 and e.upstream_code == "MODE_NOT_SUPPORTED":
                return {"outcome": "no_answer", "reason": "not_available", "question_seq": seq, "questions_left": remaining}, replayed
            if e.status in DEFINITE and not e.retryable:
                raise err(422, "QUESTION_NOT_ANSWERABLE", "this question cannot be answered for this lesson") from None
            raise err(503, "UPSTREAM_UNAVAILABLE", "lesson service is temporarily unavailable", retryable=True) from None
        text = self._answer_text(data)
        if text is None:
            raise err(502, "ADAPTER_RESULT_REJECTED", "adapter answer does not match the contract")
        return {"outcome": "answered", "text": text, "question_seq": seq, "questions_left": remaining}, replayed

    @staticmethod
    def _answer_text(data: dict) -> str | None:
        if plugin_contract(CONTRACT).validate("TeachResponse", data) or data.get("contract_version") != CONTRACT:
            return None
        segs = (data.get("result") or {}).get("segments") or []
        if len(segs) != 1 or segs[0].get("type") != "text" or not isinstance(segs[0].get("text"), str) or not segs[0]["text"].strip():
            return None
        return segs[0]["text"]
