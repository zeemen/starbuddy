"""中圈指标（spec 设计要点 5；验收“在合成数据上与手算一致；样本不足不写”）。每个指标一组手算用例 + 一个样本不足的反例。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from insight import metrics as m
from insight.claims import InputError, apply_ops
from insight.shared import forbidden_matcher
from support import STUDENT, T0, ids, make_policy, templates

P = make_policy()
FORBID = forbidden_matcher()
NOW = T0


def att(i: int, *, days: float = 1, hint: int | None = 0, kind: str | None = "independent", verdict: str | None = "correct",
        subject: str = "aya", item: str | None = None, node: str | None = None, level: str | None = None) -> m.AttemptFact:
    return m.AttemptFact(f"a{i:04d}", subject, item or f"item-{i}", NOW - timedelta(days=days), hint, kind, verdict, node, level)


def one(results, key, subject="aya", qualifier=None):
    hits = [r for r in results if r.key == key and r.subject == subject and r.qualifier == qualifier]
    assert len(hits) == 1, [(r.key, r.subject, r.qualifier) for r in results]
    return hits[0]


def test_independent_solve_rate_hand_calc():
    xs = [att(i, level="apply", verdict="correct" if i < 7 else "incorrect") for i in range(10)]   # 7 / 10
    xs += [att(100, level="apply", hint=2, kind="hinted")]                                           # 用了提示：不计
    xs += [att(101, level="apply", days=20)]                                                         # 窗口外：不计
    xs += [att(102, level="apply", verdict="partial")]                                               # 部分对：不计
    xs += [att(103, level=None)]                                                                     # 没有认知层次：不计
    xs += [att(104, item="item-0", level="apply", verdict="incorrect", days=0.5)]                    # 非首次作答：不计
    r = one(m.independent_solve_rate(m.MetricInputs(tuple(xs)), P, NOW), "independent_solve_rate", qualifier="apply")
    assert r.value == {"v": 0.7, "n": 10} and r.target_id == "independent_solve_rate.apply" and r.confidence == 0.5
    assert m.independent_solve_rate(m.MetricInputs(tuple(xs[:9])), P, NOW) == []                     # 9 < 10


def test_hint_dependency_hand_calc():
    hints = [0] * 10 + [2, 3, 4, 6, 6]           # 15 题，阶梯长度 6：(2+3+4+6+6)/6/15 = 21/90
    xs = [att(i, hint=h, kind="hinted" if h else "independent") for i, h in enumerate(hints)]
    xs += [att(99, hint=None)]                   # 没有提示等级：不计
    r = one(m.hint_dependency(m.MetricInputs(tuple(xs)), P, NOW), "hint_dependency")
    assert r.value == {"v": round(21 / 90, 4), "n": 15}
    assert m.hint_dependency(m.MetricInputs(tuple(xs[:14])), P, NOW) == []


def test_transfer_gap_hand_calc():
    tr = [att(i, kind="transfer", node="MATH.SYN.EQ.LINEAR", verdict="correct" if i < 6 else "incorrect", days=30) for i in range(10)]
    mo = [att(100 + i, kind="independent", node="MATH.SYN.EQ.LINEAR", verdict="correct" if i < 9 else "incorrect") for i in range(12)]
    other = [att(200 + i, kind="independent", node="MATH.SYN.EQ.APPLY", verdict="incorrect") for i in range(5)]  # 别的节点：不算母题
    r = one(m.transfer_gap(m.MetricInputs(tuple(tr + mo + other)), P, NOW), "transfer_gap")
    assert r.value == {"v": round(0.6 - 0.75, 4), "n_transfer": 10, "n_mother": 12}
    assert m.transfer_gap(m.MetricInputs(tuple(tr[:9] + mo)), P, NOW) == []


def test_calibration_brier_and_direction():
    # 10 次“能”（8 对 2 错）、5 次“不能”（1 对）、5 次“不确定”（3 对）
    probes = [m.ProbeFact(f"p{i}", "aya", NOW - timedelta(days=1), "yes", "correct" if i < 8 else "incorrect") for i in range(10)]
    probes += [m.ProbeFact(f"q{i}", "aya", NOW - timedelta(days=1), "no", "correct" if i < 1 else "incorrect") for i in range(5)]
    probes += [m.ProbeFact(f"r{i}", "aya", NOW - timedelta(days=1), "unsure", "correct" if i < 3 else "incorrect") for i in range(5)]
    probes += [m.ProbeFact("s0", "aya", NOW, "yes", None)]  # 没有关联作答：不计
    brier = (2 * 1 + 1 * 1 + 5 * 0.25) / 20      # 能错 2 次 (1-0)^2；不能对 1 次 (0-1)^2；不确定 5 次 0.25
    bias = (2 * 1 - 1 + (2 * 0.5 - 3 * 0.5)) / 20
    r = one(m.self_assessment_calibration(m.MetricInputs(probes=tuple(probes)), P, NOW), "self_assessment_calibration", subject=None)
    assert r.value == {"v": round(brier, 4), "bias": round(bias, 4), "dir": "balanced", "n": 20}  # 0.025 在 ±0.05 内
    high = [m.ProbeFact(f"h{i}", "aya", NOW, "yes", "incorrect") for i in range(20)]
    assert one(m.self_assessment_calibration(m.MetricInputs(probes=tuple(high)), P, NOW), "self_assessment_calibration",
               subject=None).value["dir"] == "over"
    low = [m.ProbeFact(f"u{i}", "aya", NOW, "no", "correct") for i in range(20)]
    assert one(m.self_assessment_calibration(m.MetricInputs(probes=tuple(low)), P, NOW), "self_assessment_calibration",
               subject=None).value["dir"] == "under"
    assert m.self_assessment_calibration(m.MetricInputs(probes=tuple(probes[:19])), P, NOW) == []


def test_retention_rate_hand_calc():
    xs = [att(i, kind="delayed_retest", verdict="correct" if i < 6 else "incorrect", days=40) for i in range(8)]
    r = one(m.retention_rate(m.MetricInputs(tuple(xs)), P, NOW), "retention_rate")
    assert r.value == {"v": 0.75, "n": 8}
    assert m.retention_rate(m.MetricInputs(tuple(xs[:7])), P, NOW) == []


def test_plan_completion_hand_calc():
    def ev(i, kind, task, days):
        return m.PlanFact(f"b{i}", kind, f"t{task}", NOW - timedelta(days=days))
    plans = (ev(1, "plan_done", 1, 20), ev(2, "plan_skip", 2, 18), ev(3, "plan_skip", 3, 10), ev(4, "plan_done", 3, 9),
             ev(5, "plan_done", 4, 3), ev(6, "plan_skip", 5, 2))
    r = one(m.plan_completion_rate(m.MetricInputs(plans=plans), P, NOW), "plan_completion_rate", subject=None)
    assert r.value == {"v": 0.6, "n": 5}
    assert m.plan_completion_rate(m.MetricInputs(plans=plans[2:]), P, NOW) == []  # 不足 2 周


def test_reading_error_share_hand_calc():
    errs = [m.ErrorFact(f"x{i}", "aya", "reading_the_question" if i < 2 else "knowledge") for i in range(5)]
    errs += [m.ErrorFact(f"y{i}", "wenli", "reading_the_question" if i < 1 else "knowledge") for i in range(5)]
    r = one(m.reading_question_error_share(m.MetricInputs(errors=tuple(errs)), P, NOW), "reading_question_error_share", subject=None)
    assert r.value == {"v": 0.3, "n": 10, "subjects": 2}
    assert m.reading_question_error_share(m.MetricInputs(errors=tuple(errs[:9])), P, NOW) == []  # 文砺只有 4 条


def test_persistence_hand_calc():
    cn = timezone(timedelta(hours=8))
    now = datetime(2026, 10, 29, 12, 0, tzinfo=cn)
    # 近 4 周（10-02 … 10-29）活跃日：10-03、10-04、10-08（中断 3 天，恢复）、10-20（中断 11 天，未按时恢复）、10-21、
    # 10-27（中断 5 天，恢复）；09-20 在窗口外但证明数据已满 4 周 → 6 / 4 天每周，3 次中断 2 次恢复
    days = ["2026-09-20", "2026-10-03", "2026-10-04", "2026-10-08", "2026-10-20", "2026-10-21", "2026-10-27"]
    acts = tuple(m.ActivityFact(f"s{i}", datetime.fromisoformat(d + "T09:00:00+08:00")) for i, d in enumerate(days))
    r = one(m.persistence(m.MetricInputs(activity=acts), P, now), "persistence", subject=None)
    assert r.value == {"v": 6 / 4, "weeks": 4, "breaks": 3, "recovery": round(2 / 3, 4)} and r.n == 6
    # 数据不足 4 周
    assert m.persistence(m.MetricInputs(activity=acts[1:]), P, now) == []
    # 尾部中断（10-08 之后 21 天）超过恢复期限算一次未恢复
    r2 = one(m.persistence(m.MetricInputs(activity=acts[:4]), P, now), "persistence", subject=None)
    assert r2.value["breaks"] == 2 and r2.value["recovery"] == 0.5


def test_definitions_pass_lexicon_and_are_blocked_when_they_do_not():
    m.check_definitions(FORBID)
    with pytest.raises(InputError):
        m.check_definitions(FORBID, {"focus": ("middle.g7.x", "走神次数", "每次会话走神的次数")})
    with pytest.raises(InputError):
        m.check_definitions(FORBID, {"iq": ("middle.g7.x", "学习天赋", "说明")})


def test_metric_claims_agent_only_hypothesis_and_revised():
    tpl = templates(P)["metric"]
    res = m.compute_metrics(m.MetricInputs(tuple(att(i, kind="delayed_retest", days=40) for i in range(8))), P, NOW, FORBID)
    ops = m.plan_metric_claims([], res, P, NOW, tpl, STUDENT)
    assert [(o.to_status, o.reason) for o in ops] == [("hypothesis", "created")]
    f = ops[0].fields
    assert f["visibility"] == ("agent",) and f["target_type"] == "metric" and f["basis_id"] is None and f["evidence_ids"]
    claims = apply_ops([], ops, NOW, ids())
    # 新结果：revised；样本不足（没有结果）：不动；到期：待验证后仍只随新结果 revised，不回到成立
    res2 = m.compute_metrics(m.MetricInputs(tuple(att(i, kind="delayed_retest", verdict="incorrect", days=40) for i in range(8))),
                             P, NOW, FORBID)
    ops2 = m.plan_metric_claims(claims, res2, P, NOW + timedelta(days=1), tpl, STUDENT)
    assert [(o.to_status, o.reason) for o in ops2] == [("hypothesis", "revised")]
    assert m.plan_metric_claims(claims, [], P, NOW + timedelta(days=1), tpl, STUDENT) == []
    late = NOW + timedelta(days=15)
    ops3 = m.plan_metric_claims(claims, res2, P, late, tpl, STUDENT)
    assert [(o.to_status, o.reason) for o in ops3] == [("to_verify", "expired"), ("to_verify", "revised")]
    # 模板不允许 agent 或没有登记：不写
    assert m.plan_metric_claims([], res, P, NOW, None, STUDENT) == []


def test_compute_metrics_is_sorted_and_deterministic():
    xs = tuple(att(i, kind="delayed_retest", days=40) for i in range(8)) + tuple(att(100 + i, hint=0) for i in range(15))
    a = m.compute_metrics(m.MetricInputs(xs), P, NOW, FORBID)
    b = m.compute_metrics(m.MetricInputs(tuple(reversed(xs))), P, NOW, FORBID)
    assert a == b and [r.key for r in a] == ["hint_dependency", "retention_rate"]
