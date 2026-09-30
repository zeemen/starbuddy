"""冲刺 5-K：KGE M2 报表（核心节点内容完整率 + 数学、道法准入汇总）。数字与手算核对；全部是合成数据（规则 8）。

合成夹具在 tests/fixtures/m2/<学科>/：核心节点清单、题目清单（道法另有地图快照；数学地图直接用 ../map-v1.yaml）；
pass/ 与 fail/ 是两个场景的其余输入。
页面快照、M1 报告与体检 JSON 在测试里现生成（页面用编译器的 assemble 渲染，叙述段是合成的中性文本）。
"""
from __future__ import annotations

import copy
import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

import pytest
import yaml

from conftest import FIXTURES, MAP_V1, TODAY
from knowledge import m1
from knowledge.atoms import Snapshot, read_structured
from knowledge.compiler.rebuild import assemble
from knowledge.lint import lint_dir
from knowledge.m2 import (NOT_TAGGED, M2InputError, Source, build_report, core_completeness, main, parse_core_nodes, parse_map,
                          render_markdown,
                          resolve_inputs, write_report)
from knowledge.pages.format import KP_SECTIONS, SCHEMA_VERSION, Page, Section, serialize

M2 = FIXTURES / "m2"
T, L, A, S = "MATH.SYN.EQ.TRANSPOSE", "MATH.SYN.EQ.LINEAR", "MATH.SYN.EQ.APPLY", "MATH.SYN.EXPR.SQUARE_DIFF"
RIGHTS, DUTIES, RULE, EXTRA = "MORAL.SYN.LAW.RIGHTS", "MORAL.SYN.LAW.DUTIES", "MORAL.SYN.LAW.RULE", "MORAL.SYN.LAW.EXTRA"


# ---------- 合成输入的生成 ----------
def map_path(subject: str) -> Path:
    return MAP_V1 if subject == "aya" else M2 / subject / "map-snapshot.yaml"


def map_doc(subject: str) -> dict[str, Any]:
    return copy.deepcopy(read_structured(map_path(subject)))


def core_doc(subject: str) -> dict[str, Any]:
    return copy.deepcopy(read_structured(M2 / subject / "core-nodes.yaml"))


def snap_core(doc: dict[str, Any], subject: str = "aya") -> tuple[Snapshot, list[str]]:
    snap = parse_map(doc)
    return snap, parse_core_nodes(core_doc(subject), subject, snap)


def items_doc(subject: str) -> dict[str, Any]:
    return copy.deepcopy(read_structured(M2 / subject / "items.json"))


def build_pages(root: Path, snap: Snapshot, node_ids: list[str], *, stale: tuple[str, ...] = (),
                commit: str = "e" * 40) -> Path:
    """按 knowledge.compiler.snapshot 的导出格式生成已发布页面快照；stale 里的页面头部与 manifest 写落后的地图版本"""
    (root / "pages" / "kp").mkdir(parents=True)
    entries = []
    for nid in sorted(node_ids):
        narrative = {s.id: Section(s.id, "narrative", f"## {s.title}\n\n合成的中性讲解文本。[@node:{nid}]")
                     for s in KP_SECTIONS if s.kind == "narrative"}
        skeleton = Page({"page_id": nid, "page_type": "kp", "subject": snap.subject, "node_id": nid, "map_version": snap.map_version,
                         "schema_version": SCHEMA_VERSION, "page_version": "1.0.0", "risk_level": "low", "rebuild_kind": "render",
                         "locked_sections": [s.id for s in KP_SECTIONS if s.kind == "locked"], "atom_refs": []}, "", ())
        text = serialize(assemble(skeleton, snap, narrative, set(node_ids)))
        mv = snap.map_version
        if nid in stale:
            mv = "0.0.1"
            text = text.replace(f"map_version: {snap.map_version}", f"map_version: {mv}", 1)
        raw = text.encode("utf-8")
        (root / "pages" / "kp" / f"{nid}.md").write_bytes(raw)
        entries.append({"page_id": nid, "path": f"pages/kp/{nid}.md", "page_version": "1.0.0", "map_version": mv,
                        "sha256": hashlib.sha256(raw).hexdigest()})
    (root / "manifest.json").write_text(json.dumps({"format": "kge-page-snapshot/1", "commit": commit, "pages": entries}),
                                        encoding="utf-8")
    return root


def page_map(snap: Snapshot, node_ids: list[str], stale: tuple[str, ...] = ()) -> dict[str, str]:
    return {n: ("0.0.1" if n in stale else snap.map_version) for n in node_ids}


SCENARIOS: dict[str, dict[str, Any]] = {
    # 全部达标：数学 4 个核心节点齐全；道法 3 个核心节点齐全（非核心的 EXTRA 有页面——M1 的知识点页发布率要 100%——但没有已发布题，
    # 不影响完整率）
    "aya-pass": {"subject": "aya", "items": "aya/items.json", "pages": [T, L, A, S], "stale": (), "m1": True},
    "zhixing-pass": {"subject": "zhixing", "items": "zhixing/items.json", "pages": [RIGHTS, DUTIES, RULE, EXTRA], "stale": (),
                     "m1": True},
    # 多项未达标：数学缺题、页面落后、kappa 低、Top-1 缺、比对可比项 0、留出集恰好 2%、影子评审 1 人、C 级约束未通过、
    # 同意流程未上线、安全测试未填
    "aya-fail": {"subject": "aya", "items": "aya/fail/items.json", "pages": [T, L, A, S], "stale": (A,), "m1": True},
    # 缺输入：道法没有题目清单、没有 M1 报告、没有 kappa 与比对报告；打标报告地图版本不一致；一致率 85%；准入记录只填两节
    "zhixing-fail": {"subject": "zhixing", "items": None, "pages": [RIGHTS, RULE], "stale": (), "m1": False},
}


def scenario_inputs(tmp: Path, name: str, lexicon) -> Path:
    sc = SCENARIOS[name]
    subject, part = sc["subject"], name.split("-")[1]
    d = tmp / f"in-{name}"
    d.mkdir()
    shutil.copy(map_path(subject), d / "map-snapshot.yaml")
    shutil.copy(M2 / subject / "core-nodes.yaml", d / "core-nodes.yaml")
    if sc["items"]:
        shutil.copy(M2 / sc["items"], d / "items.json")
    for f in sorted((M2 / subject / part).iterdir()):
        if f.name == "items.json":
            continue
        (shutil.copytree if f.is_dir() else shutil.copy)(f, d / f.name)
    snap = parse_map(map_doc(subject))
    build_pages(d / "page-snapshot", snap, sc["pages"], stale=sc["stale"])
    if sc["m1"]:
        lint = json.loads(lint_dir(d / "page-snapshot" / "pages", snapshot=snap, lexicon=lexicon, today=TODAY).to_json())
        rep = m1.build_report(map_doc(subject), page_snapshot_dir=d / "page-snapshot", lint_report=lint)
        (d / "m1-report.json").write_text(json.dumps(rep, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    return d


def run(tmp: Path, name: str, lexicon) -> dict[str, Any]:
    d = scenario_inputs(tmp, name, lexicon)
    return build_report(SCENARIOS[name]["subject"], resolve_inputs(d, {}))


def verdicts(rep: dict[str, Any]) -> dict[str, str]:
    return {i["key"]: i["verdict"] for i in rep["items"]}


def item(rep: dict[str, Any], key: str) -> dict[str, Any]:
    return next(i for i in rep["items"] if i["key"] == key)


# ---------- 核心节点内容完整率：与手算核对 ----------
def test_completeness_all_complete():
    snap, core = snap_core(map_doc("aya"))
    items = items_doc("aya")["items"]
    r = core_completeness(snap, core, items, page_map(snap, [T, L, A, S]))
    # 4 个核心节点：TRANSPOSE、LINEAR、SQUARE_DIFF 各 2 层都有已发布题，APPLY 1 层有；页面全是 0.9.0 → 4 / 4
    assert (r["completeness"].num, r["completeness"].den, r["completeness"].percent) == (4, 4, "100.0%")
    assert (r["page_rate"].num, r["page_rate"].den) == (4, 4) and r["gaps"] == []
    assert r["core_published_items_without_level"] == 1  # SYN-M2-09 未标层次，单列不计


def test_completeness_missing_level_item():
    """缺层次：TRANSPOSE 的 apply 层没有题 → 3 / 4 = 75.0%，缺口只列该节点 × 层次"""
    snap, core = snap_core(map_doc("aya"))
    items = [i for i in items_doc("aya")["items"] if i["item_id"] != "SYN-M2-02"]
    r = core_completeness(snap, core, items, page_map(snap, [T, L, A, S]))
    assert (r["completeness"].num, r["completeness"].den, r["completeness"].percent) == (3, 4, "75.0%")
    assert r["gaps"] == [{"node_id": T, "level": "apply", "gap": "no_published_item"}]


def test_completeness_unpublished_items_do_not_count():
    """缺题：题在但未发布（admission 不是 admitted，或标签未审核通过）不算；SQUARE_DIFF 两层都缺 → 3 / 4"""
    snap, core = snap_core(map_doc("aya"))
    items = items_doc("aya")["items"]
    for i in items:
        if i["item_id"] == "SYN-M2-06":
            i["tag_review_status"] = "pending"
        if i["item_id"] == "SYN-M2-07":
            i["admission"] = "source_unverified"
    r = core_completeness(snap, core, items, page_map(snap, [T, L, A, S]))
    assert (r["completeness"].num, r["completeness"].den) == (3, 4)
    assert r["gaps"] == [{"node_id": S, "level": "understand", "gap": "no_published_item"},
                         {"node_id": S, "level": "apply", "gap": "no_published_item"}]
    # 题目挂在别的节点上（node_ids 不算，只看主考点）也不算
    items = [dict(i, main_node_id=L) if i["item_id"] == "SYN-M2-05" else i for i in items_doc("aya")["items"]]
    r = core_completeness(snap, core, items, page_map(snap, [T, L, A, S]))
    assert r["gaps"] == [{"node_id": A, "level": "apply", "gap": "no_published_item"}]


def test_completeness_page_stale_and_missing():
    """页面版本落后：不算完整、单列 page_stale；没有页面：no_page。两者都进页面发布率的分母不进分子"""
    snap, core = snap_core(map_doc("aya"))
    items = items_doc("aya")["items"]
    r = core_completeness(snap, core, items, page_map(snap, [T, L, A, S], stale=(A,)))
    assert (r["completeness"].num, r["page_rate"].num, r["page_rate"].den) == (3, 3, 4)
    assert r["gaps"] == [{"node_id": A, "level": None, "gap": "page_stale"}]
    r = core_completeness(snap, core, items, page_map(snap, [T, L, S]))
    assert r["gaps"] == [{"node_id": A, "level": None, "gap": "no_page"}] and r["page_rate"].percent == "75.0%"


def test_completeness_node_without_levels_and_deprecated_core():
    snap_doc = map_doc("aya")
    for n in snap_doc["nodes"]:
        if n["id"] == A:
            del n["cognitive_levels"]      # 核心节点没声明认知层次：不能算完整
        if n["id"] == S:
            n["status"] = "deprecated"     # 已撤下的核心节点不进分母
    snap, core = snap_core(snap_doc)
    r = core_completeness(snap, core, items_doc("aya")["items"], page_map(snap, [T, L, A, S]))
    assert (r["completeness"].num, r["completeness"].den, r["completeness"].percent) == (2, 3, "66.7%")
    assert r["gaps"] == [{"node_id": A, "level": None, "gap": "no_cognitive_levels"}]


def test_completeness_not_computable_without_core_list(tmp_path, lexicon):
    snap = parse_map(map_doc("aya"))
    r = core_completeness(snap, None, items_doc("aya")["items"], page_map(snap, [T, L, A, S]))
    assert r == {"status": "not_computable", "note": NOT_TAGGED}
    assert core_completeness(snap, [], [], {})["note"] == NOT_TAGGED
    # 报表：没有 core-nodes 文件 → 两项“不可计算：核心节点未标注”（不推算、不当作 0）
    d = scenario_inputs(tmp_path, "aya-pass", lexicon)
    (d / "core-nodes.yaml").unlink()
    rep = build_report("aya", resolve_inputs(d, {}))
    for k in ("core.completeness", "core.page_publish_rate"):
        assert item(rep, k)["verdict"] == "not_computable" and item(rep, k)["note"] == NOT_TAGGED and item(rep, k)["value"] is None


def test_map_snapshot_stays_whitelist_format():
    """核心节点标注不进地图快照：快照里写 tags 按 knowledge.atoms 的白名单拒收（退出 2）"""
    doc = map_doc("aya")
    doc["nodes"][3]["tags"] = ["core"]
    with pytest.raises(M2InputError):
        parse_map(doc)


@pytest.mark.parametrize("mutate", [
    lambda d: d.update(map_version="0.8.0"),              # 与快照版本不一致
    lambda d: d.update(map_version="v0.9"),               # 不合 主.次.修订
    lambda d: d.update(subject="zhixing"),
    lambda d: d["node_ids"].append("MATH.SYN.EQ.NOPE"),   # 快照里没有
    lambda d: d["node_ids"].append(d["node_ids"][0]),     # 重复
    lambda d: d["node_ids"].append("math.bad"),
    lambda d: d.update(signed_by="某老师"),               # 多余字段
    lambda d: d.update(format="other"),
])
def test_core_nodes_format_errors(mutate):
    doc = core_doc("aya")
    mutate(doc)
    with pytest.raises(M2InputError):
        parse_core_nodes(doc, "aya", parse_map(map_doc("aya")))


def test_completeness_threshold_95_percent_boundary():
    """门槛 ≥ 95%：19 / 20 达到，18 / 20 未达到（精确分数，不经浮点）"""
    doc = map_doc("aya")
    doc["nodes"] = [n for n in doc["nodes"] if n.get("level") != "node"]
    doc["curriculum_links"], doc["edges"], doc["textbook_projections"], doc["region_projections"] = [], [], [], []
    ids = [f"MATH.SYN.EQ.N{i:02d}" for i in range(20)]
    doc["nodes"] += [{"id": nid, "level": "node", "parent_id": "MATH.SYN.EQ", "kind": "kp", "name": "合成",
                      "cognitive_levels": ["apply"]} for nid in ids]
    core = {"format": "kge-m2-core-nodes/1", "subject": "aya", "map_version": "0.9.0", "node_ids": ids}
    items = [{"item_id": f"SYN-B-{i:02d}", "main_node_id": nid, "cognitive_level": "apply", "admission": "admitted",
              "tag_review_status": "approved"} for i, nid in enumerate(ids)]
    tmp = {"format": "kge-m2-items/1", "subject": "aya", "map_version": "0.9.0"}
    for n_ok, want in ((19, "met"), (18, "not_met")):
        snap = parse_map(doc)
        pages = page_map(snap, ids[:n_ok])
        src = {"map_snapshot": Source("map_snapshot", "0" * 64, doc), "core_nodes": Source("core_nodes", "3" * 64, core),
               "items": Source("items", "1" * 64, {**tmp, "items": items})}
        from knowledge import m2 as m2mod
        orig = m2mod.load_pages
        m2mod.load_pages = lambda _src, pages=pages: (pages, "e" * 40)  # 页面快照的读取另有测试；这里只核对门槛
        try:
            rep = build_report("aya", {**src, "page_snapshot": Source("page_snapshot", "2" * 64)})
        finally:
            m2mod.load_pages = orig
        assert item(rep, "core.completeness")["verdict"] == want
        assert item(rep, "core.completeness")["detail"]["numerator"] == n_ok


def test_viewpoint_core_nodes_flagged():
    """道法导入器写的 kind = viewpoint：首期页面层只有知识点页（体检报孤立页），报表在两项的说明里点出来（lessons 待人确认 A1）"""
    doc = map_doc("zhixing")
    for n in doc["nodes"]:
        if n.get("kind") == "kp":
            n["kind"] = "viewpoint"
    items = items_doc("zhixing")
    src = {"map_snapshot": Source("map_snapshot", "0" * 64, doc), "items": Source("items", "1" * 64, items),
           "core_nodes": Source("core_nodes", "3" * 64, core_doc("zhixing"))}
    rep = build_report("zhixing", src)  # 没有页面快照：两项待填，但完整率不会被当成 0
    assert item(rep, "core.completeness")["verdict"] == "pending"
    snap, core = snap_core(doc, "zhixing")
    r = core_completeness(snap, core, items["items"], {})
    assert r["core_nodes_not_kp"] == [DUTIES, RIGHTS, RULE] and r["completeness"].num == 0
    from knowledge import m2 as m2mod
    orig = m2mod.load_pages
    m2mod.load_pages = lambda _src: ({}, "e" * 40)
    try:
        rep = build_report("zhixing", {**src, "page_snapshot": Source("page_snapshot", "2" * 64)})
    finally:
        m2mod.load_pages = orig
    assert "不是 kp" in item(rep, "core.completeness")["note"] and "不是 kp" in item(rep, "core.page_publish_rate")["note"]


# ---------- 汇总报表：合成端到端 ----------
def test_aya_pass_all_met(tmp_path, lexicon):
    rep = run(tmp_path, "aya-pass", lexicon)
    assert set(verdicts(rep).values()) == {"met"}, verdicts(rep)
    assert rep["open_items"] == {"not_met": [], "not_computable": [], "pending": []}
    assert "daofa.point_agreement" not in verdicts(rep)  # 一致率只用于道法
    assert item(rep, "core.completeness")["value"] == "100.0%（4 / 4）"
    assert item(rep, "holdout.untaggable_rate")["value"] == "1.0%（1 / 100）"
    assert item(rep, "tagging.kappa")["value"] == "0.86（共同标注 20 题）"
    assert item(rep, "shadow.staffing")["value"] == "2 人；排班覆盖 5 / 5 周"
    assert rep["map"]["page_snapshot_commit"] == "e" * 40


def test_zhixing_pass_all_met(tmp_path, lexicon):
    """全部达到，含两个恰好在门槛上的值：Top-1 = 0.9、逐点一致率 18 / 20 = 90%"""
    rep = run(tmp_path, "zhixing-pass", lexicon)
    assert set(verdicts(rep).values()) == {"met"}, verdicts(rep)
    assert rep["open_items"] == {"not_met": [], "not_computable": [], "pending": []}
    assert item(rep, "daofa.point_agreement")["value"] == "90.0%（18 / 20）"
    assert item(rep, "core.completeness")["value"] == "100.0%（3 / 3）"  # EXTRA 不是核心节点


def test_aya_fail_lists_every_unmet_item(tmp_path, lexicon):
    rep = run(tmp_path, "aya-fail", lexicon)
    assert rep["open_items"] == {
        "not_met": ["m1.kp_page_publish_rate", "m1.lint_blocks", "core.completeness", "core.page_publish_rate", "tagging.kappa",
                    "holdout.untaggable_rate", "shadow.staffing", "shadow.compare_flow", "c_level.no_conclusion",
                    "guardian.consent_deletion"],
        "not_computable": [],
        "pending": ["tagging.top1", "security.pentest"],
    }
    # 手算：TRANSPOSE 缺 apply、SQUARE_DIFF 的 understand 题标签待审、APPLY 页面落后 → 只有 LINEAR 完整，1 / 4
    assert item(rep, "core.completeness")["value"] == "25.0%（1 / 4）"
    assert item(rep, "core.page_publish_rate")["value"] == "75.0%（3 / 4）"
    assert rep["core_gaps"] == [{"node_id": A, "level": None, "gap": "page_stale"},
                                {"node_id": T, "level": "apply", "gap": "no_published_item"},
                                {"node_id": S, "level": "understand", "gap": "no_published_item"}]
    assert item(rep, "holdout.untaggable_rate")["value"] == "2.0%（2 / 100）"  # 恰好 2% 不达标（严格小于）
    md = render_markdown(rep)
    assert "- 未达到（10）：" in md and "- 待填（2）：打标主考点 Top-1（金标集）、安全测试通过" in md
    assert f"| {T} | apply | 该层次没有已发布题目 |" in md


def test_zhixing_fail_missing_inputs_are_pending_not_zero(tmp_path, lexicon):
    rep = run(tmp_path, "zhixing-fail", lexicon)
    v = verdicts(rep)
    assert rep["open_items"] == {
        "not_met": ["daofa.point_agreement", "shadow.staffing"],
        "not_computable": ["tagging.top1"],
        "pending": ["m1.curriculum_coverage", "m1.kp_page_publish_rate", "m1.lint_blocks", "core.completeness",
                    "core.page_publish_rate", "tagging.kappa", "holdout.untaggable_rate", "shadow.compare_flow",
                    "c_level.no_conclusion", "guardian.consent_deletion", "security.pentest"],
    }
    assert v["core.completeness"] == "pending" and item(rep, "core.completeness")["value"] is None
    assert "items" in item(rep, "core.completeness")["note"]
    for k in ("m1.curriculum_coverage", "tagging.kappa", "security.pentest"):
        assert item(rep, k)["value"] is None and item(rep, k)["note"].startswith("待填")
    assert "0.2.0" in item(rep, "tagging.top1")["note"]  # 打标报告的地图版本与本版本不一致
    assert item(rep, "holdout.untaggable_rate")["note"].startswith("待填：执行人未签字")
    md = render_markdown(rep)
    assert "| 核心节点内容完整率 | ≥ 95% | — | 待填 |" in md and "| 核心节点页面发布率 | 100% | — | 待填 |" in md


def test_deterministic_and_no_student_data_or_paths(tmp_path, lexicon):
    for name in SCENARIOS:
        d = scenario_inputs(tmp_path, name, lexicon)
        subject = SCENARIOS[name]["subject"]
        assert main(["--subject", subject, "--inputs", str(d), "--out", str(tmp_path / f"o1-{name}")]) == 0
        # 输入复制到另一处（不同的本机路径）再跑：逐字节相同
        d2 = tmp_path / f"copy-{name}"
        shutil.copytree(d, d2)
        assert main(["--subject", subject, "--inputs", str(d2), "--out", str(tmp_path / f"o2-{name}")]) == 0
        for f in (f"m2-{subject}.md", f"m2-{subject}.json"):
            b1, b2 = (tmp_path / f"o1-{name}" / f).read_bytes(), (tmp_path / f"o2-{name}" / f).read_bytes()
            assert b1 == b2
            text = b1.decode("utf-8")
            assert str(tmp_path) not in text and "/home/" not in text and "/tmp" not in text
            assert "student_ref" not in text and "00000000-0000-4000-8000" not in text  # 比对报告里的学生编码不进报表
            assert "SYN-M2-" not in text and "SYN-D2-" not in text  # 缺口清单只含节点 ID，不列题目
            assert "2026" not in text  # 不含时间


def test_source_hashes(tmp_path, lexicon):
    d = scenario_inputs(tmp_path, "aya-pass", lexicon)
    rep = build_report("aya", resolve_inputs(d, {}))
    sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()  # noqa: E731
    assert item(rep, "tagging.kappa")["sources"] == [{"input": "kappa_report", "sha256": sha(d / "kappa-report.json")}]
    assert item(rep, "core.completeness")["sources"] == [
        {"input": "map_snapshot", "sha256": sha(d / "map-snapshot.yaml")},
        {"input": "core_nodes", "sha256": sha(d / "core-nodes.yaml")}, {"input": "items", "sha256": sha(d / "items.json")},
        {"input": "page_snapshot", "sha256": sha(d / "page-snapshot" / "manifest.json")}]
    g = item(rep, "guardian.consent_deletion")["sources"]
    assert g == [{"input": "attestation", "sha256": sha(d / "attestation.yaml")},
                 {"input": "evidence:evidence/consent-flow.txt", "sha256": sha(d / "evidence" / "consent-flow.txt")},
                 {"input": "evidence:evidence/sie03-deletion-e2e.txt", "sha256": sha(d / "evidence" / "sie03-deletion-e2e.txt")}]
    md = render_markdown(rep)
    assert f"| kappa_report | {sha(d / 'kappa-report.json')} |" in md
    assert f"| evidence:evidence/pentest-round2.txt | {sha(d / 'evidence' / 'pentest-round2.txt')} |" in md
    # 改一个字节 → 哈希变
    (d / "kappa-report.json").write_text((d / "kappa-report.json").read_text(encoding="utf-8") + " ", encoding="utf-8")
    assert item(build_report("aya", resolve_inputs(d, {})), "tagging.kappa")["sources"][0]["sha256"] != \
        item(rep, "tagging.kappa")["sources"][0]["sha256"]


def test_attestation_evidence_required(tmp_path, lexicon):
    """状态填了“通过”但没有证据文件 → 待填，不当作达到；证据路径只收相对路径"""
    d = scenario_inputs(tmp_path, "aya-pass", lexicon)
    att = yaml.safe_load((d / "attestation.yaml").read_text(encoding="utf-8"))
    att["security"] = {"pentest_round2": "passed"}
    (d / "attestation.yaml").write_text(yaml.safe_dump(att, allow_unicode=True), encoding="utf-8")
    rep = build_report("aya", resolve_inputs(d, {}))
    assert item(rep, "security.pentest")["verdict"] == "pending" and "证据" in item(rep, "security.pentest")["note"]
    for bad in ("/etc/passwd", "../outside.txt", "evidence/missing.txt"):
        att["security"] = {"pentest_round2": "passed", "evidence": [bad]}
        (d / "attestation.yaml").write_text(yaml.safe_dump(att, allow_unicode=True), encoding="utf-8")
        with pytest.raises(M2InputError):
            build_report("aya", resolve_inputs(d, {}))


@pytest.mark.parametrize("mutate", [
    lambda d: d.update(roster=["T1"]),                                          # 名册之类的未知节
    lambda d: d["shadow_review"].update(names=["某老师"]),                      # 节内未知字段
    lambda d: d["holdout"].update(total=0),
    lambda d: d["holdout"].update(untaggable=-1),
    lambda d: d["shadow_review"].update(weeks_required=0),
    lambda d: d.update(subject="zhixing"),
    lambda d: d["guardian"].update(consent_flow="maybe"),
])
def test_attestation_format_errors_exit_2(tmp_path, lexicon, mutate):
    d = scenario_inputs(tmp_path, "aya-pass", lexicon)
    att = yaml.safe_load((d / "attestation.yaml").read_text(encoding="utf-8"))
    mutate(att)
    (d / "attestation.yaml").write_text(yaml.safe_dump(att, allow_unicode=True), encoding="utf-8")
    assert main(["--subject", "aya", "--inputs", str(d), "--out", str(tmp_path / "out")]) == 2


@pytest.mark.parametrize("fname,mutate", [
    ("items.json", lambda d: d["items"][0].update(stem="合成题干")),           # 题干一类的字段一律拒收
    ("items.json", lambda d: d.update(map_version="0.8.0")),
    ("items.json", lambda d: d["items"][0].update(cognitive_level="remember")),
    ("items.json", lambda d: d["items"].append(dict(d["items"][0]))),          # 重复题目
    ("kappa-report.json", lambda d: d["threshold"].update(kappa_min=0.6)),
    ("kappa-report.json", lambda d: d["threshold"].update(passed=False)),      # 0.86 却报未通过：矛盾
    ("kappa-report.json", lambda d: d.update(subject="zhixing")),
    ("top1-report.json", lambda d: d["thresholds"]["top1"].update(rule=">= 0.8")),
    ("shadow-compare.json", lambda d: d.update(format="other")),
    ("m1-report.json", lambda d: d["map"].update(map_version="0.8.0")),
    ("m1-report.json", lambda d: d["metrics"]["lint_blocks"].update(blocks="0")),
])
def test_input_format_errors_exit_2(tmp_path, lexicon, fname, mutate):
    d = scenario_inputs(tmp_path, "aya-pass", lexicon)
    doc = json.loads((d / fname).read_text(encoding="utf-8"))
    mutate(doc)
    (d / fname).write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    assert main(["--subject", "aya", "--inputs", str(d), "--out", str(tmp_path / "out")]) == 2
    assert not (tmp_path / "out").exists()


def test_cli_errors_and_explicit_flags(tmp_path, lexicon):
    out = tmp_path / "out"
    d = scenario_inputs(tmp_path, "aya-pass", lexicon)
    # 只给单独参数、不给输入目录：其余全部待填
    assert main(["--subject", "aya", "--kappa-report", str(d / "kappa-report.json"), "--out", str(out)]) == 0
    rep = json.loads((out / "m2-aya.json").read_text(encoding="utf-8"))
    assert rep["map"] is None and item(rep, "tagging.kappa")["verdict"] == "met"
    assert sum(1 for i in rep["items"] if i["verdict"] == "pending") == len(rep["items"]) - 1
    assert main(["--subject", "aya", "--kappa-report", str(tmp_path / "missing.json"), "--out", str(out)]) == 2
    assert main(["--subject", "aya", "--inputs", str(tmp_path / "nope"), "--out", str(out)]) == 2
    (d / "kappa-report.json").write_text("{not json", encoding="utf-8")
    assert main(["--subject", "aya", "--inputs", str(d), "--out", str(out)]) == 2
    d2 = scenario_inputs(tmp_path, "zhixing-pass", lexicon)
    assert main(["--subject", "aya", "--inputs", str(d2), "--out", str(out)]) == 2          # 学科不一致
    shutil.copy(d2 / "map-snapshot.yaml", d2 / "map-snapshot.json")
    assert main(["--subject", "zhixing", "--inputs", str(d2), "--out", str(out)]) == 2      # 同一项两个文件
    (d2 / "map-snapshot.json").unlink()
    (d2 / "page-snapshot" / "manifest.json").write_text("{}", encoding="utf-8")
    assert main(["--subject", "zhixing", "--inputs", str(d2), "--out", str(out)]) == 2      # 页面快照不合格
    with pytest.raises(SystemExit):
        main(["--subject", "wenli", "--out", str(out)])                                      # 语文不在本 M2（#5）
    with pytest.raises(M2InputError):
        build_report("aya", {"daofa_grading_report": Source("daofa_grading_report", "0" * 64, {})})


def test_daofa_sample_label_not_computable(tmp_path, lexicon):
    d = scenario_inputs(tmp_path, "zhixing-pass", lexicon)
    doc = json.loads((d / "daofa-grading-report.json").read_text(encoding="utf-8"))
    doc["label"] = "流程样例"
    (d / "daofa-grading-report.json").write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    it = item(build_report("zhixing", resolve_inputs(d, {})), "daofa.point_agreement")
    assert it["verdict"] == "not_computable" and it["value"] == "90.0%（18 / 20）" and "流程样例" in it["note"]


def test_write_report_names(tmp_path, lexicon):
    rep = run(tmp_path, "aya-pass", lexicon)
    md, js = write_report(rep, tmp_path / "o")
    assert (md.name, js.name) == ("m2-aya.md", "m2-aya.json")
    assert json.loads(js.read_text(encoding="utf-8"))["format"] == "kge-m2-report/1"
    assert "不下总结论" in md.read_text(encoding="utf-8")


# ---------- 审查修复（安全审查 M1、M2、L1、L3、L5；契约审查 2–6） ----------
def _write_att(d: Path, att: dict[str, Any]) -> None:
    (d / "attestation.yaml").write_text(yaml.safe_dump(att, allow_unicode=True), encoding="utf-8")


def test_evidence_symlinks_rejected(tmp_path, lexicon):
    d = scenario_inputs(tmp_path, "aya-pass", lexicon)
    att = yaml.safe_load((d / "attestation.yaml").read_text(encoding="utf-8"))
    outside = tmp_path / "outside.txt"
    outside.write_text("合成", encoding="utf-8")
    (d / "evidence" / "link.txt").symlink_to(outside)                 # 文件是符号链接（指向目录外）
    (d / "evidence" / "inner.txt").symlink_to(d / "evidence" / "pentest-round2.txt")   # 指向目录内也拒
    (d / "linkdir").symlink_to(d / "evidence", target_is_directory=True)               # 中间段是符号链接
    for bad in ("evidence/link.txt", "evidence/inner.txt", "linkdir/pentest-round2.txt"):
        att["security"] = {"pentest_round2": "passed", "evidence": [bad]}
        _write_att(d, att)
        with pytest.raises(M2InputError, match="符号链接"):
            build_report("aya", resolve_inputs(d, {}))
        assert main(["--subject", "aya", "--inputs", str(d), "--out", str(tmp_path / "out")]) == 2


@pytest.mark.parametrize("bad", [".hidden.txt", "evidence/.x", "evidence//pentest-round2.txt", "evidence/a b.txt", "./evidence/x.txt",
                                 "evidence\\x.txt", "evidence"])
def test_evidence_path_whitelist(tmp_path, lexicon, bad):
    d = scenario_inputs(tmp_path, "aya-pass", lexicon)
    (d / ".hidden.txt").write_text("合成", encoding="utf-8")
    att = yaml.safe_load((d / "attestation.yaml").read_text(encoding="utf-8"))
    att["security"] = {"pentest_round2": "passed", "evidence": [bad]}
    _write_att(d, att)
    with pytest.raises(M2InputError):
        build_report("aya", resolve_inputs(d, {}))


def test_evidence_size_limit_and_unreadable(tmp_path, lexicon, monkeypatch):
    from knowledge import m2 as m2mod
    d = scenario_inputs(tmp_path, "aya-pass", lexicon)
    monkeypatch.setattr(m2mod, "EVIDENCE_MAX_BYTES", 8)
    with pytest.raises(M2InputError, match="10 MB"):
        build_report("aya", resolve_inputs(d, {}))
    monkeypatch.setattr(m2mod, "EVIDENCE_MAX_BYTES", 10 * 1024 * 1024)
    real = Path.read_bytes

    def flaky(self: Path) -> bytes:  # 证据文件读取时的 OSError 转成输入错误（退出 2），不抛原始异常
        if self.name == "pentest-round2.txt":
            raise PermissionError("合成")
        return real(self)
    monkeypatch.setattr(Path, "read_bytes", flaky)
    with pytest.raises(M2InputError, match="读不了"):
        build_report("aya", resolve_inputs(d, {}))


def test_executor_role_enum(tmp_path, lexicon):
    d = scenario_inputs(tmp_path, "aya-pass", lexicon)
    att = yaml.safe_load((d / "attestation.yaml").read_text(encoding="utf-8"))
    att["holdout"]["executor_role"] = "张老师"
    _write_att(d, att)
    assert main(["--subject", "aya", "--inputs", str(d), "--out", str(tmp_path / "out")]) == 2


def test_c_level_only_passed_or_failed(tmp_path, lexicon):
    d = scenario_inputs(tmp_path, "aya-pass", lexicon)
    att = yaml.safe_load((d / "attestation.yaml").read_text(encoding="utf-8"))
    att["c_level"]["status"] = "not_applicable"
    _write_att(d, att)
    assert main(["--subject", "aya", "--inputs", str(d), "--out", str(tmp_path / "out")]) == 2
    del att["c_level"]
    _write_att(d, att)
    it = item(build_report("aya", resolve_inputs(d, {})), "c_level.no_conclusion")
    assert it["verdict"] == "pending"
    att["c_level"] = {"status": "failed", "evidence": ["evidence/c-level-tests.txt"]}
    _write_att(d, att)
    it = item(build_report("aya", resolve_inputs(d, {})), "c_level.no_conclusion")
    assert it["verdict"] == "not_met" and it["note"] == "学习服务侧 C 级约束已有测试；洞察服务侧约束测试待补（A3）"


@pytest.mark.parametrize("mv", ["9.9.9-syn", "0.9", "", 9])
def test_top1_map_version_format(tmp_path, lexicon, mv):
    d = scenario_inputs(tmp_path, "aya-pass", lexicon)
    doc = json.loads((d / "top1-report.json").read_text(encoding="utf-8"))
    doc["map_version"] = mv
    (d / "top1-report.json").write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    assert main(["--subject", "aya", "--inputs", str(d), "--out", str(tmp_path / "out")]) == 2


def test_shadow_sample_not_computable(tmp_path, lexicon):
    d = scenario_inputs(tmp_path, "aya-pass", lexicon)
    doc = json.loads((d / "shadow-compare.json").read_text(encoding="utf-8"))
    doc["sample"] = True
    (d / "shadow-compare.json").write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    it = item(build_report("aya", resolve_inputs(d, {})), "shadow.compare_flow")
    assert it["verdict"] == "not_computable" and "流程样例" in it["note"]


def test_page_commit_mismatch_makes_page_items_not_computable(tmp_path, lexicon):
    d = scenario_inputs(tmp_path, "aya-pass", lexicon)
    doc = json.loads((d / "m1-report.json").read_text(encoding="utf-8"))
    doc["metrics"]["kp_page_publish_rate"]["snapshot_commit"] = "f" * 40
    (d / "m1-report.json").write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    rep = build_report("aya", resolve_inputs(d, {}))
    assert rep["open_items"]["not_computable"] == ["m1.kp_page_publish_rate", "core.page_publish_rate"]
    assert "提交号不一致" in item(rep, "core.page_publish_rate")["note"]
    assert item(rep, "core.completeness")["verdict"] == "met"


@pytest.mark.parametrize("key,mutate", [
    ("curriculum_coverage", lambda m: m.update(met=False)),               # 4 / 4 却报未达到
    ("kp_page_publish_rate", lambda m: m.update(numerator=3)),            # 3 / 4 却报达到
    ("lint_blocks", lambda m: m.update(blocks=2, by_check={"citation": 2})),
    ("kp_page_publish_rate", lambda m: m.update(snapshot_commit="short")),
])
def test_m1_met_must_match_numbers(tmp_path, lexicon, key, mutate):
    d = scenario_inputs(tmp_path, "aya-pass", lexicon)
    doc = json.loads((d / "m1-report.json").read_text(encoding="utf-8"))
    mutate(doc["metrics"][key])
    (d / "m1-report.json").write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    assert main(["--subject", "aya", "--inputs", str(d), "--out", str(tmp_path / "out")]) == 2


def test_m1_notes_pending_and_not_echoed(tmp_path, lexicon):
    d = scenario_inputs(tmp_path, "aya-pass", lexicon)
    doc = json.loads((d / "m1-report.json").read_text(encoding="utf-8"))
    doc["metrics"]["lint_blocks"] = {"status": "not_computable", "note": "不可计算：没有给体检 JSON"}
    doc["metrics"]["curriculum_coverage"] = {"status": "not_computable", "note": "<script>任意输入</script>"}
    (d / "m1-report.json").write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    rep = build_report("aya", resolve_inputs(d, {}))
    assert item(rep, "m1.lint_blocks")["verdict"] == "pending"
    cc = item(rep, "m1.curriculum_coverage")
    assert cc["verdict"] == "not_computable" and cc["note"] == "不可计算（见 M1 报告）"
    assert "script" not in render_markdown(rep) + json.dumps(rep, ensure_ascii=False)
    doc["metrics"]["curriculum_coverage"] = {"status": "not_computable", "note": m1.NOT_STORED}
    (d / "m1-report.json").write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    assert item(build_report("aya", resolve_inputs(d, {})), "m1.curriculum_coverage")["note"] == m1.NOT_STORED


def test_published_items_with_undeclared_level_counted():
    """APPLY 只声明 apply：挂在它 understand 层的已发布题不补任何层次，只在说明里计数"""
    snap, core = snap_core(map_doc("aya"))
    items = items_doc("aya")["items"] + [{"item_id": "SYN-M2-10", "main_node_id": A, "cognitive_level": "understand",
                                           "admission": "admitted", "tag_review_status": "approved"}]
    r = core_completeness(snap, core, items, page_map(snap, [T, L, A, S]))
    assert r["core_published_items_level_undeclared"] == 1 and r["completeness"].num == 4
    src = {"map_snapshot": Source("map_snapshot", "0" * 64, map_doc("aya")),
           "core_nodes": Source("core_nodes", "3" * 64, core_doc("aya")),
           "items": Source("items", "1" * 64, {**items_doc("aya"), "items": items})}
    from knowledge import m2 as m2mod
    orig = m2mod.load_pages
    m2mod.load_pages = lambda _src: (page_map(snap, [T, L, A, S]), "e" * 40)
    try:
        rep = build_report("aya", {**src, "page_snapshot": Source("page_snapshot", "2" * 64)})
    finally:
        m2mod.load_pages = orig
    assert "1 道已发布题目的认知层次不在节点声明的层次里" in item(rep, "core.completeness")["note"]
