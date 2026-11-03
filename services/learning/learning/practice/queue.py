"""错题条目：读时推导、闭环、轮次（spec 第 1 节）。纯函数，不连库。

单元 = 学生 × 学科 × 主节点 × 认知层次（与掌握单元同粒度；归属由读取层按作答时的地图版本打标映射到当前版本，同 KGE-04）。
- 错题：verdict ∈ {incorrect, partial}、不待复核、证据等级 A 或 B（C 级、没有等级的旧证据不进）、不在申诉中、
  输入通道 keypad 或 canvas、有主节点与层次的打标、记录时间在 lookback_days 之内；诊断里的错题按 include_diagnosis_attempts。
- 闭环：本单元最近一次错题之后，另一道题（不是本条目里任何一道错题）独立做对：correct、不待复核、证据等级 A 或 B、
  hint_level_used = 0、自报不是“猜的”。闭环只是再练状态，不是掌握结论，不写任何表。
- 轮次：条目开着时，之后同单元的每一道错题都是一轮再练做错（归入同一条目，不另开）。做过的轮数 = 错题数 − 1；
  做满 max_rounds 轮仍未闭环 → exhausted；否则第 k 轮在最近一次错题之后 round_delay_hours[k − 1] 小时开放。
- 闭环之后再出现错题，另开一个条目（同一单元只返回最近的那个）。新增做对的证据永远不会把已闭环的条目重新打开。
- hint_followup：开着的 retest_task(reason = hint_followup)（KGE-04 建，只读）作为同一类条目出现；
  它之后同单元另一道题独立做对，条目即闭环（与 KGE-04 关闭这个任务的口径一致，任务本身仍由重算关闭）。
时间一律按服务器记录时间（recorded_at）。
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Iterable, Mapping, Sequence

from .config import PracticeConfig

WRONG_VERDICTS = frozenset({"incorrect", "partial"})
CHANNELS = frozenset({"keypad", "canvas"})          # 拍照、语音不进（拍照错题是上传深度分析，另一项计量资源）
LEVELS = frozenset({"A", "B"})
APPEAL = "student_appeal"
STATUSES = ("due", "waiting", "closed", "exhausted", "unavailable")
SOURCES = ("wrong", "hint_followup")
ENTRY_NS = uuid.UUID("7c1f0b4e-5a2d-4e8b-9c3f-1d2e3f4a5b6c")

# 不成为错题的原因（观测与测试用；不对学生展示）
NO_GRADE, NOT_WRONG, PENDING_REVIEW, LEVEL, IN_APPEAL, CHANNEL, UNTAGGED, TOO_OLD, DIAGNOSIS = (
    "no_grade", "not_wrong", "pending_review", "level", "appeal", "channel", "untagged", "too_old", "diagnosis")


@dataclass(frozen=True)
class Unit:
    node_id: str
    cognitive_level: str


@dataclass(frozen=True)
class AttemptFact:
    """一次作答在再练判定里需要的事实（只有 ID、编码、数字与时间，不含作答内容）。"""
    attempt_id: str
    item_id: str
    recorded_at: datetime
    verdict: str | None
    human_review_required: bool
    evidence_level: str | None
    channel: str | None
    node_id: str | None              # 当前地图上的主节点；None = 未打标或不再归属
    cognitive_level: str | None
    hint_level: int | None
    self_report: str | None
    review_reasons: tuple[str, ...] = ()
    in_diagnosis: bool = False
    has_grade: bool = True

    @property
    def unit(self) -> Unit | None:
        return Unit(self.node_id, self.cognitive_level) if self.node_id and self.cognitive_level else None


def order_key(a: AttemptFact) -> tuple[datetime, str]:
    return (a.recorded_at, a.attempt_id)


def wrong_reason(a: AttemptFact, cfg: PracticeConfig, now: datetime) -> str | None:
    """None = 这次作答是错题；否则返回第一条不满足的条件。"""
    if not a.has_grade or a.verdict is None:
        return NO_GRADE
    if a.verdict not in WRONG_VERDICTS:
        return NOT_WRONG
    if a.human_review_required:
        return PENDING_REVIEW
    if APPEAL in a.review_reasons:
        return IN_APPEAL
    if a.evidence_level not in LEVELS:
        return LEVEL
    if a.channel not in CHANNELS:
        return CHANNEL
    if a.unit is None:
        return UNTAGGED
    if a.recorded_at < now - timedelta(days=cfg.lookback_days):
        return TOO_OLD
    if a.in_diagnosis and not cfg.include_diagnosis_attempts:
        return DIAGNOSIS
    return None


def independent_correct(a: AttemptFact) -> bool:
    """独立做对：correct、不待复核、不在申诉中、证据等级 A 或 B、没用提示（hint_level_used = 0）、自报不是“猜的”。"""
    return (a.has_grade and a.verdict == "correct" and not a.human_review_required and APPEAL not in a.review_reasons
            and a.evidence_level in LEVELS and a.hint_level == 0 and a.self_report != "guessed" and a.unit is not None)


@dataclass(frozen=True)
class Episode:
    """一个错题条目从第一次错到闭环（或至今）的经过。"""
    unit: Unit
    anchor: str                          # 第一道错题的 attempt_id（票据绑定它：闭环后再开的新条目不认旧票据）
    source_item_id: str                  # 原题（第一道错题）
    wrong_attempt_ids: tuple[str, ...]
    wrong_item_ids: frozenset[str]
    first_wrong_at: datetime
    last_wrong_at: datetime
    closed_at: datetime | None = None
    closing_attempt_id: str | None = None

    @property
    def rounds_done(self) -> int:
        return len(self.wrong_attempt_ids) - 1


def closes(a: AttemptFact, wrong_items: Iterable[str], after: datetime) -> bool:
    return independent_correct(a) and a.item_id not in set(wrong_items) and a.recorded_at >= after


def episodes(attempts: Iterable[AttemptFact], cfg: PracticeConfig, now: datetime) -> dict[Unit, Episode]:
    """每个单元最近的一个条目经过。与输入顺序无关（按记录时间、attempt_id 排序后逐条处理）。"""
    latest: dict[Unit, Episode] = {}
    for a in sorted(attempts, key=order_key):
        u = a.unit
        if u is None:
            continue
        ep = latest.get(u)
        if wrong_reason(a, cfg, now) is None:
            if ep is None or ep.closed_at is not None:
                latest[u] = Episode(u, a.attempt_id, a.item_id, (a.attempt_id,), frozenset({a.item_id}), a.recorded_at, a.recorded_at)
            else:
                latest[u] = replace(ep, wrong_attempt_ids=ep.wrong_attempt_ids + (a.attempt_id,),
                                    wrong_item_ids=ep.wrong_item_ids | {a.item_id}, last_wrong_at=a.recorded_at)
        elif ep is not None and ep.closed_at is None and closes(a, ep.wrong_item_ids, ep.last_wrong_at):
            latest[u] = replace(ep, closed_at=a.recorded_at, closing_attempt_id=a.attempt_id)
    return latest


@dataclass(frozen=True)
class HintTask:
    """开着的 retest_task(reason = hint_followup)：只读，关闭由 KGE-04 重算负责。"""
    task_id: str
    unit: Unit
    due_at: datetime


@dataclass(frozen=True)
class Entry:
    unit: Unit
    source: str                          # wrong / hint_followup
    status: str                          # due / waiting / closed / exhausted（unavailable 由选题结果另行标出）
    round: int                           # 当前（或最后一次开放的）轮次，从 1 起
    rounds_done: int
    anchor: str
    source_item_id: str | None           # 原题；hint_followup 取最近一次提示后做对的题，没有则为空
    next_round_at: datetime | None       # 只在 waiting 时有值
    last_activity_at: datetime
    retest_due: bool = False
    empty_reason: str | None = None
    same_family_only: bool = False

    @property
    def open(self) -> bool:
        return self.status in ("due", "waiting", "unavailable")


def entry_of(ep: Episode, cfg: PracticeConfig, now: datetime) -> Entry:
    done = ep.rounds_done
    last = ep.closed_at or ep.last_wrong_at
    if ep.closed_at is not None:
        return Entry(ep.unit, "wrong", "closed", max(done, 1), done, ep.anchor, ep.source_item_id, None, last)
    if done >= cfg.max_rounds:
        return Entry(ep.unit, "wrong", "exhausted", cfg.max_rounds, done, ep.anchor, ep.source_item_id, None, last)
    opens = ep.last_wrong_at + timedelta(hours=cfg.round_delay_hours[done])
    if now >= opens:
        return Entry(ep.unit, "wrong", "due", done + 1, done, ep.anchor, ep.source_item_id, None, last)
    return Entry(ep.unit, "wrong", "waiting", done + 1, done, ep.anchor, ep.source_item_id, opens, last)


def hint_entry(task: HintTask, attempts: Sequence[AttemptFact], now: datetime) -> Entry:
    """提示后做对 → 无提示变式。原题取到期时刻之前最近一次提示后做对的题（与它不同的题独立做对才闭环）。"""
    unit_attempts = sorted((a for a in attempts if a.unit == task.unit), key=order_key)
    hinted = [a for a in unit_attempts if a.verdict == "correct" and (a.hint_level or 0) > 0 and a.recorded_at <= task.due_at]
    src = hinted[-1].item_id if hinted else None
    for a in unit_attempts:
        if closes(a, {src} if src else set(), task.due_at):
            return Entry(task.unit, "hint_followup", "closed", 1, 0, task.task_id, src, None, a.recorded_at)
    status = "due" if now >= task.due_at else "waiting"
    return Entry(task.unit, "hint_followup", status, 1, 0, task.task_id, src, None if status == "due" else task.due_at, task.due_at)


def build(attempts: Sequence[AttemptFact], hint_tasks: Iterable[HintTask], retest_due_units: Iterable[Unit], cfg: PracticeConfig,
          now: datetime, *, capped: bool = True) -> list[Entry]:
    """全部条目：每个单元一条（开着的错题条目优先；否则开着的 hint_followup 任务；否则最近一个已闭环或做满的错题条目）。
    按最近活动时间从新到旧；capped 时最多 queue_cap 条（列表接口用；按 ID 找条目时不截断）。"""
    eps = episodes(attempts, cfg, now)
    out: dict[Unit, Entry] = {u: entry_of(ep, cfg, now) for u, ep in eps.items()}
    for t in sorted(hint_tasks, key=lambda t: (t.due_at, t.task_id)):
        cur = out.get(t.unit)
        if cur is not None and cur.source == "wrong" and cur.open:
            continue
        if cur is not None and cur.source == "hint_followup" and cur.open:
            continue
        h = hint_entry(t, attempts, now)
        if cur is None or h.open or h.last_activity_at >= cur.last_activity_at:
            out[t.unit] = h
    due = set(retest_due_units)
    entries = [replace(e, retest_due=e.unit in due) for e in out.values()]
    entries.sort(key=lambda e: (-e.last_activity_at.timestamp(), e.unit.node_id, e.unit.cognitive_level))
    return entries[:cfg.queue_cap] if capped else entries


def entry_id(student: str, subject: str, unit: Unit) -> str:
    """条目的不透明 ID：同一学生同一单元固定（不同学生同一单元不同，接口里不能拿来关联学生）。"""
    return str(uuid.uuid5(ENTRY_NS, f"{student}|{subject}|{unit.node_id}|{unit.cognitive_level}"))


def find(entries: Sequence[Entry], student: str, subject: str, eid: str) -> Entry | None:
    return next((e for e in entries if entry_id(student, subject, e.unit) == eid), None)


def counts(entries: Iterable[Entry]) -> Mapping[str, int]:
    """观测：各状态数量（不含学生 ID）。"""
    out = {s: 0 for s in STATUSES}
    for e in entries:
        out[e.status] += 1
    return out
