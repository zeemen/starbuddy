"""KGE-11 规则式语义体检：每类检查的正反例（合成页面与原子）、比对范围限制、报告格式与绑定字段（kge-lint-report/1.1）、
分级配置、输入词表校验、读取方兼容（M1、页面仓库 CI）、编译器对叙述段改写判级的现状。全部是合成数据（规则 8）。"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import APPLY, FIXTURES, LINEAR, PAGES, ROOT, SQUARE, TRANSPOSE, edit, map_doc, node_rec, page_file
from knowledge.atoms import parse_snapshot
from knowledge.lint.checks import load_files
from knowledge.lint.issues import CHECK_TYPES, PROGRAM_CHECK_TYPES, REPORT_FORMAT, SEMANTIC_CHECK_TYPES, LintIssue, LintReport
from knowledge.m1 import M1InputError, build_report
from knowledge.semantic import comparison_scope, lint_semantic_dir, skeleton_neighbors, topic_of
from knowledge.semantic.config import DEFAULT_LEVELS, LevelsError, load_levels
from knowledge.semantic.inputs import InputError, load_scope, load_style, load_terms, parse_scope, parse_style, parse_terms

SEM = FIXTURES / "semantic"
SVC = Path(__file__).resolve().parents[1]
GLOBAL_SYNTHETIC = "out_of_scope.synthetic_vocab"  # 合成超纲词表的提示：每份用合成样例的报告都有这一条（页面 ID 为 *）


def sem_doc() -> dict:
    """语义体检用的合成快照：在 map-v1 上给平方差公式节点加一条带公式的 includes，另加一个第二版次的教材投影"""
    doc = map_doc()
    node_rec(doc, SQUARE)["includes"] = ["两数和与这两数差的积等于它们的平方差", "$(a+b)(a-b) = a^2 - b^2$"]
    doc["textbook_projections"].append({"id": "syn-tp-009", "node_id": LINEAR, "edition": "syn-ed2", "edition_year": 2025,
                                        "volume": "合成新版七上", "chapter": "合成新版第二章", "page_from": 60, "page_to": 61,
                                        "mapping_state": "verified"})
    return doc


@pytest.fixture()
def snap():
    return parse_snapshot(sem_doc())


@pytest.fixture(scope="module")
def inputs():
    return {"scope_vocab": load_scope(SEM / "scope-vocab.json"), "style": load_style(SEM / "style-lexicon.json"),
            "terms": load_terms(SEM / "terms.json")}


@pytest.fixture()
def run(lexicon, inputs, snap):
    def go(pages: Path, snapshot=None, **kw):
        args = {**inputs, **kw}
        return lint_semantic_dir(pages, snapshot=snapshot or snap, lexicon=lexicon, **args)
    return go


def add_para(pages: Path, node: str, text: str, section: str = "key_points") -> None:
    edit(page_file(pages, node), f"<!-- kge:end id={section} -->", f"\n{text}\n<!-- kge:end id={section} -->")


def page_issues(result) -> list[LintIssue]:
    """页面上的条目（去掉页面 ID 为 * 的全局提示）"""
    return [i for i in result.report.issues if i.page_id != "*"]


def rules(result, severity: str | None = None) -> set[str]:
    return {i.detail["rule"] for i in page_issues(result) if severity is None or i.severity == severity}


def global_rules(result) -> set[str]:
    return {i.detail["rule"] for i in result.report.issues if i.page_id == "*"}


# ---------- 干净样例 ----------

def test_clean_fixture_has_no_semantic_issues(pages_dir, run, lexicon, inputs):
    r = run(pages_dir)
    assert page_issues(r) == [] and global_rules(r) == {GLOBAL_SYNTHETIC} and not r.report.blocked
    # 原样的 map-v1（没有公式原子、只有一个版次）同样零条
    assert page_issues(lint_semantic_dir(pages_dir, snapshot=parse_snapshot(map_doc()), lexicon=lexicon, **inputs)) == []


def test_missing_vocab_and_style_warn_and_skip(pages_dir, run):
    r = run(pages_dir, scope_vocab=None, style=None)
    assert {(i.check_type, i.severity, i.page_id) for i in r.report.issues} == {("out_of_scope", "warn", "*"), ("age_style", "warn", "*")}
    assert r.report.config["skipped_checks"] == ["age_style", "out_of_scope"]
    add_para(pages_dir, SQUARE, "后面还会学合成高阶导数。[@node:MATH.SYN.EXPR.SQUARE_DIFF]")
    add_para(pages_dir, TRANSPOSE, "显然可得移项后的结果。[@node:MATH.SYN.EQ.TRANSPOSE]")
    assert not run(pages_dir, scope_vocab=None, style=None).report.blocked


def test_program_lint_still_clean_on_fixture(pages_dir, snap_v1, run_lint):
    """语义层不改程序体检：程序体检的检查类型与结果照旧"""
    assert run_lint(pages_dir, snap_v1, baseline_dir=PAGES).issues == ()
    assert set(PROGRAM_CHECK_TYPES) & set(SEMANTIC_CHECK_TYPES) == set()


# ---------- 与原子冲突 ----------

def test_formula_conflict_with_atom(pages_dir, run):
    edit(page_file(pages_dir, SQUARE), "(a+b)(a-b) = a^2 - b^2 % check", "(a+b)(a-b) = a^2 + b^2")
    hits = [i for i in page_issues(run(pages_dir)) if i.detail["rule"] == "atom_conflict.formula"]
    assert len(hits) == 1 and hits[0].severity == "block" and hits[0].page_id == SQUARE
    assert hits[0].detail["atom"] == f"node:{SQUARE}" and hits[0].section == "key_points"
    text = page_file(pages_dir, SQUARE).read_text(encoding="utf-8").split("\n")
    assert text[hits[0].line - 1] == "(a+b)(a-b) = a^2 + b^2"


def test_formula_conflict_either_orientation(pages_dir, run):
    add_para(pages_dir, SQUARE, "$$\na^2 + b^2 = (a+b)(a-b)\n$$")
    assert "atom_conflict.formula" in rules(run(pages_dir), "block")


def test_formula_consistent_or_unrelated_is_clean(pages_dir, run):
    add_para(pages_dir, SQUARE, "$$\na^2 - b^2 = (a+b)(a-b)\n$$")
    add_para(pages_dir, SQUARE, "$$\n(b+a)(a-b) = a^2 - b^2\n$$")
    add_para(pages_dir, SQUARE, "$$\n(a+b)^2 = a^2 + 2ab + b^2\n$$")
    assert page_issues(run(pages_dir)) == []


def test_formula_only_against_cited_atoms(pages_dir, run):
    """公式只与页面所引节点原子比：一元一次方程页写同一左边的式子不比（它没引平方差公式节点）"""
    add_para(pages_dir, LINEAR, "$$\n(a+b)(a-b) = a^2 + b^2\n$$")
    assert "atom_conflict.formula" not in rules(run(pages_dir))


@pytest.mark.parametrize("text", [
    "这类题在 syn-region 的权重约 0.08。[@region:syn-rp-001]",
    "这类题的分值占比约 8%。[@region:syn-rp-001]",
    "2024 年中考常考这类题。[@region:syn-rp-001]",
])
def test_region_number_conflict(pages_dir, run, text):
    add_para(pages_dir, APPLY, text)
    r = run(pages_dir)
    assert rules(r) == {"atom_conflict.region_number"} and r.report.blocked
    msg = page_issues(r)[0].message
    assert "region:syn-rp-001" in msg and "0.08" not in msg  # 不回显页面数值


def test_region_number_consistent_is_clean(pages_dir, run):
    add_para(pages_dir, APPLY, "这类题在 syn-region 的权重约 0.05，分值占比约 5%，常见于 2025 年中考。[@region:syn-rp-001]")
    add_para(pages_dir, APPLY, "权重 0.08 只是举例，本段没有引用地区投影。[@node:MATH.SYN.EQ.APPLY]")
    assert page_issues(run(pages_dir)) == []


def test_curriculum_level_conflict(pages_dir, run):
    add_para(pages_dir, LINEAR, "课标要求达到运用层次。[@curriculum:2022/SYN-EQ-01]")
    add_para(pages_dir, TRANSPOSE, "课标要求是【运用】。[@curriculum:2022/SYN-EQ-02]")  # 与条目一致
    r = run(pages_dir)
    assert [(i.page_id, i.detail["rule"]) for i in page_issues(r)] == [(LINEAR, "atom_conflict.curriculum_level")]
    assert "理解" in page_issues(r)[0].message


def test_page_range_conflict(pages_dir, run):
    add_para(pages_dir, LINEAR, "教材第 85 页有例题。[@projection:syn-tp-001]")
    add_para(pages_dir, LINEAR, "教材第 80–82 页是本节。[@projection:syn-tp-001]")
    r = run(pages_dir)
    assert rules(r) == {"atom_conflict.page_range"} and len(page_issues(r)) == 1


def test_excluded_scope_conflict(pages_dir, run):
    add_para(pages_dir, LINEAR, "二元一次方程组也属于一元一次方程。[@node:MATH.SYN.EQ.LINEAR]")
    assert rules(run(pages_dir), "block") == {"atom_conflict.excluded_scope"}


@pytest.mark.parametrize("text", [
    "二元一次方程组不属于一元一次方程。[@node:MATH.SYN.EQ.LINEAR]",
    "二元一次方程组属于下一单元的内容。[@node:MATH.SYN.EQ.LINEAR]",
])
def test_excluded_scope_negated_or_unrelated_is_clean(pages_dir, run, text):
    add_para(pages_dir, LINEAR, text)
    assert page_issues(run(pages_dir)) == []


# ---------- 跨页矛盾：前置方向 ----------

def test_prerequisite_reversed(pages_dir, run):
    add_para(pages_dir, TRANSPOSE, "先学好一元一次方程，再学移项。[@node:MATH.SYN.EQ.TRANSPOSE]")
    hit = [i for i in page_issues(run(pages_dir)) if i.check_type == "prerequisite_direction"]
    assert [(i.detail["rule"], i.severity, i.detail["claimed"]) for i in hit] == [("prerequisite_direction.reversed", "block", [LINEAR, TRANSPOSE])]


@pytest.mark.parametrize("text", [
    "先掌握移项，再学一元一次方程。[@node:MATH.SYN.EQ.LINEAR]",
    "移项是一元一次方程的基础。[@node:MATH.SYN.EQ.LINEAR]",
    "本节以移项为基础。[@node:MATH.SYN.EQ.LINEAR]",
    "学一元一次方程之前要先掌握[[MATH.SYN.EQ.TRANSPOSE]]。[@node:MATH.SYN.EQ.LINEAR]",
])
def test_prerequisite_agrees_with_skeleton(pages_dir, run, text):
    add_para(pages_dir, LINEAR, text)
    assert page_issues(run(pages_dir)) == []


@pytest.mark.parametrize("text", [
    "本节以一元一次方程的应用为基础。[@node:MATH.SYN.EQ.LINEAR]",
    "学[[MATH.SYN.EQ.LINEAR]]之前要先掌握一元一次方程的应用。[@node:MATH.SYN.EQ.LINEAR]",
])
def test_prerequisite_reversed_other_phrasings(pages_dir, run, text):
    add_para(pages_dir, LINEAR, text)
    assert "prerequisite_direction.reversed" in rules(run(pages_dir), "block")


def test_prerequisite_without_edge_warns_and_pages_disagree_blocks(pages_dir, run):
    add_para(pages_dir, SQUARE, "平方差公式是移项的基础。[@node:MATH.SYN.EXPR.SQUARE_DIFF]")
    r = run(pages_dir)
    assert {(i.detail["rule"], i.severity) for i in page_issues(r)} == {("prerequisite_direction.no_edge", "warn")}
    # 移项页与一元一次方程的应用页是同主题相邻页，骨架里两者之间没有边，说法又相反：两边都报阻断
    add_para(pages_dir, TRANSPOSE, "一元一次方程的应用是移项的前置。[@node:MATH.SYN.EQ.TRANSPOSE]")
    add_para(pages_dir, APPLY, "移项是一元一次方程的应用的前置。[@node:MATH.SYN.EQ.APPLY]")
    r = run(pages_dir)
    dis = sorted((i.page_id, i.detail["other_page"]) for i in page_issues(r) if i.detail["rule"] == "prerequisite_direction.pages_disagree")
    assert dis == [(APPLY, TRANSPOSE), (TRANSPOSE, APPLY)] and r.report.blocked


# ---------- 跨页矛盾：相邻页术语写法 ----------

def test_term_variant_between_adjacent_pages(pages_dir, run):
    edit(page_file(pages_dir, LINEAR), "移项时漏改符号", "移项法则使用时漏改符号")
    hits = [i for i in page_issues(run(pages_dir)) if i.check_type == "term_consistency"]
    assert [(i.page_id, i.severity, i.detail["node"], i.detail["forms"], i.detail["other_page"]) for i in hits] == \
        [(LINEAR, "warn", TRANSPOSE, ["移项法则"], TRANSPOSE)]


def test_term_same_alias_on_both_pages_is_clean(pages_dir, run):
    edit(page_file(pages_dir, LINEAR), "移项时漏改符号", "移项法则使用时漏改符号")
    add_para(pages_dir, TRANSPOSE, "移项法则要和等式的性质一起记。[@node:MATH.SYN.EQ.TRANSPOSE]")
    assert page_issues(run(pages_dir)) == []


def test_longest_match_does_not_confuse_nodes(pages_dir, run):
    """“一元一次方程的应用”按最长匹配算作应用节点，不算一元一次方程节点的另一种写法"""
    add_para(pages_dir, LINEAR, "学完本节再看一元一次方程的应用。[@node:MATH.SYN.EQ.LINEAR]")
    assert page_issues(run(pages_dir)) == []


# ---------- 版本差异误判 ----------

@pytest.mark.parametrize("node,text,rule", [
    (LINEAR, "教材第 81 页有例题。[@node:MATH.SYN.EQ.LINEAR]", "edition_scope.unmarked"),
    (TRANSPOSE, "合成七上的例题可以先做。[@node:MATH.SYN.EQ.TRANSPOSE]", "edition_scope.unmarked"),
    (APPLY, "合成旧版专栏里有类似的题。[@node:MATH.SYN.EQ.APPLY]", "edition_scope.unmarked"),
    (LINEAR, "合成新版七上另有一种写法，见第 81 页。[@projection:syn-tp-001]", "edition_scope.mismatch"),
])
def test_edition_specific_expression(pages_dir, run, node, text, rule):
    add_para(pages_dir, node, text)
    r = run(pages_dir)
    assert rules(r) == {rule} and r.report.blocked


def test_expression_shared_by_two_editions_is_not_edition_specific(pages_dir, lexicon, inputs):
    doc = sem_doc()
    doc["textbook_projections"][-1]["chapter"] = "合成第三章"  # 第二版次也有同名章：不再是哪一版的专属表述
    add_para(pages_dir, LINEAR, "合成第三章的例题可以先做。[@node:MATH.SYN.EQ.LINEAR]")
    assert page_issues(lint_semantic_dir(pages_dir, snapshot=parse_snapshot(doc), lexicon=lexicon, **inputs)) == []
    assert lint_semantic_dir(pages_dir, snapshot=parse_snapshot(sem_doc()), lexicon=lexicon, **inputs).report.blocked


def test_single_edition_snapshot_does_not_infer_from_projections(pages_dir, lexicon, inputs):
    """快照只有一个版次时，册名、章名不当版次专属表述（只用术语表的 edition_terms）"""
    doc = map_doc()
    for p in doc["textbook_projections"]:
        p["chapter"] = "移项" if p["id"] == "syn-tp-002" else p["chapter"]  # 章名 = 节点名
    add_para(pages_dir, TRANSPOSE, "合成七上的例题可以先做。[@node:MATH.SYN.EQ.TRANSPOSE]")
    r = lint_semantic_dir(pages_dir, snapshot=parse_snapshot(doc), lexicon=lexicon, **inputs)
    assert page_issues(r) == []
    add_para(pages_dir, APPLY, "合成旧版专栏里有类似的题。[@node:MATH.SYN.EQ.APPLY]")  # 术语表登记的仍然算
    r = lint_semantic_dir(pages_dir, snapshot=parse_snapshot(doc), lexicon=lexicon, **inputs)
    assert rules(r) == {"edition_scope.unmarked"} and {i.page_id for i in page_issues(r)} == {APPLY}


def test_chapter_equal_to_node_name_is_not_edition_specific(pages_dir, lexicon, inputs):
    """两个版次时，与节点名称、别名重合的章名也剔除：干净页面（处处写“移项”）零报"""
    doc = sem_doc()
    next(p for p in doc["textbook_projections"] if p["id"] == "syn-tp-002")["chapter"] = "移项"
    doc["textbook_projections"][-1]["chapter"] = "移项法则讲解"  # 与别名“移项法则”重合
    r = lint_semantic_dir(pages_dir, snapshot=parse_snapshot(doc), lexicon=lexicon, **inputs)
    assert page_issues(r) == []
    add_para(pages_dir, LINEAR, "移项法则讲解里有例题。[@node:MATH.SYN.EQ.LINEAR]")
    assert "edition_scope.unmarked" not in rules(lint_semantic_dir(pages_dir, snapshot=parse_snapshot(doc), lexicon=lexicon, **inputs))


def test_edition_marked_paragraph_is_clean(pages_dir, run):
    add_para(pages_dir, LINEAR, "合成七上第 81 页有例题。[@projection:syn-tp-001]")
    add_para(pages_dir, LINEAR, "合成新版七上第 60 页也有。[@projection:syn-tp-009]")
    assert page_issues(run(pages_dir)) == []


# ---------- 超纲 ----------

def test_out_of_scope_term(pages_dir, run):
    add_para(pages_dir, SQUARE, "以后还会学合成高阶导数和合成矩阵。[@node:MATH.SYN.EXPR.SQUARE_DIFF]")
    r = run(pages_dir)
    assert sorted(i.detail["term"] for i in page_issues(r)) == ["合成矩阵", "合成高阶导数"]
    assert all(i.detail["rule"] == "out_of_scope.term" and i.severity == "block" for i in page_issues(r))


def test_out_of_scope_respects_stage_and_node_allow(pages_dir, run):
    add_para(pages_dir, SQUARE, "拓展里会提到合成复数域。[@node:MATH.SYN.EXPR.SQUARE_DIFF]")  # 本节点放行
    add_para(pages_dir, LINEAR, "合成代入法是初中内容。[@node:MATH.SYN.EQ.LINEAR]")  # 学段在适用范围内
    assert page_issues(run(pages_dir)) == []
    add_para(pages_dir, LINEAR, "合成复数域不在本节。[@node:MATH.SYN.EQ.LINEAR]")  # 放行只对平方差公式节点
    assert rules(run(pages_dir)) == {"out_of_scope.term"}


def test_synthetic_vocab_is_flagged_in_report(pages_dir, run, inputs, tmp_path):
    r = run(pages_dir)
    assert GLOBAL_SYNTHETIC in global_rules(r) and r.report.config["synthetic_inputs"]["scope_vocab"] is True
    raw = json.loads((SEM / "scope-vocab.json").read_text(encoding="utf-8"))
    raw["synthetic"] = False
    p = tmp_path / "vocab.json"
    p.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    r = run(pages_dir, scope_vocab=load_scope(p))
    assert GLOBAL_SYNTHETIC not in global_rules(r) and r.report.config["synthetic_inputs"]["scope_vocab"] is False


# ---------- 适龄文风 ----------

def test_age_style_rules(pages_dir, run):
    add_para(pages_dir, TRANSPOSE, "显然可得移项后的结果，易证。[@node:MATH.SYN.EQ.TRANSPOSE]")
    r = run(pages_dir)
    assert rules(r) == {"age_style.STY-SYN-OBVIOUS", "age_style.STY-SYN-TRIVIAL"}
    assert all(i.severity == "warn" for i in page_issues(r)) and not r.report.blocked


def test_age_style_ignores_formula_text(pages_dir, lexicon, inputs, snap, tmp_path):
    p = tmp_path / "style.json"
    p.write_text(json.dumps({"format": "kge-semantic-style/1", "synthetic": True,
                             "rules": [{"id": "STY-X", "kind": "literal", "pattern": "a^2", "message": "合成"}]}), encoding="utf-8")
    add_para(pages_dir, SQUARE, "式子 $a^2$ 读作 a 的平方。[@node:MATH.SYN.EXPR.SQUARE_DIFF]")
    r = lint_semantic_dir(pages_dir, snapshot=snap, lexicon=lexicon, scope_vocab=inputs["scope_vocab"], style=load_style(p),
                          terms=inputs["terms"])
    assert page_issues(r) == []


# ---------- 角色语言混入 ----------

@pytest.mark.parametrize("node,text,term", [
    (LINEAR, "这一步鸦 老师也提醒过。[@node:MATH.SYN.EQ.LINEAR]", "鸦老师"),
    (APPLY, "先说角度，再答。[@node:MATH.SYN.EQ.APPLY]", "先说角度再答"),
    (TRANSPOSE, "这里点·点也容易错。[@node:MATH.SYN.EQ.TRANSPOSE]", "点点"),
    (TRANSPOSE, "- 要点一：先移项，点\n- 点出符号。[@node:MATH.SYN.EQ.TRANSPOSE]", "点点"),
])
def test_persona_mix_normalized(pages_dir, run, run_lint, snap_v1, node, text, term):
    add_para(pages_dir, node, text)
    hits = [i for i in page_issues(run(pages_dir)) if i.check_type == "persona_mix"]
    assert term in {i.detail["term"] for i in hits} and all(i.severity == "block" for i in hits)
    assert "persona" not in {i.check_type for i in run_lint(pages_dir, snap_v1).issues}  # 程序层逐行字面比对比不上


def test_persona_mix_does_not_repeat_program_hits(pages_dir, run):
    add_para(pages_dir, LINEAR, "鸦老师提醒过。[@node:MATH.SYN.EQ.LINEAR]")  # 程序层 persona 已阻断
    assert "persona_mix" not in {i.check_type for i in page_issues(run(pages_dir))}


def test_persona_mix_setting_pattern(pages_dir, run):
    add_para(pages_dir, LINEAR, "像规律·碎片一样拼起来。[@node:MATH.SYN.EQ.LINEAR]")
    assert any(i.detail.get("rule_id") == "SET-aya-world" for i in page_issues(run(pages_dir)))


# ---------- 合成样例：全部问题一起出现 ----------

DIRTY = [  # （页面，段落里加的文本，期望规则）
    (APPLY, "这类题在 syn-region 的权重约 0.08。[@region:syn-rp-001]", "atom_conflict.region_number"),
    (LINEAR, "课标要求达到运用层次。[@curriculum:2022/SYN-EQ-01]", "atom_conflict.curriculum_level"),
    (LINEAR, "教材第 85 页有例题。[@projection:syn-tp-001]", "atom_conflict.page_range"),
    (LINEAR, "二元一次方程组也属于一元一次方程。[@node:MATH.SYN.EQ.LINEAR]", "atom_conflict.excluded_scope"),
    (TRANSPOSE, "先学好一元一次方程，再学移项。[@node:MATH.SYN.EQ.TRANSPOSE]", "prerequisite_direction.reversed"),
    (SQUARE, "平方差公式是移项的基础。[@node:MATH.SYN.EXPR.SQUARE_DIFF]", "prerequisite_direction.no_edge"),
    (TRANSPOSE, "合成七上的例题可以先做。[@node:MATH.SYN.EQ.TRANSPOSE]", "edition_scope.unmarked"),
    (LINEAR, "合成新版七上另有一种写法。[@projection:syn-tp-001]", "edition_scope.mismatch"),
    (SQUARE, "以后还会学合成高阶导数。[@node:MATH.SYN.EXPR.SQUARE_DIFF]", "out_of_scope.term"),
    (TRANSPOSE, "显然可得移项后的结果。[@node:MATH.SYN.EQ.TRANSPOSE]", "age_style.STY-SYN-OBVIOUS"),
    (APPLY, "先说角度，再答。[@node:MATH.SYN.EQ.APPLY]", "persona_mix.normalized"),
]
NEAR_MISS = [  # 与上面相似但没有问题的写法：一条都不能报
    (APPLY, "这类题在 syn-region 的权重约 0.05，分值占比约 5%，常见于 2025 年中考。[@region:syn-rp-001]"),
    (LINEAR, "课标要求是理解。[@curriculum:2022/SYN-EQ-01]"),
    (LINEAR, "合成七上第 81 页有例题。[@projection:syn-tp-001]"),
    (LINEAR, "二元一次方程组不属于一元一次方程。[@node:MATH.SYN.EQ.LINEAR]"),
    (TRANSPOSE, "先掌握移项，再学一元一次方程。[@node:MATH.SYN.EQ.TRANSPOSE]"),
    (SQUARE, "拓展里会提到合成复数域，合成代入法则是初中内容。[@node:MATH.SYN.EXPR.SQUARE_DIFF]"),
    (SQUARE, "$$\na^2 - b^2 = (a+b)(a-b)\n$$"),
    (TRANSPOSE, "移项后的结果要代回检验。[@node:MATH.SYN.EQ.TRANSPOSE]"),
    (APPLY, "先读题，再找等量关系。[@node:MATH.SYN.EQ.APPLY]"),
]


def test_synthetic_sample_all_found_and_near_misses_clean(pages_dir, run, tmp_path):
    clean = tmp_path / "near"
    shutil.copytree(PAGES, clean)
    for node, text in NEAR_MISS:
        add_para(clean, node, text)
    assert page_issues(run(clean)) == []
    for node, text, _ in DIRTY:
        add_para(pages_dir, node, text)
    edit(page_file(pages_dir, SQUARE), "(a+b)(a-b) = a^2 - b^2 % check", "(a+b)(a-b) = a^2 + b^2")
    edit(page_file(pages_dir, LINEAR), "移项时漏改符号", "移项法则使用时漏改符号")
    r = run(pages_dir, affected=[SQUARE, LINEAR, TRANSPOSE, APPLY, "MATH.SYN.EQ.NO_PAGE"])
    want = {rule for _, _, rule in DIRTY} | {"atom_conflict.formula", "term_consistency.variant", "affected_scope.missing"}
    assert rules(r) == want
    assert {i.check_type for i in page_issues(r)} == set(SEMANTIC_CHECK_TYPES)
    assert len(page_issues(r)) == len(want)  # 每处问题恰好一条，没有连带误报


# ---------- 比对范围 ----------

def test_topic_is_three_segment_prefix():
    assert topic_of(LINEAR) == "MATH.SYN.EQ" and topic_of("MATH.SYN.EQ") is None and topic_of("MATH.SYN.EQ.A.B") == "MATH.SYN.EQ"


def test_neighbors_are_prerequisite_confusable_and_same_topic(snap):
    assert skeleton_neighbors(snap, LINEAR) == {TRANSPOSE, APPLY}
    assert skeleton_neighbors(snap, SQUARE) == set()
    doc = sem_doc()
    doc["edges"].append({"from_id": SQUARE, "to_id": LINEAR, "type": "confusable"})
    assert skeleton_neighbors(parse_snapshot(doc), SQUARE) == {LINEAR}
    # 同主题按 ID 前缀，不看 parent_id：挂错上级的同前缀节点仍是相邻页，异前缀的上级相同也不算
    doc = sem_doc()
    doc["nodes"].append({"id": "MATH.SYN.EQ.EXTRA", "level": "node", "parent_id": "MATH.SYN.EXPR", "kind": "kp", "name": "合成附加节点"})
    doc["nodes"].append({"id": "MATH.SYN.EXPR.OTHER", "level": "node", "parent_id": "MATH.SYN.EQ", "kind": "kp", "name": "合成别处节点"})
    s = parse_snapshot(doc)
    assert "MATH.SYN.EQ.EXTRA" in skeleton_neighbors(s, LINEAR) and "MATH.SYN.EXPR.OTHER" not in skeleton_neighbors(s, LINEAR)
    assert skeleton_neighbors(s, SQUARE) == {"MATH.SYN.EXPR.OTHER"}


def test_scope_is_affected_pages_and_their_neighbors(snap):
    pages = {pf.page.page_id: pf for pf in load_files(PAGES)}
    scope, pairs = comparison_scope(pages, snap, None)
    assert scope == tuple(sorted(pages)) and pairs == ((APPLY, LINEAR), (APPLY, TRANSPOSE), (LINEAR, TRANSPOSE))  # 不是全量 6 对
    assert comparison_scope(pages, snap, [SQUARE]) == ((SQUARE,), ())
    assert comparison_scope(pages, snap, [APPLY]) == (tuple(sorted((APPLY, LINEAR, TRANSPOSE))), ((APPLY, LINEAR), (APPLY, TRANSPOSE)))
    assert comparison_scope(pages, snap, ["MATH.SYN.EQ.NO_PAGE"]) == ((), ())


def test_scope_limits_what_is_reported(pages_dir, run):
    edit(page_file(pages_dir, LINEAR), "移项时漏改符号", "移项法则使用时漏改符号")  # 一元一次方程页 × 移项页
    add_para(pages_dir, LINEAR, "课标要求达到运用层次。[@curriculum:2022/SYN-EQ-01]")  # 一元一次方程页本身
    assert page_issues(run(pages_dir, affected=[SQUARE])) == []
    assert run(pages_dir, affected=[SQUARE]).report.pages == 1
    r = run(pages_dir, affected=[APPLY])  # 一元一次方程页在范围里（应用页的相邻页），但它与移项页这一对不比
    assert rules(r) == {"atom_conflict.curriculum_level"} and r.pairs == ((APPLY, LINEAR), (APPLY, TRANSPOSE))
    assert rules(run(pages_dir, affected=[LINEAR])) == {"atom_conflict.curriculum_level", "term_consistency.variant"}
    assert rules(run(pages_dir, affected=[TRANSPOSE])) == {"atom_conflict.curriculum_level", "term_consistency.variant"}


def test_affected_page_missing_from_directory_blocks(pages_dir, run):
    r = run(pages_dir, affected=[SQUARE, "MATH.SYN.EQ.NO_PAGE"])
    hits = [i for i in r.report.issues if i.check_type == "affected_scope"]
    assert [(i.page_id, i.severity) for i in hits] == [("MATH.SYN.EQ.NO_PAGE", "block")] and r.report.blocked
    assert r.missing == ("MATH.SYN.EQ.NO_PAGE",) and r.report.scope["missing"] == ["MATH.SYN.EQ.NO_PAGE"]


# ---------- 报告格式与绑定字段（kge-lint-report/1.1） ----------

def test_report_format_and_binding(pages_dir, run, inputs):
    add_para(pages_dir, LINEAR, "课标要求达到运用层次。[@curriculum:2022/SYN-EQ-01]")
    r = run(pages_dir, affected=[LINEAR])
    d = r.report.to_dict()
    assert d["format"] == REPORT_FORMAT == "kge-lint-report/1.1"
    assert set(d) == {"format", "layer", "map_version", "scope", "inputs", "config", "summary", "issues"}
    assert d["layer"] == "semantic" and d["map_version"] == "0.9.0"
    issue = next(i for i in d["issues"] if i["page_id"] == LINEAR)
    assert set(issue) == {"id", "page_id", "page_version", "layer", "check_type", "severity", "detail", "status"}
    assert issue["layer"] == "semantic" and issue["check_type"] == "atom_conflict" and issue["status"] == "open"
    assert issue["detail"]["rule"] == "atom_conflict.curriculum_level" and set(issue["detail"]["location"]) == {"file", "line", "section"}
    assert d["summary"] == {"pages": 3, "blocks": 1, "warnings": 1,
                            "by_check": {"atom_conflict/block": 1, "out_of_scope/warn": 1}}
    assert [p["page_id"] for p in d["scope"]["pages"]] == sorted((APPLY, LINEAR, TRANSPOSE))
    assert all(p["page_version"] == "1.0.0" and re.fullmatch(r"[0-9a-f]{64}", p["sha256"]) for p in d["scope"]["pages"])
    assert d["scope"]["affected"] == [LINEAR] and d["scope"]["pairs"] == [[APPLY, LINEAR], [LINEAR, TRANSPOSE]]
    assert set(d["inputs"]) == {"map_snapshot", "lexicon", "scope_vocab", "style_lexicon", "terms"}
    assert all(re.fullmatch(r"[0-9a-f]{64}", v) for v in d["inputs"].values())
    assert d["inputs"]["scope_vocab"] == inputs["scope_vocab"].digest
    assert d["config"] == {"levels_default": True, "levels_overrides": {},
                           "synthetic_inputs": {"scope_vocab": True, "style_lexicon": True, "terms": True}, "skipped_checks": []}
    assert str(pages_dir) not in r.report.to_json()  # 不含本机路径


def test_report_without_terms_and_with_overrides(pages_dir, run, tmp_path, inputs):
    from knowledge.semantic.inputs import EMPTY_TERMS
    p = tmp_path / "levels.json"
    p.write_text(json.dumps({"format": "kge-semantic-levels/1", "levels": {"term_consistency.variant": "block"}}), encoding="utf-8")
    d = run(pages_dir, terms=EMPTY_TERMS, levels=load_levels(p)).report.to_dict()
    assert d["inputs"]["terms"] is None and d["config"]["synthetic_inputs"]["terms"] is None
    assert d["config"]["levels_default"] is False and d["config"]["levels_overrides"] == {"term_consistency.variant": "block"}


def test_program_report_has_binding_and_ids_unchanged(pages_dir, snap_v1, run_lint, lexicon):
    import hashlib
    d = run_lint(pages_dir, snap_v1, baseline_dir=PAGES).to_dict()
    assert d["format"] == "kge-lint-report/1.1" and d["layer"] == "program" and d["map_version"] == "0.9.0"
    assert [p["page_id"] for p in d["scope"]["pages"]] == sorted((APPLY, LINEAR, SQUARE, TRANSPOSE))
    assert d["config"]["skipped_checks"] == [] and d["inputs"]["baseline"] is not None
    assert d["inputs"]["lexicon"] == hashlib.sha256(lexicon.to_json().encode("utf-8")).hexdigest()
    d = run_lint(pages_dir).to_dict()  # 没给快照、没给上一审核版：跳过的检查写进 config
    assert d["map_version"] is None and d["inputs"]["map_snapshot"] is None
    assert d["config"]["skipped_checks"] == ["atom_expired", "atom_hash", "locked_baseline", "node_id", "template", "textbook_quote"]
    i = LintIssue("sympy", "block", "MATH.SYN.EQ.LINEAR", "1.0.0", "kp/MATH.SYN.EQ.LINEAR.md", "SymPy 验算不通过：x", 70, "key_points")
    body = {"page_id": i.page_id, "page_version": "1.0.0", "layer": "program", "check_type": "sympy", "severity": "block",
            "detail": {"message": i.message, "location": {"file": i.file, "line": 70, "section": "key_points"}}}
    want = "li-" + hashlib.sha256(json.dumps(body, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:16]
    assert i.to_dict()["id"] == want  # layer 字段加在末尾、缺省 program：条目 id 与 /1 相同


def test_report_rejects_issues_of_other_layer():
    with pytest.raises(ValueError):
        LintReport((LintIssue("atom_conflict", "block", "p", "1.0.0", "f", "m", layer="semantic"),), 1)  # 缺省层 program
    with pytest.raises(ValueError):
        LintReport((), 1, "model")


def test_check_types_merged_into_enum_and_bound_to_layer():
    assert CHECK_TYPES == PROGRAM_CHECK_TYPES + SEMANTIC_CHECK_TYPES
    LintIssue("atom_conflict", "block", "p", "1.0.0", "f", "m", layer="semantic")
    with pytest.raises(ValueError):
        LintIssue("atom_conflict", "block", "p", "1.0.0", "f", "m")  # 缺省层是 program
    with pytest.raises(ValueError):
        LintIssue("sympy", "block", "p", "1.0.0", "f", "m", layer="semantic")
    with pytest.raises(ValueError):
        LintIssue("sympy", "block", "p", "1.0.0", "f", "m", layer="model")


def _risk_module():
    """审核台的分流纯函数（services/learning/review_console/risk.py 只依赖标准库）：按文件路径载入，不改 sys.path"""
    import importlib.util
    spec = importlib.util.spec_from_file_location("kge11_review_risk", ROOT / "services/learning/review_console/risk.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = mod  # dataclass 需要按模块名找到自己
    spec.loader.exec_module(mod)
    return mod


def _real(path: Path, tmp_path: Path, loader):
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["synthetic"] = False
    p = tmp_path / f"real-{path.name}"
    p.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    return loader(p)


def test_reports_bind_end_to_end_with_review_console_triage(pages_dir, snap_v1, run_lint, lexicon, tmp_path):
    """知识服务产出的两份真报告喂给审核台分流：绑定齐全才给 medium_spot_check；合成词表、跳过检查、版本不符、
    两份报告依据的快照不同、有阻断都按高"""
    risk = _risk_module()
    snap = snap_v1
    other = json.loads(lint_semantic_dir(pages_dir, snapshot=parse_snapshot(sem_doc()), lexicon=lexicon,  # 同为 0.9.0、内容不同的快照
                                         scope_vocab=_real(SEM / "scope-vocab.json", tmp_path, load_scope),
                                         style=_real(SEM / "style-lexicon.json", tmp_path, load_style), affected=[LINEAR]).report.to_json())
    vocab, style = _real(SEM / "scope-vocab.json", tmp_path, load_scope), _real(SEM / "style-lexicon.json", tmp_path, load_style)
    prog = json.loads(run_lint(pages_dir, snap, baseline_dir=PAGES).to_json())
    sem = json.loads(lint_semantic_dir(pages_dir, snapshot=snap, lexicon=lexicon, scope_vocab=vocab, style=style,
                                       affected=[LINEAR]).report.to_json())
    bind = {"page_id": LINEAR, "page_version": "1.0.0", "map_version": "0.9.0", "enabled": True}
    assert risk.suggest_review_level("medium", prog, sem, **bind) == "medium_spot_check"
    assert risk.suggest_review_level("medium", prog, sem, **{**bind, "enabled": False}) == "high"
    assert risk.suggest_review_level("medium", prog, sem, **{**bind, "page_version": "1.0.1"}) == "high"
    assert risk.suggest_review_level("medium", prog, sem, **{**bind, "page_id": SQUARE}) == "high"  # 语义报告没查这一页
    assert risk.suggest_review_level("medium", sem, prog, **bind) == "high"
    assert risk.suggest_review_level("medium", prog, other, **bind) == "high"  # 地图版本号相同、快照摘要不同
    synthetic = json.loads(lint_semantic_dir(pages_dir, snapshot=snap, lexicon=lexicon, scope_vocab=load_scope(SEM / "scope-vocab.json"),
                                             style=style, affected=[LINEAR]).report.to_json())
    assert risk.suggest_review_level("medium", prog, synthetic, **bind) == "high"
    no_base = json.loads(run_lint(pages_dir, snap).to_json())  # 没有上一审核版：跳过 locked_baseline
    assert risk.suggest_review_level("medium", no_base, sem, **bind) == "high"
    add_para(pages_dir, LINEAR, "课标要求达到运用层次。[@curriculum:2022/SYN-EQ-01]")
    blocked = json.loads(lint_semantic_dir(pages_dir, snapshot=snap, lexicon=lexicon, scope_vocab=vocab, style=style,
                                           affected=[LINEAR]).report.to_json())
    assert risk.suggest_review_level("medium", prog, blocked, **bind) == "high"


# ---------- 读取方兼容 ----------

def test_m1_counts_only_program_reports_and_accepts_both_formats(pages_dir, snap_v1, run_lint, run):
    prog = json.loads(run_lint(PAGES, snap_v1).to_json())
    assert build_report(map_doc(), lint_report=prog)["metrics"]["lint_blocks"]["met"] is True
    legacy = {"format": "kge-lint-report/1", "summary": prog["summary"], "issues": prog["issues"]}  # 旧格式（无顶层 layer）
    assert build_report(map_doc(), lint_report=legacy)["metrics"]["lint_blocks"]["blocks"] == 0
    add_para(pages_dir, LINEAR, "课标要求达到运用层次。[@curriculum:2022/SYN-EQ-01]")
    sem = json.loads(run(pages_dir).report.to_json())
    with pytest.raises(M1InputError, match="程序体检"):
        build_report(map_doc(), lint_report=sem)
    with pytest.raises(M1InputError):
        build_report(map_doc(), lint_report={**legacy, "issues": sem["issues"], "summary": sem["summary"]})
    with pytest.raises(M1InputError):
        build_report(map_doc(), lint_report={**prog, "layer": None})


def test_pages_repo_ci_reads_only_summary(tmp_path, snap_v1, run_lint):
    """页面仓库 CI（tools/kge-pages-template/repo/ci/lint.sh）只读 summary 的 pages、blocks、warnings：/1.1 报告照样能读"""
    script = (ROOT / "tools/kge-pages-template/repo/ci/lint.sh").read_text(encoding="utf-8")
    snippet = script.split("<<'PY' || true\n", 1)[1].split("\nPY\n", 1)[0]
    assert '["summary"]' in snippet and "format" not in snippet and "layer" not in snippet
    report = tmp_path / "lint-report.json"
    report.write_text(run_lint(PAGES, snap_v1).to_json(), encoding="utf-8")
    r = subprocess.run([sys.executable, "-c", snippet], capture_output=True, text=True, timeout=60, check=False,
                       env={"REPORT": str(report), "PATH": "/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1"})
    assert r.stdout.strip() == "kge-lint: 4 页，阻断 0，警告 0"


# ---------- 分级配置 ----------

def test_levels_are_configurable_and_fail_closed(pages_dir, run, tmp_path, inputs):
    edit(page_file(pages_dir, LINEAR), "移项时漏改符号", "移项法则使用时漏改符号")
    p = tmp_path / "levels.json"
    p.write_text(json.dumps({"format": "kge-semantic-levels/1", "levels": {"term_consistency.variant": "block",
                                                                         "age_style.STY-SYN-SCARE": "block"}}), encoding="utf-8")
    levels = load_levels(p, inputs["style"].ids)
    r = run(pages_dir, levels=levels)
    assert r.report.blocked and {i.severity for i in page_issues(r)} == {"block"}
    add_para(pages_dir, TRANSPOSE, "这一步错了必挂。[@node:MATH.SYN.EQ.TRANSPOSE]")
    assert "age_style.STY-SYN-SCARE" in rules(run(pages_dir, levels=levels), "block")
    assert "age_style.STY-SYN-SCARE" in rules(run(pages_dir), "warn")
    for bad in ({"format": "kge-semantic-levels/1", "levels": {"atom_conflict.nope": "block"}},
                {"format": "kge-semantic-levels/1", "levels": {"atom_conflict.formula": "error"}},
                {"format": "kge-semantic-levels/1", "levels": {"age_style.STY-UNKNOWN": "block"}},
                {"format": "kge-semantic-levels/2", "levels": {}}):
        p.write_text(json.dumps(bad), encoding="utf-8")
        with pytest.raises(LevelsError):
            load_levels(p, inputs["style"].ids)


def test_default_levels_cover_every_semantic_check_and_are_marked_pending():
    assert {k.split(".")[0] for k in DEFAULT_LEVELS} == set(SEMANTIC_CHECK_TYPES)
    src = (SVC / "knowledge/semantic/config.py").read_text(encoding="utf-8")
    assert "待教研确认" in src


def test_no_vocab_level_can_be_made_blocking(pages_dir, run, tmp_path):
    p = tmp_path / "levels.json"
    p.write_text(json.dumps({"format": "kge-semantic-levels/1", "levels": {"out_of_scope.no_vocab": "block"}}), encoding="utf-8")
    assert run(pages_dir, scope_vocab=None, levels=load_levels(p)).report.blocked


# ---------- 输入词表校验 ----------

@pytest.mark.parametrize("parse,raw", [
    (parse_scope, {"subjects": {}}),
    (parse_scope, {"subjects": {"math": {"stages": ["junior"], "terms": []}}}),
    (parse_scope, {"subjects": {"aya": {"stages": [], "terms": []}}}),
    (parse_scope, {"subjects": {"aya": {"stages": ["junior"], "terms": [{"term": "合成 词", "stage": "senior"}]}}}),
    (parse_scope, {"subjects": {"aya": {"stages": ["junior"], "terms": [{"term": "合成词", "stage": "senior"}],
                                        "node_allow": {"MATH.SYN.EQ.LINEAR": ["未登记"]}}}}),
    (parse_style, {"rules": []}),
    (parse_style, {"rules": [{"id": "default", "kind": "literal", "pattern": "x", "message": "m"}]}),
    (parse_style, {"rules": [{"id": "A", "kind": "regex", "pattern": "(", "message": "m"}]}),
    (parse_style, {"rules": [{"id": "A", "kind": "regex", "pattern": "x*", "message": "m"}]}),
    (parse_style, {"rules": [{"id": "A", "kind": "glob", "pattern": "x", "message": "m"}]}),
    (parse_terms, {"aliases": {"MATH.SYN.EQ.LINEAR": ["同名"], "MATH.SYN.EQ.APPLY": ["同名"]}}),
    (parse_terms, {"aliases": ["x"]}),
    (parse_terms, {"edition_terms": {"syn-ed": ["同一表述"], "syn-ed2": ["同一表述"]}}),
])
def test_inputs_reject_malformed(parse, raw):
    with pytest.raises(InputError):
        parse({"synthetic": True, **raw})


def test_input_files_require_format_and_synthetic_flag(tmp_path):
    p = tmp_path / "v.json"
    p.write_text(json.dumps({"format": "kge-semantic-scope/1", "subjects": {}}), encoding="utf-8")
    with pytest.raises(InputError):
        load_scope(p)
    p.write_text(json.dumps({"format": "kge-semantic-style/1", "synthetic": True, "rules": []}), encoding="utf-8")
    with pytest.raises(InputError):
        load_scope(p)
    for name in ("scope-vocab.json", "style-lexicon.json", "terms.json"):
        assert json.loads((SEM / name).read_text(encoding="utf-8"))["synthetic"] is True  # 仓库里只有合成样例


# ---------- 命令行 ----------

def _cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, "-m", "knowledge.semantic", *args], capture_output=True, text=True, cwd=SVC,
                          env={"PYTHONDONTWRITEBYTECODE": "1", "PATH": "/usr/bin:/bin"}, timeout=120, check=False)


def test_cli_json_and_exit_codes(pages_dir, tmp_path):
    snap = tmp_path / "snap.json"
    snap.write_text(json.dumps(sem_doc(), ensure_ascii=False), encoding="utf-8")
    common = [str(pages_dir), "--map-snapshot", str(snap), "--scope-vocab", str(SEM / "scope-vocab.json"),
              "--style-lexicon", str(SEM / "style-lexicon.json"), "--terms", str(SEM / "terms.json")]
    r = _cli(*common, "--json")
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["summary"]["blocks"] == 0 and out["layer"] == "semantic" and out["format"] == "kge-lint-report/1.1"
    add_para(pages_dir, LINEAR, "课标要求达到运用层次。[@curriculum:2022/SYN-EQ-01]")
    r = _cli(*common, "--json")
    assert r.returncode == 1 and all(i["layer"] == "semantic" for i in json.loads(r.stdout)["issues"])
    r = _cli(*common, "--affected-pages", SQUARE)
    assert r.returncode == 0 and "1 页" in r.stdout
    assert _cli(*common, "--affected-pages", "MATH.SYN.EQ.NO_PAGE").returncode == 1  # 受影响页不在目录：阻断
    assert _cli(*common, "--affected-pages", "not an id").returncode == 2
    assert _cli(str(pages_dir), "--map-snapshot", str(tmp_path / "missing.json")).returncode == 2
    bad = tmp_path / "bad.json"
    bad.write_text("{}", encoding="utf-8")
    assert _cli(*common, "--levels", str(bad)).returncode == 2


@pytest.mark.parametrize("plan,code", [
    ({"to_map_version": "0.9.0", "jobs": [{"page_id": SQUARE}]}, 0),
    ({"to_map_version": "0.9.1", "jobs": [{"page_id": SQUARE}]}, 2),   # 清单按别的地图版本编出来
    ({"to_map_version": None, "jobs": [{"page_id": SQUARE}]}, 2),
    ({"to_map_version": None, "jobs": []}, 0),                          # 只有信号的清单：没有受影响页
    ({"to_map_version": "0.9.0", "jobs": [{"page_id": "bad id"}]}, 2),
    ({"to_map_version": "0.9.0", "jobs": ["x"]}, 2),
])
def test_cli_affected_plan_is_checked(pages_dir, tmp_path, plan, code):
    snap = tmp_path / "snap.json"
    snap.write_text(json.dumps(sem_doc(), ensure_ascii=False), encoding="utf-8")
    p = tmp_path / "plan.json"
    p.write_text(json.dumps({"format": "kge-compile-plan/1", **plan}), encoding="utf-8")
    assert _cli(str(pages_dir), "--map-snapshot", str(snap), "--affected", str(p)).returncode == code


# ---------- 口径现状（spec 范围 4：只记录、不改行为） ----------

def test_compiler_classifies_narrative_rewrite_as_high():
    """编译器 rebuild.classify 把叙述段改写判高；审核台 risk.classify_page 判中（services/learning/tests/test_review_triage.py
    固定另一侧）。两处口径不一致，实际效果相同（审核台中风险按高审），统一与否待人确认（kge11 #3）"""
    from knowledge.compiler.rebuild import classify
    from knowledge.pages.format import Section, parse
    page = parse((PAGES / "kp" / f"{LINEAR}.md").read_text(encoding="utf-8"))
    new = page.with_sections(tuple(Section(s.id, s.kind, s.content + "\n\n补一句。[@node:MATH.SYN.EQ.LINEAR]")
                                   if s.id == "pitfalls" else s for s in page.sections))
    risk, reasons, changed = classify(page, new)
    assert (risk, reasons, changed) == ("high", ("叙述段 pitfalls 改写（高风险）",), ("pitfalls",))


def test_semantic_lint_does_not_touch_program_lint_results(pages_dir, snap_v1, run_lint, run):
    add_para(pages_dir, LINEAR, "课标要求达到运用层次。[@curriculum:2022/SYN-EQ-01]")
    before = run_lint(pages_dir, snap_v1)
    run(pages_dir)
    after = run_lint(pages_dir, snap_v1)
    assert after.to_json() == before.to_json() and all(i.layer == "program" for i in after.issues)
