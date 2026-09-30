"""中圈指标静默计算（spec 设计要点 5；SIE V1.1 第五章“中圈学习力指标”；字段字典 middle.g5 / middle.g7）：纯函数，不连库。

八个指标，定义与最低样本取 SIE 第五章（最低样本是策略配置位）；样本不足不出结果（不写判断）：
  independent_solve_rate      无提示首次作答正确数 ÷ 无提示首次作答数；按学科 × 认知层次，滚动窗口（window_days），每层 ≥ 最低样本
  hint_dependency             每题所用最高提示等级 ÷ 该科提示阶梯长度，取均值；按学科，滚动窗口
  transfer_gap                迁移题（evidence_kind = transfer）独立正确率 − 同节点母题（independent）独立正确率；按学科，两类各 ≥ 最低样本
  self_assessment_calibration 做题前“能做对吗”的 Brier 分数与偏差方向（高估 over / 低估 under / 基本一致 balanced）；跨科
  retention_rate              延迟复测（evidence_kind = delayed_retest，KGE：间隔 ≥ 7 天）做对 ÷ 复测数；按学科
  plan_completion_rate        计划任务里完成的比例（以学生调整后的计划为准：看每个任务最后留下的是完成还是跳过）；跨科，需 ≥ 2 周数据
  reading_question_error_share 审题大类错因 ÷ 全部错因（当前错因）；跨科，至少两科各有 ≥ 5 条
  persistence                 近 N 周的周活跃天数；中断 ≥ break_days 天后 recover_days 天内恢复的比例；需 ≥ N 周数据
判分只取 correct / incorrect（partial、needs_review、not_applicable 不计入），与字段字典 middle.g7.confidence_probe 的口径一致。
偏差（lessons）：保持率的分母暂用“实际复测数”（KGE-04 的复测任务表还没有，“应复测”数不出来）。

结果只写 visibility = {agent} 的 metric 判断（待人确认 #6 默认），状态只到“假设”（指标未过效度检验）；任何学生、家长可见接口都不返回。
不推断智力、性格、情绪、专注力等（规则 12）：指标名与说明在计算前过禁用推断词表检查，命中即拒绝计算。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable, Mapping, Sequence

from .claims import AUDIENCE_ORDER, ClaimRow, InputError, Op, TemplateInfo, check_ops
from .policy import Policy
from .shared import COGNITIVE_LEVELS, Forbidden

ALGORITHM = "sie-middle-metrics"
ALGORITHM_VERSION = "0.1.0"
MODEL_VERSION = f"algo:{ALGORITHM}@{ALGORITHM_VERSION}"
MAX_EVIDENCE = 200
CN = timezone(timedelta(hours=8))  # 活跃日按北京时间划分
GRADED = ("correct", "incorrect")

# 指标键 → 字段字典 ID、名称、说明（计算前过禁用推断词表）
METRIC_DEFS: dict[str, tuple[str, str, str]] = {
    "independent_solve_rate": ("middle.g7.independent_solve_rate", "独立解题率", "无提示首次作答正确数除以无提示首次作答数，按学科与认知层次滚动计算"),
    "hint_dependency": ("middle.g7.hint_dependency", "提示依赖度", "每题所用最高提示等级按阶梯长度归一后的均值"),
    "transfer_gap": ("middle.g7.transfer_gap", "迁移差", "变式与跨章题独立正确率减同节点母题独立正确率"),
    "self_assessment_calibration": ("middle.g7.self_assessment_calibration", "自评校准", "做题前预判与实际结果的 Brier 分数及偏差方向"),
    "retention_rate": ("middle.g7.retention_rate", "保持率", "延迟复测做对数除以复测数"),
    "plan_completion_rate": ("middle.g5.plan_completion_rate", "计划完成率", "完成的计划任务数除以计划任务数"),
    "reading_question_error_share": ("middle.g7.reading_question_error_share", "审题类错因占比", "审题大类错因数除以全部错因数"),
    "persistence": ("middle.g5.persistence", "学习持续性", "周活跃天数与中断后按时恢复的比例，只与自己过去比较"),
}


@dataclass(frozen=True)
class AttemptFact:
    fact_id: str                 # platform.attempt.attempt_id（事实层 ID；见 lessons：0028 未授 response_evidence_id）
    subject: str
    item_id: str
    occurred_at: datetime
    hint_level_used: int | None
    evidence_kind: str | None    # independent / hinted / transfer / delayed_retest
    verdict: str | None          # 当前（未被取代的）判分
    node_id: str | None = None   # 来自 kge.item_tag（洞察服务暂无读权限，见 lessons）
    cognitive_level: str | None = None


@dataclass(frozen=True)
class ProbeFact:
    fact_id: str                 # insight.confidence_probe.id
    subject: str
    asked_at: datetime
    prediction: str              # yes / no / unsure
    verdict: str | None          # 关联作答的当前判分


@dataclass(frozen=True)
class PlanFact:
    fact_id: str                 # insight.behavior_event.id
    kind: str                    # plan_done / plan_skip
    plan_task_id: str
    occurred_at: datetime


@dataclass(frozen=True)
class ActivityFact:
    fact_id: str                 # 会话开始事件或作答
    occurred_at: datetime


@dataclass(frozen=True)
class ErrorFact:
    fact_id: str                 # platform.error_attribution.id（当前行）
    subject: str
    cross_subject_category: str


@dataclass(frozen=True)
class MetricInputs:
    attempts: tuple[AttemptFact, ...] = ()
    probes: tuple[ProbeFact, ...] = ()
    plans: tuple[PlanFact, ...] = ()
    activity: tuple[ActivityFact, ...] = ()
    errors: tuple[ErrorFact, ...] = ()


@dataclass(frozen=True)
class MetricResult:
    key: str
    subject: str | None
    qualifier: str | None
    value: Mapping[str, Any]
    n: int
    min_sample: int
    evidence_ids: tuple[str, ...]

    @property
    def target_id(self) -> str:
        return self.key if self.qualifier is None else f"{self.key}.{self.qualifier}"

    @property
    def field_id(self) -> str:
        return METRIC_DEFS[self.key][0]

    @property
    def confidence(self) -> float:
        return round(self.n / (self.n + self.min_sample), 3)


def check_definitions(forbidden: Forbidden, defs: Mapping[str, tuple[str, str, str]] = METRIC_DEFS) -> None:
    for key, (_field, name, desc) in defs.items():
        if forbidden(f"{key}\n{name}\n{desc}"):
            raise InputError("metric definition hits the forbidden-inference lexicon")


def _r(x: float) -> float:
    return round(x, 4)


def _ids(facts: Iterable[Any]) -> tuple[str, ...]:
    ids = sorted({f.fact_id for f in facts})
    return tuple(ids[-MAX_EVIDENCE:])


def _in_window(t: datetime, now: datetime, days: int) -> bool:
    return now - timedelta(days=days) < t <= now


def independent_solve_rate(inp: MetricInputs, policy: Policy, now: datetime) -> list[MetricResult]:
    mp = policy.metrics
    first: dict[tuple[str, str], AttemptFact] = {}
    for a in sorted(inp.attempts, key=lambda x: (x.occurred_at, x.fact_id)):
        first.setdefault((a.subject, a.item_id), a)
    groups: dict[tuple[str, str], list[AttemptFact]] = {}
    for a in first.values():
        if (a.hint_level_used == 0 and a.verdict in GRADED and a.cognitive_level in COGNITIVE_LEVELS
                and _in_window(a.occurred_at, now, mp.window_days)):
            groups.setdefault((a.subject, a.cognitive_level), []).append(a)
    k = mp.min_samples["independent_solve_rate"]
    out = []
    for (subject, level), xs in sorted(groups.items()):
        if len(xs) >= k:
            correct = sum(1 for a in xs if a.verdict == "correct")
            out.append(MetricResult("independent_solve_rate", subject, level, {"v": _r(correct / len(xs)), "n": len(xs)},
                                    len(xs), k, _ids(xs)))
    return out


def hint_dependency(inp: MetricInputs, policy: Policy, now: datetime) -> list[MetricResult]:
    mp = policy.metrics
    groups: dict[str, list[AttemptFact]] = {}
    for a in inp.attempts:
        if a.hint_level_used is not None and _in_window(a.occurred_at, now, mp.window_days):
            groups.setdefault(a.subject, []).append(a)
    k = mp.min_samples["hint_dependency"]
    out = []
    for subject, xs in sorted(groups.items()):
        if len(xs) >= k:
            ladder = mp.hint_ladder_length[subject]
            v = sum(min(a.hint_level_used or 0, ladder) / ladder for a in xs) / len(xs)
            out.append(MetricResult("hint_dependency", subject, None, {"v": _r(v), "n": len(xs)}, len(xs), k, _ids(xs)))
    return out


def transfer_gap(inp: MetricInputs, policy: Policy, now: datetime) -> list[MetricResult]:
    k = policy.metrics.min_samples["transfer_gap"]
    by_subject: dict[str, list[AttemptFact]] = {}
    for a in inp.attempts:
        if a.hint_level_used == 0 and a.verdict in GRADED and a.node_id and a.occurred_at <= now:
            by_subject.setdefault(a.subject, []).append(a)
    out = []
    for subject, xs in sorted(by_subject.items()):
        tr = [a for a in xs if a.evidence_kind == "transfer"]
        nodes = {a.node_id for a in tr}
        mother = [a for a in xs if a.evidence_kind == "independent" and a.node_id in nodes]
        if len(tr) >= k and len(mother) >= k:
            rt = sum(a.verdict == "correct" for a in tr) / len(tr)
            rm = sum(a.verdict == "correct" for a in mother) / len(mother)
            n = min(len(tr), len(mother))
            out.append(MetricResult("transfer_gap", subject, None,
                                    {"v": _r(rt - rm), "n_transfer": len(tr), "n_mother": len(mother)}, n, k, _ids(tr + mother)))
    return out


def self_assessment_calibration(inp: MetricInputs, policy: Policy, now: datetime) -> list[MetricResult]:
    mp = policy.metrics
    xs = [p for p in inp.probes if p.verdict in GRADED and p.prediction in mp.probe_probability and p.asked_at <= now]
    k = mp.min_samples["self_assessment_calibration"]
    if len(xs) < k:
        return []
    diffs = [mp.probe_probability[p.prediction] - (1.0 if p.verdict == "correct" else 0.0) for p in xs]
    brier = sum(d * d for d in diffs) / len(xs)
    bias = sum(diffs) / len(xs)
    eps = mp.calibration_bias_epsilon
    direction = "over" if bias > eps else "under" if bias < -eps else "balanced"
    return [MetricResult("self_assessment_calibration", None, None, {"v": _r(brier), "bias": _r(bias), "dir": direction,
                                                                      "n": len(xs)}, len(xs), k, _ids(xs))]


def retention_rate(inp: MetricInputs, policy: Policy, now: datetime) -> list[MetricResult]:
    k = policy.metrics.min_samples["retention_rate"]
    groups: dict[str, list[AttemptFact]] = {}
    for a in inp.attempts:
        if a.evidence_kind == "delayed_retest" and a.verdict in GRADED and a.occurred_at <= now:
            groups.setdefault(a.subject, []).append(a)
    out = []
    for subject, xs in sorted(groups.items()):
        if len(xs) >= k:
            v = sum(a.verdict == "correct" for a in xs) / len(xs)
            out.append(MetricResult("retention_rate", subject, None, {"v": _r(v), "n": len(xs)}, len(xs), k, _ids(xs)))
    return out


def plan_completion_rate(inp: MetricInputs, policy: Policy, now: datetime) -> list[MetricResult]:
    days = policy.metrics.min_samples["plan_completion_rate_days"]
    xs = sorted((p for p in inp.plans if p.kind in ("plan_done", "plan_skip") and p.occurred_at <= now),
                key=lambda p: (p.occurred_at, p.fact_id))
    if not xs or xs[0].occurred_at > now - timedelta(days=days):
        return []
    last: dict[str, str] = {}
    for p in xs:
        last[p.plan_task_id] = p.kind  # 以学生调整后的计划为准：同一任务最后一次记录说了算
    done = sum(1 for kind in last.values() if kind == "plan_done")
    return [MetricResult("plan_completion_rate", None, None, {"v": _r(done / len(last)), "n": len(last)}, len(last), 1, _ids(xs))]


def reading_question_error_share(inp: MetricInputs, policy: Policy, now: datetime) -> list[MetricResult]:
    per = policy.metrics.min_samples["reading_question_error_share_per_subject"]
    need = policy.metrics.min_samples["reading_question_error_share_subjects"]
    counts: dict[str, int] = {}
    for e in inp.errors:
        counts[e.subject] = counts.get(e.subject, 0) + 1
    if sum(1 for n in counts.values() if n >= per) < need:
        return []
    reading = sum(1 for e in inp.errors if e.cross_subject_category == "reading_the_question")
    n = len(inp.errors)
    return [MetricResult("reading_question_error_share", None, None,
                         {"v": _r(reading / n), "n": n, "subjects": sum(1 for c in counts.values() if c >= per)},
                         n, per * need, _ids(inp.errors))]


def persistence(inp: MetricInputs, policy: Policy, now: datetime) -> list[MetricResult]:
    mp = policy.metrics
    weeks = mp.min_samples["persistence_weeks"]
    facts = [a for a in inp.activity if a.occurred_at <= now]
    if not facts or min(a.occurred_at for a in facts) > now - timedelta(days=7 * weeks):
        return []
    today = now.astimezone(CN).date()
    start = today - timedelta(days=7 * weeks - 1)
    in_window = [a for a in facts if start <= a.occurred_at.astimezone(CN).date() <= today]
    days: list[date] = sorted({a.occurred_at.astimezone(CN).date() for a in in_window})
    breaks, recovered = 0, 0
    for a, b in zip(days, days[1:]):
        gap = (b - a).days - 1
        if gap >= mp.break_days:
            breaks += 1
            recovered += gap <= mp.recover_days
    if days:
        trailing = (today - days[-1]).days
        if trailing > mp.recover_days:
            breaks += 1  # 已中断且超过恢复期限：计入未恢复；中断中但仍在期限内的不计（结果未定）
    value: dict[str, Any] = {"v": _r(len(days) / weeks), "weeks": weeks, "breaks": breaks}
    if breaks:
        value["recovery"] = _r(recovered / breaks)
    return [MetricResult("persistence", None, None, value, len(days), weeks, _ids(in_window))]


CALCULATORS = (independent_solve_rate, hint_dependency, transfer_gap, self_assessment_calibration, retention_rate,
               plan_completion_rate, reading_question_error_share, persistence)


def compute_metrics(inp: MetricInputs, policy: Policy, now: datetime, forbidden: Forbidden) -> list[MetricResult]:
    if now.tzinfo is None:
        raise InputError("now must be timezone-aware")
    check_definitions(forbidden)
    out: list[MetricResult] = []
    for calc in CALCULATORS:
        out.extend(calc(inp, policy, now))
    return sorted(out, key=lambda r: (r.key, r.subject or "", r.qualifier or ""))


# ---------- 写成判断（visibility = {agent}，只到“假设”） ----------
def plan_metric_claims(existing: Sequence[ClaimRow], results: Sequence[MetricResult], policy: Policy, now: datetime,
                       tpl: TemplateInfo | None, student_id: str) -> list[Op]:
    """每个“指标 × 学科 × 限定”至多一条活着的判断。样本不足（本次没有结果）不新建、不改；已有判断照常到期。
    指标未过效度检验：新建只到假设；待验证的指标判断只随新结果 revised，不回到成立"""
    if tpl is None or "agent" not in tpl.max_visibility:
        return []
    ref = policy.templates["metric"]
    live: dict[tuple[str, ...], ClaimRow] = {}
    for c in sorted(existing, key=lambda x: (x.created_at, x.id)):
        if c.template_key == ref.key and c.target_type == "metric" and c.status in ("hypothesis", "established", "to_verify"):
            live.setdefault(("metric", c.subject or "", c.target_id), c)
    res = {("metric", r.subject or "", r.target_id): r for r in results}
    ops: list[Op] = []
    for u in sorted(set(live) | set(res)):
        c, r = live.get(u), res.get(u)
        status = c.status if c else None
        if c is not None and status in ("hypothesis", "established") and c.expires_at <= now:
            ops.append(Op(c.id, u, status, "to_verify", "expired"))
            status = "to_verify"
        if r is None:
            continue
        want = {"value": dict(r.value), "confidence": r.confidence, "evidence_ids": r.evidence_ids, "model_version": MODEL_VERSION}
        if c is None:
            ops.append(Op(None, u, None, "hypothesis", "created", {
                "student_id": student_id, "subject": r.subject, "target_type": "metric", "target_id": r.target_id,
                "template_key": tpl.key, "template_version": tpl.version, "source_type": "inferred", "basis_kind": None,
                "basis_id": None, "visibility": tuple(a for a in AUDIENCE_ORDER if a == "agent"),
                "expires_at": now + min(timedelta(days=policy.ttl_days["metric"]), tpl.max_ttl), **want}))
            continue
        changed = {k: v for k, v in want.items() if _changed(getattr(c, k), v)}
        if changed:
            ops.append(Op(c.id, u, status, status, "revised", changed))
        if tuple(c.visibility) != ("agent",):
            ops.append(Op(c.id, u, status, status, "visibility_changed", {"visibility": ("agent",)}))
    check_ops(ops)
    return ops


def _changed(old: Any, new: Any) -> bool:
    if isinstance(new, float):
        return old is None or abs(float(old) - new) > 5e-4
    if isinstance(new, tuple):
        return tuple(old or ()) != new
    if isinstance(new, dict):
        return dict(old or {}) != new
    return old != new
