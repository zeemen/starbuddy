"""四项报表（spec 范围 1）：纯函数，不连库、不读系统时钟；都带最小样本门槛，不足时只输出 insufficient_sample，不出结论。

- G1-2 retest_report：首轮 vs 8 周后复测正确率（逐学生配对）与 8 周续用率；
- G1-3 exam_report：每轮考试 80% 区间覆盖率、中值 MAE、轮间对比；区间内外直接调用洞察服务的 reconcile_plan（insight/predict.py）；
- G1-4 validity_report：中圈指标值与结局的相关与方向；指标值由 validity_pairs 调洞察服务 metrics.py 的纯函数计算；
- 附加 fairness_report：分组误差差异，分组只用合成的非敏感分组键（规则 12）。
结果只有三种：insufficient_sample / meets_unsigned_threshold / below_unsigned_threshold——对照的是未签字配置，
不是 Gate 1 判定（Gate 1 判定一律由人做）。
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta
from typing import Any, Callable, Mapping, Sequence

from insight import metrics as M
from insight.policy import Policy
from insight.predict import reconcile_plan

from .basis import predicted_median
from .cohort import Attempt, Cohort, ExamRecord, Session, Student
from .config import ExamCfg, FairnessCfg, RetestCfg, ValidityCfg, check_group_key

INSUFFICIENT = "insufficient_sample"
MEETS = "meets_unsigned_threshold"
BELOW = "below_unsigned_threshold"
GRADED = ("correct", "incorrect")


def _r(x: float) -> float:
    return round(x, 4)


def _mean(xs: Sequence[float]) -> float:
    return sum(xs) / len(xs)


def pearson(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    """皮尔逊相关；样本不足 2 或任一侧方差为 0 时返回 None（不出结论）"""
    if len(xs) != len(ys) or len(xs) < 2:
        return None
    mx, my = _mean(xs), _mean(ys)
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx <= 1e-12 or syy <= 1e-12:
        return None
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / math.sqrt(sxx * syy)


# ---------- G1-2 ----------
def improvement(retests: Sequence[Attempt], cfg: RetestCfg) -> dict[str, Any]:
    """首轮 vs 8 周后：同一名学生两轮都至少 min_items_per_round 道已判分复测才配对"""
    acc: dict[str, dict[str, list[bool]]] = {}
    for a in retests:
        if a.retest_round in ("first", "week8") and a.fact.verdict in GRADED:
            acc.setdefault(a.student_id, {}).setdefault(a.retest_round, []).append(a.fact.verdict == "correct")
    pairs = []
    for sid in sorted(acc):
        f, w = acc[sid].get("first", []), acc[sid].get("week8", [])
        if len(f) >= cfg.min_items_per_round and len(w) >= cfg.min_items_per_round:
            pairs.append((sum(f) / len(f), sum(w) / len(w)))
    out: dict[str, Any] = {"paired_students": len(pairs), "min_paired_students": cfg.min_paired_students,
                           "min_items_per_round": cfg.min_items_per_round}
    if len(pairs) < cfg.min_paired_students:
        return {**out, "result": INSUFFICIENT}
    deltas = [w - f for f, w in pairs]
    mean_delta = _mean(deltas)
    share = sum(1 for d in deltas if d > 0) / len(deltas)
    ok = mean_delta > cfg.min_mean_delta and share >= cfg.min_share_improved
    return {**out, "first_accuracy": _r(_mean([f for f, _ in pairs])), "week8_accuracy": _r(_mean([w for _, w in pairs])),
            "mean_delta": _r(mean_delta), "share_improved": _r(share), "result": MEETS if ok else BELOW}


def continuation(students: Sequence[Student], sessions: Sequence[Session], horizon_end: datetime,
                 cfg: RetestCfg) -> dict[str, Any]:
    """8 周续用：入组后第 after_days 天起 window_days 天内仍有会话（左闭右开）。
    分母 = 入组学生中观察窗完整者，含入组后从未使用的学生（计为未续用）；观察窗超出数据截止的学生不计（删失）"""
    by: dict[str, list[datetime]] = {}
    for s in sessions:
        by.setdefault(s.student_id, []).append(s.occurred_at)
    evaluable = continued = censored = never_used = 0
    for st in students:
        start = st.enrolled_at + timedelta(days=cfg.continuation_after_days)
        end = start + timedelta(days=cfg.continuation_window_days)
        if end > horizon_end:
            censored += 1
            continue
        evaluable += 1
        times = by.get(st.student_id, [])
        never_used += not times
        continued += any(start <= t < end for t in times)
    out: dict[str, Any] = {"enrolled": len(students), "evaluable": evaluable, "censored": censored,
                           "never_used_evaluable": never_used, "min_enrolled_students": cfg.min_enrolled_students}
    if evaluable < cfg.min_enrolled_students:
        return {**out, "result": INSUFFICIENT}
    rate = continued / evaluable
    return {**out, "continued": continued, "rate": _r(rate), "min_rate": cfg.continuation_min_rate,
            "result": MEETS if rate >= cfg.continuation_min_rate else BELOW}


def _combine(*results: str) -> str:
    if INSUFFICIENT in results:
        return INSUFFICIENT
    return MEETS if all(r == MEETS for r in results) else BELOW


def retest_report(students: Sequence[Student], retests: Sequence[Attempt], sessions: Sequence[Session], horizon_end: datetime,
                  cfg: RetestCfg) -> dict[str, Any]:
    imp = improvement(retests, cfg)
    cont = continuation(students, sessions, horizon_end, cfg)
    return {"improvement": imp, "continuation": cont, "result": _combine(imp["result"], cont["result"])}


# ---------- G1-3 ----------
def exam_report(records: Sequence[ExamRecord], z: float, cfg: ExamCfg) -> dict[str, Any]:
    """每轮：覆盖率（reconcile_plan 判区间内外）、中值 MAE 与偏差（占满分比例）；轮间：各轮对首轮"""
    by_round: dict[int, list[ExamRecord]] = {}
    for e in records:
        by_round.setdefault(e.round_index, []).append(e)
    rounds: list[dict[str, Any]] = []
    raw: list[tuple[str, float, float]] = []   # 样本足够的轮：(轮次, 覆盖率, MAE)
    for idx in sorted(by_round):
        xs = by_round[idx]
        students = len({e.student_id for e in xs})
        row: dict[str, Any] = {"round": xs[0].round, "exam_type": xs[0].exam_type, "n": len(xs), "students": students}
        if len(xs) < cfg.min_records_per_round or students < cfg.min_students_per_round:
            rounds.append({**row, "result": INSUFFICIENT})
            continue
        pos = {"inside": 0, "below": 0, "above": 0}
        for e in xs:
            pos[reconcile_plan(e.actual, e.low, e.high, e.basis, z).position] += 1
        cov = pos["inside"] / len(xs)
        med = [predicted_median(e.basis, e.full_score) for e in xs]
        mae = _mean([abs(e.actual - m) / e.full_score for e, m in zip(xs, med)])
        bias = _mean([(e.actual - m) / e.full_score for e, m in zip(xs, med)])
        within = cfg.coverage_low <= cov <= cfg.coverage_high
        rounds.append({**row, "coverage": _r(cov), "inside": pos["inside"], "below": pos["below"], "above": pos["above"],
                       "mae": _r(mae), "bias": _r(bias), "coverage_within": within, "result": MEETS if within else BELOW})
        raw.append((xs[0].round, cov, mae))
    out: dict[str, Any] = {"nominal": cfg.nominal, "coverage_range": [cfg.coverage_low, cfg.coverage_high],
                           "min_records_per_round": cfg.min_records_per_round,
                           "min_students_per_round": cfg.min_students_per_round, "min_rounds": cfg.min_rounds,
                           "rounds": rounds, "sufficient_rounds": len(raw)}
    if len(raw) < cfg.min_rounds:
        return {**out, "result": INSUFFICIENT}
    first = raw[0]
    out["comparisons"] = [{"round": rnd, "vs": first[0], "coverage_change": _r(cov - first[1]), "mae_change": _r(mae - first[2]),
                           "mae_relative_change": _r((mae - first[2]) / first[2]) if first[2] > 0 else None}
                          for rnd, cov, mae in raw[1:]]
    last = raw[-1]
    decreased = last[2] < first[2] and (first[2] - last[2]) >= cfg.min_mae_relative_drop * first[2]
    out["mae_decreased_vs_first"] = decreased
    all_within = all(r.get("coverage_within") for r in rounds if r["result"] != INSUFFICIENT)
    return {**out, "result": MEETS if all_within and decreased else BELOW}


# ---------- G1-4 ----------
def _metric_values(facts: Sequence[M.AttemptFact], policy: Policy, now: datetime) -> dict[tuple[str, str], tuple[float, int]]:
    """(指标, 学科) → (值, 样本数)。独立解题率按认知层次分开算，这里按样本数加权合并到学科"""
    calcs: dict[str, Callable[..., list[M.MetricResult]]] = {
        "independent_solve_rate": M.independent_solve_rate, "hint_dependency": M.hint_dependency,
        "transfer_gap": M.transfer_gap, "retention_rate": M.retention_rate}
    inp = M.MetricInputs(attempts=tuple(a for a in facts if a.occurred_at <= now))  # 只用测量时点以前的作答（不泄漏结局）
    out: dict[tuple[str, str], tuple[float, int]] = {}
    for key, calc in calcs.items():
        acc: dict[str, list[tuple[float, int]]] = {}
        for res in calc(inp, policy, now):
            acc.setdefault(res.subject or "", []).append((float(res.value["v"]), res.n))
        for subject, vs in acc.items():
            n = sum(k for _, k in vs)
            out[(key, subject)] = (sum(v * k for v, k in vs) / n, n)
    return out


def outcomes(cohort: Cohort) -> dict[tuple[str, str, str], float]:
    """(结局, 学生, 学科) → 值：week8_retest_accuracy（8 周后复测正确率）、last_exam_fraction（最后一轮考试得分率）"""
    out: dict[tuple[str, str, str], float] = {}
    acc: dict[tuple[str, str], list[bool]] = {}
    for a in cohort.retests:
        if a.retest_round == "week8" and a.fact.verdict in GRADED:
            acc.setdefault((a.student_id, a.fact.subject), []).append(a.fact.verdict == "correct")
    for (sid, subject), xs in acc.items():
        out[("week8_retest_accuracy", sid, subject)] = sum(xs) / len(xs)
    if cohort.exams:
        last = max(e.round_index for e in cohort.exams)
        for e in cohort.exams:
            if e.round_index == last:
                out[("last_exam_fraction", e.student_id, e.subject)] = e.actual / e.full_score
    return out


def validity_pairs(cohort: Cohort, policy: Policy, cfg: ValidityCfg) -> list[dict[str, Any]]:
    """逐学生 × 学科：第 measure_week 周末的指标值（洞察服务纯函数）与之后的结局。指标样本不足或没有结局的不成对"""
    now = cohort.base + timedelta(days=7 * cfg.measure_week)
    res = outcomes(cohort)
    rows = []
    for st in cohort.students:
        values = _metric_values(cohort.facts_of(st.student_id), policy, now)
        for (key, subject), (v, n) in sorted(values.items()):
            if key not in cfg.metrics:
                continue
            outcome = cfg.metrics[key][0]
            y = res.get((outcome, st.student_id, subject))
            if y is None:
                continue
            rows.append({"label": st.label, "student_id": st.student_id, "subject": subject, "metric": key, "value": _r(v),
                         "n": n, "outcome": outcome, "outcome_value": _r(y)})
    return rows


def student_pairs(rows: Sequence[Mapping[str, Any]]) -> dict[str, list[tuple[float, float]]]:
    """按学生聚合：同一学生同一指标的各科值取均值、结局取均值，一名学生一对（多科不重复计入独立样本）。按学生 ID 排序"""
    acc: dict[str, dict[str, list[tuple[float, float]]]] = {}
    for r in rows:
        acc.setdefault(r["metric"], {}).setdefault(r["student_id"], []).append((float(r["value"]), float(r["outcome_value"])))
    return {m: [(_mean([x for x, _ in v]), _mean([y for _, y in v])) for _, v in sorted(by.items())] for m, by in acc.items()}


def validity_report(pairs: Mapping[str, Sequence[tuple[float, float]]], cfg: ValidityCfg) -> dict[str, Any]:
    """pairs：指标 → 按学生聚合后的 (指标值, 结局)（student_pairs 的输出），一名学生一对"""
    items = []
    for key, (outcome, expected) in cfg.metrics.items():
        xs = pairs.get(key, ())
        row: dict[str, Any] = {"metric": key, "outcome": outcome, "expected_direction": expected, "students": len(xs)}
        if len(xs) < cfg.min_students:
            items.append({**row, "result": INSUFFICIENT})
            continue
        r = pearson([x for x, _ in xs], [y for _, y in xs])
        if r is None:
            items.append({**row, "result": INSUFFICIENT, "reason": "zero_variance"})
            continue
        observed = "positive" if r > 0 else "negative" if r < 0 else "none"
        ok = observed == expected and abs(r) >= cfg.min_abs_r
        items.append({**row, "r": _r(r), "observed_direction": observed, "result": MEETS if ok else BELOW})
    passing = sum(1 for i in items if i["result"] == MEETS)
    pending = sum(1 for i in items if i["result"] == INSUFFICIENT)
    result = MEETS if passing >= cfg.min_metrics_passing else INSUFFICIENT if passing + pending >= cfg.min_metrics_passing else BELOW
    return {"method": "pearson", "aggregation": "per_student_mean", "min_students": cfg.min_students, "min_abs_r": cfg.min_abs_r,
            "min_metrics_passing": cfg.min_metrics_passing, "measure_week": cfg.measure_week,
            "metrics": items, "passing": passing, "result": result}


# ---------- 附加：分组误差差异 ----------
def group_values(students: Sequence[Student]) -> dict[str, dict[str, str]]:
    return {s.student_id: {"wave": s.wave, "grade": str(s.grade)} for s in students}


def fairness_report(records: Sequence[ExamRecord], groups: Mapping[str, Mapping[str, str]], cfg: FairnessCfg) -> dict[str, Any]:
    """各分组的预测误差（占满分比例）：MAE 与平均偏差（实得 − 中值），组间最大差距对照阈值"""
    keys = []
    rows = []
    for key in cfg.group_keys:
        check_group_key(key)
        errs: dict[str, list[float]] = {}
        who: dict[str, set[str]] = {}
        for e in records:
            g = groups.get(e.student_id, {}).get(key)
            if g is not None:
                errs.setdefault(g, []).append((e.actual - predicted_median(e.basis, e.full_score)) / e.full_score)
                who.setdefault(g, set()).add(e.student_id)
        sizes = {g: len(v) for g, v in sorted(errs.items())}
        heads = {g: len(v) for g, v in sorted(who.items())}
        row: dict[str, Any] = {"group_key": key, "groups": sizes, "students": heads,
                               "min_records_per_group": cfg.min_records_per_group,
                               "min_students_per_group": cfg.min_students_per_group}
        if (len(errs) < 2 or min(sizes.values()) < cfg.min_records_per_group
                or min(heads.values()) < cfg.min_students_per_group):
            keys.append({**row, "result": INSUFFICIENT})
            continue
        mae = {g: _mean([abs(x) for x in v]) for g, v in errs.items()}
        bias = {g: _mean(v) for g, v in errs.items()}
        mae_gap, bias_gap = max(mae.values()) - min(mae.values()), max(bias.values()) - min(bias.values())
        for g in sorted(errs):
            rows.append({"group_key": key, "group": g, "n": sizes[g], "students": heads[g], "mae": _r(mae[g]), "bias": _r(bias[g])})
        ok = mae_gap <= cfg.max_mae_gap and bias_gap <= cfg.max_bias_gap
        keys.append({**row, "mae_gap": _r(mae_gap), "bias_gap": _r(bias_gap), "max_mae_gap": cfg.max_mae_gap,
                     "max_bias_gap": cfg.max_bias_gap, "result": MEETS if ok else BELOW})
    return {"keys": keys, "rows": rows, "result": _combine(*(k["result"] for k in keys))}
