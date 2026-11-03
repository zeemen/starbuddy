"""复测与验证任务 → 学生端条目（纯函数，不连库）。KGE-05 spec 范围第 1、3 条。

任务只读（platform.retest_task，KGE-04 建、KGE-04 关）：复测轮 scheduled / early、衰减 decay、平台内验证 verify_in_platform、
“我其实会”的自述验证 verify_self_report；hint_followup（提示后做对的无提示变式）由错题再练提供，这里不重复列出。
作答用途（ingest.purpose，掌握计算的输入：attributes.PURPOSE_KIND 映射成证据情境）只在 PURPOSE 这一处常量里定，服务端按任务原因写进
票据的条目锚点（签名覆盖），客户端不能指定（规则 3）。教研签字前取保守口径（审查阻断 1）：
- 只有衰减 decay 写 retest（证据情境 delayed_retest）；
- 复测轮 scheduled / early、两种验证任务、8 周复测点一律写 practice（证据情境 independent），不写 transfer_check，
  不让未签字的映射去推动“迁移”门槛的迁移类证据。复测轮照样由 KGE-04 按“到期后第一道计入的非提示作答”判通过或失败（与用途无关）。
条目在任务到期（due_at ≤ now）后才出现，每条任务一条。任务关不关、什么时候关，一律由 KGE-04 的重算决定，本模块不写、不关。
唯一的例外是 verify_self_report：KGE-04 不经重放管理它（重算从不关它），所以这里读时判断“验证题做满没有”：只统计用本任务票据锚点
作答的题（作答 ID 由幂等键与锚点确定地派生，见 attempt_id_for；读时用学生已成功的判分键反推，不新增表），做满
verify_items_per_task 道有判分的作答即算做完（不再出题；任务本身仍开着，由洞察服务按掌握结案）。
8 周复测点（followup，默认关）：同单元有当前掌握记录、没有开着的本模块任务、最近一次作答距今满 followup_after_days 天，
读时算出一条复测条目（不建任务、不加原因、不改调度）。时间一律按服务器记录时间（recorded_at）与服务时钟。
"""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Iterable, Sequence

from ..practice.queue import AttemptFact, Unit, order_key
from .config import RetestConfig

RETEST_REASONS = ("scheduled", "early", "decay", "verify_in_platform", "verify_self_report")
FOLLOWUP = "followup"
# 作答用途的唯一映射（审查阻断 1，教研签字前的保守口径）：只有衰减写 retest，其余一律 practice
PURPOSE = {"scheduled": "practice", "early": "practice", "decay": "retest", FOLLOWUP: "practice",
           "verify_in_platform": "practice", "verify_self_report": "practice"}
# 对前端只分两类（文案占位用）：复测 / 验证
KIND = {"scheduled": "retest", "early": "retest", "decay": "retest", FOLLOWUP: "retest",
        "verify_in_platform": "verify", "verify_self_report": "verify"}
ENTRY_NS = uuid.UUID("0b5e7c2a-9d14-4f63-8a2e-6c1d3e5f7a90")
FOLLOWUP_NS = uuid.UUID("4d2a9e61-7b3c-4f08-9e15-2a6c8b0d1f37")
ATTEMPT_NS = uuid.UUID("6a3f2c18-4e7b-4d95-b1c0-8e2d7f5a9c34")
ANCHOR_RE = re.compile(r"^(practice|retest):[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
STATUSES = ("due", "done")


@dataclass(frozen=True)
class TaskRow:
    """开着的任务（只读）：只有 ID、单元、原因与到期时间。"""
    task_id: str
    unit: Unit
    reason: str
    due_at: datetime


@dataclass(frozen=True)
class Entry:
    unit: Unit
    reason: str                      # 任务原因，或 followup
    anchor: str                      # "<用途>:<任务 ID 或 8 周复测点 ID>"：票据绑定它
    due_at: datetime
    source_item_id: str | None       # 选题的参照题：这个单元最近一次作答的题；没有作答为空（这一轮不可用）
    status: str                      # due / done

    @property
    def purpose(self) -> str:
        return PURPOSE[self.reason]

    @property
    def kind(self) -> str:
        return KIND[self.reason]


def anchor_of(reason: str, ref: str) -> str:
    return f"{PURPOSE[reason]}:{ref}"


def purpose_of_anchor(anchor: str) -> str | None:
    """票据锚点里的用途（签名覆盖）；格式不对为空。"""
    m = ANCHOR_RE.fullmatch(anchor) if isinstance(anchor, str) else None
    return m.group(1) if m else None


def attempt_id_for(key: str, anchor: str) -> str:
    """复测作答的作答 ID：由幂等键与条目锚点确定地派生（UUIDv4 形状）。同一键重试不变；读时据此认出“用本任务票据作答的题”。"""
    b = bytearray(uuid.uuid5(ATTEMPT_NS, f"{key}|{anchor}|attempt").bytes)
    b[6] = (b[6] & 0x0F) | 0x40
    b[8] = (b[8] & 0x3F) | 0x80
    return str(uuid.UUID(bytes=bytes(b)))


def source_item(attempts: Iterable[AttemptFact], unit: Unit, before: datetime) -> str | None:
    """选题参照：这个单元在 before 之前最近一次作答的题（不论对错）。"""
    xs = sorted((a for a in attempts if a.unit == unit and a.recorded_at <= before), key=order_key)
    return xs[-1].item_id if xs else None


def verify_answers(attempts: Iterable[AttemptFact], keys: Iterable[str], anchor: str, unit: Unit) -> int:
    """用这条任务的票据（锚点）作答、同单元、有判分的作答道数。keys：学生已成功的判分幂等键。"""
    mine = {attempt_id_for(k, anchor) for k in keys}
    return sum(1 for a in attempts if a.unit == unit and a.has_grade and a.attempt_id in mine)


def task_entry(t: TaskRow, attempts: Sequence[AttemptFact], keys: Sequence[str], cfg: RetestConfig, now: datetime) -> Entry | None:
    """到期的任务 → 条目；没到期、或原因不归本模块的返回空。"""
    if t.reason not in RETEST_REASONS or t.due_at > now:
        return None
    anchor = anchor_of(t.reason, t.task_id)
    status = "due"
    if t.reason == "verify_self_report" and verify_answers(attempts, keys, anchor, t.unit) >= cfg.verify_items_per_task:
        status = "done"
    return Entry(t.unit, t.reason, anchor, t.due_at, source_item(attempts, t.unit, now), status)


def followup_entries(attempts: Sequence[AttemptFact], estimated: Iterable[Unit], taken: Iterable[Unit], cfg: RetestConfig,
                     now: datetime) -> list[Entry]:
    """8 周复测点（默认关）：有当前掌握记录、没有开着的本模块任务、最近一次作答距今满 followup_after_days 天的单元。"""
    if not cfg.followup_enabled or cfg.followup_after_days is None:
        return []
    gap = timedelta(days=cfg.followup_after_days)
    busy = set(taken)
    out = []
    for u in sorted(set(estimated) - busy, key=lambda u: (u.node_id, u.cognitive_level)):
        xs = sorted((a for a in attempts if a.unit == u), key=order_key)
        if not xs or xs[-1].recorded_at > now - gap:
            continue
        last = xs[-1]
        ref = str(uuid.uuid5(FOLLOWUP_NS, f"{u.node_id}|{u.cognitive_level}|{last.attempt_id}"))
        out.append(Entry(u, FOLLOWUP, anchor_of(FOLLOWUP, ref), last.recorded_at + gap, last.item_id, "due"))
    return out


def build(tasks: Iterable[TaskRow], attempts: Sequence[AttemptFact], keys: Sequence[str], estimated: Iterable[Unit],
          cfg: RetestConfig, now: datetime, *, capped: bool = True) -> list[Entry]:
    """全部条目（到期的任务 + 8 周复测点）。按到期时间从早到晚；capped 时只留 queue_cap 条 due（列表用；按 ID 找时不截断）。"""
    ts = [t for t in tasks if t.reason in RETEST_REASONS]
    entries = [e for e in (task_entry(t, attempts, keys, cfg, now) for t in ts) if e is not None]
    entries += followup_entries(attempts, estimated, {t.unit for t in ts}, cfg, now)
    entries.sort(key=lambda e: (e.due_at, e.unit.node_id, e.unit.cognitive_level, e.anchor))
    if not capped:
        return entries
    return [e for e in entries if e.status == "due"][:cfg.queue_cap]


def entry_id(student: str, subject: str, anchor: str) -> str:
    """条目的不透明 ID：同一学生同一任务固定；不同学生不同（接口里不能拿来关联学生）。"""
    return str(uuid.uuid5(ENTRY_NS, f"{student}|{subject}|{anchor}"))


def find(entries: Sequence[Entry], student: str, subject: str, eid: str) -> Entry | None:
    return next((e for e in entries if entry_id(student, subject, e.anchor) == eid), None)
