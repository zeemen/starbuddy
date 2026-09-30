"""判断策略配置：合成占位只允许 test 环境；未签字 → 最高只到假设；形状与方向校验；占位文字过禁用推断词表。"""
from __future__ import annotations

import copy

import pytest

from insight.policy import PolicyError, load_check_config, parse_policy, synthetic_check_config, synthetic_policy, CONFIG_DIR
from support import make_policy, policy_doc


def test_synthetic_policy_loads_only_in_test_and_is_unsigned():
    p = synthetic_policy()
    assert p.synthetic and not p.signed and p.visible_subjects == ("aya",)
    with pytest.raises(PolicyError):
        parse_policy(policy_doc(), env="dev")
    with pytest.raises(PolicyError):
        load_check_config(CONFIG_DIR / "report-check.synthetic.json", env="dev")
    assert synthetic_check_config().connective_phrases


def test_signed_requires_both_fields_and_not_synthetic():
    doc = policy_doc()
    doc.update(synthetic=False, signed_by="someone", signed_at="")
    assert not parse_policy(doc, env="dev").signed
    doc.update(signed_at="2026-09-30")
    assert parse_policy(doc, env="dev").signed
    assert make_policy(signed=True).signed


@pytest.mark.parametrize("path, value", [
    (("establish", "min_gate"), "unseen"),
    (("establish", "confidence_min"), 1.5),
    (("establish", "evidence_min"), 0),
    (("ttl_days", "node_mastery"), 400),
    (("confidence_bands", "moderate_min"), 0.9),
    (("templates", "metric", "key"), "Bad Key"),
    (("templates", "metric", "key"), "syn.math.node_mastery"),
    (("metrics", "probe_probability", "unsure"), 1.2),
    (("metrics", "hint_ladder_length", "aya"), 0),
    (("metrics", "persistence", "recover_days"), 1),
    (("mastery_gates",), ["unseen"]),
    (("visible_subjects",), ["math"]),
    (("report", "notice"), "【占位】孩子智商很高"),
    (("report", "connectives", "node_mastery"), "【占位】家庭条件一般"),
])
def test_invalid_values_rejected(path, value):
    doc = copy.deepcopy(policy_doc())
    cur = doc
    for k in path[:-1]:
        cur = cur[k]
    cur[path[-1]] = value
    with pytest.raises(PolicyError):
        parse_policy(doc, env="test")


def test_unknown_or_missing_keys_rejected():
    doc = policy_doc()
    doc["extra"] = 1
    with pytest.raises(PolicyError):
        parse_policy(doc, env="test")
    doc = policy_doc()
    del doc["metrics"]["window_days"]
    with pytest.raises(PolicyError):
        parse_policy(doc, env="test")


def test_confidence_bands_and_category_lookup():
    p = synthetic_policy()
    assert [p.confidence_band(x) for x in (0.1, 0.4, 0.74, 0.75, 1.0)] == ["few_evidence", "moderate", "moderate", "strong", "strong"]
    assert p.category_of("syn.math.node_transfer") == "node_transfer" and p.category_of("nope.x") is None
    assert p.fingerprint == synthetic_policy().fingerprint
