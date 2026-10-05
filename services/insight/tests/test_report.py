"""首份报告与渲染（spec 设计要点 3、4；验收“每一句都带判断 ID；删掉任一判断或降为不可见，对应句子不再出现；
程度升级、新增结论、禁用推断词任一命中即不出报告（各有测试）”）。模板与衔接短语全部是合成占位。"""
from __future__ import annotations

import dataclasses
from datetime import timedelta

import pytest

from insight import report as report_mod
from insight.policy import synthetic_check_config
from insight.render import ClaimForRender, render_statement, render_text
from insight.report import ReportClaim, build_first_report, to_json
from insight.shared import consistency, forbidden_matcher
from support import NODE_A, NODE_B, T0, make_policy

POLICY = make_policy()
CFG = synthetic_check_config()
FORBID = forbidden_matcher()
TEXT_M = "【占位模板·未审核】{node_id}｜{level}｜{gate}"
TEXT_T = "【占位模板·未审核】迁移｜{node_id}｜{level}"


def rc(cid: str, key: str = "syn.math.node_mastery", status: str = "hypothesis", node: str = NODE_A, level: str = "apply",
       gate: str = "execute", vis=("agent", "student", "guardian"), subject: str = "aya", text: str | None = None,
       conf: float = 0.5, target_type: str = "node", expires=T0 + timedelta(days=10)) -> ReportClaim:
    text = text if text is not None else (TEXT_T if key.endswith("transfer") else TEXT_M)
    return ReportClaim(ClaimForRender(cid, key, 1, text, status, node, {"level": level, "gate": gate}, conf), subject, target_type,
                       tuple(vis), expires)


def cid(i: int) -> str:
    return f"c1a00000-0000-4000-8000-{i:012d}"


CLAIMS = [rc(cid(1)), rc(cid(2), status="established", node=NODE_B, gate="stable", conf=0.9),
          rc(cid(3), key="syn.math.node_transfer", gate="transfer", conf=0.8)]


def build(claims=CLAIMS, audience="student", policy=POLICY, cfg=CFG, forbid=FORBID):
    return build_first_report(claims, audience, policy, cfg, forbid, T0)


def sentences(res):
    return [s for sec in res.report["sections"] for s in sec["sentences"]]


def test_every_sentence_carries_a_claim_id_and_structure():
    res = build()
    assert res.ok, res.blocked
    r = res.report
    assert r["notice"] == POLICY.report.notice and r["title"] == POLICY.report.titles["student"]
    assert [s["key"] for s in r["sections"]] == ["node_mastery", "node_transfer"]
    assert [s["connective"] for s in r["sections"]] == ["【占位衔接·甲】", "【占位衔接·乙】"]
    assert all(s["claim_id"] for s in sentences(res)) and r["claim_ids"] == [cid(2), cid(1), cid(3)]  # 按节点 ID 排序
    assert sentences(res)[1]["text"] == f"【占位模板·未审核】{NODE_A}｜apply｜execute"


def test_student_and_guardian_versions_differ_by_label_and_confidence():
    stu, gua = sentences(build()), sentences(build(audience="guardian"))
    assert [s.get("label") for s in stu] == [None, "preliminary", "preliminary"]
    assert all("confidence_band" not in s for s in stu)
    assert [s["confidence_band"] for s in gua] == ["strong", "moderate", "strong"]
    assert [s.get("label") for s in gua] == [None, "preliminary", "preliminary"]


def test_removed_or_invisible_claim_disappears():
    base = {s["claim_id"] for s in sentences(build())}
    without = [c for c in CLAIMS if c.claim.claim_id != cid(2)]
    assert {s["claim_id"] for s in sentences(build(without))} == base - {cid(2)}
    hidden = [dataclasses.replace(c, visibility=("agent",)) if c.claim.claim_id == cid(1) else c for c in CLAIMS]
    assert cid(1) not in {s["claim_id"] for s in sentences(build(hidden))}
    guardian_only = [dataclasses.replace(c, visibility=("agent", "guardian")) if c.claim.claim_id == cid(1) else c for c in CLAIMS]
    assert cid(1) not in {s["claim_id"] for s in sentences(build(guardian_only))}
    assert cid(1) in {s["claim_id"] for s in sentences(build(guardian_only, audience="guardian"))}


@pytest.mark.parametrize("mutate", [
    lambda c: dataclasses.replace(c, claim=dataclasses.replace(c.claim, status="to_verify")),     # 待验证不作陈述（规则 11）
    lambda c: dataclasses.replace(c, claim=dataclasses.replace(c.claim, status="refuted")),
    lambda c: dataclasses.replace(c, expires_at=T0 - timedelta(seconds=1)),                        # 已到期
    lambda c: dataclasses.replace(c, subject="wenli"),                                          # 首发只数学
    lambda c: dataclasses.replace(c, target_type="metric"),                                     # 中圈指标永远不进报告
    lambda c: dataclasses.replace(c, template_approved=False),
    lambda c: dataclasses.replace(c, claim=dataclasses.replace(c.claim, template_key="syn.metric.middle_circle")),
])
def test_ineligible_claims_are_excluded(mutate):
    claims = [mutate(CLAIMS[0])] + CLAIMS[1:]
    assert cid(1) not in {s["claim_id"] for s in sentences(build(claims))}


def test_degree_escalation_blocks():
    policy = make_policy(report={**_report_doc(), "connectives": {"node_mastery": "【占位衔接·甲】很薄弱", "node_transfer": "【占位衔接·乙】"}})
    cfg = _cfg_with_connectives(["【占位衔接·甲】很薄弱", "【占位衔接·乙】"])
    res = build(policy=policy, cfg=cfg)
    assert not res.ok and "degree_escalation" in res.blocked


def test_new_conclusion_blocks():
    policy = make_policy(report={**_report_doc(), "connectives": {"node_mastery": "【占位衔接·甲】", "node_transfer": "【占位衔接·乙】优秀"}})
    cfg = _cfg_with_connectives(["【占位衔接·甲】", "【占位衔接·乙】优秀"])
    res = build(policy=policy, cfg=cfg)
    assert not res.ok and "new_conclusion" in res.blocked


def test_unapproved_connective_blocks():
    cfg = _cfg_with_connectives(["【占位衔接·甲】"])
    res = build(cfg=cfg)
    assert not res.ok and res.blocked == ("connective_unapproved",)


def test_forbidden_inference_in_rendered_text_blocks():
    claims = [rc(cid(9), text="【占位模板·未审核】{node_id} 这孩子有点懒惰")] + CLAIMS[1:]
    res = build(claims)
    assert not res.ok and "render_failed" in res.blocked  # 渲染阶段就拦住（命中禁用推断不渲染）
    # 绕过渲染直接进检查：一致性检查同样拦（全文禁用推断）
    sections = [consistency.Section("node_mastery", "【占位衔接·甲】", (consistency.Statement(cid(9), "这孩子有点懒惰"),))]
    views = {cid(9): consistency.ClaimView(cid(9), "hypothesis", "syn.math.node_mastery")}
    assert "forbidden_inference" in consistency.check_page(sections, views, CFG, FORBID).codes


def test_forbidden_inference_in_fixed_text_blocks():
    policy = dataclasses.replace(POLICY, report=dataclasses.replace(POLICY.report, notice="【占位】这孩子天赋很高"))
    res = build(policy=policy)
    assert not res.ok and "forbidden_inference" in res.blocked


def test_no_visible_claims_gives_no_report():
    assert build([]).blocked == ("no_visible_claims",)
    with pytest.raises(ValueError):
        build(audience="agent")


def _many(n: int, key: str = "syn.math.node_mastery", start: int = 1000) -> list[ReportClaim]:
    return [rc(cid(start + i), key=key) for i in range(n)]


def _envelope(report: dict) -> dict:
    result = {k: report[k] for k in ("format", "audience", "policy_version", "notice", "title", "sections", "claim_ids")}
    return {"contract_version": "0.9.0", "result": result, "evidence_ids": [], "confidence": 0.5,
            "source_version": "algo:sie-report@0.1.0", "model_version": "algo:sie-report@0.1.0", "human_review_required": False}


def test_report_carries_policy_version():
    """固定文字可核对：报告带所用策略配置版本（提示语、标题、衔接短语须与该版本登记文字逐字一致）"""
    r = build().report
    assert r["policy_version"] == POLICY.config_version
    assert r["notice"] == POLICY.report.notice and [s["connective"] for s in r["sections"]] == [
        POLICY.report.connectives[s["key"]] for s in r["sections"]]


def test_report_limits_match_contract_and_never_truncate():
    """报告单独上限（每节 200 句、全报告 400 个判断 ID）：到上限照常出且符合契约；超出整份阻断，不截断、不产出超契约的报告"""
    from insight.service.contract import contract
    C = contract()
    full = _many(report_mod.MAX_SECTION_SENTENCES) + _many(report_mod.MAX_SECTION_SENTENCES, "syn.math.node_transfer", 5000)
    for aud in ("student", "guardian"):
        res = build(full, audience=aud)
        assert res.ok, res.blocked
        assert len(res.report["claim_ids"]) == report_mod.MAX_REPORT_CLAIMS
        assert C.validate("InsightReportResponse", _envelope(res.report)) == []
    # 一节多一句：阻断（不是截到 200 句）
    assert build(_many(report_mod.MAX_SECTION_SENTENCES + 1)).blocked == ("too_many_claims",)
    # 读取多出一条（store 多取一条用来发现超限）：阻断，即便其中有不合格的判断
    over = full + [rc(cid(9999), status="to_verify")]
    assert len(over) == report_mod.MAX_REPORT_CLAIMS + 1 and build(over).blocked == ("too_many_claims",)


@pytest.mark.parametrize("change", [
    lambda p: dataclasses.replace(p, report=dataclasses.replace(p.report, titles={**p.report.titles, "student": "【占位】" + "合" * 80})),
    lambda p: dataclasses.replace(p, report=dataclasses.replace(p.report, notice="【占位】" + "合" * 200)),
    lambda p: dataclasses.replace(p, report=dataclasses.replace(
        p.report, connectives={**p.report.connectives, "node_mastery": "【占位衔接·甲】" + "合" * 60})),
])
def test_fixed_text_over_contract_limit_blocks(change):
    res = build(policy=change(POLICY))
    assert not res.ok and "over_length" in res.blocked


def test_sentence_over_contract_limit_blocks():
    """句子 300 字上限由共用渲染规则保证（与契约 ReportSentence.text 一致）：超长不渲染，整份阻断"""
    from insight.render import MAX_TEXT
    assert MAX_TEXT == 300
    res = build([rc(cid(9), text="【占位模板·未审核】{node_id}" + "合" * 300)] + CLAIMS[1:])
    assert not res.ok and "render_failed" in res.blocked


@pytest.mark.anyio
async def test_store_report_claims_reads_one_over_the_report_limit():
    """store.report_claims 不沿用 visible_claims 缺省 200 条：按报告上限多取一条"""
    from insight.store import InsightStore
    seen = {}

    class Probe(InsightStore):
        async def visible_claims(self, student, audience, **kw):
            seen.update(kw)
            return []

    assert await Probe(None).report_claims("00000000-0000-4000-8000-00000000a001", "student") == []  # type: ignore[arg-type]
    assert seen["limit"] == report_mod.MAX_REPORT_CLAIMS + 1


def test_same_input_same_bytes():
    a, b = build(), build(list(reversed(CLAIMS)))
    assert to_json(a.report) == to_json(b.report)
    assert to_json(a.report).encode() == to_json(build().report).encode()


def test_render_rules():
    assert render_text("【占位】{node_id}｜{gate}", NODE_A, {"gate": "stable"}, FORBID) == f"【占位】{NODE_A}｜stable"
    assert render_text("【占位】{missing}", NODE_A, {}, FORBID) is None
    assert render_text("【占位】{gate}", NODE_A, {"gate": "自由文本"}, FORBID) is None  # 值只收编码
    assert render_text("【占位】{gate} {", NODE_A, {"gate": "x"}, FORBID) is None
    assert render_text(None, NODE_A, {}, FORBID) is None
    assert render_text("【占位】智商", NODE_A, {}, FORBID) is None
    c = ClaimForRender(cid(1), "syn.math.node_mastery", 1, TEXT_M, "to_verify", NODE_A, {"level": "apply", "gate": "stable"}, 0.3)
    s = render_statement(c, "few_evidence", FORBID)
    assert s["display"] == "dashed" and s["status"] == "to_verify" and s["confidence_band"] == "few_evidence"
    assert render_statement(dataclasses.replace(c, status="hypothesis"), "moderate", FORBID)["display"] == "solid"
    assert render_statement(dataclasses.replace(c, status="withdrawn"), "moderate", FORBID) is None
    # 调用点也把禁用推断匹配传给共用的渲染规则（learning/mastery/render.py）：命中即不出陈述
    assert render_statement(dataclasses.replace(c, text_template="【占位】这孩子智商高"), "moderate", FORBID) is None


def test_report_reuses_learning_consistency_engine():
    """复用 KGE-04 的规则引擎，不另写一套（同一个模块对象）"""
    import learning.mastery.consistency as learning_consistency
    assert report_mod.consistency is learning_consistency


def _report_doc():
    return {"notice": POLICY.report.notice, "title": dict(POLICY.report.titles), "connectives": dict(POLICY.report.connectives)}


def _cfg_with_connectives(phrases):
    import json

    from insight.policy import CONFIG_DIR
    doc = json.loads((CONFIG_DIR / "report-check.synthetic.json").read_text(encoding="utf-8"))
    doc["connective_phrases"] = phrases
    # 固定衔接短语表的解析会拒绝含程度词与结论词的短语；这里绕过它构造“人审过但写坏了”的词表，证明检查本身能拦
    cfg = consistency.parse_check_config({**doc, "connective_phrases": ["【占位衔接·甲】"]}, env="test")
    return dataclasses.replace(cfg, connective_phrases=frozenset(consistency.normalize(p) for p in phrases))


def test_shadow_export_matches_eval_format():
    import sys

    from insight.report import shadow_judgments
    from insight.shared import ROOT
    sys.path.insert(0, str(ROOT / "evals" / "sie-shadow"))
    import compare_shadow as cs
    res = build()
    js = shadow_judgments(res.report, CLAIMS)
    assert js == [{"node_id": NODE_B, "level": "apply", "state": "stable"}, {"node_id": NODE_A, "level": "apply", "state": "execute"}]
    side = cs._judgments([{"student_ref": "00000000-0000-4000-8000-00000000a001", "judgments": js}], "报告")
    assert len(side) == 2
