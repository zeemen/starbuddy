"""Gate 1 证据管线（specs/gate1-evidence-sie04）：配置、合成队列、四项报表的纯函数测试与反例、证据包确定性与样例一致。

反例覆盖 spec 验收：覆盖率边界、MAE、轮间、相关方向、样本门槛；分组键不得是地区、学校、家庭（规则 12）。
不连库、不联网；用洞察服务的 venv 运行（make gate1-test）。
"""
from __future__ import annotations

import copy
import dataclasses
import hashlib
import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from insight import metrics as M
from insight import predict as P
from insight.policy import synthetic_policy
from insight.sie03_policy import synthetic_sie03

from gate1_evidence import SYNTHETIC_NOTICE
from gate1_evidence import reports as R
from gate1_evidence.basis import BasisError, predicted_median
from gate1_evidence.cohort import (NODES, OTHER_ERRORS, SIZE, WEEKS, Attempt, Cohort, ErrorEvent, ExamRecord, PlanEvent, Probe, Session,
                                   Student, generate)
from gate1_evidence.config import (CONFIG_PATH, UNSIGNED, VALIDITY_METRICS, ConfigError, ExamCfg, FairnessCfg, RetestCfg, ValidityCfg,
                                   load_config, parse_config)
from gate1_evidence.evidence import DATA_FILES, NEEDS_HUMAN, VOLATILE_METADATA, PackRefused, build, render, write_pack

TOOL = Path(__file__).resolve().parents[1]
SAMPLE = TOOL / "sample"
CN = timezone(timedelta(hours=8))
T0 = datetime(2027, 3, 1, 19, 0, tzinfo=CN)
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def _uid(tag: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, tag))


@pytest.fixture(scope="module")
def pack():
    return build()


@pytest.fixture(scope="module")
def files(pack):
    return render(pack, {"commit": "test", "worktree_dirty": False})


def _raw_config() -> dict:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


# ---------- 配置 ----------
def test_config_is_unsigned_and_fingerprinted():
    cfg = load_config()
    assert not cfg.signed and cfg.provenance()["signoff"] == UNSIGNED
    assert re.fullmatch(r"[0-9a-f]{16}", cfg.fingerprint)
    doc = _raw_config()
    assert all(doc[s]["signoff"] == UNSIGNED for s in ("retest_g12", "exam_g13", "validity_g14", "fairness"))
    assert (cfg.exam.coverage_low, cfg.exam.coverage_high, cfg.exam.nominal) == (0.7, 0.9, 0.8)
    changed = _raw_config()
    changed["exam_g13"]["min_rounds"] = 3
    assert parse_config(changed).fingerprint != cfg.fingerprint


@pytest.mark.parametrize("key", ["school", "region", "household", "family_income", "学校", "wave2"])
def test_config_rejects_sensitive_or_unknown_group_keys(key):
    doc = _raw_config()
    doc["fairness"]["group_keys"] = ["wave", key]
    with pytest.raises(ConfigError):
        parse_config(doc)


@pytest.mark.parametrize("path,value", [
    (("exam_g13", "coverage_low"), 0.85),          # 区间不包住 0.8
    (("exam_g13", "coverage_high"), 0.75),
    (("exam_g13", "nominal"), 0.9),
    (("retest_g12", "signoff"), "已签字"),           # 未签字配置里不能自称已签字
    (("validity_g14", "method"), "spearman"),
    (("retest_g12", "min_paired_students"), 1),
])
def test_config_rejects_bad_values(path, value):
    doc = _raw_config()
    doc[path[0]][path[1]] = value
    with pytest.raises(ConfigError):
        parse_config(doc)


def test_config_rejects_extra_and_missing_keys():
    doc = _raw_config()
    doc["fairness"]["max_gap_free_text"] = 1
    with pytest.raises(ConfigError):
        parse_config(doc)
    doc = _raw_config()
    del doc["validity_g14"]["min_abs_r"]
    with pytest.raises(ConfigError):
        parse_config(doc)


# ---------- 合成队列 ----------
def test_cohort_is_deterministic_and_synthetic():
    sie = synthetic_sie03()
    a = generate(sie.exam.interval, sie.exam.scales, sie.config_version)
    b = generate(sie.exam.interval, sie.exam.scales, sie.config_version)
    c = generate(sie.exam.interval, sie.exam.scales, sie.config_version, seed=7)
    assert a.digest == b.digest and a == b
    assert c.digest != a.digest
    assert len(a.students) == SIZE == 40 and a.weeks == WEEKS == 12
    assert set(Student.__dataclass_fields__) == {"label", "student_id", "wave", "grade", "full_score", "enrolled_at"}
    never = [s for s in a.students if not any(x.student_id == s.student_id for x in a.sessions)]
    assert never and all(not any(e.student_id == s.student_id for e in a.exams) for s in never)   # 有从未使用的学生
    assert all(UUID_RE.fullmatch(s.student_id) for s in a.students)
    assert {s.wave for s in a.students} == {"a", "b"} and {s.grade for s in a.students} == {8, 9}
    rounds = {r.retest_round for r in a.retests}
    assert rounds == {"first", "week8"}
    assert len({e.round for e in a.exams}) == 3
    assert all(0 <= e.low <= e.high <= e.full_score and 0 <= e.actual <= e.full_score for e in a.exams)
    # 8 周后复测与首轮相隔 56 天（同一学生同一节点）
    first = {(x.student_id, x.fact.node_id): x.fact.occurred_at for x in a.retests if x.retest_round == "first"}
    for x in a.retests:
        if x.retest_round == "week8":
            assert x.fact.occurred_at - first[(x.student_id, x.fact.node_id)] == timedelta(days=56)


def test_cohort_has_dropouts_and_sparse_students(pack):
    c = pack.cohort
    with_week8 = {x.student_id for x in c.retests if x.retest_round == "week8"}
    assert 0 < len(with_week8) < len(c.students)               # 有中途停用的学生
    assert any(r["metric"] == "independent_solve_rate" for r in pack.metric_rows)
    assert len({r["student_id"] for r in pack.metric_rows if r["metric"] == "independent_solve_rate"}) < len(c.students)


# ---------- G1-2 ----------
RCFG = RetestCfg(min_mean_delta=0.0, min_share_improved=0.5, min_items_per_round=4, min_paired_students=3,
                 continuation_after_days=56, continuation_window_days=14, continuation_min_rate=0.6, min_enrolled_students=3)


def _retests(sid: str, first: list[bool], week8: list[bool]) -> list[Attempt]:
    out = []
    for rnd, xs in (("first", first), ("week8", week8)):
        for i, ok in enumerate(xs):
            f = M.AttemptFact(_uid(f"{sid}{rnd}{i}"), "aya", f"i{i}", T0, 0, "delayed_retest", "correct" if ok else "incorrect",
                              "SYN.AYA.N01", "apply")
            out.append(Attempt(sid, f, rnd))
    return out


def test_improvement_meets_when_week8_better():
    rows = sum((_retests(f"s{i}", [True, False, False, False], [True, True, True, False]) for i in range(3)), [])
    rep = R.improvement(rows, RCFG)
    assert rep["result"] == R.MEETS and rep["paired_students"] == 3
    assert rep["first_accuracy"] == 0.25 and rep["week8_accuracy"] == 0.75 and rep["mean_delta"] == 0.5
    assert rep["share_improved"] == 1.0


def test_improvement_counterexamples():
    worse = sum((_retests(f"s{i}", [True, True, True, False], [True, False, False, False]) for i in range(3)), [])
    assert R.improvement(worse, RCFG)["result"] == R.BELOW
    flat = sum((_retests(f"s{i}", [True, True, False, False], [True, True, False, False]) for i in range(3)), [])
    rep = R.improvement(flat, RCFG)
    assert rep["mean_delta"] == 0.0 and rep["result"] == R.BELOW
    # 均值差恰好等于门槛（0）而改善占比够：仍不算改善（严格大于）
    tie = (_retests("s0", [False] * 4, [True, False, False, False]) + _retests("s1", [False] * 4, [True, False, False, False])
           + _retests("s2", [True, True, False, False], [False] * 4))
    rep = R.improvement(tie, RCFG)
    assert rep["mean_delta"] == 0.0 and rep["share_improved"] >= 0.5 and rep["result"] == R.BELOW
    # 均值上升但只有少数学生改善：改善占比不到一半
    skew = (_retests("s0", [False] * 4, [True] * 4) + _retests("s1", [True, True, False, False], [True, False, False, False])
            + _retests("s2", [True, True, False, False], [True, True, False, False]))
    rep = R.improvement(skew, RCFG)
    assert rep["mean_delta"] > 0 and rep["share_improved"] < 0.5 and rep["result"] == R.BELOW


def test_improvement_share_boundary_is_inclusive():
    rows = (_retests("s0", [False] * 4, [True] * 4) + _retests("s1", [False] * 4, [True] * 4)
            + _retests("s2", [True] * 4, [True, True, True, False]) + _retests("s3", [True] * 4, [True] * 4))
    rep = R.improvement(rows, RCFG)
    assert rep["share_improved"] == 0.5 and rep["result"] == R.MEETS


def test_improvement_sample_threshold():
    rows = sum((_retests(f"s{i}", [False] * 4, [True] * 4) for i in range(2)), [])
    rep = R.improvement(rows, RCFG)
    assert rep["result"] == R.INSUFFICIENT and "mean_delta" not in rep and "week8_accuracy" not in rep
    # 某轮题数不够的学生不配对
    rows += _retests("s2", [False] * 3, [True] * 4)
    assert R.improvement(rows, RCFG)["paired_students"] == 2
    # 只判分 correct / incorrect：needs_review 不计入题数
    rows = sum((_retests(f"s{i}", [False] * 4, [True] * 4) for i in range(3)), [])
    rows = [Attempt(a.student_id, M.AttemptFact(**{**a.fact.__dict__, "verdict": "needs_review"}), a.retest_round)
            if a.student_id == "s0" and a.retest_round == "week8" else a for a in rows]
    assert R.improvement(rows, RCFG)["result"] == R.INSUFFICIENT


def _sessions(sid: str, days: list[float]) -> list[Session]:
    return [Session(_uid(f"{sid}{d}"), sid, T0 + timedelta(days=d)) for d in days]


def _st(sid: str, enrolled_day: float = 0) -> Student:
    return Student(sid, sid, "a", 9, 120, T0 + timedelta(days=enrolled_day))


def test_continuation_window_boundaries():
    """观察窗从入组时间起算（不是首次会话）：入组后第 56 天起 14 天，左闭右开"""
    horizon = T0 + timedelta(days=84)
    sts = [_st(x) for x in "abcd"]
    rows = (_sessions("a", [0, 56]) +              # 窗口起点：计入
            _sessions("b", [0, 70]) +              # 窗口终点（开区间）：不计入
            _sessions("c", [0, 30]) +              # 停用
            _sessions("d", [0, 69.9]))
    rep = R.continuation(sts, rows, horizon, RCFG)
    assert rep["evaluable"] == 4 and rep["continued"] == 2 and rep["rate"] == 0.5 and rep["result"] == R.BELOW
    more = sts + [_st("e", 1), _st("f", 2)]
    rep = R.continuation(more, rows + _sessions("e", [1, 60]) + _sessions("f", [58]), horizon, RCFG)
    assert rep["rate"] == round(4 / 6, 4) and rep["result"] == R.MEETS
    rep = R.continuation(sts + [_st("e", 1)], rows + _sessions("e", [1, 60]), horizon, RCFG)   # 恰好等于门槛 0.6：计入
    assert rep["rate"] == 0.6 and rep["result"] == R.MEETS
    # 锚点是入组时间：入组第 5 天、首次会话第 10 天；第 62 天的会话是入组后 57 天（计入），按首次会话算只有 52 天（不计）
    rep = R.continuation([_st("g", 5), _st("h", 5), _st("i", 5)],
                         _sessions("g", [10, 62]) + _sessions("h", [10, 62]) + _sessions("i", [10, 62]), horizon, RCFG)
    assert rep["continued"] == 3


def test_continuation_counts_never_used_students():
    """分母含入组后从未使用的学生（计为未续用）；只看会话会漏掉他们"""
    horizon = T0 + timedelta(days=84)
    sts = [_st("a"), _st("b"), _st("c"), _st("ghost1"), _st("ghost2")]
    rows = _sessions("a", [0, 60]) + _sessions("b", [0, 60]) + _sessions("c", [0, 60])
    rep = R.continuation(sts, rows, horizon, RCFG)
    assert rep["enrolled"] == 5 and rep["evaluable"] == 5 and rep["never_used_evaluable"] == 2
    assert rep["continued"] == 3 and rep["rate"] == 0.6 and rep["result"] == R.MEETS
    rep = R.continuation(sts + [_st("ghost3")], rows, horizon, RCFG)
    assert rep["rate"] == 0.5 and rep["result"] == R.BELOW
    # 会话里出现的非入组学生不计入分母
    assert R.continuation(sts, rows + _sessions("x", [0, 60]), horizon, RCFG)["evaluable"] == 5


def test_continuation_censoring_and_sample_threshold():
    horizon = T0 + timedelta(days=84)
    sts = [_st("a"), _st("b"), _st("late", 20)]
    rows = _sessions("a", [0, 60]) + _sessions("b", [0, 60]) + _sessions("late", [20, 80])
    rep = R.continuation(sts, rows, horizon, RCFG)
    assert rep["censored"] == 1 and rep["evaluable"] == 2 and rep["result"] == R.INSUFFICIENT and "rate" not in rep
    # 观察窗恰好到截止：不删失
    rep = R.continuation([_st("a"), _st("b"), _st("edge", 14)], rows, horizon, RCFG)
    assert rep["censored"] == 0 and rep["evaluable"] == 3


def test_retest_report_combines():
    horizon = T0 + timedelta(days=84)
    sts = [_st(f"s{i}") for i in range(3)]
    good = sum((_retests(f"s{i}", [False] * 4, [True] * 4) for i in range(3)), [])
    cont = sum((_sessions(f"s{i}", [0, 60]) for i in range(3)), [])
    assert R.retest_report(sts, good, cont, horizon, RCFG)["result"] == R.MEETS
    quit_ = sum((_sessions(f"s{i}", [0, 10]) for i in range(3)), [])
    assert R.retest_report(sts, good, quit_, horizon, RCFG)["result"] == R.BELOW
    assert R.retest_report(sts, good[:8], cont, horizon, RCFG)["result"] == R.INSUFFICIENT


# ---------- G1-3 ----------
ECFG = ExamCfg(nominal=0.8, coverage_low=0.7, coverage_high=0.9, min_records_per_round=10, min_students_per_round=10,
               min_rounds=2, min_mae_relative_drop=0.0)
Z = 1.2816
NODES_BASIS = [{"node_id": "SYN.AYA.N01", "p": 0.6, "t": 1.0, "sd": 0.1}]


def _exam(i: int, idx: int, actual: float, low: float = 50.0, high: float = 70.0, median: float = 60.0,
          full: int = 100, subject: str = "aya") -> ExamRecord:
    basis = {"mean": median / full, "nodes": NODES_BASIS}
    return ExamRecord(_uid(f"s{i}"), subject, f"r{idx + 1}", idx, "monthly", 4 * (idx + 1), full, low, high, median, basis, actual)


def test_predicted_median():
    assert predicted_median({"mean": 0.12345}, 120) == 14.8
    assert predicted_median({"mean": 0.55}, 100) == 55.0 and predicted_median({"mean": 1}, 100) == 100.0
    for bad in ({}, {"mean": None}, {"mean": 1.2}, {"mean": -0.1}, {"mean": float("nan")}, {"mean": True}):
        with pytest.raises(BasisError):
            predicted_median(bad, 100)
    with pytest.raises(BasisError):
        predicted_median({"mean": 0.5}, 0)


def test_exam_median_comes_from_basis_not_record_field():
    """中值一律由 predicted_median(basis) 算，不信任记录里的中值字段"""
    xs = [dataclasses.replace(_exam(i, 0, 60.0), median=0.0) for i in range(10)]
    rnd = R.exam_report(xs + _round(1, 8, err=1.0), Z, ECFG)["rounds"][0]
    assert rnd["mae"] == 0.0 and rnd["bias"] == 0.0


def test_exam_students_threshold():
    """每轮按学生计：10 条记录只来自 5 名学生（两科）时样本不足"""
    two = [_exam(i, 0, 60.0, subject=s) for i in range(5) for s in ("aya", "zhixing")]
    rep = R.exam_report(two + _round(1, 8), Z, ECFG)
    assert rep["rounds"][0]["n"] == 10 and rep["rounds"][0]["students"] == 5 and rep["rounds"][0]["result"] == R.INSUFFICIENT
    loose = ExamCfg(**{**ECFG.__dict__, "min_students_per_round": 5})
    assert R.exam_report(two + _round(1, 8), Z, loose)["rounds"][0]["result"] != R.INSUFFICIENT
    # 记录数门槛独立生效：9 名学生 9 条记录，学生门槛 5、记录门槛 10
    assert R.exam_report(_round(0, 8, n=9) + _round(1, 8), Z, loose)["rounds"][0]["result"] == R.INSUFFICIENT


def _round(idx: int, inside: int, n: int = 10, err: float = 5.0) -> list[ExamRecord]:
    """n 条记录，其中 inside 条落在 [50, 70]（偏离中值 err），其余高于区间"""
    return [_exam(i, idx, 60.0 + err if i < inside else 80.0) for i in range(n)]


@pytest.mark.parametrize("inside,within", [(6, False), (7, True), (8, True), (9, True), (10, False)])
def test_exam_coverage_boundaries(inside, within):
    rep = R.exam_report(_round(0, inside) + _round(1, inside, err=1.0), Z, ECFG)
    assert rep["rounds"][0]["coverage"] == inside / 10 and rep["rounds"][0]["coverage_within"] is within
    assert rep["result"] == (R.MEETS if within else R.BELOW)


def test_exam_interval_edges_follow_reconcile_plan():
    xs = [_exam(0, 0, 50.0), _exam(1, 0, 70.0), _exam(2, 0, 49.9), _exam(3, 0, 70.1)] + [_exam(i, 0, 60.0) for i in range(4, 10)]
    rnd = R.exam_report(xs, Z, ECFG)["rounds"][0]
    assert (rnd["inside"], rnd["below"], rnd["above"]) == (8, 1, 1)
    for e in xs:
        assert (P.reconcile_plan(e.actual, e.low, e.high, e.basis, Z).position == "inside") == (e.low <= e.actual <= e.high)


def test_exam_uses_insight_reconcile_plan(monkeypatch):
    calls = []

    def fake(raw, low, high, basis, z, *a, **k):
        calls.append((raw, low, high, z))
        return P.ReconcilePlan(False, "below", (), ("diagnosis_optimistic",))
    monkeypatch.setattr(R, "reconcile_plan", fake)
    rep = R.exam_report(_round(0, 10), Z, ECFG)
    assert len(calls) == 10 and all(c[3] == Z for c in calls)
    assert rep["rounds"][0]["coverage"] == 0.0 and rep["rounds"][0]["below"] == 10


def test_exam_mae_and_bias():
    xs = [_exam(i, 0, 60.0 + d, full=100) for i, d in enumerate([5, -5, 5, -5, 5, -5, 5, -5, 10, 0])]
    rnd = R.exam_report(xs + _round(1, 8, err=1.0), Z, ECFG)["rounds"][0]
    assert rnd["mae"] == 0.05 and rnd["bias"] == 0.01
    # 满分不同的口径按占满分比例计
    ys = [_exam(i, 0, 72.0 + (6 if i % 2 else -6), low=60, high=84, median=72.0, full=120) for i in range(10)]
    assert R.exam_report(ys + _round(1, 8, err=1.0), Z, ECFG)["rounds"][0]["mae"] == 0.05
    # 中值取预测依据的均值，不是区间中点（截断后区间不对称）
    zs = [_exam(i, 0, 55.0, low=50, high=70, median=55.0) for i in range(10)]
    rnd = R.exam_report(zs + _round(1, 8, err=1.0), Z, ECFG)["rounds"][0]
    assert rnd["mae"] == 0.0 and rnd["bias"] == 0.0


def test_exam_rounds_comparison():
    rep = R.exam_report(_round(0, 8, err=5.0) + _round(1, 8, err=2.0), Z, ECFG)
    assert rep["mae_decreased_vs_first"] is True and rep["result"] == R.MEETS
    cmp_ = rep["comparisons"][0]
    assert cmp_["round"] == "r2" and cmp_["vs"] == "r1" and cmp_["coverage_change"] == 0.0 and cmp_["mae_change"] < 0
    same = R.exam_report(_round(0, 8, err=5.0) + _round(1, 8, err=5.0), Z, ECFG)
    assert same["mae_decreased_vs_first"] is False and same["result"] == R.BELOW
    worse = R.exam_report(_round(0, 8, err=2.0) + _round(1, 8, err=5.0), Z, ECFG)
    assert worse["mae_decreased_vs_first"] is False and worse["result"] == R.BELOW
    # 比较的是最后一轮对首轮（中间一轮变好不算）
    mid = R.exam_report(_round(0, 8, err=4.0) + _round(1, 8, err=1.0) + _round(2, 8, err=4.5), Z, ECFG)
    assert mid["mae_decreased_vs_first"] is False and len(mid["comparisons"]) == 2
    # 相对下降门槛
    strict = ExamCfg(**{**ECFG.__dict__, "min_mae_relative_drop": 0.5})
    small = R.exam_report(_round(0, 8, err=5.0) + _round(1, 8, err=4.0), Z, strict)
    assert small["mae_decreased_vs_first"] is False


def test_exam_coverage_must_hold_in_every_round():
    rep = R.exam_report(_round(0, 6, err=5.0) + _round(1, 8, err=1.0), Z, ECFG)
    assert rep["mae_decreased_vs_first"] is True and rep["result"] == R.BELOW


def test_exam_sample_threshold():
    rep = R.exam_report(_round(0, 8) + _round(1, 8, n=9), Z, ECFG)
    assert rep["rounds"][1]["result"] == R.INSUFFICIENT and "coverage" not in rep["rounds"][1]
    assert rep["sufficient_rounds"] == 1 and rep["result"] == R.INSUFFICIENT and "comparisons" not in rep
    assert R.exam_report(_round(0, 8), Z, ECFG)["result"] == R.INSUFFICIENT
    # 样本不足的轮不参与比较：首轮不足时以第一个样本足够的轮为基准
    rep = R.exam_report(_round(0, 8, n=5) + _round(1, 8, err=5.0) + _round(2, 8, err=2.0), Z, ECFG)
    assert rep["comparisons"][0]["vs"] == "r2" and rep["result"] == R.MEETS


# ---------- G1-4 ----------
VCFG = ValidityCfg(measure_week=6, min_students=5, min_abs_r=0.3, min_metrics_passing=2,
                   metrics={"independent_solve_rate": ("week8_retest_accuracy", "positive"),
                            "hint_dependency": ("week8_retest_accuracy", "negative")},
                   counted=frozenset({"independent_solve_rate", "hint_dependency"}))


def test_pearson():
    assert R.pearson([1, 2, 3], [2, 4, 6]) == pytest.approx(1.0)
    assert R.pearson([1, 2, 3], [3, 2, 1]) == pytest.approx(-1.0)
    assert R.pearson([1, 2, 3, 4], [1, 3, 2, 4]) == pytest.approx(0.8)
    assert R.pearson([1, 1, 1], [1, 2, 3]) is None and R.pearson([1, 2], [3, 3]) is None
    assert R.pearson([1], [1]) is None and R.pearson([1, 2], [1]) is None


UP = [(0.1, 0.2), (0.2, 0.3), (0.3, 0.5), (0.4, 0.6), (0.5, 0.8)]
DOWN = [(x, 1 - y) for x, y in UP]
NOISE = [(0.1, 0.45), (0.2, 0.6), (0.3, 0.4), (0.4, 0.6), (0.5, 0.5)]   # 方向为正但很弱（r ≈ 0.18）


def test_validity_direction_and_threshold():
    rep = R.validity_report({"independent_solve_rate": UP, "hint_dependency": DOWN}, VCFG)
    assert [m["result"] for m in rep["metrics"]] == [R.MEETS, R.MEETS] and rep["result"] == R.MEETS
    assert rep["metrics"][1]["observed_direction"] == "negative"
    wrong = R.validity_report({"independent_solve_rate": DOWN, "hint_dependency": UP}, VCFG)   # 方向反了
    assert [m["result"] for m in wrong["metrics"]] == [R.BELOW, R.BELOW] and wrong["result"] == R.BELOW
    assert wrong["metrics"][0]["r"] < 0
    weak = R.validity_report({"independent_solve_rate": NOISE, "hint_dependency": DOWN}, VCFG)   # |r| 不够
    assert 0 < weak["metrics"][0]["r"] < 0.3 and weak["metrics"][0]["result"] == R.BELOW and weak["result"] == R.BELOW


def test_validity_abs_r_boundary_inclusive():
    cfg = ValidityCfg(**{**VCFG.__dict__, "min_abs_r": 0.8, "min_metrics_passing": 1})
    pts = [(1, 1), (2, 3), (3, 2), (4, 4)]   # r = 0.8
    rep = R.validity_report({"independent_solve_rate": pts * 2, "hint_dependency": DOWN}, cfg)
    assert rep["metrics"][0]["r"] == 0.8 and rep["metrics"][0]["result"] == R.MEETS


def test_validity_sample_threshold():
    rep = R.validity_report({"independent_solve_rate": UP[:4], "hint_dependency": DOWN}, VCFG)
    assert rep["metrics"][0]["result"] == R.INSUFFICIENT and "r" not in rep["metrics"][0]
    assert rep["passing"] == 1 and rep["result"] == R.INSUFFICIENT      # 还可能凑够两项：不出结论
    flat = R.validity_report({"independent_solve_rate": [(0.5, y) for _, y in UP], "hint_dependency": DOWN}, VCFG)
    assert flat["metrics"][0]["result"] == R.INSUFFICIENT and flat["metrics"][0]["reason"] == "zero_variance"
    lost = R.validity_report({"independent_solve_rate": UP[:4], "hint_dependency": UP}, VCFG)
    assert lost["result"] == R.BELOW                                    # 一项方向反了、一项样本不足：凑不够两项
    cfg1 = ValidityCfg(**{**VCFG.__dict__, "min_metrics_passing": 1})
    assert R.validity_report({"independent_solve_rate": DOWN, "hint_dependency": UP}, cfg1)["result"] == R.BELOW
    assert R.validity_report({}, VCFG)["result"] == R.INSUFFICIENT


def _vrow(sid: str, subject: str, x: float, y: float, metric: str = "independent_solve_rate") -> dict:
    return {"student_id": sid, "subject": subject, "metric": metric, "value": x, "outcome_value": y}


def test_student_pairs_aggregate_per_student():
    """同一学生两科只算一名独立样本：指标值与结局各取均值"""
    rows = [_vrow("s1", "aya", 0.2, 0.4), _vrow("s1", "zhixing", 0.4, 0.6), _vrow("s2", "aya", 0.5, 0.5),
            _vrow("s1", "aya", 0.9, 0.1, metric="hint_dependency")]
    got = R.student_pairs(rows)
    assert got["independent_solve_rate"] == [(pytest.approx(0.3), pytest.approx(0.5)), (0.5, 0.5)]
    assert got["hint_dependency"] == [(0.9, 0.1)]


def test_validity_students_threshold_counts_students_not_subjects():
    """4 名学生 × 2 科 = 8 行，门槛 5 名学生：按学生聚合后只有 4 对，样本不足"""
    rows = [_vrow(f"s{i}", s, 0.1 * i + (0.05 if s == "aya" else 0), 0.2 * i) for i in range(4) for s in ("aya", "zhixing")]
    rows += [_vrow(f"s{i}", "aya", x, 1 - y, metric="hint_dependency") for i, (x, y) in enumerate(UP)]
    rep = R.validity_report(R.student_pairs(rows), VCFG)
    assert rep["metrics"][0]["students"] == 4 and rep["metrics"][0]["result"] == R.INSUFFICIENT
    rows += [_vrow("s4", "aya", 0.45, 0.9)]
    rep = R.validity_report(R.student_pairs(rows), VCFG)
    assert rep["metrics"][0]["students"] == 5 and rep["metrics"][0]["result"] == R.MEETS


def test_validity_pairs_reuse_insight_metric_functions(pack):
    cohort, policy = pack.cohort, synthetic_policy()
    now = cohort.base + timedelta(days=7 * pack.config.validity.measure_week)
    rows = [r for r in pack.metric_rows if r["metric"] == "independent_solve_rate"]
    assert rows
    for r in rows[:5]:
        facts = tuple(a for a in cohort.facts_of(r["student_id"]) if a.occurred_at <= now)
        got = [x for x in M.independent_solve_rate(M.MetricInputs(attempts=facts), policy, now) if x.subject == r["subject"]]
        assert len(got) == 1 and r["value"] == round(got[0].value["v"], 4) and r["n"] == got[0].n
    # 不泄漏结局：保持率只看到测量时点前的首轮复测（每科 8 题），看不到 8 周后复测
    ret = [r for r in pack.metric_rows if r["metric"] == "retention_rate"]
    assert ret and all(r["n"] == len(NODES[r["subject"]]) for r in ret)
    # 样本不足（低频学生）不成对
    ms = policy.metrics.min_samples
    floor = {"plan_completion_rate": 1, "persistence": 1,
             "reading_question_error_share": ms["reading_question_error_share_per_subject"] * ms["reading_question_error_share_subjects"]}
    assert all(r["n"] >= floor.get(r["metric"], ms.get(r["metric"], 0)) for r in pack.metric_rows)
    assert all(r["n"] >= ms["self_assessment_calibration"] for r in pack.metric_rows if r["metric"] == "self_assessment_calibration")


def test_outcomes_use_week8_and_last_round(pack):
    res = R.outcomes(pack.cohort)
    last = max(e.round_index for e in pack.cohort.exams)
    for e in pack.cohort.exams:
        key = ("last_exam_fraction", e.student_id, e.subject)
        if e.round_index == last:
            assert res[key] == e.actual / e.full_score
    sid = next(x.student_id for x in pack.cohort.retests if x.retest_round == "week8")
    xs = [x.fact for x in pack.cohort.retests if x.student_id == sid and x.retest_round == "week8" and x.fact.subject == "aya"]
    assert res[("week8_retest_accuracy", sid, "aya")] == sum(a.verdict == "correct" for a in xs) / len(xs)


# ---------- G1-4 · SIE-05：全部 8 个静默指标 ----------
NEW_METRICS = ("self_assessment_calibration", "plan_completion_rate", "reading_question_error_share", "persistence")
METRICS_CSV = "label,student_id,subject,metric,value,n,outcome,outcome_value"


def test_validity_covers_all_eight_silent_metrics(pack, files):
    """效度检验覆盖洞察服务 metrics.py 的全部 8 个静默指标；新增四项的结果变量写在未签字配置里、逐项标“待签字”；metrics.csv 列不变"""
    assert set(VALIDITY_METRICS) == set(M.METRIC_DEFS) == set(R.METRIC_CALCULATORS) and len(VALIDITY_METRICS) == 8
    assert R.METRIC_CALCULATORS == {f.__name__: f for f in M.CALCULATORS}          # 直接用洞察服务的纯函数，不另写口径
    assert tuple(pack.config.validity.metrics) == VALIDITY_METRICS
    doc = _raw_config()["validity_g14"]["metrics"]
    for k in NEW_METRICS:
        assert "待签字" in doc[k]["$comment"] and doc[k]["outcome"] in ("week8_retest_accuracy", "last_exam_fraction")
    assert [m["metric"] for m in pack.g14["metrics"]] == list(VALIDITY_METRICS)
    assert {r["metric"] for r in pack.metric_rows} == set(VALIDITY_METRICS)        # 合成队列上每个指标都有配对
    assert files["metrics.csv"].splitlines()[0] == METRICS_CSV
    for k in NEW_METRICS:
        rows = [r for r in pack.metric_rows if r["metric"] == k]
        assert rows and all(r["subject"] == R.CROSS for r in rows)                  # 跨科指标每名学生一行
        assert len({r["student_id"] for r in rows}) == len(rows)
        assert f"| {k} |" in files["summary.md"]


def test_cohort_has_middle_circle_raw_materials(pack):
    """合成队列补齐原料：探针三种预判、计划完成与跳过（含先跳过后补做）、审题与其他错因、只浏览的会话；全部合成 ID"""
    c = pack.cohort
    ids = {s.student_id for s in c.students}
    assert {p.fact.prediction for p in c.probes} == {"yes", "no", "unsure"}
    assert {p.fact.verdict for p in c.probes} == {"correct", "incorrect"}
    attempt_at = {(a.student_id, a.fact.occurred_at) for a in c.attempts if a.retest_round is None}
    assert all((p.student_id, p.fact.asked_at + timedelta(seconds=30)) in attempt_at for p in c.probes)   # 探针都在作答之前
    assert {p.fact.kind for p in c.plans} == {"plan_done", "plan_skip"}
    by_task: dict[str, list] = {}
    for p in c.plans:
        by_task.setdefault(p.fact.plan_task_id, []).append(p.fact)
    redo = [xs for xs in by_task.values() if len(xs) > 1]
    assert redo and all([x.kind for x in sorted(xs, key=lambda x: x.occurred_at)] == ["plan_skip", "plan_done"] for xs in redo)
    cats = {e.fact.cross_subject_category for e in c.errors}
    assert "reading_the_question" in cats and cats - {"reading_the_question"} and cats <= {"reading_the_question", *OTHER_ERRORS}
    practice_days = {(a.student_id, a.fact.occurred_at.date()) for a in c.attempts if a.retest_round is None}
    assert any((s.student_id, s.occurred_at.date()) not in practice_days for s in c.sessions)              # 只浏览的会话
    assert {p.student_id for p in c.probes} | {p.student_id for p in c.plans} | {e.student_id for e in c.errors} <= ids
    assert all(UUID_RE.fullmatch(x.fact.fact_id) for x in (*c.probes, *c.plans, *c.errors))


def test_raw_materials_are_in_digest():
    sie = synthetic_sie03()
    a = generate(sie.exam.interval, sie.exam.scales, sie.config_version, size=3)
    from gate1_evidence.cohort import _digest
    base = _digest(a.seed, a.students, a.sessions, a.attempts, a.exams, a.probes, a.plans, a.errors)
    assert base == a.digest
    assert _digest(a.seed, a.students, a.sessions, a.attempts, a.exams, a.probes[1:], a.plans, a.errors) != base
    assert _digest(a.seed, a.students, a.sessions, a.attempts, a.exams, a.probes, a.plans[1:], a.errors) != base
    assert _digest(a.seed, a.students, a.sessions, a.attempts, a.exams, a.probes, a.plans, a.errors[1:]) != base


B = datetime(2027, 3, 1, 0, 0, tzinfo=CN)
NOW6 = B + timedelta(days=42)          # 第 6 周末（measure_week = 6）
# 小队列没有考试：新增四项一律对 8 周后复测（只为测配对与截断；真实配置的结局变量见 config.unsigned.json）
VCFG8 = ValidityCfg(measure_week=6, min_students=3, min_abs_r=0.3, min_metrics_passing=2,
                    metrics={k: ("week8_retest_accuracy", d) for k, (_o, d) in load_config().validity.metrics.items()},
                    counted=load_config().validity.counted)


def _week8(sid: str, subject: str, oks: list[bool]) -> list[Attempt]:
    return [Attempt(sid, M.AttemptFact(_uid(f"w8{sid}{subject}{i}"), subject, f"{subject}-w8-{i}", B + timedelta(days=69, minutes=i), 0,
                                       "delayed_retest", "correct" if ok else "incorrect", NODES[subject][0], "apply"), "week8")
            for i, ok in enumerate(oks)]


def _probes(sid: str, n: int, at_day: float = 10) -> list[Probe]:
    """n 次探针：前一半说“能”且做对、后一半说“不能”且做错（Brier = 0）"""
    return [Probe(sid, M.ProbeFact(_uid(f"pb{sid}{i}"), "aya", B + timedelta(days=at_day, minutes=i), "yes" if i < n // 2 else "no",
                                   "correct" if i < n // 2 else "incorrect")) for i in range(n)]


def _plan(sid: str, task: str, kind: str, day: float) -> PlanEvent:
    return PlanEvent(sid, M.PlanFact(_uid(f"pl{sid}{task}{kind}{day}"), kind, _uid(f"task{sid}{task}"), B + timedelta(days=day)))


def _errs(sid: str, subject: str, n: int, reading: int, day: float = 10) -> list[ErrorEvent]:
    return [ErrorEvent(sid, B + timedelta(days=day, minutes=i), M.ErrorFact(_uid(f"er{sid}{subject}{day}{i}"), subject,
                                                                           "reading_the_question" if i < reading else "thinking_method"))
            for i in range(n)]


def _mini(sid: str = "s1", *, probes=(), plans=(), errors=(), sessions=(), attempts=None) -> Cohort:
    """一名合成学生的小队列：8 周后复测数学 0.5、道法 1.0（跨科结局均值 0.75）"""
    attempts = list(attempts or []) + _week8(sid, "aya", [True, True, False, False]) + _week8(sid, "zhixing", [True] * 4)
    return Cohort(0, B, 12, (Student("S1", sid, "a", 9, 120, B),), tuple(sessions), tuple(attempts), (), "test", "test",
                  probes=tuple(probes), plans=tuple(plans), errors=tuple(errors))


def _pairs(c: Cohort) -> dict[str, dict]:
    return {r["metric"]: r for r in R.validity_pairs(c, synthetic_policy(), VCFG8)}


def test_metric_inputs_cut_at_measure_time():
    """原料按测量时点截断：之后的探针、计划、会话与错因都不进；会话开始与作答都算活跃"""
    sess = [Session(_uid("ss1"), "s1", B + timedelta(days=3)), Session(_uid("ss2"), "s1", NOW6 + timedelta(days=1))]
    c = _mini(probes=_probes("s1", 2) + _probes("s1", 2, at_day=43), plans=[_plan("s1", "t1", "plan_done", 1), _plan("s1", "t2", "plan_skip", 43)],
              errors=_errs("s1", "aya", 2, 1) + _errs("s1", "aya", 3, 3, day=43), sessions=sess)
    inp = R.metric_inputs(c, "s1", NOW6)
    assert len(inp.probes) == 2 and len(inp.plans) == 1 and len(inp.errors) == 2
    assert [a.fact_id for a in inp.activity] == [_uid("ss1")]            # 8 周后复测在测量时点之后，不算
    assert inp.attempts == ()
    assert R.metric_inputs(c, "other", NOW6) == M.MetricInputs()


def test_new_metrics_pair_with_cross_subject_outcome():
    """正例：四个跨科指标都出值，结局取各科均值（0.5 与 1.0 → 0.75），学科位为 CROSS"""
    sess = [Session(_uid(f"ss{d}"), "s1", B + timedelta(days=d, hours=19)) for d in range(0, 42)]
    plans = [_plan("s1", "t1", "plan_done", 1), _plan("s1", "t2", "plan_skip", 2), _plan("s1", "t2", "plan_done", 3),
             _plan("s1", "t3", "plan_skip", 20)]
    c = _mini(probes=_probes("s1", 20), plans=plans, errors=_errs("s1", "aya", 5, 3) + _errs("s1", "zhixing", 5, 1), sessions=sess)
    got = _pairs(c)
    assert set(got) == set(NEW_METRICS)
    assert all(r["subject"] == R.CROSS and r["outcome_value"] == 0.75 for r in got.values())
    assert got["self_assessment_calibration"]["value"] == 0.0 and got["self_assessment_calibration"]["n"] == 20
    assert got["plan_completion_rate"]["value"] == round(2 / 3, 4) and got["plan_completion_rate"]["n"] == 3   # 先跳过后补做按完成计
    assert got["reading_question_error_share"]["value"] == 0.4 and got["reading_question_error_share"]["n"] == 10
    assert got["persistence"]["value"] == 6.75        # 近 4 周（第 15–42 天，测量当天 0 点以前）活跃 27 天 ÷ 4 周
    # 结局变量取配置：未签字配置里自评校准、审题类错因占比对最后一轮考试（小队列没有考试 → 不成对），计划完成率、持续性对 8 周后复测
    cfg = dataclasses.replace(VCFG8, metrics=dict(load_config().validity.metrics))
    rows = R.validity_pairs(c, synthetic_policy(), cfg)
    assert {r["metric"]: r["outcome"] for r in rows} == {"plan_completion_rate": "week8_retest_accuracy",
                                                         "persistence": "week8_retest_accuracy"}


def test_new_metrics_insufficient_samples_do_not_pair():
    """反例：探针 19 次（< 20）、计划不足两周、只有一科错因够 5 条、活跃不足 4 周 → 洞察服务纯函数不出结果，不成对"""
    sess = [Session(_uid(f"ss{d}"), "s1", B + timedelta(days=d, hours=19)) for d in range(20, 42)]
    c = _mini(probes=_probes("s1", 19), plans=[_plan("s1", "t1", "plan_done", 30), _plan("s1", "t2", "plan_skip", 31)],
              errors=_errs("s1", "aya", 5, 3) + _errs("s1", "zhixing", 4, 1), sessions=sess)
    assert _pairs(c) == {}
    # 测量时点之后的错因不会把样本凑够
    late = _mini(errors=_errs("s1", "aya", 5, 3) + _errs("s1", "zhixing", 4, 1) + _errs("s1", "zhixing", 3, 0, day=43))
    assert "reading_question_error_share" not in _pairs(late)


def test_outcome_of_cross_subject_mean():
    res = {("week8_retest_accuracy", "s1", "aya"): 0.5, ("week8_retest_accuracy", "s1", "zhixing"): 1.0,
           ("last_exam_fraction", "s1", "aya"): 0.2, ("week8_retest_accuracy", "s2", "aya"): 0.1}
    assert R.outcome_of(res, "week8_retest_accuracy", "s1", R.CROSS) == 0.75
    assert R.outcome_of(res, "week8_retest_accuracy", "s1", "aya") == 0.5
    assert R.outcome_of(res, "last_exam_fraction", "s1", R.CROSS) == 0.2
    assert R.outcome_of(res, "last_exam_fraction", "s2", R.CROSS) is None


@pytest.mark.parametrize("key, expected", [("self_assessment_calibration", "negative"), ("plan_completion_rate", "positive"),
                                           ("reading_question_error_share", "negative"), ("persistence", "positive")])
def test_new_metrics_validity_direction_and_insufficient(key, expected):
    """新增指标的正反例：方向一致且 |r| 够 → 达到；方向反了 → 未达到；学生数不足 → 样本不足（不报通过）"""
    cfg = ValidityCfg(measure_week=6, min_students=5, min_abs_r=0.3, min_metrics_passing=1, metrics={key: ("last_exam_fraction", expected)},
                      counted=frozenset({key}))
    good, bad = (UP, DOWN) if expected == "positive" else (DOWN, UP)
    rep = R.validity_report({key: good}, cfg)
    assert rep["metrics"][0]["result"] == R.MEETS and rep["result"] == R.MEETS
    rep = R.validity_report({key: bad}, cfg)
    assert rep["metrics"][0]["result"] == R.BELOW and rep["result"] == R.BELOW
    rep = R.validity_report({key: good[:4]}, cfg)
    assert rep["metrics"][0]["result"] == R.INSUFFICIENT and "r" not in rep["metrics"][0] and rep["result"] == R.INSUFFICIENT


ORIGINAL4 = ("independent_solve_rate", "hint_dependency", "transfer_gap", "retention_rate")


def test_gate_counts_only_metrics_marked_counts_toward_gate():
    """Gate 1 只数 counts_toward_gate = true 的指标：观察用的四项全部达到、计入的四项都未达到 → 通过数 0、未达到"""
    cfg = load_config().validity
    assert cfg.counted == frozenset(ORIGINAL4)
    doc = _raw_config()["validity_g14"]["metrics"]
    assert all(doc[k]["counts_toward_gate"] is True for k in ORIGINAL4)
    assert all(doc[k]["counts_toward_gate"] is False and "由人定" in doc[k]["$comment"] and "观察用" in doc[k]["$comment"]
               for k in NEW_METRICS)
    small = dataclasses.replace(cfg, min_students=5)
    pairs = {**{k: (DOWN if cfg.metrics[k][1] == "positive" else UP) for k in ORIGINAL4},
             **{k: (UP if cfg.metrics[k][1] == "positive" else DOWN) for k in NEW_METRICS}}
    rep = R.validity_report(pairs, small)
    assert [m["counts_toward_gate"] for m in rep["metrics"]] == [k in ORIGINAL4 for k in VALIDITY_METRICS]
    assert all(m["result"] == R.MEETS for m in rep["metrics"] if m["metric"] in NEW_METRICS)        # 观察用的照常算 r
    assert rep["passing"] == 0 and rep["observational_meeting"] == 4 and rep["result"] == R.BELOW
    # 观察用的样本不足也不能让结果变成“样本不足”
    rep = R.validity_report({k: v for k, v in pairs.items() if k in ORIGINAL4}, small)
    assert rep["result"] == R.BELOW
    # 计入的两项达到 → 达到
    good = {**pairs, "independent_solve_rate": UP if cfg.metrics["independent_solve_rate"][1] == "positive" else DOWN,
            "retention_rate": UP if cfg.metrics["retention_rate"][1] == "positive" else DOWN}
    rep = R.validity_report(good, small)
    assert rep["passing"] == 2 and rep["result"] == R.MEETS


def test_config_requires_counts_toward_gate():
    for k in ("persistence", "independent_solve_rate"):
        doc = _raw_config()
        del doc["validity_g14"]["metrics"][k]["counts_toward_gate"]
        with pytest.raises(ConfigError):
            parse_config(doc)
    for bad in ("false", 0, 1, None):
        doc = _raw_config()
        doc["validity_g14"]["metrics"]["persistence"]["counts_toward_gate"] = bad
        with pytest.raises(ConfigError):
            parse_config(doc)
    doc = _raw_config()
    doc["validity_g14"]["metrics"]["persistence"]["counts_toward_gate"] = True
    assert "persistence" in parse_config(doc).validity.counted and parse_config(doc).fingerprint != load_config().fingerprint


def test_summary_marks_observational_substitutes_and_generator_caveats(pack, files):
    md = files["summary.md"]
    sec = md.split("## G1-4", 1)[1].split("## 附加", 1)[0]
    for k in VALIDITY_METRICS:
        row = next(ln for ln in sec.splitlines() if ln.startswith(f"| {k} |"))
        assert row.endswith("| 观察用，不计入 |") == (k in NEW_METRICS) and row.endswith("| 计入 |") == (k in ORIGINAL4)
        note = next(ln for ln in sec.splitlines() if ln.startswith(f"- {k}："))
        assert ("结局为替代口径（字典原文：" in note) == (k != "independent_solve_rate")
    assert "审题类错因占比随学生基础 θ" in sec and "计划完成倾向随每周增量 gain" in sec and "不得当作效度证据" in sec
    assert "persistence 只用周活跃天数" in sec
    assert f"计入 Gate 1 的指标中对照未签字阈值通过：{pack.g14['passing']}" in sec
    assert pack.g14["passing"] == sum(1 for m in pack.g14["metrics"] if m["counts_toward_gate"] and m["result"] == R.MEETS)


def test_config_rejects_bad_new_metric_entries():
    doc = _raw_config()
    doc["validity_g14"]["metrics"]["persistence"]["outcome"] = "self_reported_effort"     # 不在既有结局口径里
    with pytest.raises(ConfigError):
        parse_config(doc)
    doc = _raw_config()
    doc["validity_g14"]["metrics"]["plan_completion_rate"]["direction"] = "up"
    with pytest.raises(ConfigError):
        parse_config(doc)
    doc = _raw_config()
    doc["validity_g14"]["metrics"]["self_initiated_sessions"] = {"outcome": "last_exam_fraction", "direction": "positive"}  # 未实现的指标
    with pytest.raises(ConfigError):
        parse_config(doc)


def test_summary_reports_insufficient_sample_for_metric(pack):
    """样本不足的指标在摘要里写“样本不足”，不写“达到”"""
    from gate1_evidence.evidence import render as _render
    cfg = dataclasses.replace(pack.config, validity=dataclasses.replace(pack.config.validity, min_students=1000))
    p = dataclasses.replace(pack, config=cfg, g14=R.validity_report(R.student_pairs(pack.metric_rows), cfg.validity))
    md = _render(p, {"commit": "test", "worktree_dirty": False})["summary.md"]
    for k in VALIDITY_METRICS:
        line = next(ln for ln in md.splitlines() if ln.startswith(f"| {k} |"))
        assert "样本不足" in line and "达到未签字阈值" not in line


# ---------- 附加：分组误差差异 ----------
FCFG =FairnessCfg(group_keys=("wave",), min_records_per_group=3, min_students_per_group=3, max_mae_gap=0.05, max_bias_gap=0.05)


def _fair(errors_a: list[float], errors_b: list[float]):
    recs, groups = [], {}
    for g, errs in (("a", errors_a), ("b", errors_b)):
        for i, d in enumerate(errs):
            sid = _uid(f"{g}{i}")
            groups[sid] = {"wave": g, "grade": "9"}
            recs.append(dataclasses.replace(_exam(0, 0, 60.0 + d), student_id=sid))
    return recs, groups


def test_fairness_within_and_exceeding():
    recs, groups = _fair([2, -2, 2], [3, -3, -1])
    rep = R.fairness_report(recs, groups, FCFG)
    assert rep["result"] == R.MEETS and rep["keys"][0]["mae_gap"] == round(abs(7 / 300 - 6 / 300), 4)
    assert rep["keys"][0]["bias_gap"] == 0.01                             # 偏差带符号：2/300 − (−1/300)
    assert [(r["group"], r["n"], r["bias"]) for r in rep["rows"]] == [("a", 3, 0.0067), ("b", 3, -0.0033)]
    recs, groups = _fair([2, -2, 2], [10, 9, 8])          # b 组系统性低估
    rep = R.fairness_report(recs, groups, FCFG)
    assert rep["keys"][0]["bias_gap"] > 0.05 and rep["result"] == R.BELOW
    recs, groups = _fair([0, 0, 0], [8, -8, 8])           # 偏差接近、MAE 差距大
    rep = R.fairness_report(recs, groups, FCFG)
    assert rep["keys"][0]["mae_gap"] > 0.05 and rep["result"] == R.BELOW
    recs, groups = _fair([6, 6, 6], [-6, -6, -6])         # MAE 相同、偏差方向相反
    rep = R.fairness_report(recs, groups, FCFG)
    assert rep["keys"][0]["mae_gap"] == 0.0 and rep["keys"][0]["bias_gap"] == 0.12 and rep["result"] == R.BELOW


def test_fairness_sample_threshold_and_keys():
    recs, groups = _fair([2, -2, 2], [3, -3])
    rep = R.fairness_report(recs, groups, FCFG)
    assert rep["result"] == R.INSUFFICIENT and "mae_gap" not in rep["keys"][0] and rep["rows"] == []
    recs, groups = _fair([2, -2, 2, 1], [])
    assert R.fairness_report(recs, groups, FCFG)["result"] == R.INSUFFICIENT     # 只有一组
    with pytest.raises(ConfigError):
        R.fairness_report(recs, groups, FairnessCfg(("school",), 3, 3, 0.05, 0.05))


def test_fairness_students_threshold():
    """每组按学生计：b 组 4 条记录只来自 2 名学生（门槛 3 名）时样本不足"""
    recs, groups = _fair([2, -2, 2], [3, -3, 1, -1])
    b = sorted({r.student_id for r in recs if groups[r.student_id]["wave"] == "b"})
    recs = [dataclasses.replace(r, student_id=b[0]) if r.student_id in b[2:] else r for r in recs]
    rep = R.fairness_report(recs, groups, FCFG)
    assert rep["keys"][0]["groups"] == {"a": 3, "b": 4} and rep["keys"][0]["students"] == {"a": 3, "b": 2}
    assert rep["result"] == R.INSUFFICIENT and rep["rows"] == []
    loose = FairnessCfg(("wave",), 3, 2, 0.05, 0.05)
    rep = R.fairness_report(recs, groups, loose)
    assert rep["result"] == R.MEETS and [r["students"] for r in rep["rows"]] == [3, 2]
    # 记录数门槛独立生效：a 组 3 条记录 3 名学生，记录门槛 4
    assert R.fairness_report(recs, groups, FairnessCfg(("wave",), 4, 2, 0.05, 0.05))["result"] == R.INSUFFICIENT


# ---------- 证据包 ----------
def test_pack_is_deterministic(files):
    again = render(build(), {"commit": "test", "worktree_dirty": False})
    assert again == files


def test_pack_matches_committed_sample(files):
    """与仓库样例逐字节一致（run_metadata.json 去掉提交号与工作区状态）。
    样例过期时用 make gate1-evidence GATE1_OUT=tools/gate1-evidence/sample 重生并在 PR 里说明差异。"""
    assert sorted(p.name for p in SAMPLE.iterdir()) == sorted(files)
    for name, content in files.items():
        got = (SAMPLE / name).read_text(encoding="utf-8")
        if name == "run_metadata.json":
            want, have = json.loads(content), json.loads(got)
            for k in VOLATILE_METADATA:
                assert k in have
                want.pop(k), have.pop(k)
            assert want == have, "run_metadata.json（去掉提交号）与样例不同"
        else:
            assert got == content, f"{name} 与样例不同"


def test_pack_notice_judgements_and_hashes(files):
    for name in ("summary.md", "checks.json", "run_metadata.json"):
        assert SYNTHETIC_NOTICE in files[name]
    doc = json.loads(files["checks.json"])
    assert [i["id"] for i in doc["items"]] == ["G1-1", "G1-2", "G1-3", "G1-4", "G1-5", "G1-6", "G1-附加"]
    assert all(i["gate_judgement"] == NEEDS_HUMAN for i in doc["items"])
    for i in doc["items"]:
        if i["id"] in ("G1-1", "G1-5", "G1-6"):
            assert i["references"] and "result" not in i and "report" not in i
        else:
            assert i["result"] in (R.INSUFFICIENT, R.MEETS, R.BELOW) and i["report"]["result"] == i["result"]
    assert doc["config"]["signoff"] == UNSIGNED and doc["config"]["fingerprint"] == load_config().fingerprint
    for name in DATA_FILES:
        assert f"`{name}` {hashlib.sha256(files[name].encode()).hexdigest()}" in files["summary.md"]
    for v in ("满足 |", "不满足 |"):
        assert v not in files["summary.md"].replace("无法自动判断（需人） |", "")


def test_pack_has_only_synthetic_ids_and_no_vendor_or_sensitive_fields(files):
    blob = "\n".join(files.values())
    assert not re.search(r"claude|anthropic|openai|gpt|qwen|deepseek|glm|kimi|doubao|ernie|gemini|llama|moonshot", blob, re.I)
    header = "\n".join(files[n].splitlines()[0] for n in files if n.endswith(".csv"))
    assert not re.search(r"name|school|region|district|household|family|guardian|phone|地区|学校|家庭", header, re.I)
    ids = set(re.findall(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", blob))
    assert ids and ids <= {s.student_id for s in build().cohort.students}


def test_insufficient_sample_gives_no_conclusion():
    """全队列规模太小时，四项报表都只给 insufficient_sample"""
    sie = synthetic_sie03()
    cfg = load_config()
    small = generate(sie.exam.interval, sie.exam.scales, sie.config_version, size=2)
    assert R.retest_report(small.students, small.retests, small.sessions, small.horizon_end, cfg.retest)["result"] == R.INSUFFICIENT
    assert R.exam_report(small.exams, sie.exam.interval.z, cfg.exam)["result"] == R.INSUFFICIENT
    pairs = R.student_pairs(R.validity_pairs(small, synthetic_policy(), cfg.validity))
    assert R.validity_report(pairs, cfg.validity)["result"] == R.INSUFFICIENT
    assert R.fairness_report(small.exams, R.group_values(small.students), cfg.fairness)["result"] == R.INSUFFICIENT


def test_write_pack_refuses_foreign_or_nonempty_dirs(tmp_path, files):
    fresh = tmp_path / "new" / "pack"
    assert len(write_pack(files, fresh)) == len(files)                 # 不存在的目录：新建
    empty = tmp_path / "empty"
    empty.mkdir()
    write_pack(files, empty)                                            # 空目录
    write_pack(files, empty)                                            # 只含本工具同名文件：覆盖（重生仓库样例）
    assert (empty / "summary.md").read_text(encoding="utf-8") == files["summary.md"]
    for setup in ("foreign_file", "same_name_dir", "symlink_entry", "symlink_dir", "not_a_dir"):
        d = tmp_path / setup
        if setup == "foreign_file":
            d.mkdir()
            (d / "notes.txt").write_text("x", encoding="utf-8")
        elif setup == "same_name_dir":
            d.mkdir()
            (d / "checks.json").mkdir()
        elif setup == "symlink_entry":
            d.mkdir()
            (d / "summary.md").symlink_to(empty / "summary.md")
        elif setup == "symlink_dir":
            d.symlink_to(empty)
        else:
            d.write_text("x", encoding="utf-8")
        before = sorted(p.name for p in d.iterdir()) if d.is_dir() and not d.is_symlink() else None
        with pytest.raises(PackRefused):
            write_pack(files, d)
        if before is not None:
            assert sorted(p.name for p in d.iterdir()) == before           # 拒绝时不写任何文件


def test_config_signed_requires_signoff_records():
    doc = _raw_config()
    doc["signed_by"], doc["signed_at"] = "SIE 负责人", "2027-06-01"
    with pytest.raises(ConfigError):                                    # 填了签字人，各节却仍是“待签字”
        parse_config(doc)
    for s in ("retest_g12", "exam_g13", "validity_g14", "fairness"):
        doc[s]["signoff"] = "已签字 2027-06-01"
    cfg = parse_config(doc)
    assert cfg.signed and cfg.provenance()["signoff"] == "已签字"
    doc["fairness"]["signoff"] = UNSIGNED                               # 只要有一节还是“待签字”就拒绝
    with pytest.raises(ConfigError):
        parse_config(doc)


def test_copy_of_config_is_not_mutated_by_parse():
    doc = _raw_config()
    snap = copy.deepcopy(doc)
    parse_config(doc)
    assert doc == snap
