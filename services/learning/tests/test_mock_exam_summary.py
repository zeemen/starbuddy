"""PLT-18 模考第一步：整卷汇总（specs/mock-exam-plt18/spec.md 范围第 3 条）。纯函数；全部合成数据，判分结果是手写的合成验证器 / Rubric 结果。

- 与手算一致：总分、各节得分与作答数、未作答题数、按节点的得分 / 失分 / 可得分；
- 折算：卷面得分 = 判分得分 / 判分满分 × 卷面分值，四舍五入到 0.01（ROUND_HALF_UP）；判分满分 ≤ 0 拒绝；
- 采分点：判分带采分点且快照完整覆盖时按采分点归到节点，否则整题计入并标注 whole_item；
- 待复核：human_review_required、置信度低于阈值（严格小于）、得分为 null 的题单列，总分标暂定；未校准的题让输出带未校准标记；
- 超时：过期的会话 timed_out，已作答部分照常计分；
- 拒绝：缺溯源五字段任一（或为空）、字段不合规、多余字段（夹带讲解文本）、非验证器 / Rubric 来源、对旧作答的判分、
  未作答却有判分、作答了没判分、同题两条判分、会话未结束、换卷、判分版本与登记不一致、节点映射不是列表；
- 版本号正则与插件契约 plugin-api.v1.1.yaml 的 pattern 逐字相同；判分方式词表就是再练登记的词表；
- 输出里没有对学生的结论性陈述：所有字符串都是 ID 或编码。
"""
from __future__ import annotations

import copy
import uuid
from pathlib import Path

import pytest
import yaml

from learning.mock_exam import session as S
from learning.mock_exam import summary as Y
from learning.practice.presenter import GRADING
from test_mock_exam_manifest import CFG, ITEM_NODES, POINT_NODES, load_daofa, load_math
from test_mock_exam_session import DEADLINE, MIN, SESSION_ID, STUDENT, T0, started

MATH = load_math()
DAOFA = load_daofa()
NS = uuid.UUID("5b8e1c52-0000-4000-8000-00000000e5e5")
REPO = Path(__file__).resolve().parents[3]


def grade(item_id: str, score, *, ref: str | None = None, max_score=None, paper=MATH, confidence=1.0, review=False,
          grading="validator", calibrated=True, **over) -> dict:
    it = paper.item(item_id)
    assert it is not None
    g = {"item_id": item_id, "answer_ref": ref or f"att-{item_id}", "grading": grading, "score": score,
         "max_score": Y.num(it.paper_cents) if max_score is None else max_score, "calibrated": calibrated,
         "evidence_ids": [str(uuid.uuid5(NS, f"{item_id}|{ref}"))], "confidence": confidence,
         "source_version": "verifier:aya-symbolic-ts@0.1.0" if paper is MATH else "rubric:zhixing-synthetic@0.1.0",
         "model_version": "algo:aya-symbolic-ts@0.1.0" if paper is MATH else "algo:zhixing-synthetic@0.1.0",
         "human_review_required": review}
    if review:
        g["review_reasons"] = ["verifier_boundary_case"]
    g.update(over)
    return g


# 数学：手写的作答与判分（卷面分值见 config/mock_exam/paper.math-fun-syn.json；判分满分与卷面分值相同）
MATH_GRADES = [
    # 一、选择题（每题 3 分，全做）：7 对 3 错 = 21
    grade("Q-FUN-U01-T-002", 3), grade("Q-FUN-U03-L-003", 3), grade("Q-FUN-U03-P-002", 0), grade("Q-FUN-U04-D-001", 3),
    grade("Q-FUN-U05-P-003", 3), grade("Q-FUN-U06-D-001", 0), grade("Q-FUN-U06-R-001", 3), grade("Q-FUN-U06-T-002", 3),
    grade("Q-FUN-U07-D-001", 3), grade("Q-FUN-U07-R-001", 0),
    # 二、填空题（每题 3 分，做 5 道）：3 + 1.5 + 3 + 0 + 3 = 10.5；U10-D-001 需人工复核，U13-D-001 低置信
    grade("Q-FUN-U07-P-004", 3), grade("Q-FUN-U10-D-001", 1.5, review=True, confidence=0.5, grading="deterministic_rubric"),
    grade("Q-FUN-U11-D-001", 3), grade("Q-FUN-U11-R-001", 0), grade("Q-FUN-U13-D-001", 3, confidence=0.59),
    # 三、综合题（每题 10 分，做 2 道）：10 + 6.5 = 16.5；SPR-DP-S1 置信度恰好等于阈值 0.6，不算低置信
    grade("Q-SPR-DP-S1", 10, confidence=0.6), grade("Q-SPR-QF-S1", 6.5, grading="model_rubric", confidence=0.9),
]


def finished(paper=MATH, grades=MATH_GRADES, *, expire=False) -> S.Session:
    s = started(paper=paper)
    for i, g in enumerate(grades):
        s = S.answer(s, paper, item_id=g["item_id"], answer_ref=g["answer_ref"], now=T0 + (i + 1) * MIN)
    return S.advance(s, now=DEADLINE) if expire else S.submit(s, now=T0 + 50 * MIN)


def summarize(grades=MATH_GRADES, session=None, paper=MATH, nodes=ITEM_NODES, cfg=CFG, **kw) -> dict:
    return Y.summarize(paper, session or finished(paper, grades), grades, nodes, cfg, **kw)


def code_of(fn) -> str:
    with pytest.raises(Y.SummaryError) as e:
        fn()
    return e.value.code


def elp(r: dict, node: str) -> tuple:
    n = {x["node_id"]: x for x in r["nodes"]}[node]
    return n["earned"], n["lost"], n["possible"]


# ---------- 与手算一致 ----------
def test_math_summary_matches_hand_calculation():
    r = summarize()
    assert (r["score"], r["full_score"], r["state"], r["timed_out"]) == (48, 120, "submitted", False)
    assert [(s["score"], s["full_score"], s["answered"], s["unanswered"]) for s in r["sections"]] == \
        [(21, 30, 10, 0), (10.5, 30, 5, 5), (16.5, 60, 2, 4)]
    assert (r["answered_count"], r["unanswered_count"]) == (17, 9)
    assert r["pending_review"] == [{"item_id": "Q-FUN-U10-D-001", "reasons": ["human_review_required", "low_confidence"]},
                                   {"item_id": "Q-FUN-U13-D-001", "reasons": ["low_confidence"]}]
    assert r["provisional"] is True
    assert (r["uncalibrated"], r["uncalibrated_item_ids"]) == (False, [])
    assert elp(r, "MATH.SYN.EQ.QUAD_SOLVE") == (6, 0, 6)           # U01-T-002 3/3 + U07-P-004 3/3
    assert elp(r, "MATH.SYN.QUAD.CONCEPT") == (7.5, 1.5, 9)        # U03-L-003 3/3 + U10-D-001 1.5/3 + U11-D-001 3/3
    assert elp(r, "MATH.SYN.QUAD.APPLY") == (6.5, 16.5, 23)        # U07-R-001 0/3 + SPR-QF-S1 6.5/10 + SPR-QF-S3 未作答 0/10
    assert {x["node_id"]: x for x in r["nodes"]}["MATH.SYN.QUAD.APPLY"]["items"] == [
        {"item_id": "Q-FUN-U07-R-001", "basis": "whole_item"}, {"item_id": "Q-SPR-QF-S1", "basis": "whole_item"},
        {"item_id": "Q-SPR-QF-S3", "basis": "whole_item"}]
    assert elp(r, "MATH.SYN.QUAD.COEF_SIGN") == (13, 3, 16)        # U06-R-001 3/3 + SPR-DP-C1 未作答 0/3 + SPR-DP-S1 10/10
    assert "MATH.SYN.QUAD.ANALYTIC" not in {x["node_id"] for x in r["nodes"]}
    assert sum(n["possible"] for n in r["nodes"]) == 120           # 数学每题只映射一个节点
    assert len(r["whole_item_ids"]) == 26 and r["unmapped_item_ids"] == []
    assert r["evidence_ids"] == sorted({e for g in MATH_GRADES for e in g["evidence_ids"]})


def test_section_scores_add_up_to_total():
    r = summarize()
    assert sum(s["score"] for s in r["sections"]) == r["score"]
    assert sum(s["full_score"] for s in r["sections"]) == r["full_score"]
    assert sum(s["unanswered"] for s in r["sections"]) == r["unanswered_count"]


def test_not_provisional_when_all_confident():
    gs = [grade(g["item_id"], g["score"]) for g in MATH_GRADES]
    r = summarize(gs)
    assert (r["provisional"], r["pending_review"], r["score"]) == (False, [], 48)


def test_human_review_alone_marks_provisional():
    gs = [grade(g["item_id"], g["score"]) for g in MATH_GRADES]
    gs[0] = grade(gs[0]["item_id"], 3, review=True)
    r = summarize(gs)
    assert r["provisional"] and r["pending_review"] == [{"item_id": gs[0]["item_id"], "reasons": ["human_review_required"]}]


def test_low_confidence_threshold_is_strict():
    gs = [grade(g["item_id"], g["score"]) for g in MATH_GRADES]
    gs[0] = grade(gs[0]["item_id"], 3, confidence=CFG.review_confidence_below)
    assert summarize(gs)["provisional"] is False
    gs[0] = grade(gs[0]["item_id"], 3, confidence=CFG.review_confidence_below - 0.01)
    assert summarize(gs)["pending_review"] == [{"item_id": gs[0]["item_id"], "reasons": ["low_confidence"]}]


def test_null_score_is_pending_not_rejected():
    gs = [grade(g["item_id"], g["score"]) for g in MATH_GRADES]
    gs[16] = grade("Q-SPR-QF-S1", None)                             # 原 6.5 分
    r = summarize(gs)
    assert (r["score"], r["provisional"]) == (41.5, True)
    assert r["pending_review"] == [{"item_id": "Q-SPR-QF-S1", "reasons": ["score_unavailable"]}]
    assert r["sections"][2]["answered"] == 2


def test_uncalibrated_flag():
    gs = [grade(g["item_id"], g["score"]) for g in MATH_GRADES]
    gs[1] = grade("Q-FUN-U03-L-003", 3, calibrated=False)
    r = summarize(gs)
    assert (r["uncalibrated"], r["uncalibrated_item_ids"], r["provisional"], r["score"]) == (True, ["Q-FUN-U03-L-003"], False, 48)


def test_scaling_to_paper_score_round_half_up():
    gs = [grade("SYN-MOCK-LAW-S01", 1, max_score=16, paper=DAOFA),            # 1/16 × 2 = 0.125 → 0.13（不是银行家舍入的 0.12）
          grade("SYN-MOCK-LAW-S02", 1, max_score=3, paper=DAOFA),             # 1/3 × 2 = 0.666… → 0.67
          grade("SYN-MOCK-LAW-M01", 4, max_score=6, paper=DAOFA, grading="deterministic_rubric")]   # 4/6 × 9 = 6
    r = summarize(gs, finished(DAOFA, gs), DAOFA)
    assert [s["score"] for s in r["sections"]] == [0.8, 6]
    assert r["score"] == 6.8
    for bad in (0, -1):
        g2 = copy.deepcopy(gs)
        g2[0]["max_score"] = bad
        g2[0]["score"] = 0
        assert code_of(lambda: summarize(g2, finished(DAOFA, gs), DAOFA)) == "GRADE_INVALID"


def daofa_grades(points=True):
    m01 = grade("SYN-MOCK-LAW-M01", 4, max_score=6, paper=DAOFA, grading="deterministic_rubric")
    if points:
        m01["scoring_points"] = [{"point_id": "p1", "achieved": True, "points_awarded": 2, "evidence_ids": m01["evidence_ids"]},
                                 {"point_id": "p2", "achieved": False, "points_awarded": 0},
                                 {"point_id": "p3", "achieved": True, "points_awarded": 2}]
    return [grade("SYN-MOCK-LAW-S01", 2, paper=DAOFA), grade("SYN-MOCK-LAW-S02", 0, paper=DAOFA),
            grade("SYN-MOCK-LAW-S03", 2, paper=DAOFA), m01]


def test_expired_session_scores_answered_part_and_points_to_nodes():
    gs = daofa_grades()
    s = finished(DAOFA, gs, expire=True)
    assert s.state == "expired"
    r = summarize(gs, s, DAOFA, point_nodes=POINT_NODES)
    assert (r["score"], r["full_score"], r["timed_out"], r["state"], r["provisional"]) == (10, 60, True, "expired", False)
    assert [(x["score"], x["answered"], x["unanswered"]) for x in r["sections"]] == [(4, 3, 9), (6, 1, 3)]
    assert r["unanswered_count"] == 12
    # 观点甲一：S01 2/2 + S07 未作答 0/2 + M01 采分点 p1、p3 各 2/6 × 9 = 3 / 3
    assert elp(r, "MORAL.LAW.SYN_BC_A.VP_A1") == (8, 2, 10)
    # 观点甲二：S02 0/2 + S08 未作答 0/2 + M01 采分点 p2 0 / 3
    assert elp(r, "MORAL.LAW.SYN_BC_A.VP_A2") == (0, 7, 7)
    items = {x["node_id"]: x for x in r["nodes"]}["MORAL.LAW.SYN_BC_A.VP_A1"]["items"]
    assert {"item_id": "SYN-MOCK-LAW-M01", "basis": "scoring_points"} in items and len(items) == 3
    assert "SYN-MOCK-LAW-M01" not in r["whole_item_ids"]


def test_whole_item_when_no_points_or_incomplete():
    s = finished(DAOFA, daofa_grades(), expire=True)
    r = summarize(daofa_grades(), s, DAOFA)                               # 没给采分点快照
    assert elp(r, "MORAL.LAW.SYN_BC_A.VP_A1") == (8, 5, 13)                # M01 整题 6/9 计入
    assert elp(r, "MORAL.LAW.SYN_BC_A.VP_A2") == (6, 7, 13)                # 也整题计入观点甲二
    assert "SYN-MOCK-LAW-M01" in r["whole_item_ids"]
    gs = daofa_grades()
    gs[3]["scoring_points"] = gs[3]["scoring_points"][:2]                 # 判分只报了两个采分点：不完整
    r = summarize(gs, finished(DAOFA, gs, expire=True), DAOFA, point_nodes=POINT_NODES)
    assert elp(r, "MORAL.LAW.SYN_BC_A.VP_A1") == (8, 5, 13)
    gs = daofa_grades()
    del gs[3]["scoring_points"][0]["points_awarded"]                     # 没有逐点得分
    r = summarize(gs, finished(DAOFA, gs, expire=True), DAOFA, point_nodes=POINT_NODES)
    assert "SYN-MOCK-LAW-M01" in r["whole_item_ids"]
    gs = daofa_grades()
    gs[3]["max_score"] = 9                                               # 快照采分点合计 6 ≠ 判分满分 9：不按采分点
    r = summarize(gs, finished(DAOFA, gs, expire=True), DAOFA, point_nodes=POINT_NODES)
    assert elp(r, "MORAL.LAW.SYN_BC_A.VP_A1") == (6, 7, 13)                # M01 整题 4/9 × 9 = 4 计入
    assert "SYN-MOCK-LAW-M01" in r["whole_item_ids"]


def test_nothing_answered():
    s = S.advance(started(), now=DEADLINE)
    r = summarize([], s)
    assert (r["score"], r["unanswered_count"], r["timed_out"], r["provisional"], r["evidence_ids"]) == (0, 26, True, False, [])


def test_unmapped_items_listed():
    nodes = {k: v for k, v in ITEM_NODES.items() if k != "Q-FUN-U01-T-002"}
    r = summarize(nodes=nodes)
    assert r["unmapped_item_ids"] == ["Q-FUN-U01-T-002"]
    assert "Q-FUN-U01-T-002" not in r["whole_item_ids"]
    assert {n["node_id"]: n["possible"] for n in r["nodes"]}["MATH.SYN.EQ.QUAD_SOLVE"] == 3


def test_node_map_values_must_be_lists():
    for bad in ("MATH.SYN.EQ.QUAD_SOLVE", None, [""], [1], {"MATH.SYN.EQ.QUAD_SOLVE": 1}):
        nodes = {**ITEM_NODES, "Q-FUN-U01-T-002": bad}
        assert code_of(lambda: summarize(nodes=nodes)) == "NODE_MAP_INVALID"
    assert summarize(nodes={**ITEM_NODES, "Q-FUN-U01-T-002": ("MATH.SYN.EQ.QUAD_SOLVE",)})["score"] == 48
    gs = daofa_grades()
    bad_points = {"SYN-MOCK-LAW-M01": {"p1": {"score": 2, "node_ids": "MORAL.LAW.SYN_BC_A.VP_A1"}}}
    assert code_of(lambda: summarize(gs, finished(DAOFA, gs), DAOFA, point_nodes=bad_points)) == "NODE_MAP_INVALID"


def test_last_answer_counts():
    s = started()
    s = S.answer(s, MATH, item_id="Q-FUN-U01-T-002", answer_ref="att-first", now=T0 + MIN)
    s = S.answer(s, MATH, item_id="Q-FUN-U01-T-002", answer_ref="att-second", now=T0 + 2 * MIN)
    s = S.submit(s, now=T0 + 3 * MIN)
    assert summarize([grade("Q-FUN-U01-T-002", 0, ref="att-second")], s)["score"] == 0
    assert code_of(lambda: summarize([grade("Q-FUN-U01-T-002", 3, ref="att-first")], s)) == "GRADE_STALE"


def test_rubric_version_checked_when_registered():
    rub = {"Q-FUN-U01-T-002": "aya-symbolic-ts@0.1.0"}
    assert summarize(item_rubrics=rub)["score"] == 48
    assert code_of(lambda: summarize(item_rubrics={"Q-FUN-U01-T-002": "aya-symbolic-ts@0.2.0"})) == "RUBRIC_MISMATCH"
    gs = copy.deepcopy(MATH_GRADES)
    gs[0]["source_version"] = "prompt:aya-symbolic-ts@0.1.0"                # 同名但不是 rubric / verifier 段
    assert code_of(lambda: summarize(gs, finished(), item_rubrics=rub)) == "RUBRIC_MISMATCH"


# ---------- 与契约、再练登记一致 ----------
def test_version_patterns_match_contract():
    spec = yaml.safe_load((REPO / "packages" / "contracts" / "openapi" / "plugin-api.v1.1.yaml").read_text(encoding="utf-8"))
    schemas = spec["components"]["schemas"]
    assert Y.MODEL_VERSION_RE.pattern == schemas["ModelVersion"]["pattern"]
    assert Y.SOURCE_VERSION_RE.pattern == schemas["SourceVersion"]["pattern"]
    assert schemas["EvidenceId"]["format"] == "uuid"


def test_grading_vocabulary_is_practice_registry():
    assert Y.GRADINGS is GRADING
    assert set(GRADING) == {"validator", "deterministic_rubric", "model_rubric"}


# ---------- 拒绝 ----------
# 写成字面量（不取 Y.PROVENANCE）：模块里少列一个字段时，这里的用例不会跟着少
FIVE = ("evidence_ids", "confidence", "source_version", "model_version", "human_review_required")


def test_provenance_fields_are_the_rule_3_five():
    assert set(Y.PROVENANCE) == set(FIVE)


@pytest.mark.parametrize("field", FIVE)
def test_missing_provenance_rejected(field):
    gs = copy.deepcopy(MATH_GRADES)
    del gs[3][field]
    assert code_of(lambda: summarize(gs, finished())) == "MISSING_PROVENANCE"
    gs = copy.deepcopy(MATH_GRADES)
    gs[3][field] = None
    assert code_of(lambda: summarize(gs, finished())) == "MISSING_PROVENANCE"


BAD = {
    "证据为空": {"evidence_ids": []},
    "证据不是 UUID": {"evidence_ids": ["ev-1"]},
    "证据重复": {"evidence_ids": [str(NS), str(NS)]},
    "置信度越界": {"confidence": 1.2},
    "置信度是布尔": {"confidence": True},
    "模型版本不可追溯": {"model_version": "routed"},
    "来源版本是自然语言": {"source_version": "验证器 0.1"},
    "复核标记不是布尔": {"human_review_required": "no"},
    "得分超过满分": {"score": 4},
    "得分为负": {"score": -1},
    "得分是字符串": {"score": "3"},
    "缺校准标记": {"calibrated": None},
    "不是验证器或 Rubric": {"grading": "persona"},
    "夹带讲解文本": {"comment": "这道题你掌握得很好"},
    "复核原因是自然语言": {"review_reasons": ["看起来不对"]},
    "采分点不是列表": {"scoring_points": {"p1": 1}},
    "采分点夹带文本": {"scoring_points": [{"point_id": "p1", "points_awarded": 1, "note": "答得好"}]},
    "采分点得分为负": {"scoring_points": [{"point_id": "p1", "points_awarded": -1}]},
    "采分点重复": {"scoring_points": [{"point_id": "p1"}, {"point_id": "p1"}]},
}


@pytest.mark.parametrize("name", list(BAD))
def test_invalid_grade_rejected(name):
    gs = copy.deepcopy(MATH_GRADES)
    gs[0].update(BAD[name])
    assert code_of(lambda: summarize(gs, finished())) == "GRADE_INVALID"


def test_missing_calibrated_rejected():
    gs = copy.deepcopy(MATH_GRADES)
    del gs[0]["calibrated"]
    assert code_of(lambda: summarize(gs, finished())) == "GRADE_INVALID"


def test_grade_consistency_with_session_and_paper():
    s = finished()
    extra = grade("Q-FUN-U13-L-002", 3)                                              # 卷里有、没作答
    assert code_of(lambda: summarize(MATH_GRADES + [extra], s)) == "GRADE_FOR_UNANSWERED"
    assert code_of(lambda: summarize(MATH_GRADES[1:], s)) == "GRADE_MISSING"
    assert code_of(lambda: summarize(MATH_GRADES + [MATH_GRADES[0]], s)) == "GRADE_DUPLICATE"
    foreign = copy.deepcopy(MATH_GRADES)
    foreign[0]["item_id"] = "SYN-MOCK-LAW-S01"
    assert code_of(lambda: summarize(foreign, s)) == "ITEM_NOT_IN_PAPER"


def test_session_must_be_finished_and_match_paper():
    s = started()
    assert code_of(lambda: summarize([], s)) == "NOT_FINISHED"
    assert code_of(lambda: summarize([], S.create(MATH, session_id=SESSION_ID, student=STUDENT, now=T0))) == "NOT_FINISHED"
    assert code_of(lambda: Y.summarize(DAOFA, finished(), MATH_GRADES, ITEM_NODES, CFG)) == "PAPER_MISMATCH"


def test_output_has_no_statements_about_the_student():
    """规则 11：汇总只是证据汇总。所有字符串值都必须是 ID 或编码（卷、题、节点、证据、节名、状态码、复核原因码、计入方式码）。"""
    gs = daofa_grades()
    r_d = summarize(gs, finished(DAOFA, gs, expire=True), DAOFA, point_nodes=POINT_NODES)
    for r, paper in ((summarize(), MATH), (r_d, DAOFA)):
        allowed = ({paper.paper_id, paper.version, paper.subject, SESSION_ID, "submitted", "expired",
                    Y.REVIEW_HUMAN, Y.REVIEW_LOW_CONFIDENCE, Y.REVIEW_SCORE_UNAVAILABLE, Y.BASIS_POINTS, Y.BASIS_WHOLE}
                   | {i.item_id for i in paper.items} | {s.name for s in paper.sections}
                   | {n for v in ITEM_NODES.values() for n in v} | set(r["evidence_ids"]))
        seen: list[str] = []

        def walk(x):
            if isinstance(x, dict):
                for v in x.values():
                    walk(v)
            elif isinstance(x, list):
                for v in x:
                    walk(v)
            elif isinstance(x, str):
                seen.append(x)

        walk(r)
        assert seen and set(seen) <= allowed
        assert set(r) == {"paper_id", "paper_version", "subject", "session_id", "state", "timed_out", "score", "full_score",
                          "provisional", "uncalibrated", "uncalibrated_item_ids", "sections", "answered_count",
                          "unanswered_count", "pending_review", "nodes", "whole_item_ids", "unmapped_item_ids", "evidence_ids"}


def test_deterministic_and_order_independent():
    a = summarize()
    b = Y.summarize(MATH, finished(), list(reversed(MATH_GRADES)), dict(reversed(list(ITEM_NODES.items()))), CFG)
    assert a == b
    assert summarize(session=finished()) == a
