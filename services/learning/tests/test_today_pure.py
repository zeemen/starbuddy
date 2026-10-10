"""PLT-11 今日计划跨科编排：纯函数与配置（specs/today-plan-orchestration-plt11/spec.md 范围第 1 条、验收“合成数据上去重、优先级、
预算截断各有测试”）。不连库。全部合成数据：学科、节点、条目 ID 都是占位（规则 8）。
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest

from learning.today import config as C
from learning.today import merge as M
from learning.today.service import validate

FIXTURE = Path(__file__).resolve().parent / "fixtures/today-config-synthetic.json"
PRIORITY = ("retest", "practice", "plan")


def eid() -> str:
    return str(uuid.uuid4())


def rt(subject: str, node: str, kind: str = "retest", minutes: int = 5) -> M.Item:
    return M.Item(subject, kind, node, minutes, "retest", eid(), None)


def pr(subject: str, node: str, minutes: int = 8) -> M.Item:
    return M.Item(subject, "practice", node, minutes, "practice", eid(), None)


def pl(subject: str, node: str | None, kind: str = "practice", minutes: int = 10, code: str = "high_exam_weight") -> M.Item:
    return M.Item(subject, kind, node, minutes, "plan", None, code)


# ---------- 去重 ----------
def test_plan_retest_task_dropped_when_retest_entry_on_same_node():
    r = [rt("aya", "MATH.SYN.A")]
    plan = [pl("aya", "MATH.SYN.A", "retest"), pl("aya", "MATH.SYN.A", "practice"), pl("aya", "MATH.SYN.B", "retest"),
            pl("zhixing", "MATH.SYN.A", "retest"), pl("aya", None, "retest")]
    _, _, kept = M.dedupe(r, [], plan)
    # 只去掉同一学科同一节点的 retest 任务；同节点的其他类任务、别的节点、别的学科、没有节点的任务都保留
    assert kept == plan[1:]


def test_practice_dropped_when_retest_on_same_node_and_retest_kept():
    r = [rt("aya", "MATH.SYN.A"), rt("aya", "MATH.SYN.C", "verify")]
    p = [pr("aya", "MATH.SYN.A"), pr("aya", "MATH.SYN.B"), pr("aya", "MATH.SYN.C"), pr("zhixing", "MATH.SYN.A")]
    kr, kp, _ = M.dedupe(r, p, [])
    assert kr == r
    assert kp == [p[1], p[3]]


def test_build_end_to_end_dedupe():
    r = [rt("aya", "MATH.SYN.A")]
    p = [pr("aya", "MATH.SYN.A"), pr("aya", "MATH.SYN.B")]
    plan = [pl("aya", "MATH.SYN.A", "retest", 10, "retest_scheduled"), pl("aya", "MATH.SYN.D", "lesson", 5)]
    items, cut = M.build(r, p, plan, priority=PRIORITY, subjects=["aya"], budget=40)
    assert [(i.source, i.node_id) for i in items] == [("retest", "MATH.SYN.A"), ("practice", "MATH.SYN.B"), ("plan", "MATH.SYN.D")]
    assert cut == 0


# ---------- 排序：来源优先级 → 来源内原有顺序 → 学科轮转 ----------
def test_priority_then_original_order_then_subject_rotation():
    r = [rt("zhixing", "POL.SYN.A"), rt("aya", "MATH.SYN.A"), rt("aya", "MATH.SYN.B")]
    p = [pr("aya", "MATH.SYN.C"), pr("aya", "MATH.SYN.D"), pr("wenli", "CHN.SYN.A")]
    plan = [pl("aya", "MATH.SYN.E"), pl("zhixing", "POL.SYN.B"), pl("zhixing", "POL.SYN.C")]
    items = M.order({"plan": plan, "retest": r, "practice": p}, PRIORITY, ["aya", "zhixing", "wenli"])
    assert [(i.source, i.node_id) for i in items] == [
        ("retest", "MATH.SYN.A"), ("retest", "POL.SYN.A"), ("retest", "MATH.SYN.B"),
        ("practice", "MATH.SYN.C"), ("practice", "CHN.SYN.A"), ("practice", "MATH.SYN.D"),
        ("plan", "MATH.SYN.E"), ("plan", "POL.SYN.B"), ("plan", "POL.SYN.C")]


def test_priority_comes_from_config():
    r, p, plan = [rt("aya", "MATH.SYN.A")], [pr("aya", "MATH.SYN.B")], [pl("aya", "MATH.SYN.C")]
    items = M.order({"retest": r, "practice": p, "plan": plan}, ("plan", "practice", "retest"), ["aya"])
    assert [i.source for i in items] == ["plan", "practice", "retest"]


def test_rotation_keeps_subject_order_of_request():
    p = [pr("aya", "MATH.SYN.A"), pr("zhixing", "POL.SYN.A")]
    assert [i.subject for i in M.rotate(p, ["zhixing", "aya"])] == ["zhixing", "aya"]


# ---------- 预算截断 ----------
def test_truncate_cumulative_and_count():
    xs = [rt("aya", f"MATH.SYN.N{i}", minutes=m) for i, m in enumerate([10, 10, 10, 5, 1])]
    kept, cut = M.truncate(xs, 30)
    assert kept == xs[:3] and cut == 2                    # 第 4 条放不进（35 > 30）：它和之后的都截掉，不跳着往后塞
    kept, cut = M.truncate(xs, 36)
    assert kept == xs[:5] and cut == 0
    kept, cut = M.truncate(xs, 20)
    assert kept == xs[:2] and cut == 3


def test_truncate_exact_budget_and_oversized_first():
    xs = [rt("aya", "MATH.SYN.A", minutes=20), rt("aya", "MATH.SYN.B", minutes=20)]
    assert M.truncate(xs, 40) == (xs, 0)                  # 恰好等于预算：放得下
    big = [pl("aya", "MATH.SYN.C", minutes=45), rt("aya", "MATH.SYN.D", minutes=5)]
    assert M.truncate(big, 30) == ([big[0]], 1)           # 第一条本身超预算：仍保留，其后截掉
    assert M.truncate([], 30) == ([], 0)


def test_view_has_exactly_the_contract_fields():
    v = rt("aya", "MATH.SYN.A").view()
    assert set(v) == {"subject", "kind", "node_id", "estimated_minutes", "source", "entry_id", "rationale_code"}


# ---------- 配置 ----------
def test_synthetic_config_and_fixture_file():
    cfg = C.load("synthetic", env="test")
    assert (cfg.default_budget_minutes, cfg.source_priority, dict(cfg.entry_minutes)) == (
        30, ("retest", "practice", "plan"), {"retest": 5, "verify": 5, "practice": 8})
    assert cfg.synthetic and not cfg.signed
    f = C.load(str(FIXTURE), env="dev")
    assert f.default_budget_minutes == 25 and f.entry_minutes["verify"] == 4 and f.synthetic


@pytest.mark.parametrize("env", ["pilot", "staging", "production", ""])
def test_unsigned_config_refused_outside_test_dev(env):
    with pytest.raises(C.TodayConfigUnsigned):
        C.load("synthetic", env=env)


def test_signed_config_allowed_anywhere():
    doc = {**C.SYNTHETIC, "synthetic": False, "signed_by": "（合成）签字人", "signed_at": "2026-10-09T10:00:00+08:00"}
    assert C.parse(doc, env="pilot").signed


@pytest.mark.parametrize("over", [
    {"default_budget_minutes": 19}, {"default_budget_minutes": 41}, {"default_budget_minutes": True}, {"default_budget_minutes": 30.0},
    {"source_priority": ["retest", "practice"]}, {"source_priority": ["retest", "practice", "practice"]},
    {"source_priority": ["retest", "practice", "plan", "verify"]},
    {"entry_minutes": {"retest": 5, "verify": 5}}, {"entry_minutes": {"retest": 0, "verify": 5, "practice": 8}},
    {"entry_minutes": {"retest": 5, "verify": 5, "practice": 61}}, {"entry_minutes": {"retest": 5, "verify": 5, "practice": 8, "plan": 1}},
    {"format": "starbuddy-today-config/2"}, {"synthetic": "yes"}, {"signed_by": "（合成）"}, {"extra": 1},
    {"signed_at": "2026-10-09"},
])
def test_invalid_config_rejected(over):
    with pytest.raises(C.TodayConfigError):
        C.parse({**C.SYNTHETIC, **over}, env="test")


def test_config_file_unreadable(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    for spec in (str(bad), str(tmp_path / "missing.json")):
        with pytest.raises(C.TodayConfigError):
            C.load(spec, env="test")
    assert json.loads(FIXTURE.read_text(encoding="utf-8"))["synthetic"] is True     # 示例配置标明为合成


# ---------- 请求校验 ----------
STUDENT = "5b8e1c52-0000-4000-8000-00000000aa01"


def q(**over) -> dict:
    return {"request_id": str(uuid.uuid4()), "student_ref": STUDENT, "subjects": ["aya"], **over}


def test_validate_accepts_budget_range():
    for b in (20, 30, 40):
        assert validate(q(time_budget_minutes=b))["time_budget_minutes"] == b
    assert validate(q(subjects=["aya", "zhixing", "wenli"]))


@pytest.mark.parametrize("body", [
    None, [], q(time_budget_minutes=19), q(time_budget_minutes=41), q(time_budget_minutes=True), q(time_budget_minutes="30"),
    q(subjects=[]), q(subjects=["aya", "aya"]), q(subjects=["echo"]), q(subjects=["aya", "zhixing", "wenli", "echo"]),
    q(student_ref=STUDENT.upper()), q(student_ref="not-a-uuid"), q(request_id="x"), q(mastery=[]), q(account_id=STUDENT),
    {"student_ref": STUDENT, "subjects": ["aya"]},
])
def test_validate_rejects(body):
    from learning.errors import IngestError
    with pytest.raises(IngestError) as e:
        validate(body)
    assert (e.value.status, e.value.code) == (400, "INVALID_REQUEST")
