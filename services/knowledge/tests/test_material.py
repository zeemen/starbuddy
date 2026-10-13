"""KGE-07 第一步：编译层素材卡（page_type = material）。全部合成数据：语文合成地图快照在本文件里构造，页面由编译器生成到临时目录。

素材卡段落（KGE 分册 :443）：词条（锁定：名称、释义要点、不包括）、原文（只放原句占位）、出处（册-课-页）、例句、易混项与关联、积累要点（叙述）。
"""
from __future__ import annotations

import asyncio
import copy
import datetime as dt
import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

from conftest import LINEAR, PAGES, checks_of, edit, make_page_snapshot, page_file
from knowledge.atoms import parse_snapshot
from knowledge.compiler.snapshot import ExportError, export_snapshot
from knowledge.pages.format import KP_SECTIONS
from knowledge.service.pages import SnapshotLoadError, load_snapshot_dir
from knowledge.compiler.full import full_recompile
from knowledge.compiler.jobs import full_job
from knowledge.compiler.output import compile_render, load_repo
from knowledge.compiler.rebuild import CST, CompileError, assemble
from knowledge.pages.format import MATERIAL_SECTIONS, PAGE_TYPES, RESERVED_PAGE_TYPES, SCHEMA_VERSION, Page, Section, parse, serialize

NOW = dt.datetime(2026, 10, 12, 10, 0, tzinfo=CST)
WORD_A = "CHN.SYN_ACC.SYN_WORD.WORD_A"
WORD_B = "CHN.SYN_ACC.SYN_WORD.WORD_B"
ABILITY = "CHN.SYN_READ.SYN_CLASSICAL.WORD_INFER_L1"
# 合成占位原句：只在快照的 quote_text 里，用来证明原句不进页面
QUOTE = "（合成）占位原句甲用于检查原句不进页面"

DOC: dict[str, Any] = {
    "map_version": "0.0.1", "subject": "wenli", "status": "published", "standard_year": 2022,
    "nodes": [
        {"id": "CHN.SYN_ACC", "level": "domain", "name": "（合成）积累与运用领域"},
        {"id": "CHN.SYN_ACC.SYN_WORD", "level": "topic", "parent_id": "CHN.SYN_ACC", "name": "（合成）文言词条"},
        {"id": WORD_A, "level": "node", "parent_id": "CHN.SYN_ACC.SYN_WORD", "kind": "material", "name": "（合成）词条甲",
         "includes": ["（合成）义项占位一", "（合成）义项占位二"], "excludes": ["（合成）词条乙的义项"], "examples": ["（合成）含词条甲的例句占位"]},
        {"id": WORD_B, "level": "node", "parent_id": "CHN.SYN_ACC.SYN_WORD", "kind": "material", "name": "（合成）词条乙",
         "includes": ["（合成）义项占位三"]},
        {"id": "CHN.SYN_READ", "level": "domain", "name": "（合成）阅读领域"},
        {"id": "CHN.SYN_READ.SYN_CLASSICAL", "level": "topic", "parent_id": "CHN.SYN_READ", "name": "（合成）文本类型丙阅读"},
        {"id": ABILITY, "level": "node", "parent_id": "CHN.SYN_READ.SYN_CLASSICAL", "kind": "ability_kp", "name": "（合成）文言理解一级",
         "includes": ["（合成）推断词条意思"], "excludes": ["（合成）整句翻译"], "examples": ["（合成）题例占位"], "cognitive_levels": ["understand"]},
    ],
    "edges": [
        {"from_id": WORD_A, "to_id": WORD_B, "type": "confusable", "strength": "strong"},
        {"from_id": WORD_A, "to_id": ABILITY, "type": "related", "strength": "strong"},
    ],
    "textbook_projections": [
        {"id": "syn-chn-p1", "node_id": WORD_A, "edition": "syn-tb-2024", "edition_year": 2024, "volume": "（合成）册甲",
         "chapter": "（合成）第3课", "page_from": 12, "page_to": 12, "quote_text": QUOTE, "mapping_state": "verified"},
        {"id": "syn-chn-p2", "node_id": WORD_B, "edition": "syn-tb-2024", "edition_year": 2024, "volume": "（合成）册乙",
         "chapter": "（合成）第7课", "page_from": 40, "page_to": 41, "mapping_state": "unverified"},
    ],
}
NOTES = {
    WORD_A: "词条甲常和词条乙一起考，先看所在句子的语境再判断义项。[@node:CHN.SYN_ACC.SYN_WORD.WORD_A]\n\n"
            "复习时把两个义项各配一个合成例句。[@edge:CHN.SYN_ACC.SYN_WORD.WORD_A>confusable>CHN.SYN_ACC.SYN_WORD.WORD_B]",
    WORD_B: "词条乙只有一个常考义项，按出处所在课次复习。[@node:CHN.SYN_ACC.SYN_WORD.WORD_B]",
}


def doc() -> dict[str, Any]:
    return copy.deepcopy(DOC)


def skeleton(node_id: str, page_type: str = "material", subject: str = "wenli") -> Page:
    specs = MATERIAL_SECTIONS if page_type == "material" else KP_SECTIONS
    return Page({"page_id": node_id, "page_type": page_type, "subject": subject, "node_id": node_id, "map_version": "0.0.1",
                 "schema_version": SCHEMA_VERSION, "page_version": "1.0.0", "risk_level": "low", "rebuild_kind": "render",
                 "locked_sections": [s.id for s in specs if s.kind == "locked"], "atom_refs": []}, "", ())


def write_cards(root: Path, notes: dict[str, str] = NOTES, snapshot_doc: dict[str, Any] | None = None) -> Path:
    snap = parse_snapshot(snapshot_doc or doc())
    pages = root / "pages"
    for nid, text in notes.items():
        narrative = {"study_notes": Section("study_notes", "narrative", f"## 积累要点\n\n{text}")}
        page = assemble(skeleton(nid), snap, narrative, set(notes))
        (pages / "material").mkdir(parents=True, exist_ok=True)
        (pages / "material" / f"{nid}.md").write_text(serialize(page), encoding="utf-8")
    return pages


def card(pages: Path, nid: str = WORD_A) -> Path:
    return pages / "material" / f"{nid}.md"


def test_material_is_an_implemented_page_type():
    assert "material" in PAGE_TYPES and "material" not in RESERVED_PAGE_TYPES
    assert [(s.id, s.kind) for s in MATERIAL_SECTIONS] == [("entry", "locked"), ("source_text", "render"), ("provenance", "render"),
                                                          ("examples", "render"), ("confusables", "render"), ("study_notes", "narrative")]


def test_card_renders_fields_and_keeps_source_text_as_placeholder(tmp_path):
    pages = write_cards(tmp_path)
    text = card(pages).read_text(encoding="utf-8")
    page = parse(text)
    assert serialize(page) == text, "规范写法逐字节往返"
    assert page.title == "（合成）词条甲"
    assert page.header["locked_sections"] == ["entry"] and page.header["page_type"] == "material"
    assert page.section("entry").content == ("## 词条\n\n**（合成）词条甲** [@node:CHN.SYN_ACC.SYN_WORD.WORD_A]\n\n"
                                             "释义要点：（合成）义项占位一；（合成）义项占位二。 [@node:CHN.SYN_ACC.SYN_WORD.WORD_A]\n\n"
                                             "不包括：（合成）词条乙的义项。 [@node:CHN.SYN_ACC.SYN_WORD.WORD_A]")
    assert page.section("source_text").content == "## 原文\n\n- {{quote:syn-chn-p1}} [@projection:syn-chn-p1]"
    assert page.section("provenance").content == "## 出处\n\n- syn-tb-2024（2024）（合成）册甲-（合成）第3课-第 12 页 [@projection:syn-chn-p1]"
    assert page.section("examples").content == "## 例句\n\n- （合成）含词条甲的例句占位 [@node:CHN.SYN_ACC.SYN_WORD.WORD_A]"
    assert page.section("confusables").content == (
        "## 易混项与关联\n\n易混：\n- [[CHN.SYN_ACC.SYN_WORD.WORD_B]] （合成）词条乙 [@edge:CHN.SYN_ACC.SYN_WORD.WORD_A>confusable>CHN.SYN_ACC.SYN_WORD.WORD_B]\n\n"
        "关联：\n- （合成）文言理解一级 [@edge:CHN.SYN_ACC.SYN_WORD.WORD_A>related>CHN.SYN_READ.SYN_CLASSICAL.WORD_INFER_L1]")
    assert QUOTE not in text, "原句只在快照里，不写进页面"
    refs = {f"{r.type}:{r.id}" for r in page.atom_refs}
    assert {"projection:syn-chn-p1", f"node:{WORD_A}"} <= refs
    # 没有例句、不包括、关联的素材：写暂无并引用节点
    b = parse(card(pages, WORD_B).read_text(encoding="utf-8"))
    assert b.section("examples").content == f"## 例句\n\n（暂无） [@node:{WORD_B}]"
    assert b.section("provenance").content.endswith("第 40–41 页 [@projection:syn-chn-p2]")
    assert "易混：\n- [[CHN.SYN_ACC.SYN_WORD.WORD_A]] （合成）词条甲" in b.section("confusables").content
    assert b.section("confusables").content.endswith(f"关联：\n- （暂无） [@node:{WORD_B}]")


def test_material_cards_lint_clean(tmp_path, run_lint):
    pages = write_cards(tmp_path)
    report = run_lint(pages, parse_snapshot(doc()), baseline_dir=pages)
    assert report.issues == () and report.pages == 2


@pytest.mark.parametrize("old,new,check", [
    # 原文段写进原句：教材原句检查与模板检查都阻断
    ("- {{quote:syn-chn-p1}} [@projection:syn-chn-p1]", f"- {QUOTE} [@projection:syn-chn-p1]", "textbook_quote"),
    ("- {{quote:syn-chn-p1}} [@projection:syn-chn-p1]", f"- {QUOTE} [@projection:syn-chn-p1]", "template"),
    # 人手改出处页码、改易混项
    ("（合成）第3课-第 12 页", "（合成）第3课-第 13 页", "template"),
    ("易混：\n- [[CHN.SYN_ACC.SYN_WORD.WORD_B]]", "易混：\n- [[CHN.SYN_ACC.SYN_WORD.WORD_B]] [[CHN.SYN_ACC.SYN_WORD.WORD_C]]", "link"),
    # 头部：锁定段登记不对、页面类型与文件位置
    ("locked_sections: [entry]", "locked_sections: [definition, curriculum]", "header"),
    ("page_type: material", "page_type: kp", "sections"),
    # 叙述段：缺引用、F:chinese 禁语（语文页面才查）、句长
    ("复习时把两个义项各配一个合成例句。[@edge:CHN.SYN_ACC.SYN_WORD.WORD_A>confusable>CHN.SYN_ACC.SYN_WORD.WORD_B]", "复习时把两个义项各配一个合成例句。", "citation"),
    ("先看所在句子的语境再判断义项。", "先看所在句子的语境，多读书就好了。", "forbidden_phrase"),
    ("先看所在句子的语境再判断义项。", "先看所在句子的语境" + "再判断义项" * 12 + "。", "sentence_length"),
    # 个人信息
    ("先看所在句子的语境再判断义项。", "先看所在句子的语境再判断义项，联系 13812345678。", "pii"),
])
def test_material_card_checks(tmp_path, run_lint, old, new, check):
    pages = write_cards(tmp_path)
    edit(card(pages), old, new)
    report = run_lint(pages, parse_snapshot(doc()))
    assert check in checks_of(report), [(i.check_type, i.message) for i in report.issues]


def test_forbidden_f_chinese_only_on_wenli_pages(tmp_path, run_lint):
    """F:chinese 禁语只对语文页面生效：同一句话放进数学知识点页不报 F 类（A–E、G 类对所有页面都查，另有测试）"""
    pages = write_cards(tmp_path, {WORD_A: NOTES[WORD_A].replace("先看所在句子的语境再判断义项。", "语言再优美些。"), WORD_B: NOTES[WORD_B]})
    report = run_lint(pages, parse_snapshot(doc()))
    hits = [i for i in report.blocks if i.check_type == "forbidden_phrase"]
    assert hits and all(i.detail["rule"].startswith("F-wenli-") for i in hits)
    kp = tmp_path / "kp-pages"
    shutil.copytree(PAGES, kp)
    edit(page_file(kp, LINEAR), "去括号时括号前是负号", "语言再优美些。去括号时括号前是负号")
    assert "forbidden_phrase" not in checks_of(run_lint(kp))


def test_material_card_must_hang_on_material_node(tmp_path, run_lint):
    snap = parse_snapshot(doc())
    pages = write_cards(tmp_path)
    narrative = {"study_notes": Section("study_notes", "narrative", f"## 积累要点\n\n能力点不挂素材卡。[@node:{ABILITY}]")}
    wrong = assemble(skeleton(ABILITY), snap, narrative, set(NOTES) | {ABILITY})
    (pages / "material" / f"{ABILITY}.md").write_text(serialize(wrong), encoding="utf-8")
    report = run_lint(pages, snap)
    assert any(i.check_type == "orphan" and "挂不了素材卡" in i.message and i.page_id == ABILITY for i in report.blocks)
    # 素材卡挂在素材节点上不报孤立页（WORD_A 的关联另一端多了页面、链接写法变了，只报模板）
    assert not any(i.check_type == "orphan" and i.page_id in (WORD_A, WORD_B) for i in report.blocks)


def test_kp_page_on_material_node_is_orphan(tmp_path, run_lint):
    pages = write_cards(tmp_path)
    edit(card(pages), "page_type: material", "page_type: kp")
    (pages / "kp").mkdir()
    card(pages).rename(pages / "kp" / f"{WORD_A}.md")
    report = run_lint(pages, parse_snapshot(doc()))
    assert any(i.check_type == "orphan" and "挂不了知识点页" in i.message for i in report.blocks)


def test_render_rebuild_of_material_cards(tmp_path):
    pages = write_cards(tmp_path)
    repo = load_repo(pages)
    old = parse_snapshot(doc())
    # 1. 易混另一端改名：只改 WORD_A 卡的“易混项与关联”（渲染段，低风险）；叙述段逐字保留
    d = doc()
    d["nodes"][3]["name"] = "（合成）词条乙改名"
    jobs, results = compile_render(repo, old, parse_snapshot(d), NOW)
    by = {r.page_id: r for r in results}
    assert WORD_A in by and by[WORD_A].changed_sections == ("confusables",) and by[WORD_A].risk == "low"
    assert by[WORD_A].new_page.section("study_notes") == repo.files[WORD_A][1].section("study_notes")
    assert by[WORD_A].new_page.page_version == "1.0.1"
    # 2. 出处页码变：原文占位不变，出处段变，高风险
    d = doc()
    d["textbook_projections"][0]["page_to"] = 13
    _, results = compile_render(repo, old, parse_snapshot(d), NOW)
    r = {x.page_id: x for x in results}[WORD_A]
    assert r.risk == "high" and "provenance" in r.changed_sections and "教材投影（页码或版次）变更（高风险）" in r.reasons
    assert r.new_page.page_version == "1.1.0"
    # 3. 只改原句（原句不在页面里）：页面只有哈希变化，仍按高风险须核对
    d = doc()
    d["textbook_projections"][0]["quote_text"] = QUOTE + "改"
    _, results = compile_render(repo, old, parse_snapshot(d), NOW)
    r = {x.page_id: x for x in results}[WORD_A]
    assert r.risk == "high" and r.changed_sections == () and any("原句" in x for x in r.reasons)
    # 4. 释义要点（锁定段）变：高风险
    d = doc()
    d["nodes"][2]["includes"] = ["（合成）义项占位一"]
    _, results = compile_render(repo, old, parse_snapshot(d), NOW)
    r = {x.page_id: x for x in results}[WORD_A]
    assert r.risk == "high" and r.changed_sections == ("entry",)


def test_link_neighbors_rebuild_only_material_cards(tmp_path):
    """易混、关联另一端改名：只重编挂素材卡的邻居；挂知识点页的邻居保持原行为（知识点页只渲染前置与后续）"""
    snap = parse_snapshot(doc())
    pages = write_cards(tmp_path)
    narrative = {s.id: Section(s.id, "narrative", f"## {s.title}\n\n（合成）叙述占位。[@node:{ABILITY}]") for s in KP_SECTIONS if s.kind == "narrative"}
    kp = assemble(skeleton(ABILITY, "kp"), snap, narrative, set(NOTES) | {ABILITY})
    (pages / "kp").mkdir()
    (pages / "kp" / f"{ABILITY}.md").write_text(serialize(kp), encoding="utf-8")
    repo = load_repo(pages)
    d = doc()
    d["nodes"][2]["name"] = "（合成）词条甲改名"  # WORD_A：与 WORD_B 易混（素材卡），与 ABILITY 关联（知识点页）
    jobs, _ = compile_render(repo, snap, parse_snapshot(d), NOW)
    got = {j.page_id for j in jobs}
    assert WORD_B in got, "易混另一端的素材卡要重编"
    assert ABILITY not in got, "关联另一端的知识点页不因改名而重编"


# ---------- 现行行为锁定：快照导出与 page.get 只收知识点页（素材卡发布任务再改这两条，并同步改测试） ----------

def _git(repo: Path, *args: str) -> str:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_AUTHOR_NAME="synthetic", GIT_AUTHOR_EMAIL="synthetic@example.invalid", GIT_COMMITTER_NAME="synthetic",
               GIT_COMMITTER_EMAIL="synthetic@example.invalid", GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_NOSYSTEM="1")
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True, env=env).stdout.strip()


def test_snapshot_export_rejects_material_cards_for_now(tmp_path):
    """pages/material 存在时导出报 ExportError（页面路径不合规范）：素材卡暂不发布到知识服务，待素材卡发布任务再改"""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    write_cards(repo)
    (repo / "snapshots").mkdir()
    (repo / "snapshots" / "current.yaml").write_text(yaml.safe_dump(doc(), allow_unicode=True, sort_keys=True), encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "合成：两张素材卡")
    with pytest.raises(ExportError, match="页面路径不合规范：pages/material/"):
        export_snapshot(repo, "main", tmp_path / "snap", today=dt.date(2026, 10, 12))
    assert not (tmp_path / "snap").exists()


def _add_entry(root: Path, rel: str, text: str, page_id: str) -> None:
    raw = text.encode("utf-8")
    (root / rel).parent.mkdir(parents=True, exist_ok=True)
    (root / rel).write_bytes(raw)
    m = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    m["pages"].append({"page_id": page_id, "path": rel, "page_version": "1.0.0", "map_version": "0.0.1",
                       "sha256": hashlib.sha256(raw).hexdigest()})
    (root / "manifest.json").write_text(json.dumps(m), encoding="utf-8")


def test_page_get_snapshot_loader_only_takes_kp_pages_for_now(tmp_path, lexicon):
    """知识服务载入已发布快照只收 pages/kp/ 下、头部 page_type = kp 的页面（page.get 返回形状因此不变），待素材卡发布任务再改"""
    cards = write_cards(tmp_path / "src")
    kp_text = page_file(PAGES, LINEAR).read_text(encoding="utf-8")
    # 1. 路径在 pages/material/ 下（内容是合格的知识点页）：拒绝
    a = make_page_snapshot(tmp_path / "a")
    _add_entry(a, f"pages/material/{LINEAR}.md", kp_text, LINEAR)
    with pytest.raises(SnapshotLoadError, match="取值不合规范"):
        load_snapshot_dir(a, lexicon)
    # 2. 路径合规但页面是素材卡：拒绝
    b = make_page_snapshot(tmp_path / "b")
    _add_entry(b, f"pages/kp/{WORD_A}.md", card(cards).read_text(encoding="utf-8"), WORD_A)
    with pytest.raises(SnapshotLoadError, match="与页面头部不一致"):
        load_snapshot_dir(b, lexicon)
    # 对照：只有知识点页的快照照常载入
    assert len(load_snapshot_dir(make_page_snapshot(tmp_path / "c"), lexicon).pages) == 4


def test_full_recompile_rejects_material_cards(tmp_path):
    pages = write_cards(tmp_path)
    repo = load_repo(pages)
    rel, page, text = repo.files[WORD_A]
    snap = parse_snapshot(doc())
    job = full_job(page, snap, "codification_changed")
    with pytest.raises(CompileError, match="暂不支持全量重编"):
        asyncio.run(full_recompile(job, rel, page, text, snap, repo.page_ids, object(), NOW))
