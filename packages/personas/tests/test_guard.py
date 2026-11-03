"""禁语分类器（spec 设计要点 2、验收第 3、4 条）：种子正例 100% 命中、反例 0 误报；同一句话按角色给出不同结论；
结构上下文层三种情况各一正一反；模型层只登记不执行；词表加载 fail closed；旧位置镜像与新位置一致；命令行输出 JSON。"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import PKG_PARENT, ROOT
from personas import PersonaConfigError
from personas.guard import applicable_rules, classify, classify_detail, export_kge, load_context
from personas.lexicon import CONTEXT_CHECKS, load_lexicon
from personas.registry import legacy_mirror_problems, load_registry

CTX = load_context(ROOT)
# 每个禁语类在哪个登记角色下测（该角色的 forbidden_classes 或类型覆盖这一类）
PERSONA_FOR_CLASS = {"A": "diandian", "B": "diandian", "C": "diandian", "D": "diandian", "E": "diandian",
                     "F:math": "aya", "F:chinese": "miaobi", "G": "fangyuan", "platform": "chunqiu"}


def _example(x):
    return (x, None) if isinstance(x, str) else (x["text"], x.get("context"))


def _ids(hits):
    return [h["rule_id"] for h in hits]


def test_every_class_has_rules_and_personas_cover_them():
    classes = {r.cls for r in CTX.lexicon.rules}
    assert classes == set(PERSONA_FOR_CLASS)
    for cls, pid in PERSONA_FOR_CLASS.items():
        assert any(r.cls == cls for r in applicable_rules(CTX, pid)), (cls, pid)


@pytest.mark.parametrize("rule", [r for r in CTX.lexicon.rules if r.method != "model"], ids=lambda r: r.rule_id)
def test_seed_examples_positive_hit_negative_clean(rule):
    """正例：该条规则必须命中（召回 100%）；反例：同一类的任何规则都不得命中（0 误报）"""
    pid = PERSONA_FOR_CLASS[rule.cls]
    assert rule.examples_positive and rule.examples_negative
    for ex in rule.examples_positive:
        text, ctx = _example(ex)
        assert rule.rule_id in _ids(classify(pid, text, ctx, ctx=CTX)), (rule.rule_id, text)
    for ex in rule.examples_negative:
        text, ctx = _example(ex)
        same_class = [h for h in classify(pid, text, ctx, ctx=CTX) if h["class"] == rule.cls]
        assert same_class == [], (rule.rule_id, text, same_class)


def test_same_sentence_differs_by_persona():
    """“差一点”只对考官型是 G 类：对方圆命中、对点点与阿鸦不命中"""
    assert _ids(classify("fangyuan", "差一点。", ctx=CTX)) == ["G-01"]
    assert classify("diandian", "差一点。", ctx=CTX) == []
    assert classify("aya", "差一点。", ctx=CTX) == []
    # “粗心”：阿鸦是 F:math 与平台级；点点是 E 类的“你就是粗心”才算；考官型只有平台级
    assert set(_ids(classify("aya", "粗心了", ctx=CTX))) == {"F-aya-01", "PLAT-01"}
    assert _ids(classify("diandian", "粗心了", ctx=CTX)) == []
    assert _ids(classify("chunqiu", "粗心了", ctx=CTX)) == ["PLAT-01"]


def test_hit_fields():
    [h] = classify("fangyuan", "合成：可惜。", ctx=CTX)
    assert h == {"class": "G", "rule_id": "G-02", "span": [3, 5], "method": "rule", "confidence": 1.0,
                 "lexicon_version": CTX.lexicon.version}


# ---------- 结构上下文层：三种情况各一正一反（验收第 4 条）----------
@pytest.mark.parametrize("persona,text,pos,neg,rule", [
    ("aya", "【合成】先看下一步的提示", {"allowed_hint_level": 2, "hint_level_used": 3}, {"allowed_hint_level": 2, "hint_level_used": 2},
     "F-aya-CTX-01"),
    ("aya", "【合成】这一步是对的", {"verified_claims": []}, {"verified_claims": [{"claim_id": "syn-1", "verified": True}]}, "F-aya-CTX-02"),
    ("fangyuan", "对采分点。", {"grading_moment": False}, {"grading_moment": True}, "G-CTX-01"),
])
def test_context_layer_positive_and_negative(persona, text, pos, neg, rule):
    hit = [h for h in classify(persona, text, pos, ctx=CTX) if h["rule_id"] == rule]
    assert hit and hit[0]["method"] == "context"
    assert rule not in _ids(classify(persona, text, neg, ctx=CTX))
    assert rule not in _ids(classify(persona, text, None, ctx=CTX)), "没有结构上下文时不判定结构层"


def test_unverified_claim_with_unverified_entry_still_hits():
    ctx = {"verified_claims": [{"claim_id": "syn-1", "verified": False}]}
    assert "PLAT-CTX-01" in _ids(classify("chunqiu", "第 1 点：命中。", ctx, ctx=CTX))


def test_star_outside_two_places():
    assert "PLAT-CTX-02" in _ids(classify("aya", "【合成】用星星讲函数", {"moment": "lesson"}, ctx=CTX))
    assert "PLAT-CTX-02" not in _ids(classify("aya", "【合成】这颗亮了", {"moment": "threshold"}, ctx=CTX))
    assert "PLAT-CTX-02" not in _ids(classify("aya", "【合成】星期三见", {"moment": "lesson"}, ctx=CTX))


def test_full_generation_by_length():
    long_para = "【合成】" + "这是一段连续生成的正文" * 20
    assert "F-wenli-CTX-03" in _ids(classify("miaobi", long_para, {}, ctx=CTX))
    assert "F-wenli-CTX-03" not in _ids(classify("miaobi", "这段你写。", {}, ctx=CTX))


def test_model_layer_is_listed_not_executed():
    d = classify_detail("miaobi", "【合成】语言再优美些", ctx=CTX)
    assert {"F-wenli-M-01", "F-wenli-M-02", "F-wenli-M-03"} <= set(d["pending_model_rules"])
    assert all(h["method"] in ("rule", "context") for h in d["hits"])


def test_platform_surface_only_a_to_e():
    assert _ids(classify("platform", "你应该先读题", ctx=CTX)) == ["A-01"]
    assert classify("platform", "差一点。粗心了", ctx=CTX) == []
    with pytest.raises(PersonaConfigError):
        classify("nobody", "x", ctx=CTX)


def test_examiner_sentence_rules():
    long = "第 2 点，国家尊重和保障人权以及公民依法享有的基本权利和应当履行的义务：命中。"
    assert "G-S-01" in _ids(classify("athena", long, ctx=CTX))
    assert "G-S-02" in _ids(classify("athena", "你确定吗？", ctx=CTX))
    assert classify("athena", "第 2 点：命中。依据：评分项。", ctx=CTX) == []


# ---------- 词表：加载 fail closed、版本、镜像 ----------
def _copy_lexicon(tmp: Path) -> Path:
    shutil.copytree(PKG_PARENT / "personas", tmp / "packages" / "personas", ignore=shutil.ignore_patterns("__pycache__", "tests"))
    return tmp


@pytest.mark.parametrize("rel,old,new,msg", [
    ("forbidden/manifest.yaml", "G.yaml, ", "", None),
    ("forbidden/G.yaml", "method: context, check: off_moment", "method: context, check: not_implemented", "check"),
    ("forbidden/G.yaml", 'examples_positive: ["差一点。"], examples_negative: ["第 2 点：部分命中。"] ', "", "正例与反例"),
    ("forbidden/A.yaml", 'pattern: "你应该"', 'pattern: "(("', "正则"),
    ("forbidden/A.yaml", "rule_id: A-02,", "rule_id: A-01,", "重复"),
    ("forbidden/E.yaml", "origin: verbatim, styles: all, method: rule, pattern: \"你总是\"", "origin: guess, styles: all, method: rule, pattern: \"你总是\"", "origin"),
])
def test_lexicon_fails_closed(tmp_path, rel, old, new, msg):
    root = _copy_lexicon(tmp_path)
    load_lexicon(root)
    p = root / "packages" / "personas" / rel
    text = p.read_text(encoding="utf-8")
    assert old in text, old
    p.write_text(text.replace(old, new, 1), encoding="utf-8")
    if msg is None:  # 整类缺失：加载仍然合法，但登记表引用了不存在的类
        lex = load_lexicon(root)
        assert "G" not in {r.cls for r in lex.rules}
        from personas.registry import check  # noqa: PLC0415
        assert any("forbidden_classes 里的 G 不是已知的禁语类" in e for e in check(root)["errors"])
        return
    with pytest.raises(PersonaConfigError, match=msg):
        load_lexicon(root)


def test_all_context_checks_are_used_and_tested():
    used = {r.check for r in CTX.lexicon.rules if r.method == "context"}
    assert used == set(CONTEXT_CHECKS)


def test_lexicon_versions_and_review_flags():
    lex = CTX.lexicon
    assert lex.version == "0.3.0" and lex.reviewed is False
    assert {lex.class_versions[c] for c in "ABCDE"} == {"0.2.0"}, "A–E 类原样迁入，版本不改"
    assert all(r.source and r.origin in ("verbatim", "inferred") for r in lex.rules)
    # F、G 与平台级种子只从规格书逐条摘录：词面条目全部 verbatim + literal
    for r in lex.rules:
        if r.cls in ("F:math", "F:chinese", "G", "platform") and r.method == "rule" and r.kind is None:
            assert r.origin == "verbatim" and r.literal, r.rule_id


def test_legacy_mirror_in_sync_and_mismatch_detected(tmp_path):
    reg = load_registry(ROOT)
    assert legacy_mirror_problems(ROOT, reg) == []
    root = _copy_lexicon(tmp_path)
    mirror = root / "evals" / "persona" / "diandian" / "lexicon" / "forbidden.yaml"
    mirror.parent.mkdir(parents=True)
    shutil.copy(ROOT / "evals" / "persona" / "diandian" / "lexicon" / "forbidden.yaml", mirror)
    shutil.copy(ROOT / "packages" / "personas" / "registry.yaml", root / "packages" / "personas" / "registry.yaml")
    assert legacy_mirror_problems(root, reg) == []
    mirror.write_text(mirror.read_text(encoding="utf-8").replace('regex: "你必须"', 'regex: "你一定要"'), encoding="utf-8")
    assert any("不一致" in e for e in legacy_mirror_problems(root, reg))


def test_export_kge_scopes_f_classes_by_subject():
    exp = export_kge(CTX)
    f = {x["id"]: x for x in exp["forbidden"] if x["cls"] == "F"}
    assert f["F-aya-04"]["subjects"] == ["aya"] and f["F-wenli-05"]["subjects"] == ["wenli"]
    assert all(x["subjects"] == [] for x in exp["forbidden"] if x["cls"] in "ABCDEG")
    assert not any(x["id"].startswith("PLAT") or "-CTX-" in x["id"] or "-M-" in x["id"] or "-S-" in x["id"] for x in exp["forbidden"])
    assert {"阿鸦", "点点", "妙笔", "方圆", "春秋", "雅典娜", "考官型老师"} == set(exp["role_names"])
    assert "阿基" not in exp["role_names"], "未立项的角色不导出"


# ---------- 命令行（前端文案单测复用 guard 的入口，lessons“待前端接入”）----------
def _cli(*args, stdin: str | None = None):
    import os  # noqa: PLC0415
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "PYTHONPATH": str(PKG_PARENT), "PYTHONDONTWRITEBYTECODE": "1"}
    return subprocess.run([sys.executable, "-m", "personas.guard", *args], capture_output=True, text=True, input=stdin, env=env, cwd=ROOT)


def test_cli_scan_outputs_json_and_exit_code():
    r = _cli("scan", "--persona", "platform", "--text", "你应该先读题", "--text", "这道题你觉得能做对吗？")
    assert r.returncode == 1
    out = json.loads(r.stdout)
    assert out["lexicon_version"] == CTX.lexicon.version and out["persona_id"] == "platform"
    assert [x["hits"][0]["rule_id"] for x in out["results"] if x["hits"]] == ["A-01"]
    assert "你应该先读题" not in r.stdout, "输出不回显原文"
    r = _cli("scan", "--persona", "platform", "--stdin-json", stdin=json.dumps(["不确定", "能"]))
    assert r.returncode == 0 and all(not x["hits"] for x in json.loads(r.stdout)["results"])
    assert _cli("scan", "--persona", "nobody", "--text", "x").returncode == 2


def test_cli_rules_and_export():
    r = _cli("rules", "--persona", "fangyuan")
    rows = json.loads(r.stdout)
    assert {"G-01", "PLAT-01", "A-01"} <= {x["rule_id"] for x in rows} and "F-aya-01" not in {x["rule_id"] for x in rows}
    r = _cli("export-kge")
    assert r.returncode == 0 and json.loads(r.stdout)["lexicon_version"] == CTX.lexicon.version
