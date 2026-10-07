"""诊断会话服务（spec 设计要点 1–3）：显式状态机，每一步幂等，可暂停、跨天续做。

- 开始 / 续做：同一学生同一学科最多一个未结束的会话（0027 唯一索引）；同一幂等键重放返回同一个会话；
  已有暂停的会话时“开始”即续做（不新建、不另占额度）。
- 每一步：出题 → 探针（可跳过；由 BFF 以学生身份写 insight.confidence_probe，把 probe_id 交给这里记到步骤）→
  作答（经学习服务既有的 grade 写入流程调阿鸦验证器，证据、作答、判分在那里落库并幂等）→ 按规则选下一步。
- 判分只来自验证器（规则 3）：这里只把 grade_result 的 ID 记到步骤上，对错只在复算选题时读 grade_result.verdict。
- 作答走 v1 写入路径（learning-internal v1 的 {request, ingest} 信封，与 /internal/v1/grade 同一流程）：channel = keypad，
  证据等级按 attributes 的默认推导（键盘 A），purpose 取缺省 practice（契约里还没有“诊断”这个用途，lessons 待人确认）。
- 一步的判分请求键 = 由（会话、序号、n）派生的 UUIDv4 形态的键：同一内容重放得到同一结果；上一次确定失败（例如验证器拒收）后换 n 重来；
  已成功过就不再判第二次（重放任一步不重复写证据）。
- 作答的幂等键记到步骤的 answer_key（安全审查 S5）：同键重放回原结果；键不同而内容相同也回原结果；键不同且内容不同 → 409 ANSWER_CONFLICT。
- 用时封顶（安全审查 S1）：记入的用时 = min(上报值, 暂停前已记的用时 + 现在 − max(出题时间, 最近一次续做时间))；
  判分前把封顶后的用时与作答键一起记到步骤上，重试时判分请求的指纹不变。作答表里的用时也是封顶值。
- 过程题、讲解题（process / explain）：最终答案同样由验证器判；书写原文由本服务记为一条 response_text 证据
  （学生原文，不评分，准入固定 pending_review），证据 ID 记到步骤的 work_evidence_id（审核台按它取原文，契约审查 B1），
  步骤作答后以 process_observation 进统一复核队列（0027）。
- 计量在 BFF（ADR 06）：会话记 usage_id；BFF 在第一道题判分成功后结算，放弃时只有还没答过题才释放。本服务不预留、不结算。
- 不算掌握度、不产生对学生 / 家长可见的结论（规则 11）：返回给 BFF 的只有题面、序号、进度与状态。
"""
from __future__ import annotations

import json
import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone

import asyncpg

from .. import store as ingest_store
from ..attributes import V0
from ..errors import IngestError
from ..service import IngestService
from . import store
from .engine import (ALGORITHM_VERSION, Event, HistoryMismatch, InvalidTransition, Params, State, derive_state, trace,
                     transition)
from .presenter import ItemPresenter
from .source import SourceError

UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
UUID4_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
RUBRIC_VERSION = "aya-symbolic-ts@0.1.0"
# 阿鸦内容包的六段（adapters/aya/content/pack.json 的 curriculum）；真实题库接入后由 present_item 的内容版本给出
CONTENT_VERSION = {"region": "nanjing", "exam_year": 2027, "grade": 9, "textbook_version": "suke-2024", "subject": "aya",
                   "content_version": "0.1.0"}
MAX_FILL = 200
MAX_WORK = 20000
GRADE_VERSION = "1.0.0"           # 作答走 v1 写入路径（信封 {request, ingest}）
MAX_GRADE_TRIES = 8               # 同一步最多换几次判分键（每次都是上一次确定失败）
INGEST = {"channel": "keypad"}    # 诊断作答是键盘输入；证据等级按默认推导（A）


def v4_shaped(name: str) -> str:
    """由名字确定地派生一个 UUIDv4 形态的键（v1 契约要求幂等键与 attempt_id 是小写 UUIDv4）。"""
    b = bytearray(uuid.uuid5(store.STEP_NS, name).bytes)
    b[6] = (b[6] & 0x0F) | 0x40
    b[8] = (b[8] & 0x3F) | 0x80
    return str(uuid.UUID(bytes=bytes(b)))


def err(status: int, code: str, title: str, *, retryable: bool = False) -> IngestError:
    return IngestError(status, code, title, retryable=retryable)


NOT_FOUND = ("SESSION_NOT_FOUND", "diagnosis session not found")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class StartResult:
    session_id: str
    usage_id: str
    status: str
    created: bool
    replayed: bool


class DiagnosisService:
    def __init__(self, pool: asyncpg.Pool, ingest: IngestService, source_factory, presenter: ItemPresenter,
                 params: Params = Params(), clock: Callable[[], datetime] = utcnow) -> None:
        self.pool = pool
        self.ingest = ingest
        self.source_factory = source_factory   # async (grade) -> engine.Source
        self.presenter = presenter
        self.params = params
        # 服务时钟（测试注入）：开始诊断时按这一刻推算年级（platform.current_grade），记为会话建立时刻
        self.clock = clock

    # ---------- 工具 ----------
    def _params(self, s: store.SessionRow) -> Params:
        p = self.params
        return Params(item_limit=s.item_limit, time_budget_sec=s.time_budget_sec, process_count=p.process_count,
                      explain_count=p.explain_count, adaptive_step_sec=p.adaptive_step_sec, process_step_sec=p.process_step_sec)

    async def _load(self, grade: int):
        try:
            return await self.source_factory(grade)
        except SourceError:
            raise err(503, "KNOWLEDGE_UNAVAILABLE", "knowledge service is unavailable", retryable=True) from None

    async def _source(self, conn, s: store.SessionRow):
        # 年级按会话建立时刻推算：跨 9 月 1 日暂停后续做，选题规则复算出的仍是同一组步骤。
        # 届别在会话中途被更正时按新届别重算建立时刻的年级：不在 7–9 → 下面直接 409；仍在 7–9 但变了 → 选题规则用新年级复算
        # 已记下的步骤，对不上时由 HistoryMismatch 兜底 409；还没出题时会换成新年级的题目；新年级没有目标时复算判定已结束，
        # 会话标为 finished（已出未答的一步留在原处；tests/test_diagnosis_session.py 钉住这一现有行为）
        _, grade = await store.student_grade(conn, s.student_id, s.created_at)
        if grade is None:
            raise err(409, "SESSION_INCONSISTENT", "student grade at session start is no longer in range")
        src = await self._load(grade)
        if s.algorithm_version != ALGORITHM_VERSION or s.map_version != src.map_version:
            raise err(409, "SESSION_INCONSISTENT", "session was created with another algorithm or map version")
        return grade, src

    async def _advance(self, conn, s: store.SessionRow) -> None:
        """selecting：按规则补齐下一步（无题可测的步直接记下并继续），或结束会话。只在会话进行中时执行。"""
        if s.status != "active":
            return
        grade, src = await self._source(conn, s)
        for _ in range(200):
            rows = await store.steps(conn, s.id)
            try:
                t = trace(src, grade, [r.done() for r in rows], self._params(s))
            except HistoryMismatch:
                raise err(409, "SESSION_INCONSISTENT", "recorded steps do not match the selection rules") from None
            if t.pending_index is not None:
                return
            if t.finished:
                transition(State.SELECTING, Event.FINISH)
                await store.set_status(conn, s.id, "finished")
                s.status = "finished"
                return
            plan = t.next
            assert plan is not None
            if plan.reason != "no_item" and self.presenter.present(s.subject, plan.item_id or "") is None:
                raise err(501, "PRESENTER_UNAVAILABLE", "no item view for the selected item")
            await store.insert_step(conn, s.id, len(rows) + 1, plan.kind, plan.node_id, plan.item_id, plan.reason)
            if plan.reason != "no_item":
                return
        raise err(500, "INTERNAL", "selection did not converge", retryable=True)

    @staticmethod
    def _current(rows: list[store.StepRow]) -> store.StepRow | None:
        last = rows[-1] if rows else None
        return last if last is not None and last.status == "presented" else None

    async def _locked(self, conn, student: str, session_id: str) -> store.SessionRow:
        if not (UUID_RE.fullmatch(student) and UUID_RE.fullmatch(session_id)):
            raise err(404, *NOT_FOUND)
        s = await store.lock_session(conn, session_id, student)
        if s is None:
            raise err(404, *NOT_FOUND)
        return s

    @staticmethod
    def _check(state: State, event: Event) -> None:
        try:
            transition(state, event)
        except InvalidTransition:
            raise err(409, "INVALID_STATE", f"{event.value} is not allowed while the session is {state.value}") from None

    # ---------- 开始 / 续做 ----------
    async def start(self, student: str, subject: str, usage_id: str, key: str) -> StartResult:
        if subject != "aya":
            raise err(400, "SUBJECT_NOT_SUPPORTED", "adaptive diagnosis is only available for aya in the pilot")
        if not (UUID_RE.fullmatch(student) and UUID_RE.fullmatch(usage_id) and UUID4_RE.fullmatch(key)):
            raise err(400, "INVALID_REQUEST", "student_ref, usage_id and idempotency_key must be UUIDs (key: lowercase v4)")
        for _ in range(3):
            try:
                async with ingest_store.learning_tx(self.pool) as conn:
                    await conn.execute("SELECT pg_advisory_xact_lock(hashtextextended('platform.diagnosis:' || $1, 0))", student)
                    same = await store.session_by_key(conn, student, key)
                    if same is not None:
                        if same.usage_id != usage_id:
                            raise err(409, "IDEMPOTENCY_CONFLICT", "idempotency_key was used with a different usage_id")
                        return StartResult(same.id, same.usage_id, same.status, created=False, replayed=True)
                    s = await store.open_session(conn, student, subject, lock=True)
                    if s is not None:
                        if s.status == "paused":
                            self._check(State.PAUSED, Event.RESUME)
                        # 续做（暂停后，或进行中但隔了一段时间再来）：resumed_at 记为现在，之后的用时按墙钟封顶（S1）
                        await store.resume(conn, s.id)
                        s.status = "active"
                        await self._advance(conn, s)
                        return StartResult(s.id, s.usage_id, s.status, created=False, replayed=False)
                    at = self.clock()
                    found, grade = await store.student_grade(conn, student, at)
                    if not found:
                        raise err(404, "STUDENT_NOT_FOUND", "student_ref is not a registered student")
                    # 当前年级不在 7–9（已毕业）：没有可测的目标，与“这个年级暂无诊断”同一个 422
                    if grade is None:
                        raise err(422, "NO_TARGETS", "no diagnosis targets for this grade in the pilot map")
                    src = await self._load(grade)
                    if not src.targets(grade):
                        raise err(422, "NO_TARGETS", "no diagnosis targets for this grade in the pilot map")
                    s = await store.insert_session(conn, student=student, subject=subject, algorithm_version=ALGORITHM_VERSION,
                                                   map_version=src.map_version, usage_id=usage_id, key=key,
                                                   time_budget_sec=self.params.time_budget_sec, item_limit=self.params.item_limit,
                                                   created_at=at)
                    transition(State.CREATED, Event.SELECT)
                    await self._advance(conn, s)
                    return StartResult(s.id, s.usage_id, s.status, created=True, replayed=False)
            except asyncpg.UniqueViolationError:
                continue  # 并发开始：另一个请求刚建好，再查一次
            except asyncpg.PostgresError as e:
                raise ingest_store.db_error(e) from None
        raise err(503, "BUSY", "concurrent start, retry", retryable=True)

    # ---------- 当前题 ----------
    async def current(self, student: str, session_id: str) -> dict:
        async with ingest_store.learning_tx(self.pool) as conn:
            s = await self._locked(conn, student, session_id)
            rows = await store.steps(conn, s.id)
            cur = self._current(rows)
            if s.status == "active" and cur is not None and cur.kind == "adaptive":
                # 判分已成功、步骤还没推进（上一次请求在两段之间中断）：用已记录的判分推进
                if await self._recover(conn, s, cur):
                    rows = await store.steps(conn, s.id)
                    cur = self._current(rows)
            if s.status == "active" and cur is None:
                await self._advance(conn, s)
                rows = await store.steps(conn, s.id)
                cur = self._current(rows)
            return self._view(s, cur, rows)

    def _view(self, s: store.SessionRow, cur: store.StepRow | None, rows: list[store.StepRow]) -> dict:
        state = derive_state(s.status, cur.done() if cur else None, bool(cur and cur.probe_id))
        # process_tasks：本次是否出过过程题或讲解题（前端据此决定结束页说不说“老师会看你的过程”）；不是对学生的结论
        view: dict = {"session_id": s.id, "status": s.status, "state": state.value, "usage_id": s.usage_id,
                      "elapsed_sec": s.elapsed_sec, "time_budget_sec": s.time_budget_sec,
                      "item_count": s.item_count, "item_limit": s.item_limit,
                      "process_tasks": any(r.kind in ("process", "explain") and r.status == "answered" for r in rows), "step": None}
        if cur is not None and s.status in ("active", "paused"):
            item = self.presenter.present(s.subject, cur.item_id or "")
            if item is None:
                raise err(501, "PRESENTER_UNAVAILABLE", "no item view for the current item")
            view["step"] = {"seq": cur.seq, "kind": cur.kind, "subject": s.subject, "item_id": cur.item_id, "item": dict(item),
                            "probe_recorded": cur.probe_id is not None, "step_elapsed_sec": cur.elapsed_sec or 0}
        return view

    # ---------- 探针 ----------
    async def probe(self, student: str, session_id: str, seq: int, probe_id: str | None, probe_key: str | None = None) -> dict:
        if not UUID_RE.fullmatch(probe_id or probe_key or ""):
            raise err(400, "INVALID_REQUEST", "probe_id or probe_key must be a UUID")
        async with ingest_store.learning_tx(self.pool) as conn:
            s = await self._locked(conn, student, session_id)
            if probe_id is None:
                probe_id = await store.probe_by_key(conn, student, probe_key or "")
                if probe_id is None:
                    raise err(422, "PROBE_MISMATCH", "no probe with this key for this student")
            rows = await store.steps(conn, s.id)
            step = next((r for r in rows if r.seq == seq), None)
            if step is None:
                raise err(409, "STEP_NOT_CURRENT", "step is not the current one")
            if step.probe_id == probe_id:
                return {"status": "ok", "replayed": True}
            cur = self._current(rows)
            if cur is None or cur.seq != seq:
                raise err(409, "STEP_NOT_CURRENT", "step is not the current one")
            self._check(derive_state(s.status, cur.done(), cur.probe_id is not None), Event.PROBE)
            p = await store.probe(conn, probe_id)
            if (p is None or p["student_id"] != student or p["subject"] != s.subject or p["item_id"] != cur.item_id
                    or p["attempt_id"] is not None):
                raise err(422, "PROBE_MISMATCH", "probe does not belong to this student and item")
            await store.set_probe(conn, cur.id, probe_id)
            return {"status": "ok", "replayed": False}

    # ---------- 作答 ----------
    def _validate_answer(self, subject: str, item_id: str, kind: str, answer: object, work: object) -> dict:
        view = self.presenter.present(subject, item_id)
        if view is None:
            raise err(501, "PRESENTER_UNAVAILABLE", "no item view for the current item")
        fmt = view["format"]
        keys = [o["key"] for o in view.get("options", [])]
        if not isinstance(answer, dict) or len(answer) != 1:
            raise err(422, "INVALID_ANSWER", "answer must be {text} or {choice}")
        if fmt == "fill":
            t = answer.get("text")
            if not isinstance(t, str) or not t.strip() or len(t) > MAX_FILL:
                raise err(422, "INVALID_ANSWER", "fill item expects answer.text")
        else:
            c = answer.get("choice")
            if (not isinstance(c, list) or not c or len(set(c)) != len(c) or not all(isinstance(k, str) and k in keys for k in c)
                    or (fmt == "single" and len(c) != 1)):
                raise err(422, "INVALID_ANSWER", "choice item expects answer.choice with known option keys")
        if kind == "adaptive":
            if work is not None:
                raise err(422, "INVALID_ANSWER", "work is only for process and explain tasks")
        elif not isinstance(work, str) or not work.strip() or len(work) > MAX_WORK:
            raise err(422, "INVALID_ANSWER", "process and explain tasks need the written work")
        return answer

    def _grade_body(self, s: store.SessionRow, step: store.StepRow, answer: dict, occurred_at: str, elapsed: int, key: str) -> dict:
        """v1 信封：插件的 GradeRequest（契约 1.0.0）+ 平台侧作答上下文（键盘作答）。attempt_id 由判分键确定地派生，重试不变。"""
        request = {
            "context": {"request_id": str(uuid.uuid4()), "contract_version": GRADE_VERSION, "subject": s.subject, "student_ref": s.student_id,
                        "data_class": "L2", "usage_id": s.usage_id, "idempotency_key": key, "content_version": dict(CONTENT_VERSION)},
            "attempt": {"attempt_id": v4_shaped(f"{key}|attempt"), "item_ref": f"{s.subject}:{step.item_id}", "occurred_at": occurred_at,
                        "response": answer, "elapsed_sec": elapsed, "hint_level_used": 0},
            "rubric_version": RUBRIC_VERSION,
        }
        return {"request": request, "ingest": dict(INGEST)}

    @staticmethod
    def _grade_keys(s: store.SessionRow, seq: int) -> list[str]:
        """这一步第 0、1、2… 次判分请求的键（确定性：同一步在任何进程里都算出同一组键）。"""
        return [v4_shaped(f"diag|{s.id}|{seq}|grade|{n}") for n in range(MAX_GRADE_TRIES)]

    async def _occurred_at(self, conn, step: store.StepRow) -> str:
        # 作答发生时间：有探针取探针写入时间（2-L 关联作答的时间窗以它为准），否则取出题时间；重试时不变，请求指纹也就不变
        started = step.created_at
        if step.probe_id:
            p = await store.probe(conn, step.probe_id)
            started = p["created_at"] if p is not None else started
        return started.astimezone(timezone.utc).isoformat()

    async def _capped(self, conn, step: store.StepRow, reported: int) -> int:
        """用时封顶（安全审查 S1）：min(上报值, 暂停前已记的用时 + 现在 − max(出题时间, 最近一次续做时间))。"""
        wall, resumed = await store.wall_clock(conn, step.id)
        before = (step.elapsed_sec or 0) if resumed else 0
        return max(0, min(reported, before + wall, 7200))

    async def _same_content(self, conn, s: store.SessionRow, step: store.StepRow, answer: object, work: object) -> bool:
        """键不同的请求是不是同一内容：按已成功的判分请求的指纹比（用时取步骤上已定的值），过程题另比书写原文。"""
        if not isinstance(answer, dict):
            return False
        occurred_at = await self._occurred_at(conn, step)
        for r in await store.grade_requests(conn, self._grade_keys(s, step.seq)):
            if r["status"] != "succeeded":
                continue
            try:
                fp = self.ingest.envelope_fingerprint(
                    "grade", self._grade_body(s, step, answer, occurred_at, step.elapsed_sec or 0, r["idempotency_key"]), GRADE_VERSION)
            except IngestError:
                return False
            if fp != r["request_fingerprint"]:
                return False
            if step.kind in ("process", "explain") and step.work_evidence_id is not None:
                return work == await store.work_text(conn, step.work_evidence_id)
            return True
        return False

    async def _recover(self, conn, s: store.SessionRow, step: store.StepRow) -> bool:
        for r in await store.grade_requests(conn, self._grade_keys(s, step.seq)):
            if r["status"] == "succeeded":
                summary = json.loads(r["summary"])
                # 用时以已落库的作答为准（作答表记的是封顶后的用时）
                elapsed = await conn.fetchval("SELECT elapsed_sec FROM platform.attempt WHERE attempt_id = $1::uuid",
                                              summary["attempt_ids"][0])
                await self._finish_step(conn, s, step, summary, max(elapsed or 0, step.elapsed_sec or 0), None, None)
                return True
        return False

    def _replay(self, s: store.SessionRow, step: store.StepRow) -> dict:
        return {"attempt_id": step.attempt_id, "probe_id": step.probe_id, "status": s.status, "usage_id": s.usage_id, "replayed": True}

    async def answer(self, student: str, session_id: str, seq: int, answer: object, work: object, elapsed_sec: object,
                     key: object) -> dict:
        if not isinstance(elapsed_sec, int) or isinstance(elapsed_sec, bool) or not 0 <= elapsed_sec <= 7200:
            raise err(422, "INVALID_ANSWER", "elapsed_sec must be an integer between 0 and 7200")
        if not isinstance(key, str) or not UUID4_RE.fullmatch(key):
            raise err(400, "INVALID_REQUEST", "idempotency_key must be a lowercase UUIDv4")
        # ---- 第一段（事务内）：核对状态，定判分请求 ----
        async with ingest_store.learning_tx(self.pool) as conn:
            s = await self._locked(conn, student, session_id)
            rows = await store.steps(conn, s.id)
            step = next((r for r in rows if r.seq == seq), None)
            if step is not None and step.status == "answered":
                # 同键重放，或键不同而内容相同：回原结果；键不同且内容不同：冲突（S5）
                if step.answer_key == key or await self._same_content(conn, s, step, answer, work):
                    return self._replay(s, step)
                raise err(409, "ANSWER_CONFLICT", "this step was already answered with different content")
            cur = self._current(rows)
            if step is None or cur is None or cur.seq != seq:
                raise err(409, "STEP_NOT_CURRENT", "step is not the current one")
            self._check(derive_state(s.status, cur.done(), cur.probe_id is not None), Event.ANSWER)
            answer = self._validate_answer(s.subject, cur.item_id or "", cur.kind, answer, work)
            occurred_at = await self._occurred_at(conn, cur)
            keys = self._grade_keys(s, seq)
            prior = await store.grade_requests(conn, keys)
            recorded = None
            for r in prior:
                if r["status"] == "succeeded":
                    recorded = json.loads(r["summary"])
                    break
            body = None
            if recorded is not None:
                # 判分已成功、步骤没推进（上一次在两段之间中断）：同键或同内容才沿用已记录的结果
                elapsed = cur.elapsed_sec or 0
                if cur.answer_key != key and not await self._same_content(conn, s, cur, answer, work):
                    raise err(409, "ANSWER_CONFLICT", "this step was already graded with different content")
            else:
                if any(r["status"] == "pending" and r["leased"] for r in prior):
                    raise err(503, "BUSY", "the answer to this step is being graded", retryable=True)
                if cur.answer_key == key and cur.elapsed_sec is not None:
                    elapsed = cur.elapsed_sec  # 同一请求重试：用第一次定下的封顶用时（判分请求的指纹不变）
                else:
                    # 与库里写入的一致（只增不减）：暂停前已记的用时不会因为这次上报得少而变少
                    elapsed = max(await self._capped(conn, cur, elapsed_sec), cur.elapsed_sec or 0)
                    await store.set_answer_key(conn, cur.id, key, elapsed)
                n = len(prior)
                for i, r in enumerate(prior):
                    cand = self._grade_body(s, cur, answer, occurred_at, elapsed, r["idempotency_key"])
                    if self.ingest.envelope_fingerprint("grade", cand, GRADE_VERSION) == r["request_fingerprint"]:
                        n = i  # 同一内容上次没完成：沿用原键（学习服务幂等表接管或返回原结果）
                        break
                if n >= MAX_GRADE_TRIES:
                    raise err(422, "ANSWER_RETRIES_EXHAUSTED", "too many failed attempts to grade this step")
                body = self._grade_body(s, cur, answer, occurred_at, elapsed, keys[n])
        # ---- grading（事务外）：经既有 v1 写入流程调阿鸦验证器；证据、作答、判分在那里落库并幂等 ----
        replayed = recorded is not None  # 这一步已判过（上一次在两段之间中断）：沿用已记录的结果，不再判第二次
        if recorded is None:
            assert body is not None
            recorded, _ = await self.ingest.ingest("grade", body, GRADE_VERSION)
        # ---- 第二段（事务内）：记到步骤上，按规则选下一步 ----
        async with ingest_store.learning_tx(self.pool) as conn:
            s = await self._locked(conn, student, session_id)
            rows = await store.steps(conn, s.id)
            step = next(r for r in rows if r.seq == seq)
            if step.status == "answered":
                return self._replay(s, step)
            await self._finish_step(conn, s, step, recorded, elapsed, work if isinstance(work, str) else None, occurred_at)
            return {"attempt_id": recorded["attempt_ids"][0], "probe_id": step.probe_id, "status": s.status, "usage_id": s.usage_id,
                    "replayed": replayed}

    async def _finish_step(self, conn, s: store.SessionRow, step: store.StepRow, summary: dict, elapsed: int, work: str | None,
                           occurred_at: str | None) -> None:
        grade_id = summary["grade_result_id"]
        process = step.kind in ("process", "explain")
        if process and work is not None and occurred_at is not None:
            # 书写原文证据与它的引用要在步骤记为已作答之前写（0027：已作答的步骤不能再改）
            await self._record_work(conn, s, step, work, occurred_at)
        await store.answer_step(conn, step.id, grade_id, elapsed)
        await store.add_progress(conn, s.id, elapsed)
        s.elapsed_sec += elapsed
        s.item_count += 1
        if process:
            await store.enqueue_process_observation(conn, subject=s.subject, student=s.student_id, step_id=step.id)
        transition(State.GRADING, Event.GRADED)
        await self._advance(conn, s)

    async def _record_work(self, conn, s: store.SessionRow, step: store.StepRow, work: str, occurred_at: str) -> None:
        """过程题的书写原文：学生原文，客观记录，不评分；准入固定 pending_review（等复核看过，契约审查建议 1）。
        证据 ID 记到步骤的 work_evidence_id，审核台按它取原文（契约审查 B1）。"""
        key = None
        for r in await store.grade_requests(conn, self._grade_keys(s, step.seq)):
            if r["status"] == "succeeded":
                key = r["idempotency_key"]
        evidence_id = str(uuid.uuid5(store.STEP_NS, f"{step.id}|work"))
        e = {"evidence_id": evidence_id, "occurred_at": occurred_at, "kind": "response_text",
             "item_ref": f"{s.subject}:{step.item_id}", "payload": {"text": work}, "admission": "pending_review"}
        ctx = {"idempotency_key": key or v4_shaped(f"diag|{s.id}|{step.seq}|work"),
               "request_id": str(uuid.uuid5(store.STEP_NS, f"{step.id}|work-request"))}
        await ingest_store._insert_evidence(conn, e, student_id=s.student_id, subject=s.subject, method="grade", ctx=ctx, attrs=V0,
                                            evidence_kind=None)
        await store.set_work_evidence(conn, step.id, evidence_id)

    # ---------- 暂停 / 放弃 ----------
    async def pause(self, student: str, session_id: str, seq: int | None, step_elapsed_sec: object) -> dict:
        if step_elapsed_sec is not None and (not isinstance(step_elapsed_sec, int) or isinstance(step_elapsed_sec, bool)
                                             or not 0 <= step_elapsed_sec <= 7200):
            raise err(422, "INVALID_REQUEST", "step_elapsed_sec must be an integer between 0 and 7200")
        async with ingest_store.learning_tx(self.pool) as conn:
            s = await self._locked(conn, student, session_id)
            if s.status == "paused":
                return {"status": "paused", "replayed": True}
            rows = await store.steps(conn, s.id)
            cur = self._current(rows)
            self._check(derive_state(s.status, cur.done() if cur else None, bool(cur and cur.probe_id)), Event.PAUSE)
            if cur is not None and seq == cur.seq and step_elapsed_sec is not None:
                # 这道题已用的时间（按墙钟封顶，S1）：续做时接着算
                await store.set_partial_elapsed(conn, cur.id, await self._capped(conn, cur, step_elapsed_sec))
            await store.set_status(conn, s.id, "paused")
            return {"status": "paused", "replayed": False}

    async def abandon(self, student: str, session_id: str) -> dict:
        async with ingest_store.learning_tx(self.pool) as conn:
            s = await self._locked(conn, student, session_id)
            # item_count：已作答的题数。BFF 据此决定放弃时退不退额度（答过题就不退，安全审查 B1）
            if s.status == "abandoned":
                return {"status": "abandoned", "usage_id": s.usage_id, "item_count": s.item_count, "replayed": True}
            rows = await store.steps(conn, s.id)
            cur = self._current(rows)
            self._check(derive_state(s.status, cur.done() if cur else None, bool(cur and cur.probe_id)), Event.ABANDON)
            await store.set_status(conn, s.id, "abandoned")
            return {"status": "abandoned", "usage_id": s.usage_id, "item_count": s.item_count, "replayed": False}

    # ---------- 内部：断点候选（给 KGE-04 / SIE-02b；不经 BFF，不对学生、家长展示） ----------
    async def break_points(self, session_id: str) -> dict:
        async with ingest_store.learning_tx(self.pool) as conn:
            r = await conn.fetchrow("SELECT student_id::text FROM platform.diagnosis_session WHERE id = $1::uuid", session_id)
            if r is None:
                raise err(404, *NOT_FOUND)
            s = await store.lock_session(conn, session_id, r[0])
            grade, src = await self._source(conn, s)
            rows = await store.steps(conn, s.id)
            t = trace(src, grade, [x.done() for x in rows], self._params(s))
            return {"algorithm_version": s.algorithm_version, "map_version": s.map_version, "break_points": t.break_points,
                    "status": dict(sorted(t.status.items()))}

