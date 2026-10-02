"""五道门槛状态机：按服务器时间重放一个单元（学生 × 节点 × 认知层次）的证据（spec 第 4、6、7、8 节）。

同样的证据、同样的配置、同样的 now，永远得到同样的结果（与输入顺序无关：先按 recorded_at、evidence_id 排序）。
门槛：unseen → understand → execute → stable → transfer（星亮）→ retain；只有复测失败会退级（迁移 / 保持 → 稳定）。
- 执行：≥ execute.families_min 个不同题目族独立做对，p ≥ execute.p_floor；到达时排当日复测 rung0；
- 稳定：当日复测通过（到期后第一道新族题独立做对），题型组、题目族数量够，p ≥ stable.p_floor；到达时排 rung1；
- 迁移：配置已签字；rung1 通过（到期后第一道 A 级、已准入、formal 新族题独立做对）；迁移类证据够；
  只用 A 级、已准入、formal、不待复核的证据重新检查稳定的数量条件；p ≥ transfer.p_floor；到达时排 rung2；
- 保持：rung2 通过；之后不再排复测（#14）；
- 复测失败：执行 / 稳定保持原级、从失败时刻重排本轮；迁移 / 保持退回稳定并从失败时刻重排 rung1（第 8 节）；
- 已亮的星做错普通练习：p 跌破稳定层下限时把 rung2 提前到当下（reason = early，#13）；
- 错过复测不降级（#15）：任务一直开着，门槛不变，只按衰减降置信度。
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from typing import Any, Iterable, Mapping, Sequence

from . import evidence as E
from .bkt import bkt_step, confidence_of, decay_crossing_seconds, decayed_confidence
from .config import COGNITIVE_LEVELS, MasteryConfig
from .evidence import Classification, Evidence, Unit, classify

GATES = ("unseen", "understand", "execute", "stable", "transfer", "retain")
RANK = {g: i for i, g in enumerate(GATES)}
LIT = frozenset({"transfer", "retain"})
RUNG_OF_GATE = {"execute": "rung0", "stable": "rung1", "transfer": "rung2", "retain": "rung2"}


@dataclass(frozen=True)
class Retest:
    rung: str
    due_at: datetime
    reason: str  # scheduled / early


@dataclass(frozen=True)
class RetestOutcome:
    rung: str
    reason: str
    due_at: datetime
    outcome: str  # passed / failed
    evidence_id: str


@dataclass(frozen=True)
class Transition:
    from_gate: str
    to_gate: str
    at: datetime
    evidence_id: str
    cause: str  # promote / retest_failed


@dataclass(frozen=True)
class Task:
    reason: str  # scheduled / early / decay / hint_followup / verify_in_platform（verify_self_report 不经重放，由 schedule_verification 建）
    rung: str | None
    due_at: datetime


@dataclass(frozen=True)
class Condition:
    gate: str
    code: str
    met: bool
    current: float
    required: float
    evidence_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class UnitResult:
    """一次重算的结果：写入判定（write.py）与 mastery_estimate 行都从这里取。"""
    node_id: str
    cognitive_level: str
    state: str | None              # None = 没有计入证据，不写记录
    p_mastery: float
    confidence: float              # 已按 now 衰减
    next_retest_at: datetime | None
    algorithm_version: str
    evidence_ids: tuple[str, ...]  # 迁移及以上只列严格证据（A 级、已准入、formal），其余计入证据在解释里

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["next_retest_at"] = _iso(self.next_retest_at)
        d["evidence_ids"] = list(self.evidence_ids)
        return d


def _iso(t: datetime | None) -> str | None:
    return None if t is None else t.isoformat()


@dataclass
class Replay:
    result: UnitResult
    transitions: list[Transition] = field(default_factory=list)
    retests: list[RetestOutcome] = field(default_factory=list)
    excluded: list[tuple[str, tuple[str, ...]]] = field(default_factory=list)
    annotations: list[tuple[str, str]] = field(default_factory=list)
    counted: list[tuple[str, Classification]] = field(default_factory=list)
    tasks: list[Task] = field(default_factory=list)
    conditions: list[Condition] = field(default_factory=list)
    confidence_raw: float = 0.0
    calibrated: bool = False
    config_signed: bool = False
    # 每条计入证据的更新强度 a ∈ [0, 1]（bkt_step 里实际用的那个值，只读记录，不参与任何判定）：mastery.explain 证据链的 weight
    strengths: dict[str, float] = field(default_factory=dict)


@dataclass
class _Counts:
    correct: list[str] = field(default_factory=list)
    families: dict[str, str] = field(default_factory=dict)
    groups: dict[str, list[str]] = field(default_factory=dict)
    transfer: list[str] = field(default_factory=list)

    def add(self, ev: Evidence, c: Classification, cfg: MasteryConfig) -> None:
        if c.correct:
            self.correct.append(ev.evidence_id)
        if c.independent_correct:
            self.families.setdefault(ev.family, ev.evidence_id)
            grp = cfg.type_group(ev.item_type)
            if grp is not None:
                self.groups.setdefault(grp, []).append(ev.evidence_id)
            if c.kind == "transfer":
                self.transfer.append(ev.evidence_id)


def _aware(t: datetime, what: str) -> None:
    if t.tzinfo is None or t.utcoffset() is None:
        raise ValueError(f"{what} 必须带时区")


class _Machine:
    def __init__(self, unit: Unit, cfg: MasteryConfig):
        self.unit, self.cfg = unit, cfg
        g = cfg["gates"]
        self.g = g
        r = cfg["retest"]
        self.gap = {"rung0": timedelta(seconds=r["rung0_min_gap_seconds"]),
                    "rung1": timedelta(seconds=r["rung1_interval_seconds"]),
                    "rung2": timedelta(seconds=r["rung2_interval_seconds"])}
        self.delayed_min = timedelta(seconds=cfg["delayed_retest_min_interval_seconds"])
        self.p = cfg["bkt"]["p_init"]
        self.mass = 0.0
        self.gate: str | None = None
        self.all, self.strict = _Counts(), _Counts()
        self.pending: Retest | None = None
        self.passed = {"rung0": None, "rung1": None, "rung2": None}  # 通过本轮的证据 ID
        self.rung1_lenient: str | None = None  # 非严格证据“做对了 rung1”：只用于排 verify_in_platform
        self.last_counted_at: datetime | None = None
        self.last_indep_correct_at: datetime | None = None
        self.hint_followup_at: datetime | None = None
        self.rep_trans: list[Transition] = []
        self.rep_retests: list[RetestOutcome] = []
        self.excluded: list[tuple[str, tuple[str, ...]]] = []
        self.annotations: list[tuple[str, str]] = []
        self.counted: list[tuple[str, Classification]] = []
        self.strengths: dict[str, float] = {}

    # ---- 条件 ----
    def stable_counts_ok(self, c: _Counts) -> bool:
        return (len(c.correct) >= self.g["understand"]["correct_min"]
                and len(c.families) >= self.g["stable"]["families_min"]
                and len(c.groups) >= self.g["stable"]["item_type_groups_min"])

    def transfer_ok(self, *, strict: bool) -> bool:
        c = self.strict if strict else self.all
        rung1 = self.passed["rung1"] is not None or (not strict and self.rung1_lenient is not None)
        return (rung1 and len(c.transfer) >= self.g["transfer"]["transfer_evidence_min"]
                and self.stable_counts_ok(c) and self.p >= self.g["transfer"]["p_floor"])

    def _next_gate(self) -> str | None:
        g, gate = self.g, self.gate
        if gate == "unseen" and len(self.all.correct) >= g["understand"]["correct_min"]:
            return "understand"
        if gate == "understand" and len(self.all.families) >= g["execute"]["families_min"] and self.p >= g["execute"]["p_floor"]:
            return "execute"
        if (gate == "execute" and self.passed["rung0"] is not None and self.stable_counts_ok(self.all)
                and self.p >= g["stable"]["p_floor"]):
            return "stable"
        # 未签字的配置最高只到“稳定”（spec 第 4 节）
        if gate == "stable" and self.cfg.signed and self.transfer_ok(strict=True):
            return "transfer"
        if gate == "transfer" and self.passed["rung2"] is not None:
            return "retain"
        return None

    def _move(self, to: str, ev: Evidence, cause: str) -> None:
        assert self.gate is not None
        self.rep_trans.append(Transition(self.gate, to, ev.recorded_at, ev.evidence_id, cause))
        self.gate = to

    def _schedule(self, rung: str, t: datetime, reason: str = "scheduled") -> None:
        self.passed[rung] = None
        if rung == "rung1":
            self.rung1_lenient = None
        self.pending = Retest(rung, t + (timedelta(0) if reason == "early" else self.gap[rung]), reason)

    # ---- 一条证据 ----
    def step(self, ev: Evidence, consumed: set[str]) -> None:
        c = classify(ev, self.unit, self.cfg)
        if not c.counted:
            self.excluded.append((ev.evidence_id, c.reasons))
            if c.consumes_family:
                consumed.add(ev.family)
            if c.hint_followup:
                self.hint_followup_at = ev.recorded_at
            return
        if ev.family in consumed:  # 同族重做只计第一次（#9）
            self.excluded.append((ev.evidence_id, (E.REPEAT_FAMILY,)))
            return
        consumed.add(ev.family)
        t = ev.recorded_at
        weight_key = c.weight_key
        assert weight_key is not None
        if c.kind == "delayed_retest" and (self.last_counted_at is None or t - self.last_counted_at < self.delayed_min):
            self.annotations.append((ev.evidence_id, E.DELAYED_TOO_SOON))
            if c.correct:
                weight_key = "independent"
        level_w = 1.0 if c.level == "A" else self.cfg["level_weight_B"]
        src_w = self.cfg["source_weights"]["diagnose" if ev.source == "diagnose" else "practice"]
        strength = abs(self.cfg.weights[weight_key]) * level_w * src_w * c.penalty
        self.p = bkt_step(self.p, c.correct, strength, self.cfg, ev.item_type)
        self.strengths[ev.evidence_id] = min(1.0, strength)  # 与 bkt_step 的插值系数同一口径（只记录）
        if weight_key != "hinted_wrong":  # 提示后做错：置信度不变（第 3 节）
            self.mass += strength * float(ev.grade_confidence or 0.0)
        self.counted.append((ev.evidence_id, c))
        self.last_counted_at = t
        if self.gate is None:
            self.gate = "unseen"
        self.all.add(ev, c, self.cfg)
        if c.strict:
            self.strict.add(ev, c, self.cfg)
        if c.independent_correct:
            self.last_indep_correct_at = t

        self._retest(ev, c)
        # 已亮的星做错普通练习：p 跌破稳定层下限时把 rung2 提前到当下（#13）
        if self.gate in LIT and self.p < self.g["stable"]["p_floor"]:
            if self.pending is None or self.pending.due_at > t:
                self._schedule("rung2", t, "early")
        while (nxt := self._next_gate()) is not None:
            self._move(nxt, ev, "promote")
            if nxt == "execute":
                self._schedule("rung0", t)
            elif nxt == "stable":
                self._schedule("rung1", t)
            elif nxt == "transfer":
                self._schedule("rung2", t)
            elif nxt == "retain":
                self.pending = None  # 到达保持后不再排复测（#14）

    def _retest(self, ev: Evidence, c: Classification) -> None:
        pend = self.pending
        # 复测只认题目族的第一次作答、且不是提示后作答（每轮只看到期后的第一道符合条件的题，第 6、8 节）
        if pend is None or c.kind not in E.NON_HINTED_KINDS:
            return
        t = ev.recorded_at
        if t < pend.due_at:
            self.annotations.append((ev.evidence_id, E.RETEST_TOO_EARLY))
            return
        if pend.rung != "rung0" and not c.strict:
            # rung1 / rung2 只认 A 级、已准入、formal；非严格证据只记“宽松通过”，用于排平台内验证任务
            if pend.rung == "rung1":
                self.rung1_lenient = ev.evidence_id if c.correct else None
            return
        outcome = "passed" if c.correct else "failed"
        self.rep_retests.append(RetestOutcome(pend.rung, pend.reason, pend.due_at, outcome, ev.evidence_id))
        self.pending = None
        if c.correct:
            self.passed[pend.rung] = ev.evidence_id
            return
        if pend.rung == "rung2":
            self._move("stable", ev, "retest_failed")
            self.passed["rung2"] = None
            self._schedule("rung1", t)
        else:
            self._schedule(pend.rung, t)

    # ---- 结果 ----
    def finish(self, now: datetime) -> Replay:
        cfg = self.cfg
        conf_raw = confidence_of(self.mass, cfg)
        conf = decayed_confidence(conf_raw, self.last_counted_at, now, cfg)
        if self.gate in LIT:
            ids = tuple(eid for eid, c in self.counted if c.strict)
        else:
            ids = tuple(eid for eid, _ in self.counted)
        result = UnitResult(self.unit.node_id, self.unit.cognitive_level, self.gate, self.p, conf,
                            self.pending.due_at if self.pending else None, cfg.algorithm_version, ids)
        tasks: list[Task] = []
        if self.pending is not None:
            tasks.append(Task(self.pending.reason, self.pending.rung, self.pending.due_at))
        if self.hint_followup_at is not None and (self.last_indep_correct_at is None
                                                  or self.hint_followup_at > self.last_indep_correct_at):
            tasks.append(Task("hint_followup", None, self.hint_followup_at))
        if self.gate == "stable" and self.transfer_ok(strict=False) and not self.transfer_ok(strict=True):
            assert self.last_counted_at is not None
            tasks.append(Task("verify_in_platform", None, self.last_counted_at))
        crossing = decay_crossing_seconds(conf_raw, cfg)
        if crossing is not None and self.last_counted_at is not None:
            due = self.last_counted_at + timedelta(seconds=crossing)
            if now >= due and conf < cfg["review_confidence_floor"]:
                tasks.append(Task("decay", None, due))
        return Replay(result=result, transitions=self.rep_trans, retests=self.rep_retests, excluded=self.excluded,
                      annotations=self.annotations, counted=self.counted, tasks=tasks,
                      conditions=self.conditions(), confidence_raw=conf_raw,
                      calibrated=bool(cfg["calibrated"]), config_signed=cfg.signed, strengths=dict(self.strengths))

    def conditions(self) -> list[Condition]:
        g, a, s = self.g, self.all, self.strict
        fam = tuple(a.families.values())
        grp = tuple(e for ids in a.groups.values() for e in ids)

        def passed(rung: str) -> tuple[bool, tuple[str, ...]]:
            eid = self.passed[rung]
            return eid is not None, ((eid,) if eid else ())

        r0, r0e = passed("rung0")
        r1, r1e = passed("rung1")
        r2, r2e = passed("rung2")
        out = [
            Condition("understand", "understand.correct_min", len(a.correct) >= g["understand"]["correct_min"],
                      len(a.correct), g["understand"]["correct_min"], tuple(a.correct)),
            Condition("execute", "execute.families_min", len(a.families) >= g["execute"]["families_min"],
                      len(a.families), g["execute"]["families_min"], fam),
            Condition("execute", "execute.p_floor", self.p >= g["execute"]["p_floor"], self.p, g["execute"]["p_floor"]),
            Condition("stable", "stable.rung0_passed", r0, int(r0), 1, r0e),
            Condition("stable", "stable.item_type_groups_min", len(a.groups) >= g["stable"]["item_type_groups_min"],
                      len(a.groups), g["stable"]["item_type_groups_min"], grp),
            Condition("stable", "stable.families_min", len(a.families) >= g["stable"]["families_min"],
                      len(a.families), g["stable"]["families_min"], fam),
            Condition("stable", "stable.p_floor", self.p >= g["stable"]["p_floor"], self.p, g["stable"]["p_floor"]),
            Condition("transfer", "transfer.rung1_passed", r1, int(r1), 1, r1e),
            Condition("transfer", "transfer.transfer_evidence_min", len(s.transfer) >= g["transfer"]["transfer_evidence_min"],
                      len(s.transfer), g["transfer"]["transfer_evidence_min"], tuple(s.transfer)),
            Condition("transfer", "transfer.strict_families_min", len(s.families) >= g["stable"]["families_min"],
                      len(s.families), g["stable"]["families_min"], tuple(s.families.values())),
            Condition("transfer", "transfer.strict_item_type_groups_min",
                      len(s.groups) >= g["stable"]["item_type_groups_min"], len(s.groups),
                      g["stable"]["item_type_groups_min"], tuple(e for ids in s.groups.values() for e in ids)),
            Condition("transfer", "transfer.p_floor", self.p >= g["transfer"]["p_floor"], self.p, g["transfer"]["p_floor"]),
            Condition("transfer", "transfer.config_signed", self.cfg.signed, int(self.cfg.signed), 1),
            Condition("retain", "retain.rung2_passed", r2, int(r2), 1, r2e),
        ]
        return out


def replay(evidences: Iterable[Evidence], unit: Unit, cfg: MasteryConfig, now: datetime) -> Replay:
    _aware(now, "now")
    evs = list(evidences)
    for ev in evs:
        _aware(ev.recorded_at, f"{ev.evidence_id}.recorded_at")
    evs.sort(key=lambda e: (e.recorded_at, e.evidence_id))
    m = _Machine(unit, cfg)
    consumed: set[str] = set()
    seen_ids: set[str] = set()
    seen_attempts: set[str] = set()
    for ev in evs:
        # 同一证据重复提交、同一作答重复出现：按 evidence_id、attempt_id 去重（第 6 节）
        if ev.evidence_id in seen_ids or (ev.attempt_id is not None and ev.attempt_id in seen_attempts):
            m.excluded.append((ev.evidence_id, (E.DUPLICATE,)))
            continue
        seen_ids.add(ev.evidence_id)
        if ev.attempt_id is not None:
            seen_attempts.add(ev.attempt_id)
        m.step(ev, consumed)
    return m.finish(now)


def explain_gates(rep: Replay) -> dict[str, Any]:
    """门槛条件的逐条结果与不计入原因。只输出编码、数字、ID 与时间，不输出自然语言（spec 第 13 节）。"""
    r = rep.result
    capped = [{"evidence_id": eid, "caps": list(c.caps)} for eid, c in rep.counted if c.caps]
    supporting = [eid for eid, c in rep.counted if eid not in set(r.evidence_ids)]
    return {
        "unit": {"node_id": r.node_id, "cognitive_level": r.cognitive_level},
        "gate": r.state,
        "algorithm_version": r.algorithm_version,
        "p_mastery": r.p_mastery,
        "confidence": r.confidence,
        "next_retest_at": _iso(r.next_retest_at),
        "evidence_ids": list(r.evidence_ids),
        "supporting_evidence_ids": supporting,
        "conditions": [{"gate": c.gate, "code": c.code, "met": c.met, "current": c.current, "required": c.required,
                        "evidence_ids": list(c.evidence_ids)} for c in rep.conditions],
        "excluded": [{"evidence_id": eid, "reasons": list(rs)} for eid, rs in rep.excluded],
        "capped": capped,
        "annotations": [{"evidence_id": eid, "code": code} for eid, code in rep.annotations],
        "transitions": [{"from": t.from_gate, "to": t.to_gate, "at": _iso(t.at), "evidence_id": t.evidence_id,
                         "cause": t.cause} for t in rep.transitions],
        "retests": [{"rung": o.rung, "reason": o.reason, "due_at": _iso(o.due_at), "outcome": o.outcome,
                     "evidence_id": o.evidence_id} for o in rep.retests],
        "tasks": [{"reason": t.reason, "rung": t.rung, "due_at": _iso(t.due_at)} for t in rep.tasks],
        "calibrated": rep.calibrated,        # BKT 参数未校准时如实显示（第 5 节）
        "config_signed": rep.config_signed,
    }


def star_lit(node_id: str, node_levels: Sequence[str], gates: Mapping[str, str | None], cfg: MasteryConfig) -> bool:
    """星亮（ADR 11；#3 默认 star_level）：星亮层次由教研按节点配置，未配置时取该节点最高的认知层次。"""
    levels = [lv for lv in COGNITIVE_LEVELS if lv in node_levels]
    if not levels:
        return False
    rule = cfg["star_level_rule"]
    if rule["mode"] == "all_levels":
        return all(gates.get(lv) in LIT for lv in levels)
    level = rule["node_levels"].get(node_id) or levels[-1]
    return level in levels and gates.get(level) in LIT
