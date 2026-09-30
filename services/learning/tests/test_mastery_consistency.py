"""档案页陈述—判断一致性检查（spec 第 14 节；开发计划验收“一致性检查拦截程度升级”）。

词表是合成占位（config/mastery/profile-check.synthetic.json，待人确认 #20）；禁用推断用 SIE 的同一个匹配函数与词表。
每条规则都有正例（被阻断）与反例（合规通过）。
"""
from __future__ import annotations

import json

import pytest

from learning.mastery.config import CONFIG_DIR, ConfigError
from learning.mastery.consistency import (ClaimView, Section, Statement, check_page, default_forbidden_matcher, finalize,
                                          load_check_config, parse_check_config)

CFG = load_check_config(CONFIG_DIR / "profile-check.synthetic.json", env="test")
FORBID = default_forbidden_matcher()

CLAIMS = {
    "claim-unstable": ClaimView("claim-unstable", "established", "syn.mastery.unstable"),
    "claim-weak": ClaimView("claim-weak", "hypothesis", "syn.mastery.weak"),
    "claim-stable": ClaimView("claim-stable", "established", "syn.mastery.stable"),
    "claim-progress": ClaimView("claim-progress", "established", "syn.progress.recent"),
    "claim-verify": ClaimView("claim-verify", "to_verify", "syn.mastery.stable"),
    "claim-refuted": ClaimView("claim-refuted", "refuted", "syn.mastery.stable"),
    "claim-hidden": ClaimView("claim-hidden", "established", "syn.mastery.stable", readable=False),
}


def st(cid: str | None, text: str = "合成模板渲染文字") -> Statement:
    return Statement(cid, text)


def page(connective: str, *claim_ids: str | None, text: str = "合成模板渲染文字"):
    return [Section("working_on", connective, tuple(st(c, text) for c in claim_ids))]


def codes(sections):
    return check_page(sections, CLAIMS, CFG, FORBID).codes


# ---- 合规用例：全部通过（通过率 100%）----

COMPLIANT = [
    page("", "claim-unstable"),
    page("接下来", "claim-unstable", "claim-weak"),
    page("另外", "claim-stable"),
    page("最近一段时间", "claim-progress"),
    [Section("current", "先看这一节", (st("claim-stable"),)), Section("next", "下一步", (st("claim-unstable"),))],
]


@pytest.mark.parametrize("i", range(len(COMPLIANT)))
def test_compliant_pages_pass(i):
    res = check_page(COMPLIANT[i], CLAIMS, CFG, FORBID)
    assert res.passed, res.violations
    out = finalize(COMPLIANT[i], CLAIMS, CFG, FORBID)
    assert out.check_status == "passed" and list(out.sections) == COMPLIANT[i]


# ---- 程度升级（开发计划验收）----

def test_degree_escalation_unstable_to_weak_blocked():
    assert "degree_escalation" in codes(page("薄弱", "claim-unstable"))
    assert "degree_escalation" in codes(page("这部分很薄弱", "claim-weak"))       # 2 → 3


def test_same_or_lighter_degree_is_not_escalation():
    assert "degree_escalation" not in codes(page("薄弱", "claim-weak"))
    assert "degree_escalation" not in codes(page("尚不稳定", "claim-weak"))
    assert "degree_escalation" not in codes(page("有待巩固", "claim-unstable"))
    # “尚不稳定”里的“稳定”不是结论词
    assert "new_conclusion" not in codes(page("尚不稳定", "claim-unstable"))


# ---- 新增结论 ----

def test_new_conclusion_blocked():
    assert "new_conclusion" in codes(page("已经掌握", "claim-unstable"))      # 判断说不稳定，衔接语说掌握
    assert "new_conclusion" in codes(page("进步很大", "claim-stable"))
    assert "new_conclusion" in codes(page("薄弱", ))                          # 同组没有判断
    assert "new_conclusion" in codes(page("表现优秀", "claim-progress"))


def test_conclusion_backed_by_claim_passes_that_rule():
    assert "new_conclusion" not in codes(page("已经掌握", "claim-stable"))
    assert "new_conclusion" not in codes(page("有进步", "claim-progress"))


# ---- 数字 ----

@pytest.mark.parametrize("conn", ["对了3道", "正确率８０％", "做对了五道题", "80%"])
def test_numbers_in_connective_blocked(conn):
    assert "number_in_connective" in codes(page(conn, "claim-stable"))


def test_no_number_passes_that_rule():
    assert "number_in_connective" not in codes(page("接下来", "claim-stable"))
    # 陈述里的数值来自判断模板渲染，不查
    assert "number_in_connective" not in codes(page("接下来", "claim-stable", text="合成渲染：3 道"))


# ---- 禁用推断（SIE 词表）----

def test_forbidden_inference_blocked_in_connective_and_text():
    assert "forbidden_inference" in codes(page("你很聪明", "claim-stable"))
    assert "forbidden_inference" in codes(page("接下来", "claim-stable", text="孩子有点懒惰"))


def test_learning_fact_passes_forbidden_rule():
    assert "forbidden_inference" not in codes(page("接下来", "claim-stable", text="二次函数图像这一节还需要练习"))


# ---- 角色语言 ----

@pytest.mark.parametrize("name", ["点点", "阿鸦", "妙笔"])
def test_persona_language_blocked(name):
    assert "persona_language" in codes(page(f"{name}说接下来", "claim-stable"))


def test_no_persona_passes_that_rule():
    assert "persona_language" not in codes(page("接下来", "claim-stable"))


# ---- 引用 ----

def test_statement_must_cite_visible_claim():
    assert "no_claim" in codes(page("", None))
    assert "claim_unreadable" in codes(page("", "claim-missing"))
    assert "claim_unreadable" in codes(page("", "claim-hidden"))
    assert "claim_status" in codes(page("", "claim-verify"))       # 待验证只作 Agent 待办，不作陈述
    assert "claim_status" in codes(page("", "claim-refuted"))


def test_unapproved_connective_blocked():
    assert "connective_unapproved" in codes(page("然后呢我们看看", "claim-stable"))
    assert "connective_unapproved" not in codes(page("接下来", "claim-stable"))


# ---- 阻断后的降级 ----

def test_blocked_page_falls_back_to_template_only():
    sections = [Section("working_on", "薄弱", (st("claim-unstable"), st("claim-verify"), st(None))),
                Section("next", "下一步", (st("claim-stable"),))]
    out = finalize(sections, CLAIMS, CFG, FORBID)
    assert out.check_status == "blocked_fallback"
    assert all(s.connective == "" for s in out.sections)
    assert [[x.claim_id for x in s.statements] for s in out.sections] == [["claim-unstable"], ["claim-stable"]]
    assert {v.code for v in out.violations} >= {"degree_escalation", "claim_status", "no_claim"}
    esc = next(v for v in out.violations if v.code == "degree_escalation")
    assert esc.claim_ids == ("claim-unstable",) and esc.fragment == "薄弱"
    # 降级后的纯模板版本自身必须通过检查
    assert check_page(out.sections, CLAIMS, CFG, FORBID).passed


# ---- 配置 ----

def test_check_config_rules():
    with open(CONFIG_DIR / "profile-check.synthetic.json", encoding="utf-8") as f:
        doc = json.load(f)
    assert "合成占位" in doc["$comment"] and "#20" in doc["$comment"]
    for env in ("dev", "pilot", "prod"):
        with pytest.raises(ConfigError):
            parse_check_config(doc, env=env)
    bad = dict(doc, connective_phrases=doc["connective_phrases"] + ["接下来很薄弱"])
    with pytest.raises(ConfigError):
        parse_check_config(bad, env="test")
    bad = dict(doc, connective_phrases=doc["connective_phrases"] + ["第3节"])
    with pytest.raises(ConfigError):
        parse_check_config(bad, env="test")
    bad = dict(doc, template_conclusions={"syn.mastery.stable": ["天才"]})
    with pytest.raises(ConfigError):
        parse_check_config(bad, env="test")
    bad = dict(doc, extra=1)
    with pytest.raises(ConfigError):
        parse_check_config(bad, env="test")


def test_engine_does_not_call_models():
    import learning.mastery.consistency as mod
    src = open(mod.__file__, encoding="utf-8").read()
    assert "model_router" not in src and "httpx" not in src
