"""跨科周报 v0（冲刺 5-H spec 设计要点 4；验收“周报：每句带判断 ID；删掉或降为不可见任一判断，对应句子消失；禁用推断、程度升级
任一命中则不出周报；家长版无对话原文、同伴比较、排名；学生版无家长问卷与评价”）。纯函数单测，全部合成占位。"""
from __future__ import annotations

import copy
import dataclasses
import json
from datetime import date, datetime, timedelta, timezone

import pytest

from insight import weekly as weekly_mod
from insight.policy import CONFIG_DIR, PolicyError
from insight.render import ClaimForRender
from insight.shared import consistency, forbidden_matcher
from insight.weekly import (ALL_SECTIONS, ObjectiveFacts, WeeklyClaim, build_weekly, objective_column, parse_weekly_policy,
                            sentences_of, synthetic_weekly_check_config, synthetic_weekly_policy, to_json, week_of, week_window)
from support import NODE_A, NODE_B, NODE_C, make_policy

UTC = timezone.utc
POLICY = make_policy()
WEEKLY = synthetic_weekly_policy()
CFG = synthetic_weekly_check_config()
FORBID = forbidden_matcher()
WEEK = date(2026, 9, 28)                                  # 周一
NOW = datetime(2026, 10, 1, 8, 0, tzinfo=UTC)             # 周四
IN_WEEK = datetime(2026, 9, 29, 3, 0, tzinfo=UTC)         # 本周
BEFORE = datetime(2026, 9, 27, 15, 59, tzinfo=UTC)        # 上周日 23:59（上海）
TEXT_M = "【占位模板·未审核】{node_id}｜{level}｜{gate}"
TEXT_T = "【占位模板·未审核】迁移｜{node_id}｜{level}"
FACTS = ObjectiveFacts(session_seconds=5 * 3600 + 59, plan_done=4, plan_skipped=1,
                       practice={"aya": (3600, 18), "echo": (600, 3), "wenli": (125, 2)})


def cid(i: int) -> str:
    return f"c1b00000-0000-4000-8000-{i:012d}"


def wc(i: int, key: str = "syn.math.node_mastery", status: str = "hypothesis", node: str = NODE_A, gate: str = "execute",
       vis=("agent", "student", "guardian"), subject: str = "aya", text: str | None = None, conf: float = 0.5, target_type: str = "node",
       expires=NOW + timedelta(days=10), source: str = "inferred", created=IN_WEEK, version: int = 1, level: str = "apply") -> WeeklyClaim:
    text = text if text is not None else (TEXT_T if key.endswith("transfer") else TEXT_M)
    return WeeklyClaim(ClaimForRender(cid(i), key, version, text, status, node, {"level": level, "gate": gate}, conf), subject, target_type,
                       tuple(vis), expires, source, created)


CLAIMS = [wc(1), wc(2, status="established", node=NODE_B, gate="stable", conf=0.9),
          wc(3, key="syn.math.node_transfer", node=NODE_C, gate="transfer", conf=0.8),
          wc(4, node=NODE_C, gate="transfer", conf=0.8)]   # 知识掌握判断且已到迁移：不进“攻克”


def build(claims=CLAIMS, audience="student", policy=POLICY, weekly=WEEKLY, cfg=CFG, forbid=FORBID, facts=FACTS):
    return build_weekly(claims, audience, WEEK, policy, weekly, cfg, forbid, NOW, facts)


def ids(res) -> list[str]:
    return [s["claim_id"] for s in sentences_of(res.report)]


def test_student_notebook_structure_every_sentence_has_a_claim_id():
    res = build()
    assert res.ok, res.blocked
    r = res.report
    assert r["audience"] == "student" and r["week_start"] == "2026-09-28" and r["week_end"] == "2026-10-04"
    assert r["title"] == WEEKLY.titles["student"]
    assert [s["key"] for s in r["sections"]] == ["lit", "working_on"]            # “下一步”没有对应的判断模板：v0 不出
    assert [s["connective"] for s in r["sections"]] == [WEEKLY.connectives["lit"], WEEKLY.connectives["working_on"]]
    assert ids(res) == [cid(3), cid(2), cid(1)] == r["claim_ids"]
    for s in sentences_of(r):
        assert set(s) <= {"claim_id", "template_key", "template_version", "section", "text", "label"} and s["claim_id"]
    assert [s.get("label") for s in sentences_of(r)] == ["preliminary", None, "preliminary"]  # 假设标“初步判断”，学生版不带置信度
    assert sentences_of(r)[0]["text"] == f"【占位模板·未审核】迁移｜{NODE_C}｜apply"
    assert r["objective"] == {"source_type": "objective", "usage_minutes": 300, "plan_done": 4, "plan_skipped": 1,
                              "subjects": [{"subject": "aya", "practice_minutes": 60, "attempts": 18},
                                           {"subject": "wenli", "practice_minutes": 2, "attempts": 2}]}


def test_guardian_weekly_structure_with_confidence_bands():
    res = build(audience="guardian")
    assert res.ok, res.blocked
    assert [s["key"] for s in res.report["sections"]] == ["progress", "stuck"]
    assert [s["confidence_band"] for s in sentences_of(res.report)] == ["strong", "strong", "moderate"]
    assert all(s["section"] in ("progress", "stuck") for s in sentences_of(res.report))


def test_lit_only_this_week_and_working_on_only_below_transfer():
    old_transfer = wc(3, key="syn.math.node_transfer", node=NODE_C, gate="transfer", created=BEFORE)
    res = build([old_transfer, *CLAIMS[:2]])
    assert [s["key"] for s in res.report["sections"]] == ["working_on"] and cid(3) not in ids(res)
    edge = wc(3, key="syn.math.node_transfer", node=NODE_C, gate="transfer", created=datetime(2026, 10, 4, 15, 59, tzinfo=UTC))
    assert cid(3) in ids(build([edge]))                  # 周日 23:59（上海）仍是本周
    late = wc(3, key="syn.math.node_transfer", node=NODE_C, gate="transfer", created=datetime(2026, 10, 4, 16, 0, tzinfo=UTC))
    assert build([late]).empty                           # 下周一 00:00（上海）起不算本周


def test_removed_or_invisible_claim_disappears():
    base = set(ids(build()))
    assert set(ids(build([c for c in CLAIMS if c.claim.claim_id != cid(2)]))) == base - {cid(2)}
    hidden = [dataclasses.replace(c, visibility=("agent",)) if c.claim.claim_id == cid(1) else c for c in CLAIMS]
    assert cid(1) not in ids(build(hidden))
    guardian_only = [dataclasses.replace(c, visibility=("agent", "guardian")) if c.claim.claim_id == cid(1) else c for c in CLAIMS]
    assert cid(1) not in ids(build(guardian_only)) and cid(1) in ids(build(guardian_only, audience="guardian"))


@pytest.mark.parametrize("mutate", [
    lambda c: dataclasses.replace(c, claim=dataclasses.replace(c.claim, status="to_verify")),   # 规则 11：待验证不作陈述
    lambda c: dataclasses.replace(c, claim=dataclasses.replace(c.claim, status="refuted")),
    lambda c: dataclasses.replace(c, claim=dataclasses.replace(c.claim, status="withdrawn")),
    lambda c: dataclasses.replace(c, expires_at=NOW),                                           # 到期
    lambda c: dataclasses.replace(c, target_type="metric"),                                     # 中圈指标永远不进
    lambda c: dataclasses.replace(c, subject="wenli"),                                          # 不在首发学科
    lambda c: dataclasses.replace(c, subject="echo"),                                           # 英语不出现
    lambda c: dataclasses.replace(c, claim=dataclasses.replace(c.claim, template_version=2)),   # 模板版本对不上
    lambda c: dataclasses.replace(c, claim=dataclasses.replace(c.claim, template_key="syn.metric.middle_circle")),
])
def test_ineligible_claims_never_become_sentences(mutate):
    claims = [mutate(c) if c.claim.claim_id == cid(1) else c for c in CLAIMS]
    for aud in ("student", "guardian"):
        assert cid(1) not in ids(build(claims, audience=aud))


def test_student_version_has_no_guardian_sourced_claims():
    g = wc(9, node="MATH.SYN.EQ.OTHER", source="guardian")
    assert cid(9) not in ids(build([*CLAIMS, g]))
    assert cid(9) in ids(build([*CLAIMS, g], audience="guardian"))
    assert build([g]).empty


def test_max_per_section():
    many = [wc(20 + i, node=f"MATH.SYN.N{i:02d}") for i in range(8)]
    res = build(many)
    assert len(sentences_of(res.report)) == WEEKLY.max_per_section


def test_unapproved_connective_withholds():
    cfg = dataclasses.replace(CFG, connective_phrases=frozenset({consistency.normalize(WEEKLY.connectives["lit"])}))
    res = build(cfg=cfg)
    assert not res.ok and res.blocked == ("connective_unapproved",)


def test_degree_escalation_withholds():
    weekly = dataclasses.replace(WEEKLY, connectives={**WEEKLY.connectives, "working_on": "【占位衔接·丁】很薄弱"})
    cfg = dataclasses.replace(CFG, connective_phrases=CFG.connective_phrases | {consistency.normalize("【占位衔接·丁】很薄弱")})
    res = build(weekly=weekly, cfg=cfg)
    assert not res.ok and "degree_escalation" in res.blocked


def test_new_conclusion_withholds():
    weekly = dataclasses.replace(WEEKLY, connectives={**WEEKLY.connectives, "lit": "【占位衔接·丙】优秀"})
    cfg = dataclasses.replace(CFG, connective_phrases=CFG.connective_phrases | {consistency.normalize("【占位衔接·丙】优秀")})
    res = build(weekly=weekly, cfg=cfg)
    assert not res.ok and "new_conclusion" in res.blocked


def test_forbidden_inference_withholds():
    lazy = wc(9, node="MATH.SYN.EQ.OTHER", text="【占位模板·未审核】{node_id} 这孩子有点懒惰")
    res = build([*CLAIMS, lazy])
    assert not res.ok and "render_failed" in res.blocked          # 渲染阶段就拦住（命中禁用推断不渲染）
    weekly = dataclasses.replace(WEEKLY, titles={**WEEKLY.titles, "student": "【占位】这孩子天赋很高"})
    res = build(weekly=weekly)
    assert not res.ok and "forbidden_inference" in res.blocked


@pytest.mark.parametrize("audience,word", [("guardian", "排名"), ("guardian", "同学"), ("guardian", "班级平均"), ("guardian", "梯队"),
                                           ("student", "家长说"), ("student", "问卷")])
def test_excluded_terms_withhold(audience, word):
    """家长版不含同伴比较、排名与层级标签；学生版不含家长问卷与家长评价的说法（排除词表全文另查）"""
    bad = wc(9, node="MATH.SYN.EQ.OTHER", text=f"【占位模板·未审核】{{node_id}} {word}")
    res = build([*CLAIMS, bad], audience=audience)
    assert not res.ok and "excluded_term" in res.blocked


def test_guardian_version_has_no_dialogue_or_free_text_fields():
    r = build(audience="guardian").report
    assert set(r) == {"format", "audience", "week_start", "week_end", "title", "sections", "claim_ids", "objective", "algorithm_version",
                      "policy_version", "check"}
    for s in sentences_of(r):
        assert set(s) <= {"claim_id", "template_key", "template_version", "section", "text", "label", "confidence_band"}


def test_no_claims_is_empty_not_withheld():
    res = build([])
    assert res.empty and not res.ok
    with pytest.raises(ValueError):
        build(audience="agent")


def test_same_input_same_bytes():
    a, b = build(), build(list(reversed(CLAIMS)))
    assert to_json(a.report) == to_json(b.report)


def test_reuses_first_report_pipeline():
    """复用首份报告的规则引擎与渲染（同一个模块对象），不另写一套"""
    import learning.mastery.consistency as learning_consistency
    from insight import render, report
    assert weekly_mod.consistency is learning_consistency and weekly_mod.render_text is render.render_text
    assert weekly_mod._order is report._order


def test_objective_column_rules():
    col = objective_column(ObjectiveFacts(10 ** 9, 10 ** 6, -3, {"zhixing": (59, 1), "echo": (6000, 9)}))
    assert col == {"source_type": "objective", "usage_minutes": 10080, "plan_done": 9999, "plan_skipped": 0,
                   "subjects": [{"subject": "zhixing", "practice_minutes": 0, "attempts": 1}]}
    assert objective_column(ObjectiveFacts())["subjects"] == []


def test_week_helpers():
    assert week_of(date(2026, 10, 4)) == (WEEK, date(2026, 10, 4)) and week_of(WEEK) == (WEEK, date(2026, 10, 4))
    start, end = week_window(WEEK)
    assert start == datetime(2026, 9, 27, 16, 0, tzinfo=UTC) and end - start == timedelta(days=7)
    with pytest.raises(ValueError):
        week_window(date(2026, 9, 29))


def _doc():
    return json.loads((CONFIG_DIR / "weekly.synthetic.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("change,msg", [
    (lambda d: d["connectives"].pop("stuck"), "connectives"),
    (lambda d: d["titles"].update(extra="x"), "titles"),
    (lambda d: d["sample"].update(ratio=0.6), "sample.ratio"),
    (lambda d: d["sample"].update(ratio=0), "sample.ratio"),
    (lambda d: d["sample"].update(min=-1), "sample.min"),
    (lambda d: d.update(max_per_section=0), "max_per_section"),
    (lambda d: d["excluded_terms"].update(guardian=[]), "排除词表"),
    (lambda d: d["titles"].update(student="【占位】这孩子天赋很高"), "禁用推断"),
    (lambda d: d["connectives"].update(progress="【占位】排名"), "排除词"),
    (lambda d: d.update(unknown=1), "配置位"),
])
def test_weekly_policy_validation(change, msg):
    d = copy.deepcopy(_doc())
    change(d)
    with pytest.raises(PolicyError, match=msg):
        parse_weekly_policy(d, env="test")


def test_weekly_policy_synthetic_only_in_test_and_signing():
    with pytest.raises(PolicyError, match="INSIGHT_ENV=test"):
        parse_weekly_policy(_doc(), env="dev")
    assert not WEEKLY.signed and WEEKLY.synthetic
    d = {**_doc(), "synthetic": False, "signed_by": "test-signer", "signed_at": "2026-09-30"}
    assert parse_weekly_policy(d, env="dev").signed
    assert set(WEEKLY.connectives) == set(ALL_SECTIONS) and WEEKLY.sample_ratio == 0.1 and WEEKLY.sample_min == 5
    # 衔接短语与一致性检查词表对得上（每段的衔接短语都在人审过的固定短语表里）
    assert {consistency.normalize(c) for c in WEEKLY.connectives.values()} <= CFG.connective_phrases


def test_sentences_carry_the_claim_revision_marker():
    """每句记所引判断的修订标记（判断的 updated_at），BFF 读取时丢掉生成后被修订过的句子（安全审查 F1）"""
    rev = datetime(2026, 9, 30, 1, 2, 3, 456789, tzinfo=UTC)
    res = build([dataclasses.replace(wc(1), updated_at=rev)])
    assert [s["claim_revision"] for s in sentences_of(res.report)] == ["2026-09-30T01:02:03.456789+00:00"]
    assert "claim_revision" not in sentences_of(build([wc(1)]).report)[0]
