"""KGE-11 范围 3、4：风险分流建议纯函数（开关缺省关；关时与 review_level 逐字节相同；开时报告须绑定被审页面、版本与地图版本）、
建议级别与合并窗口 / 对账所需人数的映射说明（尚未生效），以及“叙述段改写”判级口径的现状。
不连库；体检报告是合成的 kge-lint-report/1.1 字典（规则 8）。"""
from __future__ import annotations

import ast
import copy
import inspect
import json
import re
from pathlib import Path

import pytest

from review_console import pages as review_pages
from review_console import risk
from review_console.risk import (LEVELS, SEMANTIC_TRIAGE_DEFAULT, SPOT_CHECK, SUGGESTED_REVIEWERS, Risk, classify_page, lint_blocks,
                                 review_level, suggest_review_level)

ROOT = Path(__file__).resolve().parents[3]
PAGE, VER, MAP = "MATH.SYN.EQ.LINEAR", "1.2.0", "0.9.1"
BIND = {"page_id": PAGE, "page_version": VER, "map_version": MAP}


def report(layer: str, /, *severities: str, pages: int = 1, rules: tuple[str, ...] = (), **over) -> dict:
    issues = [{"id": f"li-{k:016x}", "page_id": PAGE, "page_version": VER, "layer": layer, "check_type": "x", "severity": s,
               "detail": {"rule": rules[k] if k < len(rules) else "x.y"}, "status": "open"} for k, s in enumerate(severities)]
    blocks = sum(1 for s in severities if s == "block")
    config = {"skipped_checks": []}
    if layer == "semantic":
        config.update({"levels_default": True, "levels_overrides": {},
                       "synthetic_inputs": {"scope_vocab": False, "style_lexicon": False, "terms": None}})
    doc = {"format": "kge-lint-report/1.1", "layer": layer, "map_version": MAP,
           "scope": {"pages": [{"page_id": "MATH.SYN.EQ.APPLY", "page_version": "1.0.0", "sha256": "0" * 64},
                               {"page_id": PAGE, "page_version": VER, "sha256": "1" * 64}]},
           "inputs": {"map_snapshot": "a" * 64}, "config": config,
           "summary": {"pages": pages, "blocks": blocks, "warnings": len(severities) - blocks, "by_check": {}}, "issues": issues}
    for path, value in over.items():  # 键形如 config__levels_default：改嵌套字段
        node = doc
        *head, last = path.split("__")
        for h in head:
            node = node[h]
        node[last] = value
    return doc


CLEAN_P, CLEAN_S = report("program"), report("semantic")
ALL_LEVELS = (*LEVELS, "", "unknown", "MEDIUM")
REPORT_CASES = [(None, None), (CLEAN_P, CLEAN_S), (report("program", "block"), CLEAN_S), ({"bogus": 1}, "x")]


# ---------- 开关关：行为不变 ----------

def test_switch_is_off_by_default():
    assert SEMANTIC_TRIAGE_DEFAULT is False
    assert inspect.signature(suggest_review_level).parameters["enabled"].default is False


@pytest.mark.parametrize("level", ALL_LEVELS)
@pytest.mark.parametrize("reports", REPORT_CASES)
@pytest.mark.parametrize("structural", [False, True])
def test_off_equals_review_level(level, reports, structural):
    p, s = reports
    assert suggest_review_level(level, p, s, **BIND, structural=structural) == review_level(level)
    assert suggest_review_level(level, p, s, enabled=False) == review_level(level)
    assert suggest_review_level(level, p, s, **BIND, enabled=1) == review_level(level)  # 只有 True 才算开


def test_review_level_itself_is_unchanged_byte_for_byte():
    """review_level 的源码与输出与 KGE-11 之前逐字节相同；Risk.as_dict 的序列化也不变"""
    assert inspect.getsource(review_level) == 'def review_level(level: str) -> str:\n    return "high" if level == "medium" else level\n'
    assert {lv: review_level(lv) for lv in ALL_LEVELS} == {"low": "low", "medium": "high", "high": "high", "": "", "unknown": "unknown",
                                                          "MEDIUM": "MEDIUM"}
    got = json.dumps([Risk(lv, "narrative_rewrite").as_dict() for lv in LEVELS], sort_keys=True)
    assert got == ('[{"level": "low", "reason": "narrative_rewrite", "review_level": "low", "structural": false}, '
                   '{"level": "medium", "reason": "narrative_rewrite", "review_level": "high", "structural": false}, '
                   '{"level": "high", "reason": "narrative_rewrite", "review_level": "high", "structural": false}]')
    assert "suggest_review_level" not in inspect.getsource(Risk) and SPOT_CHECK not in inspect.getsource(risk.summarize)


# ---------- 开关开：按规则 ----------

@pytest.mark.parametrize("level,p,s,want", [
    ("medium", CLEAN_P, CLEAN_S, SPOT_CHECK),
    ("medium", report("program", "warn"), report("semantic", "warn", "warn"), SPOT_CHECK),  # 提示不挡
    ("medium", report("program", "block"), CLEAN_S, "high"),
    ("medium", CLEAN_P, report("semantic", "warn", "block"), "high"),
    ("medium", None, CLEAN_S, "high"),                       # 少一层报告
    ("medium", CLEAN_P, None, "high"),
    ("medium", CLEAN_S, CLEAN_P, "high"),                    # 两份报告放反（顶层 layer 不对）
    ("medium", report("semantic", "warn", layer="program"), CLEAN_S, "high"),  # 条目层与顶层不一致
    ("medium", CLEAN_P, report("semantic", pages=0), "high"),  # 没覆盖任何页面
    ("medium", CLEAN_P, report("semantic", "block", summary__blocks=0, summary__warnings=1), "high"),  # 摘要与条目对不上
    ("medium", CLEAN_P, report("semantic", summary__blocks=1), "high"),  # 摘要说有阻断、条目被截掉
    ("medium", CLEAN_P, report("semantic", summary__warnings=1), "high"),
    ("medium", CLEAN_P, report("semantic", format="kge-lint-report/1"), "high"),  # 旧格式没有绑定字段
    ("medium", CLEAN_P, report("semantic", pages=True), "high"),
    ("medium", CLEAN_P, report("semantic", "fatal"), "high"),
    ("medium", CLEAN_P, report("semantic", map_version="0.9.0"), "high"),  # 地图版本对不上
    ("medium", report("program", map_version="0.9.0"), CLEAN_S, "high"),
    ("medium", CLEAN_P, report("semantic", inputs__map_snapshot="b" * 64), "high"),  # 版本号相同、快照内容不同
    ("medium", report("program", inputs__map_snapshot=None), report("semantic", inputs__map_snapshot=None), "high"),
    ("medium", CLEAN_P, report("semantic", inputs="x"), "high"),
    ("medium", CLEAN_P, report("semantic", scope__pages=[{"page_id": PAGE, "page_version": "1.1.0"}]), "high"),  # 不是被审版本
    ("medium", report("program", scope__pages=[]), CLEAN_S, "high"),  # 没查被审页面
    ("medium", CLEAN_P, report("semantic", scope={}), "high"),
    ("medium", report("program", config__skipped_checks=["template"]), CLEAN_S, "high"),  # 程序体检没给快照或基线
    ("medium", CLEAN_P, report("semantic", config__skipped_checks=["out_of_scope"]), "high"),
    ("medium", CLEAN_P, report("semantic", config={"levels_default": True, "levels_overrides": {},
                                                   "synthetic_inputs": {"scope_vocab": False, "style_lexicon": False}}), "high"),
    ("medium", CLEAN_P, report("semantic", config__levels_default=False), "high"),  # 分级不是缺省
    ("medium", CLEAN_P, report("semantic", config__levels_overrides={"term_consistency.variant": "warn"}), "high"),
    ("medium", CLEAN_P, report("semantic", config__synthetic_inputs={"scope_vocab": True, "style_lexicon": False, "terms": None}), "high"),
    ("medium", CLEAN_P, report("semantic", config__synthetic_inputs={"scope_vocab": False, "style_lexicon": False, "terms": True}), "high"),
    ("medium", CLEAN_P, report("semantic", config__synthetic_inputs={"scope_vocab": None, "style_lexicon": False, "terms": None}), "high"),
    ("medium", CLEAN_P, report("semantic", config__synthetic_inputs="none"), "high"),
    ("medium", CLEAN_P, report("semantic", "warn", rules=("out_of_scope.no_vocab",)), "high"),  # 词表缺失
    ("medium", CLEAN_P, report("semantic", "warn", rules=("age_style.no_lexicon",)), "high"),
    ("medium", CLEAN_P, report("semantic", issues="x"), "high"),
    ("medium", CLEAN_P, report("semantic", summary="x"), "high"),
    ("medium", CLEAN_P, report("semantic", config="x"), "high"),
    ("medium", CLEAN_P, ["not a dict"], "high"),
    ("low", CLEAN_P, CLEAN_S, "low"),
    ("low", report("program", "block"), None, "low"),        # 低风险不归本函数管：与 review_level 相同
    ("high", CLEAN_P, CLEAN_S, "high"),
    ("unknown", CLEAN_P, CLEAN_S, "high"),
    ("", CLEAN_P, CLEAN_S, "high"),
])
def test_on_rules(level, p, s, want):
    assert suggest_review_level(level, p, s, **BIND, enabled=True) == want


@pytest.mark.parametrize("structural", [True, None, "yes", 1])
def test_structural_is_always_high(structural):
    for level in LEVELS:
        assert suggest_review_level(level, CLEAN_P, CLEAN_S, **BIND, structural=structural, enabled=True) == "high"


@pytest.mark.parametrize("bind", [
    {}, {**BIND, "page_id": None}, {**BIND, "page_version": ""}, {**BIND, "map_version": None}, {**BIND, "page_id": 7},
    {**BIND, "page_id": "MATH.SYN.EQ.APPLY"},  # 报告里有这一页，但版本不是被审版本
])
def test_binding_arguments_are_required(bind):
    assert suggest_review_level("medium", CLEAN_P, CLEAN_S, **bind, enabled=True) == "high"


def test_lint_blocks_counts_only_blocks():
    assert lint_blocks(report("semantic", "warn", "block", "block"), "semantic", **BIND) == 2
    assert lint_blocks(report("semantic"), "program", **BIND) is None  # 顶层 layer 不对
    assert lint_blocks(report("program", "block"), "semantic", **BIND) is None
    assert lint_blocks(CLEAN_S, "semantic") is None  # 没给被审页面与版本


def test_triage_is_pure_and_does_not_mutate_reports():
    p, s = report("program", "warn"), report("semantic")
    before = json.dumps([p, s], sort_keys=True)
    for _ in range(2):
        assert suggest_review_level("medium", copy.deepcopy(p), s, **BIND, enabled=True) == SPOT_CHECK
    assert json.dumps([p, s], sort_keys=True) == before


def test_report_format_constant_matches_knowledge():
    src = (ROOT / "services/knowledge/knowledge/lint/issues.py").read_text(encoding="utf-8")
    assert f'REPORT_FORMAT = "{risk.LINT_REPORT_FORMAT}"' in src
    for rule in risk.SEMANTIC_SKIP_RULES:
        assert f'"{rule}"' in (ROOT / "services/knowledge/knowledge/semantic/config.py").read_text(encoding="utf-8")


# ---------- 建议级别与所需人数（映射说明，尚未生效） ----------

def _window_reviewers() -> dict[str, int]:
    src = (ROOT / "services/knowledge/knowledge/compiler/window.py").read_text(encoding="utf-8")
    [node] = [n for n in ast.parse(src).body if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") == "REVIEWERS"]
    return ast.literal_eval(node.value)


def test_suggested_reviewers_mapping_is_documented_and_not_in_effect():
    window = _window_reviewers()
    assert set(SUGGESTED_REVIEWERS) == {"low", SPOT_CHECK, "high"} and SPOT_CHECK == "medium_spot_check"
    for lv in ("low", "high"):
        assert SUGGESTED_REVIEWERS[lv] == window[lv] == review_pages.REQUIRED[lv]
    # 尚未生效：合并窗口与对账里没有 medium_spot_check，中风险仍按高风险的人数
    assert SPOT_CHECK not in window and SPOT_CHECK not in review_pages.REQUIRED
    assert window["medium"] == review_pages.REQUIRED["medium"] == SUGGESTED_REVIEWERS["high"]
    src = (ROOT / "services/knowledge/knowledge/compiler/window.py").read_text(encoding="utf-8")
    assert "尚未生效" in src and "medium_spot_check" in src
    assert "尚未生效" in inspect.getsource(risk)


# ---------- 口径现状（spec 范围 4：只记录、不改行为，统一与否待人确认） ----------

def test_review_console_classifies_narrative_rewrite_as_medium_reviewed_as_high():
    for sec in ("key_points", "pitfalls", "frequent_questions"):
        r = classify_page({"source": "compiler", "sections": [sec]})
        assert (r.level, r.reason, r.as_dict()["review_level"]) == ("medium", "narrative_rewrite", "high")


def test_compiler_still_classifies_narrative_rewrite_as_high():
    """编译器 rebuild.classify 的叙述段分支判高（services/knowledge/tests/test_semantic.py 也在知识服务一侧固定）"""
    src = (ROOT / "services/knowledge/knowledge/compiler/rebuild.py").read_text(encoding="utf-8")
    branch = re.search(r'elif spec\.kind == "narrative":\n(.*?)\n        else:', src, re.S)
    assert branch is not None
    assert branch.group(1) == '            high = True\n            reasons.append(f"叙述段 {spec.id} 改写（高风险）")'
