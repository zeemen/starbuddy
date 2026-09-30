"""证据分类（计入 / 不计入 + 原因编码）与影响方向（spec 第 3 节；待人确认 #2、#9、#10、#11、#16、#23 的默认）。"""
from __future__ import annotations

import pytest

from learning.mastery import classify, replay
from learning.mastery import evidence as E
from test_mastery_support import DAY, HOUR, MIN, NODE, T0, UNIT, ev, signed_cfg, unsigned_cfg, with_

CFG = unsigned_cfg()


@pytest.mark.parametrize("kw,reason", [
    ({"has_grade": False}, E.NO_GRADE),
    ({"has_response_ref": False}, E.NO_GRADE),
    ({"tagged": False}, E.UNTAGGED),
    ({"main_node_id": None}, E.UNTAGGED),
    ({"cognitive_level": None}, E.UNTAGGED),
    ({"main_node_id": "syn-node-002", "secondary_node_ids": (NODE,)}, E.SECONDARY_NODE),
    ({"main_node_id": "syn-node-002"}, E.OTHER_UNIT),
    ({"cognitive_level": "know"}, E.OTHER_UNIT),
    ({"level": "C"}, E.LEVEL_C),
    ({"human_review_required": True, "review_reasons": ("verifier_boundary_case",)}, E.PENDING_REVIEW),
    # #11 默认：只因内容未准入而复核的结果同样不计入
    ({"human_review_required": True, "review_reasons": ("content_not_admitted",), "admission": "pending_review"},
     E.PENDING_REVIEW),
    ({"verdict": "partial"}, E.NOT_SCORED),
    ({"verdict": "needs_review"}, E.NOT_SCORED),
    ({"verdict": "not_applicable"}, E.NOT_SCORED),
    ({"grade_confidence": 0.59}, E.LOW_CONFIDENCE),
    ({"grade_confidence": None}, E.LOW_CONFIDENCE),
    ({"self_report": "guessed"}, E.GUESSED),
    ({"kind": "hinted", "hint": 3}, E.HINT_HEAVY),
    ({"kind": "hinted", "hint": None}, E.HINT_HEAVY),      # 提示信号但没有等级：按高级提示
    ({"kind": None, "hint": None}, E.HINT_HEAVY),          # #10：没有提示等级按非低级提示
])
def test_exclusion_reasons(kw, reason):
    c = classify(ev(T0, "fam-x", **kw), UNIT, CFG)
    assert not c.counted and c.reasons == (reason,)


def test_guessed_and_hint_heavy_consume_family_but_pending_review_does_not():
    assert classify(ev(T0, "f", self_report="guessed"), UNIT, CFG).consumes_family
    assert classify(ev(T0, "f", kind="hinted", hint=4), UNIT, CFG).consumes_family
    assert not classify(ev(T0, "f", human_review_required=True), UNIT, CFG).consumes_family
    assert not classify(ev(T0, "f", grade_confidence=0.1), UNIT, CFG).consumes_family


def test_hint_levels():
    low = classify(ev(T0, "f", kind="hinted", hint=2), UNIT, CFG)
    assert low.counted and low.weight_key == "hinted_low" and not low.independent_correct
    high = classify(ev(T0, "f", kind="hinted", hint=3), UNIT, CFG)
    assert not high.counted and high.hint_followup
    # 提示后做错：计入、按“提示后做错”下调（#2）
    wrong = classify(ev(T0, "f", kind="hinted", hint=5, verdict="incorrect"), UNIT, CFG)
    assert wrong.counted and wrong.weight_key == "hinted_wrong"
    # 独立作答却带了提示等级：按提示处理（防御）
    assert classify(ev(T0, "f", kind="independent", hint=1), UNIT, CFG).weight_key == "hinted_low"


def test_diagnose_without_hint_level_counts_as_no_hint():
    # #23：诊断不提供提示，没有提示等级按无提示；0017 之后诊断证据带 evidence_kind
    c = classify(ev(T0, "f", source="diagnose", kind="independent", hint=None), UNIT, CFG)
    assert c.counted and c.independent_correct and c.strict
    # grade 来源没有提示等级：按非低级提示（#10）
    assert not classify(ev(T0, "f", source="grade", kind=None, hint=None), UNIT, CFG).counted


@pytest.mark.parametrize("kw,caps", [
    ({"level": "B"}, (E.LEVEL_B_CAPPED,)),
    ({"admission": "pending_review"}, (E.NOT_ADMITTED,)),
    ({"admission": "source_unverified"}, (E.NOT_ADMITTED,)),
    ({"semantics": "practice"}, (E.NOT_FORMAL,)),
    ({"kind": None, "level": None}, (E.LEGACY_NO_KIND,)),                      # 0017 之前：提示等级 0
    ({"kind": "independent", "level": None}, (E.LEGACY_NO_KIND,)),             # v0 路径：有情境没等级
    ({"source": "diagnose", "kind": None, "level": "A", "hint": None}, (E.LEGACY_NO_KIND,)),
])
def test_caps_keep_counted_but_not_strict(kw, caps):
    c = classify(ev(T0, "f", **kw), UNIT, CFG)
    assert c.counted and c.caps == caps and not c.strict
    # 非 formal 不降权，只是不能推动迁移；其余都按 B 级
    assert c.level == ("A" if caps == (E.NOT_FORMAL,) else "B")


def test_legacy_uses_conservative_weight():
    c = classify(ev(T0, "f", kind=None, level=None, hint=0), UNIT, CFG)
    assert c.independent_correct and c.weight_key == "hinted_low"


def test_strict_evidence():
    c = classify(ev(T0, "f"), UNIT, CFG)
    assert c.strict and c.level == "A" and c.caps == ()


def test_error_penalty_by_category():
    c = classify(ev(T0, "f", verdict="incorrect", error_category="computation_or_grammar"), UNIT, CFG)
    assert c.penalty == CFG["error_penalty"]["computation_or_grammar"]
    assert classify(ev(T0, "f", verdict="incorrect"), UNIT, CFG).penalty == CFG["error_penalty"]["default"]


# ---- 影响方向（验收：对第 3 节表格的每一行，在任一状态上追加一条该类证据）----

def _prefixes():
    return {
        "empty": [],
        "some_correct": [ev(T0 + i * MIN, f"pre-{i}") for i in range(2)],
        "some_wrong": [ev(T0 + i * MIN, f"pre-{i}", verdict="incorrect") for i in range(3)],
        "mixed_b": [ev(T0, "pre-0", level="B"), ev(T0 + MIN, "pre-1", verdict="incorrect")],
    }


def _delta(prefix, extra, cfg=CFG):
    now = T0 + 30 * DAY
    before = replay(prefix, UNIT, cfg, now)
    after = replay(prefix + [extra], UNIT, cfg, now)
    return (after.result.p_mastery - before.result.p_mastery, after.confidence_raw - before.confidence_raw)


LATER = T0 + 20 * DAY  # 距前缀 ≥ delayed_retest_min_interval（合成值 5 天）


@pytest.mark.parametrize("name", list(_prefixes()))
def test_direction_table(name):
    pre = _prefixes()[name]
    dp, dc = _delta(pre, ev(LATER, "new", kind="independent"))
    assert dp > 0 and dc > 0
    lp, lc = _delta(pre, ev(LATER, "new", kind="hinted", hint=1))
    assert 0 < lp < dp and 0 < lc < dc                      # 低级提示：小幅上调，小于独立
    assert _delta(pre, ev(LATER, "new", kind="hinted", hint=4)) == (0, 0)   # 高级提示：不变
    tp, tc = _delta(pre, ev(LATER, "new", kind="transfer"))
    assert tp >= dp > 0 and tc >= dc
    rp, rc = _delta(pre, ev(LATER, "new", kind="delayed_retest"))
    assert rp >= dp > 0 and rc >= dc
    wp, wc = _delta(pre, ev(LATER, "new", verdict="incorrect"))
    assert wp < 0 and wc > 0                                  # 独立做错：下调，置信度上调
    hp, hc = _delta(pre, ev(LATER, "new", kind="hinted", hint=2, verdict="incorrect"))
    assert hp <= 0 and hc == 0 and hp >= wp                   # 提示后做错：不上调、小幅下调，置信度不变
    assert _delta(pre, ev(LATER, "new", self_report="guessed")) == (0, 0)   # 猜的做对：不变
    assert _delta(pre, ev(LATER, "new", level="C")) == (0, 0)
    assert _delta(pre, ev(LATER, "new", human_review_required=True)) == (0, 0)
    bp, bc = _delta(pre, ev(LATER, "new", level="B"))
    assert 0 < bp < dp and 0 < bc < dc                        # B 级：同向、降权


def test_diagnose_source_weighs_more_than_practice():
    pre = _prefixes()["some_correct"]
    dp, _ = _delta(pre, ev(LATER, "new", source="diagnose"))
    gp, _ = _delta(pre, ev(LATER, "new", source="grade"))
    assert dp > gp > 0


def test_delayed_retest_too_soon_counts_as_independent():
    pre = _prefixes()["some_correct"]
    soon = T0 + 2 * DAY
    assert _delta(pre, ev(soon, "new", kind="delayed_retest")) == _delta(pre, ev(soon, "new", kind="independent"))
    rep = replay(pre + [ev(soon, "new", kind="delayed_retest", eid="syn-delayed")], UNIT, CFG, soon)
    assert ("syn-delayed", E.DELAYED_TOO_SOON) in rep.annotations


def test_self_report_confident_same_as_none():
    a = [ev(T0 + i * HOUR, f"f{i}", eid=f"syn-e{i}", attempt_id=f"syn-a{i}") for i in range(5)]
    b = [with_(e, self_report="confident") for e in a]
    for cfg in (CFG, signed_cfg()):
        assert replay(a, UNIT, cfg, T0 + DAY).result == replay(b, UNIT, cfg, T0 + DAY).result


def test_occurred_at_never_used():
    a = [ev(T0 + i * HOUR, f"f{i}", eid=f"syn-e{i}", attempt_id=f"syn-a{i}") for i in range(5)]
    b = [with_(e, occurred_at=T0 - 400 * DAY) for e in a]
    assert replay(a, UNIT, CFG, T0 + DAY).result == replay(b, UNIT, CFG, T0 + DAY).result
