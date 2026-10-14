"""合成纵向队列（spec 范围 1）：固定种子、约 40 名合成学生、12 周。纯函数，不连库、不读系统时钟。

每名学生：
- 合成 ID（uuid5，由种子与标签派生）与两个非敏感分组键：wave（入组批次 a / b）、grade（8 / 9，决定满分 100 / 120）；
  不生成姓名、学校、地区、家庭（规则 8、12）；
- 会话事件（每个练习日一次；另有少数只浏览、不做题的会话）。会话的钟点（19 点、20:30）只是占位，只为排出先后，
  不代表任何作息，不得用于时段分析（迁移 0025：时间戳只用于降负触发，不做作息分析）；学习持续性只按活跃日计数；
- 练习作答（数学 aya、道法 zhixing 两科；独立、带提示、迁移三类）；
- 中圈指标的其余原料（SIE-05，形状同洞察服务 metrics.py 的 ProbeFact / PlanFact / ErrorFact）：
  做题前探针（约 3 成练习题前问“能做对吗”，yes / no / unsure，由该题真实概率加学生自评偏差与噪声决定）；
  计划事件（每个有会话的周 3 个计划任务，低频学生 2 个；完成或跳过，少数先跳过后补做，按 plan_task_id 取最后一次）；
  错因归因（约 6 成做错的练习题有一条当前错因，审题类比例随学生而异，基础较弱的学生审题类略多）；
  这些原料各用独立的随机数流，不改变作答、复测与考试的抽样；
- 入组时间；少数学生入组后从未使用（没有会话、作答、复测与考试记录，续用率分母仍计入）；
- 复测：首轮（第 2 周末，每科 8 个节点各一题）与 8 周后（首轮后第 56 天，同一批节点）；中途停用的学生没有 8 周后复测；
- 三轮学校考试（第 4、8、12 周，月考、一模、二模）：考前一天用洞察服务的 predict_interval（insight/predict.py）
  按合成掌握行出 80% 区间，考后给合成成绩。停用后的考试不录成绩。
作答直接用洞察服务的 AttemptFact 形状（insight/metrics.py），以便 G1-4 复用指标纯函数。
潜在能力只在生成器内部使用，不进证据包（不贴层级标签，规则 12）。
"""
from __future__ import annotations

import hashlib
import json
import random
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Sequence

from insight.claims import MasteryRow
from insight.metrics import AttemptFact, ErrorFact, PlanFact, ProbeFact
from insight.predict import predict_interval
from insight.sie03_policy import IntervalParams

from .basis import predicted_median

SEED = 20261012
SIZE = 40
WEEKS = 12
NS = uuid.UUID("0c7d2a64-6f1e-4b8a-9d3c-000000001204")
CN = timezone(timedelta(hours=8))
BASE = datetime(2027, 3, 1, 0, 0, tzinfo=CN)  # 合成时间：第 1 周周一 0 点（北京时间）
SUBJECTS = ("aya", "zhixing")
NODES = {"aya": tuple(f"SYN.AYA.N{i:02d}" for i in range(1, 9)), "zhixing": tuple(f"SYN.ZX.N{i:02d}" for i in range(1, 9))}
LEVEL = "apply"
FIRST_RETEST_DAY = 13          # 第 2 周末
WEEK8_RETEST_DAY = FIRST_RETEST_DAY + 56
# 轮次、契约 exam_type、考试所在周（考试日 = 该周周六）
ROUNDS = (("r1", "monthly", 4), ("r2", "mock1", 8), ("r3", "mock2", 12))
FULL_SCORE = {8: 100, 9: 120}
MASTERY_LOOKBACK_DAYS = 28
MASTERY_ALGO = "syn-mastery@0.1.0"
WEIGHTS_SOURCE = "equal:synthetic"
PROBE_RATE = 0.3               # 练习题前问探针的比例
ERROR_RATE = 0.6               # 做错的练习题有错因归因的比例
REDO_RATE = 0.2                # 跳过的计划任务之后补做的比例
BROWSE_RATE = 0.1              # 没有练习的活跃日里只浏览（有会话、不做题）的比例
READING = "reading_the_question"
OTHER_ERRORS = ("locating_information", "thinking_method", "computation_or_grammar", "expression_convention",
                "strategy_and_time", "knowledge")   # platform.cross_subject_error 的其余取值


@dataclass(frozen=True)
class Student:
    label: str
    student_id: str
    wave: str        # 入组批次（合成，非敏感）
    grade: int       # 年级（合成，非敏感）
    full_score: int
    enrolled_at: datetime   # 入组时间（续用观察窗的起点；从未使用的学生也有）


@dataclass(frozen=True)
class Session:
    fact_id: str
    student_id: str
    occurred_at: datetime


@dataclass(frozen=True)
class Attempt:
    student_id: str
    fact: AttemptFact
    retest_round: str | None = None   # None = 练习；first / week8 = 复测


@dataclass(frozen=True)
class Probe:
    student_id: str
    fact: ProbeFact                   # 做题前探针（verdict 为关联作答的判分）


@dataclass(frozen=True)
class PlanEvent:
    student_id: str
    fact: PlanFact                    # plan_done / plan_skip


@dataclass(frozen=True)
class ErrorEvent:
    student_id: str
    created_at: datetime              # 错因写入时间（ErrorFact 本身不带时间；按测量时点截断用）
    fact: ErrorFact


@dataclass(frozen=True)
class ExamRecord:
    student_id: str
    subject: str
    round: str
    round_index: int
    exam_type: str
    week: int
    full_score: int
    low: float
    high: float
    median: float
    basis: Mapping[str, Any]
    actual: float


@dataclass(frozen=True)
class Cohort:
    seed: int
    base: datetime
    weeks: int
    students: tuple[Student, ...]
    sessions: tuple[Session, ...]
    attempts: tuple[Attempt, ...]       # 练习与复测
    exams: tuple[ExamRecord, ...]
    params_version: str
    digest: str
    probes: tuple[Probe, ...] = ()
    plans: tuple[PlanEvent, ...] = ()
    errors: tuple[ErrorEvent, ...] = ()

    @property
    def horizon_end(self) -> datetime:
        return self.base + timedelta(days=7 * self.weeks)

    @property
    def retests(self) -> tuple[Attempt, ...]:
        return tuple(a for a in self.attempts if a.retest_round is not None)

    def facts_of(self, student_id: str) -> tuple[AttemptFact, ...]:
        return tuple(a.fact for a in self.attempts if a.student_id == student_id)


def _uid(seed: int, tag: str) -> str:
    return str(uuid.uuid5(NS, f"{seed}:{tag}"))


def _rng(seed: int, *parts: object) -> random.Random:
    return random.Random("|".join([str(seed), *map(str, parts)]))


def _clip(x: float, lo: float = 0.03, hi: float = 0.97) -> float:
    return min(hi, max(lo, x))


@dataclass(frozen=True)
class _Latent:
    theta: Mapping[str, float]
    gain: Mapping[str, float]            # 每周增量
    transfer_deficit: Mapping[str, float]
    exam_offset: Mapping[str, float]
    node_offset: Mapping[str, float]     # 节点难度（同科所有学生共用）
    stop_week: int | None                # 从这一周起不再使用
    sessions_per_week: int
    items_per_session: int
    start_day: int
    low: bool = False                    # 低频使用

    def p(self, subject: str, node: str, day: float) -> float:
        return _clip(self.theta[subject] + self.gain[subject] * day / 7 - self.node_offset[node])

    def active(self, day: int) -> bool:
        return self.stop_week is None or day < 7 * (self.stop_week - 1)


def _latent(seed: int, i: int, node_offset: Mapping[str, float]) -> _Latent:
    r = _rng(seed, "latent", i)
    stop = 1 if i % 20 == 13 else r.choice((5, 6, 7, 8)) if i % 6 == 5 else None   # 1/20 从未使用，约 1/6 中途停用
    low = i % 10 == 7                                               # 少数低频使用（指标样本不足）
    return _Latent(
        theta={s: r.uniform(0.35, 0.75) for s in SUBJECTS},
        gain={s: r.uniform(0.0, 0.02) for s in SUBJECTS},
        transfer_deficit={s: r.uniform(0.0, 0.2) for s in SUBJECTS},
        exam_offset={s: r.gauss(0.0, 0.03) for s in SUBJECTS},
        node_offset=node_offset, stop_week=stop,
        sessions_per_week=2 if low else 5, items_per_session=4 if low else 5, start_day=r.randint(0, 3), low=low)


@dataclass(frozen=True)
class _Habits:
    """中圈指标原料的潜在倾向（独立随机数流，只在生成器内部使用，不进证据包）"""
    self_bias: float       # 自评偏差：正 = 倾向高估
    plan_follow: float     # 计划任务完成倾向（与每周增量正相关：合成世界假设，待签字时一并复核）
    reading: float         # 错因里审题类的比例（基础较弱的学生略高：合成世界假设）


def _habits(seed: int, label: str, lat: _Latent) -> _Habits:
    r = _rng(seed, "habits", label)
    theta = sum(lat.theta.values()) / len(lat.theta)
    gain = sum(lat.gain.values()) / len(lat.gain)
    return _Habits(self_bias=r.uniform(-0.15, 0.25),
                   plan_follow=_clip(0.5 + 10 * gain + r.uniform(-0.15, 0.15), 0.1, 0.95),
                   reading=_clip(0.25 + r.uniform(-0.1, 0.1) - 0.8 * (theta - 0.55), 0.05, 0.6))


def _prediction(r: random.Random, p: float, bias: float) -> str:
    """做题前“能做对吗”：真实概率 + 自评偏差 + 噪声，≥ 0.65 说能、≤ 0.35 说不能，其余不确定"""
    x = p + bias + r.gauss(0.0, 0.15)
    return "yes" if x >= 0.65 else "no" if x <= 0.35 else "unsure"


def _plan_events(seed: int, label: str, sid: str, habits: _Habits, low: bool,
                 week_sessions: Mapping[int, Sequence[datetime]]) -> list[PlanEvent]:
    """每个有会话的周：低频学生 2 个、其余 3 个计划任务；在本周的会话里完成或跳过，跳过的少数在本周最后一次会话补做（同一任务两条事件）"""
    r = _rng(seed, "plan", label)
    out: list[PlanEvent] = []
    for w in sorted(week_sessions):
        times = sorted(week_sessions[w])
        for j in range(2 if low else 3):
            task = _uid(seed, f"plan:{label}:{w}:{j}")
            t = times[j % len(times)] + timedelta(minutes=40)
            done = r.random() < habits.plan_follow
            out.append(PlanEvent(sid, PlanFact(_uid(seed, f"plan-event:{label}:{w}:{j}:0"), "plan_done" if done else "plan_skip", task, t)))
            if not done and r.random() < REDO_RATE:
                later = max(times[-1] + timedelta(minutes=50), t + timedelta(hours=1))
                out.append(PlanEvent(sid, PlanFact(_uid(seed, f"plan-event:{label}:{w}:{j}:1"), "plan_done", task, later)))
    return out


def _verdict(r: random.Random, p: float) -> str:
    return "correct" if r.random() < p else "incorrect"


def _mastery_rows(seed: int, student: str, subject: str, facts: Sequence[AttemptFact], at: datetime) -> list[MasteryRow]:
    """合成掌握行：考前 MASTERY_LOOKBACK_DAYS 天内独立作答与复测按节点的正确率。只作 predict_interval 的输入"""
    by_node: dict[str, list[AttemptFact]] = {}
    for a in facts:
        if (a.subject == subject and a.evidence_kind in ("independent", "delayed_retest") and a.node_id is not None
                and at - timedelta(days=MASTERY_LOOKBACK_DAYS) < a.occurred_at < at):
            by_node.setdefault(a.node_id, []).append(a)
    rows = []
    for node in sorted(by_node):
        xs = by_node[node]
        p = sum(a.verdict == "correct" for a in xs) / len(xs)   # 向先验收缩由 predict_interval 做，这里不再平滑
        gate = "transfer" if p >= 0.85 else "stable" if p >= 0.7 else "execute" if p >= 0.5 else "understand"
        rows.append(MasteryRow(basis_id=_uid(seed, f"mastery:{student}:{node}:{at.isoformat()}"), student_id=student,
                               subject=subject, node_id=node, cognitive_level=LEVEL, gate=gate, p_mastery=round(p, 4),
                               confidence=round(len(xs) / (len(xs) + 5), 3), evidence_ids=tuple(sorted(a.fact_id for a in xs)),
                               algorithm_version=MASTERY_ALGO, computed_at=at))
    return rows


def generate(interval: IntervalParams, scales: Mapping[int, str], params_version: str, seed: int = SEED,
             size: int = SIZE) -> Cohort:
    """生成合成队列。interval / scales 取洞察服务的（合成、未签字）预测参数；同一输入逐字节相同"""
    node_r = _rng(seed, "nodes")
    node_offset = {n: node_r.uniform(-0.1, 0.1) for s in SUBJECTS for n in NODES[s]}
    students: list[Student] = []
    sessions: list[Session] = []
    attempts: list[Attempt] = []
    exams: list[ExamRecord] = []
    probes: list[Probe] = []
    plans: list[PlanEvent] = []
    errors: list[ErrorEvent] = []
    for i in range(size):
        label = f"G1S{i + 1:02d}"
        sid = _uid(seed, f"student:{label}")
        grade = 8 if i % 3 == 0 else 9
        lat = _latent(seed, i, node_offset)
        st = Student(label, sid, "a" if i % 2 == 0 else "b", grade, FULL_SCORE[grade], BASE + timedelta(days=lat.start_day))
        students.append(st)
        r = _rng(seed, "activity", label)
        habits = _habits(seed, label, lat)
        pr, xr, br = _rng(seed, "probe", label), _rng(seed, "error", label), _rng(seed, "browse", label)
        week_sessions: dict[int, list[datetime]] = {}
        mine: list[AttemptFact] = []
        seq = 0
        for w in range(WEEKS):
            days = sorted(r.sample(range(7), lat.sessions_per_week))
            for d in days:
                day = 7 * w + d
                if day < lat.start_day or not lat.active(day):
                    continue
                t0 = BASE + timedelta(days=day, hours=19, minutes=r.randrange(0, 60))   # 钟点是占位，不得用于时段分析
                sessions.append(Session(_uid(seed, f"session:{label}:{day}"), sid, t0))
                week_sessions.setdefault(w, []).append(t0)
                subject = r.choice(SUBJECTS)
                for k in range(lat.items_per_session):
                    seq += 1
                    node = r.choice(NODES[subject])
                    p = lat.p(subject, node, day)
                    u = r.random()
                    if u < 0.2:
                        kind, hint, pc = "transfer", 0, _clip(p - lat.transfer_deficit[subject])
                    elif u < 0.45:
                        kind, hint, pc = "hinted", r.randint(1, 5), _clip(p + 0.15)
                    else:
                        kind, hint, pc = "independent", 0, p
                    fid = _uid(seed, f"attempt:{label}:{seq}")
                    at = t0 + timedelta(minutes=3 * k)
                    a = AttemptFact(fid, subject, f"{subject}-{label}-{seq:04d}", at, hint, kind, _verdict(r, pc), node, LEVEL)
                    attempts.append(Attempt(sid, a))
                    mine.append(a)
                    if pr.random() < PROBE_RATE:   # 做题前探针（题目出现前 30 秒）
                        probes.append(Probe(sid, ProbeFact(_uid(seed, f"probe:{label}:{seq}"), subject, at - timedelta(seconds=30),
                                                           _prediction(pr, pc, habits.self_bias), a.verdict)))
                    if a.verdict == "incorrect" and xr.random() < ERROR_RATE:   # 当前错因（判分后 5 分钟写入）
                        cat = READING if xr.random() < habits.reading else xr.choice(OTHER_ERRORS)
                        errors.append(ErrorEvent(sid, at + timedelta(minutes=5), ErrorFact(_uid(seed, f"error:{label}:{seq}"), subject, cat)))
            for d in range(7):   # 只浏览、不做题的会话（独立随机数流；不改变练习日的抽样）
                day = 7 * w + d
                if d in days or day < lat.start_day or not lat.active(day) or br.random() >= BROWSE_RATE:
                    continue
                t0 = BASE + timedelta(days=day, hours=20, minutes=30)   # 钟点是占位，不得用于时段分析
                sessions.append(Session(_uid(seed, f"session:{label}:{day}"), sid, t0))
                week_sessions.setdefault(w, []).append(t0)
        plans.extend(_plan_events(seed, label, sid, habits, lat.low, week_sessions))
        # 复测：首轮与 8 周后（同一批节点；停用的学生没有 8 周后复测）
        rr = _rng(seed, "retest", label)
        for rnd, day in (("first", FIRST_RETEST_DAY + lat.start_day), ("week8", WEEK8_RETEST_DAY + lat.start_day)):
            if not lat.active(day):
                continue
            t0 = BASE + timedelta(days=day, hours=20)
            for subject in SUBJECTS:
                for j, node in enumerate(NODES[subject]):
                    fid = _uid(seed, f"retest:{label}:{rnd}:{node}")
                    a = AttemptFact(fid, subject, f"{subject}-rt-{rnd}-{node}", t0 + timedelta(minutes=2 * j), 0, "delayed_retest",
                                    _verdict(rr, _clip(lat.p(subject, node, day) - 0.05)), node, LEVEL)
                    attempts.append(Attempt(sid, a, rnd))
                    mine.append(a)
        # 三轮学校考试：考前一天出区间，考后录成绩（停用后的考试不录）
        er = _rng(seed, "exam", label)
        for idx, (rnd, exam_type, week) in enumerate(ROUNDS):
            exam_day = 7 * week - 2
            if not lat.active(exam_day):
                continue
            at = BASE + timedelta(days=exam_day - 1, hours=21)
            for subject in SUBJECTS:
                rows = _mastery_rows(seed, sid, subject, mine, at)
                pred = predict_interval(st.full_score, list(NODES[subject]), rows, interval, scales)
                true_frac = sum(lat.p(subject, n, exam_day) for n in NODES[subject]) / len(NODES[subject])
                frac = min(1.0, max(0.0, true_frac + lat.exam_offset[subject] + er.gauss(0.0, 0.04)))
                actual = round(frac * st.full_score * 2) / 2
                basis = pred.basis(st.full_score, params_version, WEIGHTS_SOURCE)
                exams.append(ExamRecord(sid, subject, rnd, idx, exam_type, week, st.full_score, pred.low, pred.high,
                                        predicted_median(basis, st.full_score), basis, actual))
    digest = _digest(seed, students, sessions, attempts, exams, probes, plans, errors)
    return Cohort(seed, BASE, WEEKS, tuple(students), tuple(sessions), tuple(attempts), tuple(exams), params_version, digest,
                  probes=tuple(probes), plans=tuple(plans), errors=tuple(errors))


def _digest(seed: int, students: Sequence[Student], sessions: Sequence[Session], attempts: Sequence[Attempt],
            exams: Sequence[ExamRecord], probes: Sequence[Probe] = (), plans: Sequence[PlanEvent] = (),
            errors: Sequence[ErrorEvent] = ()) -> str:
    def fact(a: AttemptFact) -> list:
        return [a.fact_id, a.subject, a.item_id, a.occurred_at.isoformat(), a.hint_level_used, a.evidence_kind, a.verdict,
                a.node_id, a.cognitive_level]
    doc = {
        "seed": seed,
        "students": [[s.label, s.student_id, s.wave, s.grade, s.full_score, s.enrolled_at.isoformat()] for s in students],
        "sessions": [[s.fact_id, s.student_id, s.occurred_at.isoformat()] for s in sessions],
        "attempts": [[a.student_id, a.retest_round, *fact(a.fact)] for a in attempts],
        "exams": [[e.student_id, e.subject, e.round, e.full_score, e.low, e.high, e.median, e.actual] for e in exams],
        "probes": [[p.student_id, p.fact.fact_id, p.fact.subject, p.fact.asked_at.isoformat(), p.fact.prediction, p.fact.verdict]
                   for p in probes],
        "plans": [[p.student_id, p.fact.fact_id, p.fact.kind, p.fact.plan_task_id, p.fact.occurred_at.isoformat()] for p in plans],
        "errors": [[e.student_id, e.created_at.isoformat(), e.fact.fact_id, e.fact.subject, e.fact.cross_subject_category]
                   for e in errors],
    }
    return hashlib.sha256(json.dumps(doc, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
