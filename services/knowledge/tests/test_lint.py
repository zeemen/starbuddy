"""程序体检：每类检查一个正例（应报）一个反例（不应报）。反例的基线是合成页面仓库（全部通过）。"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys

import pytest

from conftest import APPLY, LINEAR, MAP_V1, PAGES, SQUARE, TODAY, TRANSPOSE, checks_of, edit, map_doc, node_rec, page_file
from knowledge.atoms import parse_snapshot
from knowledge.lint.issues import CHECK_TYPES, LintIssue


def test_clean_fixture_repo_has_no_issues(pages_dir, snap_v1, run_lint):
    report = run_lint(pages_dir, snap_v1, baseline_dir=PAGES)
    assert report.issues == ()
    assert report.pages == 4


def test_without_snapshot_warns_and_skips(pages_dir, run_lint):
    report = run_lint(pages_dir)
    assert checks_of(report, "warn") == {"snapshot"} and not report.blocked


def test_draft_snapshot_warns(pages_dir, run_lint):
    doc = map_doc()
    doc["status"] = "draft"
    assert "snapshot" in checks_of(run_lint(pages_dir, parse_snapshot(doc)), "warn")


# ---------- 结构 ----------

def test_format_unparseable_blocks(pages_dir, snap_v1, run_lint):
    edit(page_file(pages_dir, LINEAR), "<!-- kge:end id=pitfalls -->", "<!-- kge:end id=nope -->")
    report = run_lint(pages_dir, snap_v1)
    assert [i.check_type for i in report.blocks] == ["format"]
    assert report.blocks[0].line is not None


def test_format_non_canonical_warns(pages_dir, snap_v1, run_lint):
    edit(page_file(pages_dir, LINEAR), "subject: aya", "subject: 'aya'")
    report = run_lint(pages_dir, snap_v1)
    assert checks_of(report, "warn") == {"format"} and not report.blocked


@pytest.mark.parametrize("old,new,msg", [
    ("schema_version: 1\n", "", "缺字段"),
    ("schema_version: 1\n", "schema_version: 1\nstudent_ref: s1\n", "未登记字段"),
    ("page_type: kp", "page_type: method", "已预留"),
    ("page_type: kp", "page_type: blog", "页面类型不合法"),
    ("subject: aya", "subject: math", "subject"),
    ("page_version: 1.0.0", "page_version: v1", "semver"),
    ("risk_level: low", "risk_level: none", "risk_level"),
    ("rebuild_kind: render", "rebuild_kind: full", "全量重编"),
    ("locked_sections: [definition, curriculum]", "locked_sections: [definition]", "locked_sections"),
    ("hash: sha256:4f6f299232d42ba1}", "hash: md5:4f6f}", "hash"),
    ("  - {type: curriculum,", "  - {type: student,", "type 不合法"),
    ("page_id: MATH.SYN.EQ.LINEAR", "page_id: MATH.SYN.EQ.OTHER", "page_id 必须等于 node_id"),
])
def test_header_checks(pages_dir, snap_v1, run_lint, old, new, msg):
    edit(page_file(pages_dir, LINEAR), old, new)
    report = run_lint(pages_dir)
    hits = [i for i in report.blocks if i.check_type == "header"]
    assert any(msg in i.message for i in hits), [i.message for i in report.issues]


def test_header_file_location(pages_dir, run_lint):
    shutil.move(page_file(pages_dir, LINEAR), pages_dir / "kp" / "linear.md")
    report = run_lint(pages_dir)
    assert any("文件位置" in i.message for i in report.blocks)


def test_node_id_must_exist_in_snapshot(pages_dir, run_lint):
    doc = map_doc()
    doc["nodes"] = [n for n in doc["nodes"] if n["id"] != SQUARE]
    doc["curriculum_links"] = [c for c in doc["curriculum_links"] if c["node_id"] != SQUARE]
    report = run_lint(pages_dir, parse_snapshot(doc))
    assert any(i.check_type == "node_id" and i.page_id == SQUARE for i in report.blocks)


def test_node_id_map_version_mismatch(pages_dir, run_lint):
    doc = map_doc()
    doc["map_version"] = "0.9.1"
    report = run_lint(pages_dir, parse_snapshot(doc))
    assert sum(1 for i in report.blocks if i.check_type == "node_id") == 4


@pytest.mark.parametrize("old,new", [
    ("<!-- kge:section id=pitfalls kind=narrative -->", "<!-- kge:section id=pitfalls kind=render -->"),
    ("## 易错\n", "## 易错点\n"),
    ("# 一元一次方程\n", "# 一元一次方程\n段落外的一行\n"),
])
def test_section_markers(pages_dir, run_lint, old, new):
    edit(page_file(pages_dir, LINEAR), old, new)
    assert "sections" in checks_of(run_lint(pages_dir))


def test_missing_section_blocks(pages_dir, run_lint):
    p = page_file(pages_dir, LINEAR)
    text = p.read_text(encoding="utf-8")
    start = text.index("<!-- kge:section id=region")
    end = text.index("<!-- kge:end id=region -->") + len("<!-- kge:end id=region -->\n")
    p.write_text(text[:start] + text[end:], encoding="utf-8")
    report = run_lint(pages_dir)
    assert any(i.check_type == "sections" and "region" in i.message for i in report.blocks)


def test_template_violation_blocks(pages_dir, snap_v1, run_lint):
    edit(page_file(pages_dir, APPLY), "常见题位：解答题", "常见题位：选择题")
    report = run_lint(pages_dir, snap_v1)
    assert [(i.check_type, i.section) for i in report.blocks] == [("template", "region")]


def test_locked_section_rewrite_against_baseline_blocks(pages_dir, run_lint):
    """锁定段被改写（依据原子没变）→ 阻断；不给快照时模板检查不跑，只靠上一审核版比对"""
    edit(page_file(pages_dir, LINEAR), "不包括：二元一次方程组。", "不包括：二元一次方程组与分式方程。")
    report = run_lint(pages_dir, baseline_dir=PAGES)
    assert [(i.check_type, i.section) for i in report.blocks] == [("locked_baseline", "definition")]


def test_locked_section_change_allowed_when_atom_changed(pages_dir, run_lint):
    """原子本身变了（哈希变化）时锁定段可以随之变化，不报 locked_baseline（由风险分级走高风险审核）"""
    doc = map_doc()
    node_rec(doc, LINEAR)["excludes"] = ["二元一次方程组与分式方程"]
    snap = parse_snapshot(doc)
    edit(page_file(pages_dir, LINEAR), "不包括：二元一次方程组。", "不包括：二元一次方程组与分式方程。")
    edit(page_file(pages_dir, LINEAR), "sha256:4f6f299232d42ba1", snap.get("node", LINEAR).hash)
    report = run_lint(pages_dir, snap, baseline_dir=PAGES)
    assert "locked_baseline" not in checks_of(report)
    assert report.issues == ()


# ---------- 引用 ----------

@pytest.mark.parametrize("old,new,msg", [
    ("[[MATH.SYN.EQ.APPLY]]", "[[MATH.SYN.EQ.MISSING]]", "失效链接"),
    ("合并同类项、系数化为一。[@node:MATH.SYN.EQ.LINEAR]", "合并同类项、系数化为一。[@node:MATH.SYN.EQ.APPLY]", "没有登记"),
    ("合并同类项、系数化为一。[@node:MATH.SYN.EQ.LINEAR]", "合并同类项、系数化为一。[@exam:X1]", "类型不合法"),
    ("{{quote:syn-tp-001}}", "{{quote:syn-tp-002}}", "占位"),
    ("去括号时括号前是负号", "去括号（见[另一页](../kp/x.md)）时括号前是负号", "相对路径"),
])
def test_link_checks(pages_dir, run_lint, old, new, msg):
    edit(page_file(pages_dir, LINEAR), old, new)
    report = run_lint(pages_dir)
    assert any(i.check_type == "link" and msg in i.message for i in report.blocks), [i.message for i in report.issues]


def test_link_to_atom_missing_from_snapshot(pages_dir, run_lint):
    doc = map_doc()
    doc["region_projections"] = [r for r in doc["region_projections"] if r["id"] != "syn-rp-002"]
    report = run_lint(pages_dir, parse_snapshot(doc))
    assert any(i.check_type == "link" and "失效引用" in i.message for i in report.blocks)
    assert any(i.check_type == "atom_hash" and "不在地图快照" in i.message for i in report.blocks)


def test_external_link_warns(pages_dir, run_lint):
    edit(page_file(pages_dir, LINEAR), "去括号时括号前是负号", "去括号（[参考](https://example.invalid/a)）时括号前是负号")
    report = run_lint(pages_dir)
    assert "link" in checks_of(report, "warn")


def test_orphan_deprecated_node_blocks(pages_dir, snap_v1, run_lint):
    doc = map_doc()
    node_rec(doc, SQUARE)["status"] = "deprecated"
    report = run_lint(pages_dir, parse_snapshot(doc))
    assert any(i.check_type == "orphan" and i.page_id == SQUARE for i in report.blocks)


def test_orphan_wrong_node_kind_blocks(pages_dir, run_lint):
    doc = map_doc()
    node_rec(doc, SQUARE)["kind"] = "method"
    node_rec(doc, SQUARE).pop("cognitive_levels")
    report = run_lint(pages_dir, parse_snapshot(doc))
    assert any(i.check_type == "orphan" and "挂不了" in i.message for i in report.blocks)


def test_orphan_duplicate_page_for_node(pages_dir, run_lint):
    (pages_dir / "kp" / "copy").mkdir()
    shutil.copy(page_file(pages_dir, LINEAR), pages_dir / "kp" / "copy" / f"{LINEAR}.md")
    report = run_lint(pages_dir)
    assert sum(1 for i in report.blocks if i.check_type == "orphan") == 2


def test_missing_citation_blocks(pages_dir, run_lint):
    edit(page_file(pages_dir, LINEAR), "去括号时括号前是负号，括号里的各项都要改变符号。[@node:MATH.SYN.EQ.LINEAR]",
         "去括号时括号前是负号，括号里的各项都要改变符号。")
    report = run_lint(pages_dir)
    hits = [i for i in report.blocks if i.check_type == "citation"]
    assert len(hits) == 1 and hits[0].section == "pitfalls"
    text = page_file(pages_dir, LINEAR).read_text(encoding="utf-8").split("\n")
    assert text[hits[0].line - 1].startswith("去括号时")


def test_formula_block_is_not_a_factual_paragraph(pages_dir, run_lint):
    """独立公式块不算自然段（它由 SymPy 验算）；反例已在基线里：$$ 块没有引用也不报"""
    assert "citation" not in checks_of(run_lint(pages_dir))


def test_stale_atom_hash_blocks(pages_dir, run_lint):
    doc = map_doc()
    doc["region_projections"][1]["typical_slots"] = ["填空题", "选择题"]
    report = run_lint(pages_dir, parse_snapshot(doc))
    assert any(i.check_type == "atom_hash" and i.page_id == LINEAR for i in report.blocks)


def test_expired_affair_blocks(pages_dir, run_lint):
    doc = map_doc()
    doc["current_affairs"] = [{"id": "syn-ca-1", "node_ids": [APPLY], "source": "synthetic", "valid_from": "2026-01-01",
                               "valid_until": "2026-06-30", "review_status": "published"}]
    report = run_lint(pages_dir, parse_snapshot(doc))
    assert any(i.check_type == "atom_expired" and i.page_id == APPLY for i in report.blocks)
    doc["current_affairs"][0]["valid_until"] = "2026-12-31"
    assert "atom_expired" not in checks_of(run_lint(pages_dir, parse_snapshot(doc)))


# ---------- 公式 ----------

def test_sympy_blocks_wrong_identity(pages_dir, run_lint):
    edit(page_file(pages_dir, SQUARE), "(a+b)(a-b) = a^2 - b^2 % check", "(a+b)(a-b) = a^2 + b^2 % check")
    report = run_lint(pages_dir)
    assert [(i.check_type, i.page_id) for i in report.blocks] == [("sympy", SQUARE)]


def test_sympy_blocks_wrong_solution(pages_dir, run_lint):
    edit(page_file(pages_dir, LINEAR), "2x + 3 = 7 % check x=2", "2x + 3 = 7 % check x=3")
    assert "sympy" in checks_of(run_lint(pages_dir))


def test_sympy_blocks_unverifiable_marked_formula(pages_dir, run_lint):
    edit(page_file(pages_dir, SQUARE), "(a-b)^2 = a^2 - 2ab + b^2 % check", "(a-b)^2 \\ge 0 % check")
    report = run_lint(pages_dir)
    assert any(i.check_type == "sympy" and not i.detail.get("verifiable", True) for i in report.blocks)


def test_sympy_blocks_unparseable_marked_formula(pages_dir, run_lint):
    edit(page_file(pages_dir, SQUARE), "(a-b)^2 = a^2 - 2ab + b^2 % check", "\\frac{a}{b = 1 % check")
    assert "sympy" in checks_of(run_lint(pages_dir))


def test_unmarked_unparseable_formula_only_warns(pages_dir, snap_v1, run_lint):
    edit(page_file(pages_dir, SQUARE), "(a-b)^2 = a^2 - 2ab + b^2 % check", "\\foo{a} = 1")
    report = run_lint(pages_dir, snap_v1)
    assert not report.blocked and checks_of(report, "warn") == {"latex"}


def test_unclosed_math_block_warns(pages_dir, run_lint):
    edit(page_file(pages_dir, SQUARE), "(a-b)^2 = a^2 - 2ab + b^2 % check\n$$", "(a-b)^2 = a^2 - 2ab + b^2 % check")
    assert "latex" in checks_of(run_lint(pages_dir), "warn")


# ---------- 规则 13 ----------

@pytest.mark.parametrize("text,label", [
    ("联系 13912345678 获取", "手机号"),
    ("证件 110105200801011234 已登记", "身份证号"),
    ("学号：20260101", "学号式编号"),
    ("编号 NJ20260101 的作答", "学号式编号"),
    ("记录 3f2b8a10-1c2d-4e5f-8a9b-0c1d2e3f4a5b", "UUID"),
    ("来信 someone@example.invalid", "电子邮箱"),
    ("王小明同学，合成第一中学", "姓名 + 学校"),
    ("合成实验学校的李华同学", "姓名 + 学校"),
])
def test_pii_patterns_block(pages_dir, run_lint, text, label):
    edit(page_file(pages_dir, APPLY), "设未知数时没有写单位", f"{text}，设未知数时没有写单位")
    hits = [i for i in run_lint(pages_dir).blocks if i.check_type == "pii"]
    assert any(i.detail.get("pattern") == label for i in hits)
    assert all(text not in i.message for i in hits)  # 不回显命中的原文


@pytest.mark.parametrize("text", ["方程 3x = 12345 的解", "初中阶段的方程", "合成第一中学的课程安排", "王老师布置的练习"])
def test_pii_negative(pages_dir, run_lint, text):
    edit(page_file(pages_dir, APPLY), "设未知数时没有写单位", f"{text}，设未知数时没有写单位")
    assert "pii" not in checks_of(run_lint(pages_dir))


@pytest.mark.parametrize("text,ok", [
    ("该题的学生正确率为 62%", False),
    ("该题的学生正确率为 62%（n=18）", False),
    ("该题的学生正确率为 62%（n=120）", True),
    ("该题的正确率为 62%，样本 45 人", True),
    ("打八折即原价的 80%", True),
])
def test_group_stats(pages_dir, run_lint, text, ok):
    edit(page_file(pages_dir, APPLY), "求出解后要检验它是否符合实际意义。", f"{text}。求出解后要检验它是否符合实际意义。")
    report = run_lint(pages_dir)
    assert ("group_stats" not in checks_of(report)) == ok


def test_group_stats_threshold_is_configurable(pages_dir, run_lint):
    edit(page_file(pages_dir, APPLY), "求出解后要检验它是否符合实际意义。", "该题的学生正确率为 62%（n=40）。求出解后要检验。")
    assert "group_stats" in checks_of(run_lint(pages_dir, min_group=50))
    assert "group_stats" not in checks_of(run_lint(pages_dir, min_group=30))


# ---------- 人设中立与适龄 ----------

@pytest.mark.parametrize("text,check", [
    ("阿鸦提醒", "persona"),
    ("鸦老师说", "persona"),
    ("你现在是怎么想的", "persona"),
    ("像规律碎片一样", "persona"),
    ("你必须记住", "forbidden_phrase"),       # A 类
    ("只有我懂你", "forbidden_phrase"),       # C 类
    ("数学比语文好", "forbidden_phrase"),     # E 类
    ("这题很简单", "forbidden_phrase"),       # F 类（数学，阿鸦规格书）
    ("一般来说", "forbidden_phrase"),         # G 类（考官型基线）
])
def test_persona_and_forbidden_block(pages_dir, run_lint, text, check):
    edit(page_file(pages_dir, APPLY), "设未知数时没有写单位", f"{text}，设未知数时没有写单位")
    assert check in checks_of(run_lint(pages_dir))


def test_subject_specific_f_class_only_for_that_subject(lexicon):
    rules = {r.id: r for r in lexicon.forbidden}
    assert rules["F-aya-04"].applies("aya") and not rules["F-aya-04"].applies("wenli")
    assert any(r.id.startswith("F-wenli") and not r.applies("aya") for r in lexicon.forbidden)
    assert all(r.applies("aya") for r in lexicon.forbidden if r.cls == "G")


def test_sentence_length_blocks(pages_dir, run_lint, lexicon):
    long = "列" * (lexicon.max_sentence_chars + 1)
    edit(page_file(pages_dir, APPLY), "设未知数时没有写单位", f"{long}，设未知数时没有写单位")
    report = run_lint(pages_dir)
    assert any(i.check_type == "sentence_length" and i.section == "pitfalls" for i in report.blocks)


def test_sentence_length_ignores_formulas_and_citations(pages_dir, run_lint, lexicon):
    formula = "$" + " + ".join(["x"] * 40) + "$"
    edit(page_file(pages_dir, APPLY), "设未知数时没有写单位", f"式子{formula}里", )
    assert "sentence_length" not in checks_of(run_lint(pages_dir))


def test_textbook_quote_blocks(pages_dir, snap_v1, run_lint):
    edit(page_file(pages_dir, LINEAR), "去括号时括号前是负号", "只含有一个未知数并且未知数的次数都是一的方程。去括号时括号前是负号")
    report = run_lint(pages_dir, snap_v1)
    assert any(i.check_type == "textbook_quote" and i.detail.get("projection") == "syn-tp-001" for i in report.blocks)


# ---------- 输出格式与命令行 ----------

def test_issue_json_fields_match_lint_issue(pages_dir, snap_v1, run_lint):
    edit(page_file(pages_dir, LINEAR), "2x + 3 = 7 % check x=2", "2x + 3 = 7 % check x=3")
    d = run_lint(pages_dir, snap_v1).to_dict()
    issue = d["issues"][0]
    assert set(issue) == {"id", "page_id", "page_version", "layer", "check_type", "severity", "detail", "status"}
    assert issue["layer"] == "program" and issue["status"] == "open" and issue["page_version"] == "1.0.0"
    assert set(issue["detail"]["location"]) == {"file", "line", "section"}
    assert d["summary"]["blocks"] == 1


def test_issue_rejects_unregistered_check_type():
    with pytest.raises(ValueError):
        LintIssue("semantic", "block", "p", "1.0.0", "f", "m")
    assert "sympy" in CHECK_TYPES


def _cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, "-m", "knowledge.lint", *args], capture_output=True, text=True,
                          cwd=str(PAGES.parents[2]))


def test_cli_exit_codes_and_json(pages_dir, tmp_path):
    ok = _cli(str(pages_dir), "--map-snapshot", str(MAP_V1), "--baseline", str(PAGES), "--today", TODAY.isoformat(), "--json")
    assert ok.returncode == 0, ok.stderr
    assert json.loads(ok.stdout)["summary"] == {"pages": 4, "blocks": 0, "warnings": 0, "by_check": {}}
    edit(page_file(pages_dir, SQUARE), "a^2 - b^2 % check", "a^2 + b^2 % check")
    bad = _cli(str(pages_dir), "--map-snapshot", str(MAP_V1), "--today", TODAY.isoformat(), "--json")
    assert bad.returncode == 1 and json.loads(bad.stdout)["issues"][0]["check_type"] == "sympy"
    assert _cli(str(tmp_path / "nope")).returncode == 2
    broken = tmp_path / "broken.yaml"
    broken.write_text("map_version: 1\n", encoding="utf-8")
    assert _cli(str(pages_dir), "--map-snapshot", str(broken)).returncode == 2


def test_cli_lexicon_export_and_reuse(pages_dir, tmp_path):
    lex = tmp_path / "lexicon.json"
    assert _cli("--export-lexicon", str(lex)).returncode == 0
    r = _cli(str(pages_dir), "--map-snapshot", str(MAP_V1), "--lexicon", str(lex), "--today", TODAY.isoformat())
    assert r.returncode == 0, r.stdout
    edit(page_file(pages_dir, APPLY), "设未知数时没有写单位", "点点提醒，设未知数时没有写单位")
    r = _cli(str(pages_dir), "--map-snapshot", str(MAP_V1), "--lexicon", str(lex), "--today", TODAY.isoformat())
    assert r.returncode == 1 and "persona" in r.stdout
    lex.write_text("{}", encoding="utf-8")
    assert _cli(str(pages_dir), "--lexicon", str(lex)).returncode == 2


def test_transpose_page_is_fine(pages_dir, snap_v1, run_lint):
    assert run_lint(pages_dir, snap_v1).for_page(TRANSPOSE) == []


# ---------- 审查处理：个人信息检查不可绕过、ID 白名单、消息不回显原文 ----------

REVIEW_PII_ID = "王小明@南京一中13812345678"


def _add_ref(path, atype: str, aid: str) -> None:
    edit(path, "atom_refs:\n", f"atom_refs:\n  - {{type: {atype}, id: \"{aid}\", hash: sha256:0000000000000000}}\n")


def test_review_repro_pii_in_citation_key_blocks_without_snapshot(pages_dir, run_lint):
    """审查者的复现：叙述段 [@node:王小明@南京一中13812345678] + 头部同名 atom_ref，不给地图快照也必须阻断"""
    p = page_file(pages_dir, APPLY)
    _add_ref(p, "node", REVIEW_PII_ID)
    edit(p, "设未知数时没有写单位", f"设未知数时没有写单位 [@node:{REVIEW_PII_ID}]")
    report = run_lint(pages_dir)  # 没有快照
    assert report.blocked
    kinds = {i.check_type for i in report.for_page(APPLY) if i.severity == "block"}
    assert {"pii", "header", "link"} <= kinds
    assert any(i.check_type == "pii" and i.line == 2 for i in report.blocks)       # 头部
    assert any(i.check_type == "pii" and i.line != 2 for i in report.blocks)       # 正文（不剥引用）
    out = report.to_json()
    assert "王小明" not in out and "13812345678" not in out                          # 消息与明细都不回显


def test_pii_in_header_value_blocks_even_when_id_is_well_formed(pages_dir, run_lint):
    """头部所有值都扫：投影 ID 合白名单，但就是一个手机号"""
    _add_ref(page_file(pages_dir, APPLY), "projection", "tp-13812345678")
    report = run_lint(pages_dir)
    hits = [i for i in report.blocks if i.check_type == "pii"]
    assert hits and all(i.line == 2 and i.detail.get("field") == "atom_refs" for i in hits)
    assert "13812345678" not in report.to_json()


def test_pii_inside_quote_slot_blocks(pages_dir, run_lint):
    p = page_file(pages_dir, APPLY)
    edit(p, "设未知数时没有写单位", "设未知数时没有写单位 {{quote:13812345678}}")
    assert "pii" in checks_of(run_lint(pages_dir))


@pytest.mark.parametrize("atype,aid,ok", [
    ("node", "MATH.SYN.EQ.LINEAR", True), ("node", "math.syn.eq.linear", False), ("node", "MATH.SYN.王", False),
    ("edge", "MATH.A.B>prerequisite>MATH.A.C", True), ("curriculum", "2022/SYN-EQ-01", True), ("projection", "tp-0123abcd", True),
    ("projection", "教材第一页", False), ("region", "rp 1", False), ("affair", "x" * 201, False), ("affair", "x" * 200, True),
])
def test_atom_id_whitelist(atype, aid, ok):
    from knowledge.lint.checks import valid_atom_id
    assert valid_atom_id(atype, aid) is ok


def test_header_atom_id_outside_whitelist_blocks(pages_dir, run_lint):
    _add_ref(page_file(pages_dir, APPLY), "projection", "教材第一页")
    hits = [i for i in run_lint(pages_dir).blocks if i.check_type == "header"]
    assert any("id 不合" in i.message for i in hits) and all("教材第一页" not in i.message for i in hits)


def test_messages_never_echo_page_text(pages_dir, snap_v1, run_lint):
    """所有检查项的消息只给定位与结论：缺引用段、段落外内容、公式、失效链接、外部链接都不回显原文"""
    marker = "合成回显探针文字"
    p = page_file(pages_dir, APPLY)
    edit(p, "## 易错\n\n", f"## 易错\n\n{marker}没有引用。\n\n")
    edit(p, "# ", f"{marker}段落外\n\n# ")
    edit(p, "设未知数时没有写单位", f"设未知数时没有写单位 [[{marker}页]] [链接](https://{marker}.example) [相对](../{marker}.md)")
    text = p.read_text(encoding="utf-8").replace("## 讲解要点\n\n", f"## 讲解要点\n\n$$\n{marker}x = 1 % check\n$$\n\n", 1)
    p.write_text(text, encoding="utf-8")
    report = run_lint(pages_dir, snap_v1)
    assert {"citation", "sections", "link", "sympy"} <= {i.check_type for i in report.issues}
    assert marker not in report.to_json()


def test_cli_require_map_snapshot(pages_dir):
    r = _cli(str(pages_dir), "--require-map-snapshot")
    assert r.returncode == 2
    r = _cli(str(pages_dir), "--require-map-snapshot", "--map-snapshot", str(MAP_V1), "--today", TODAY.isoformat())
    assert r.returncode == 0, r.stdout + r.stderr
