"""PR ③：M1 报表（课标承接率、知识点页发布率、体检阻断数；留出集只写来源与执行人）。数字与手算核对；全部是合成数据。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from conftest import APPLY, LINEAR, MAP_V1, PAGES, SQUARE, TRANSPOSE, TODAY, edit, make_page_snapshot, map_doc, node_rec, page_file
from knowledge.lint import lint_dir
from knowledge.m1 import (HOLDOUT_SOURCE, NOT_STORED, M1InputError, Ratio, build_report, main, render_markdown, snapshot_digest,
                          write_report)


def lint_json(pages_dir: Path, lexicon, snap=None) -> dict:
    return json.loads(lint_dir(pages_dir, snapshot=snap, lexicon=lexicon, today=TODAY).to_json())


def test_ratio_percent_rounds_half_up_exactly():
    assert Ratio(4, 4).percent == "100.0%" and Ratio(3, 4).percent == "75.0%" and Ratio(0, 5).percent == "0.0%"
    assert Ratio(2, 3).percent == "66.7%" and Ratio(1, 3).percent == "33.3%"
    assert Ratio(1, 8).percent == "12.5%" and Ratio(1, 2000).percent == "0.1%"  # 0.05% 四舍五入进位
    assert Ratio(1, 2001).percent == "0.0%"


def test_curriculum_coverage_hand_count():
    # 合成地图：4 条课标条目，4 个有效节点各承接 1 条 → 4 / 4
    m = build_report(map_doc())["metrics"]["curriculum_coverage"]
    assert (m["numerator"], m["denominator"], m["percent"], m["met"], m["uncovered"]) == (4, 4, "100.0%", True, [])
    # 去掉一条承接、把另一条挂到已撤下的节点上 → 只剩 2 / 4
    doc = map_doc()
    doc["curriculum_links"] = [ln for ln in doc["curriculum_links"] if ln["code"] != "SYN-EQ-03"]
    for ln in doc["curriculum_links"]:
        if ln["code"] == "SYN-EXPR-01":
            ln["node_id"] = "MATH.SYN.EQ.OLD_TOPIC"
    m = build_report(doc)["metrics"]["curriculum_coverage"]
    assert (m["numerator"], m["denominator"], m["percent"], m["met"]) == (2, 4, "50.0%", False)
    assert m["uncovered"] == ["2022/SYN-EQ-03", "2022/SYN-EXPR-01"]
    # 其他课标年份的条目不进分母：加一条 2011 年的 → 仍是 x / 4
    doc["curriculum_items"].append({"standard_year": 2011, "code": "OLD-01", "domain": "合成领域", "verb_level": "know",
                                    "text": "合成旧课标条目。", "source_ref": "synthetic"})
    assert build_report(doc)["metrics"]["curriculum_coverage"]["denominator"] == 4


def test_curriculum_coverage_not_computable_without_links():
    doc = map_doc()
    del doc["curriculum_links"]  # 数据库数据源就是这样：kge.* 没有承接表
    m = build_report(doc, data_source="database")["metrics"]["curriculum_coverage"]
    assert m == {"status": "not_computable", "note": NOT_STORED}
    doc["curriculum_links"] = []  # 字段在但为空：照算，结果 0
    assert build_report(doc)["metrics"]["curriculum_coverage"]["percent"] == "0.0%"


def test_kp_publish_rate_hand_count(tmp_path):
    # 有效 kp 节点 4 个（OLD_TOPIC 已撤下不算）；manifest 列出 4 页 → 4 / 4
    m = build_report(map_doc(), page_snapshot_dir=make_page_snapshot(tmp_path / "a"))["metrics"]["kp_page_publish_rate"]
    assert (m["numerator"], m["denominator"], m["percent"], m["met"]) == (4, 4, "100.0%", True)
    assert m["snapshot_commit"] == "d" * 40 and m["missing"] == [] and m["stale_map_version"] == []
    # manifest 不列 APPLY（文件在也算未发布）；新增一个没有页面的 kp 节点 → 3 / 5 = 60.0%
    doc = map_doc()
    doc["nodes"].append({"id": "MATH.SYN.EXPR.NEW_KP", "level": "node", "parent_id": "MATH.SYN.EXPR", "kind": "kp", "name": "合成新节点"})
    m = build_report(doc, page_snapshot_dir=make_page_snapshot(tmp_path / "b", drop=(APPLY,)))["metrics"]["kp_page_publish_rate"]
    assert (m["numerator"], m["denominator"], m["percent"], m["met"]) == (3, 5, "60.0%", False)
    assert m["missing"] == [APPLY, "MATH.SYN.EXPR.NEW_KP"]
    # 节点撤下后页面还在快照里：不进分母，单列
    doc = map_doc()
    node_rec(doc, SQUARE)["status"] = "deprecated"
    m = build_report(doc, page_snapshot_dir=make_page_snapshot(tmp_path / "c"))["metrics"]["kp_page_publish_rate"]
    assert (m["numerator"], m["denominator"]) == (3, 3) and m["pages_not_on_active_kp"] == [SQUARE]


def test_kp_publish_rate_excludes_stale_pages_and_reports_both(tmp_path):
    """map_version 落后的页面 page.get 不服务：主口径分子不含它们；并列“含落后页”口径"""
    m = build_report(map_doc(), page_snapshot_dir=make_page_snapshot(tmp_path / "s", map_version="0.8.0"))
    k = m["metrics"]["kp_page_publish_rate"]
    assert (k["numerator"], k["denominator"], k["percent"], k["met"]) == (0, 4, "0.0%", False)
    assert k["stale_map_version"] == sorted([APPLY, LINEAR, SQUARE, TRANSPOSE])
    assert k["including_stale"] == {"numerator": 4, "denominator": 4, "percent": "100.0%", "met": True}
    md = render_markdown(m)
    assert "| 知识点页发布率 | 100% | 0.0%（0 / 4） | 未达到 |" in md
    assert "| 知识点页发布率（含地图版本落后的页） | 100% | 100.0%（4 / 4） | 达到（仅供对照：落后页不对外服务） |" in md
    # 只有一页落后：3 / 4 与 4 / 4
    mixed = make_page_snapshot(tmp_path / "mixed")
    import hashlib
    manifest = json.loads((mixed / "manifest.json").read_text(encoding="utf-8"))
    p = mixed / "pages" / "kp" / f"{APPLY}.md"
    raw = p.read_text(encoding="utf-8").replace("map_version: 0.9.0", "map_version: 0.8.0", 1).encode("utf-8")
    p.write_bytes(raw)
    for e in manifest["pages"]:
        if e["page_id"] == APPLY:
            e.update(map_version="0.8.0", sha256=hashlib.sha256(raw).hexdigest())
    (mixed / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    k = build_report(map_doc(), page_snapshot_dir=mixed)["metrics"]["kp_page_publish_rate"]
    assert (k["numerator"], k["denominator"], k["stale_map_version"]) == (3, 4, [APPLY])
    assert (k["including_stale"]["numerator"], k["including_stale"]["percent"]) == (4, "100.0%")
    assert build_report(map_doc())["metrics"]["kp_page_publish_rate"]["status"] == "not_computable"


def test_lint_blocks_from_lint_json(tmp_path, lexicon, snap_v1):
    clean = lint_json(PAGES, lexicon, snap_v1)
    m = build_report(map_doc(), lint_report=clean)["metrics"]["lint_blocks"]
    assert m["blocks"] == 0 and m["met"] is True and m["pages"] == 4
    import shutil
    d = tmp_path / "pages"
    shutil.copytree(PAGES, d)
    edit(page_file(d, LINEAR), "移项时漏改符号会直接得到错误的解。[@edge:MATH.SYN.EQ.TRANSPOSE>prerequisite>MATH.SYN.EQ.LINEAR]",
         "移项时漏改符号会直接得到错误的解。")  # 缺引用 → 1 条 citation 阻断
    bad = lint_json(d, lexicon, snap_v1)
    m = build_report(map_doc(), lint_report=bad)["metrics"]["lint_blocks"]
    assert m["blocks"] == bad["summary"]["blocks"] == 1 and m["by_check"] == {"citation": 1} and m["met"] is False
    bad["summary"]["blocks"] = 0  # 汇总与明细不一致：不信汇总
    with pytest.raises(M1InputError):
        build_report(map_doc(), lint_report=bad)
    with pytest.raises(M1InputError):
        build_report(map_doc(), lint_report={"format": "other"})


def test_report_deterministic_and_honest(tmp_path, lexicon, snap_v1):
    snap_dir = make_page_snapshot(tmp_path / "snap")
    doc = map_doc()
    del doc["curriculum_links"]
    rep = build_report(doc, page_snapshot_dir=snap_dir, lint_report=lint_json(PAGES, lexicon, snap_v1))
    md1, js1 = write_report(rep, tmp_path / "o1")
    rep2 = build_report(doc, page_snapshot_dir=snap_dir, lint_report=lint_json(PAGES, lexicon, snap_v1))
    md2, js2 = write_report(rep2, tmp_path / "o2")
    assert md1.read_bytes() == md2.read_bytes() and js1.read_bytes() == js2.read_bytes()
    text = md1.read_text(encoding="utf-8") + js1.read_text(encoding="utf-8")
    assert NOT_STORED in text and "run_holdout.sh" in text and "教研负责人" in text
    assert str(tmp_path) not in text  # 不含本机路径
    assert rep["metrics"]["holdout_untaggable_rate"] == {"status": "external", "note": HOLDOUT_SOURCE}
    md = render_markdown(rep)
    assert "| 课标承接率 | 100% | — | 不可计算：承接关系未入库 |" in md
    assert "| 知识点页发布率 | 100% | 100.0%（4 / 4） | 达到 |" in md
    assert "| 体检阻断数 | 0 | 0 | 达到 |" in md


def test_cli(tmp_path, lexicon, snap_v1):
    lint_path = tmp_path / "lint.json"
    lint_path.write_text(json.dumps(lint_json(PAGES, lexicon, snap_v1)), encoding="utf-8")
    snap_dir = make_page_snapshot(tmp_path / "snap")
    out = tmp_path / "out"
    assert main(["--map-snapshot", str(MAP_V1), "--page-snapshot", str(snap_dir), "--lint-report", str(lint_path), "--out", str(out)]) == 0
    rep = json.loads((out / "m1-aya-0.9.0.json").read_text(encoding="utf-8"))
    assert rep["metrics"]["curriculum_coverage"]["percent"] == "100.0%"
    assert main(["--map-snapshot", str(tmp_path / "missing.yaml"), "--out", str(out)]) == 2
    (snap_dir / "manifest.json").write_text("{}", encoding="utf-8")
    assert main(["--map-snapshot", str(MAP_V1), "--page-snapshot", str(snap_dir), "--out", str(out)]) == 2
    assert main(["--database-url", "postgresql://synthetic/none", "--out", str(out)]) == 2  # 缺 --subject


def test_database_source_reports_links_not_stored(kgedb, tmp_path):
    """库上数据：kge.* 没有节点—课标承接表 → 课标承接率如实报“不可计算”，不推算"""
    from pgkge import APP_ROLE
    from knowledge.m1 import _db_doc
    doc = _db_doc(kgedb.dsn(APP_ROLE), "aya")
    assert "curriculum_links" not in doc
    rep = build_report(doc, data_source="database")
    assert rep["metrics"]["curriculum_coverage"] == {"status": "not_computable", "note": NOT_STORED}
    assert rep["map"]["data_source"] == "database"
    assert rep["map"]["snapshot_digest"] == snapshot_digest(doc)  # 库上数据源同样有规范化摘要（Decimal、日期可规范化）


# ---------- KGE-06（T33）：快照摘要 ----------
def test_snapshot_digest_is_canonical_and_recorded(tmp_path):
    import hashlib

    import yaml
    doc = map_doc()
    d = snapshot_digest(doc)
    canon = json.dumps(doc, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    assert d == hashlib.sha256(canon).hexdigest()  # 与手算一致
    # 写法无关：YAML、换了键顺序与缩进的 JSON，摘要相同
    as_json = tmp_path / "m.json"
    as_json.write_text(json.dumps(dict(reversed(list(doc.items()))), ensure_ascii=False, indent=4), encoding="utf-8")
    assert snapshot_digest(json.loads(as_json.read_text(encoding="utf-8"))) == d
    assert snapshot_digest(yaml.safe_load(MAP_V1.read_text(encoding="utf-8"))) == d
    # 内容变一点，摘要就变（中文不转义：改一个汉字也变）
    changed = map_doc()
    node_rec(changed, LINEAR)["name"] = node_rec(changed, LINEAR)["name"] + "甲"
    assert snapshot_digest(changed) != d
    with pytest.raises(M1InputError):
        snapshot_digest({"x": object()})
    with pytest.raises(M1InputError):
        snapshot_digest({"x": float("nan")})
    snap_dir = make_page_snapshot(tmp_path / "snap")
    rep = build_report(doc, page_snapshot_dir=snap_dir)
    assert rep["map"]["snapshot_digest"] == d
    kp = rep["metrics"]["kp_page_publish_rate"]
    assert kp["manifest_sha256"] == hashlib.sha256((snap_dir / "manifest.json").read_bytes()).hexdigest()
    md = render_markdown(rep)
    assert f"地图快照规范化摘要（sha256）：{d}" in md and kp["manifest_sha256"] in md


def test_manifest_read_once_and_digest_matches_parsed_bytes(tmp_path, monkeypatch):
    """manifest 只读一次：交给载入的就是算摘要的那一份字节；读完之后磁盘上被换掉也不影响这次报告"""
    import hashlib

    from knowledge.service import pages as pages_mod
    snap_dir = make_page_snapshot(tmp_path / "snap")
    real = pages_mod.load_snapshot_dir
    given: list = []

    def spy(root, lexicon=None, manifest_raw=None):
        given.append(manifest_raw)
        m = Path(root) / "manifest.json"  # 载入时磁盘上的 manifest 被换掉（提交号改了）
        m.write_text(m.read_text(encoding="utf-8").replace("d" * 40, "e" * 40), encoding="utf-8")
        return real(root, lexicon, manifest_raw=manifest_raw)
    monkeypatch.setattr(pages_mod, "load_snapshot_dir", spy)
    kp = build_report(map_doc(), page_snapshot_dir=snap_dir)["metrics"]["kp_page_publish_rate"]
    assert given and given[0] is not None
    assert kp["manifest_sha256"] == hashlib.sha256(given[0]).hexdigest() and kp["snapshot_commit"] == "d" * 40
    # manifest 是符号链接：拒绝（O_NOFOLLOW）
    snap2 = make_page_snapshot(tmp_path / "snap2")
    real_m = tmp_path / "m.json"
    real_m.write_bytes((snap2 / "manifest.json").read_bytes())
    (snap2 / "manifest.json").unlink()
    (snap2 / "manifest.json").symlink_to(real_m)
    monkeypatch.setattr(pages_mod, "load_snapshot_dir", real)
    with pytest.raises(M1InputError):
        build_report(map_doc(), page_snapshot_dir=snap2)
