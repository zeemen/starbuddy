"""写入判定与写库前自检（spec 第 4、9 节；待人确认 #12 默认）。纯函数，不连库。

- 只有实质变化才写新记录：门槛、复测时间、算法版本任一变化，或 p_mastery / confidence 变化超过 write_threshold；
- 没有计入证据不写记录；
- 准备写 transfer / retain 时，逐条核对 evidence_ids 对应的证据都是 A 级、题目当前已准入、formal、判分不待复核，
  不满足就拒绝写入（mastery_estimate 表注释那条不变量的落实）。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Mapping

from .config import MasteryConfig
from .gates import LIT, UnitResult


@dataclass(frozen=True)
class StoredEstimate:
    """当前 mastery_estimate 行里参与比较的字段。"""
    state: str
    p_mastery: float
    confidence: float
    next_retest_at: datetime | None
    algorithm_version: str


@dataclass(frozen=True)
class EvidenceFacts:
    """写库前自检用：证据与题目的现状（由调用方从库里读出）。"""
    evidence_level: str | None
    admission: str
    human_review_required: bool
    semantics: str | None


class InvariantViolation(RuntimeError):
    """准备写入的迁移 / 保持记录引用了不合格的证据：拒绝写入并报警。"""


def write_decision(prev: StoredEstimate | None, new: UnitResult, cfg: MasteryConfig) -> tuple[bool, tuple[str, ...]]:
    if new.state is None or not new.evidence_ids:
        return False, ("no_counted_evidence",)
    if prev is None:
        return True, ("first",)
    why: list[str] = []
    if prev.state != new.state:
        why.append("state")
    if prev.next_retest_at != new.next_retest_at:
        why.append("next_retest_at")
    if prev.algorithm_version != new.algorithm_version:
        why.append("algorithm_version")
    th = cfg["write_threshold"]
    if abs(prev.p_mastery - new.p_mastery) > th["p_mastery"]:
        why.append("p_mastery")
    if abs(prev.confidence - new.confidence) > th["confidence"]:
        why.append("confidence")
    return bool(why), tuple(why)


def precommit_check(new: UnitResult, facts: Mapping[str, EvidenceFacts]) -> None:
    if new.state not in LIT:
        return
    bad = []
    for eid in new.evidence_ids:
        f = facts.get(eid)
        if (f is None or f.evidence_level != "A" or f.admission != "admitted" or f.human_review_required
                or f.semantics != "formal"):
            bad.append(eid)
    if bad or not new.evidence_ids:
        raise InvariantViolation(f"transfer/retain references non-qualifying evidence: {len(bad)}")
