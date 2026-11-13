"""节点表导出（KGE-12 A）：只出已发布版本的生效节点、只出已核对投影、不带教材原句、出版社按对照表填、逐字节确定、
CSV 与 JSONL 一致、清单哈希正确、命令行退出码。全部是合成数据（MATH.SYN.*）。"""
from __future__ import annotations

import csv
import hashlib
import io
import json
from pathlib import Path

import pytest
import yaml

from conftest import APPLY, LINEAR, TRANSPOSE, map_doc, node_rec
from knowledge.atoms import parse_snapshot
from knowledge.export import node_table as nt

DEPRECATED = "MATH.SYN.EQ.OLD_TOPIC"


def doc_with_extras() -> dict:
    """在合成地图上补：一条未核对投影、一条第二版本投影、一个父节点已撤下的节点"""
    d = map_doc()
    d["textbook_projections"] += [
        {"id": "syn-tp-003", "node_id": APPLY, "edition": "syn-ed", "edition_year": 2024, "volume": "合成七上",
         "chapter": "合成第三章", "page_from": 90, "page_to": 91, "mapping_state": "unverified"},
        {"id": "syn-tp-004", "node_id": LINEAR, "edition": "syn-ed2", "edition_year": 2013, "volume": "合成七上",
         "chapter": "合成第二章", "page_from": 60, "page_to": 61, "mapping_state": "verified"},
    ]
    d["nodes"].append({"id": "MATH.SYN.EQ.OLD_TOPIC.CHILD", "level": "node", "parent_id": DEPRECATED, "kind": "kp",
                       "name": "父节点已撤下的合成节点"})
    return d


def rows_of(d: dict, publishers=None) -> list[dict]:
    return nt.build_rows(parse_snapshot(d), publishers)


def test_only_active_nodes_and_taggable_flag():
    rows = rows_of(map_doc())
    ids = [r["node_id"] for r in rows]
    assert ids == sorted(ids) and DEPRECATED not in ids
    by = {r["node_id"]: r for r in rows}
    assert by["MATH.SYN"]["taggable"] is False and by["MATH.SYN.EQ"]["taggable"] is False
    assert by[LINEAR]["taggable"] is True and by[LINEAR]["parent_node_id"] == "MATH.SYN.EQ"
    assert by[LINEAR]["aliases"] == [] and by[LINEAR]["map_version"] == "0.9.0"
    assert by[LINEAR]["includes"] == node_rec(map_doc(), LINEAR)["includes"]


def test_parent_that_is_not_active_becomes_null():
    by = {r["node_id"]: r for r in rows_of(doc_with_extras())}
    assert by["MATH.SYN.EQ.OLD_TOPIC.CHILD"]["parent_node_id"] is None


def test_only_verified_projections_without_quote_text():
    by = {r["node_id"]: r for r in rows_of(doc_with_extras())}
    assert by[APPLY]["textbook_projections"] == []                          # 未核对的不出
    lin = by[LINEAR]["textbook_projections"]
    assert [p["edition"] for p in lin] == ["syn-ed", "syn-ed2"]            # 按版本排序
    assert lin[0] == {"edition": "syn-ed", "publisher": None, "edition_year": 2024, "volume": "合成七上",
                      "chapter": "合成第三章", "page_from": 80, "page_to": 82}
    blob = json.dumps(rows_of(doc_with_extras()), ensure_ascii=False)
    assert "quote_text" not in blob and "合成教材原句" not in blob and "mapping_state" not in blob
    assert len(by[TRANSPOSE]["textbook_projections"]) == 1


def test_publishers_filled_and_missing_codes_rejected(tmp_path):
    rows = rows_of(doc_with_extras(), {"syn-ed": "合成出版社甲", "syn-ed2": "合成出版社乙"})
    lin = next(r for r in rows if r["node_id"] == LINEAR)["textbook_projections"]
    assert [p["publisher"] for p in lin] == ["合成出版社甲", "合成出版社乙"]
    with pytest.raises(nt.ExportError, match="syn-ed2"):
        rows_of(doc_with_extras(), {"syn-ed": "合成出版社甲"})
    bad = tmp_path / "p.yaml"
    for content in ("publishers: {}", "x: 1", "publishers: {syn-ed: ''}", ":::"):
        bad.write_text(content, encoding="utf-8")
        with pytest.raises(nt.ExportError):
            nt.load_publishers(bad)


def test_unpublished_snapshot_rejected():
    d = map_doc()
    d["status"] = "draft"
    with pytest.raises(nt.ExportError, match="已发布"):
        rows_of(d)


def test_export_deterministic_csv_matches_jsonl_and_manifest(tmp_path):
    snap = parse_snapshot(doc_with_extras())
    m1, m2 = nt.export(snap, tmp_path / "a"), nt.export(snap, tmp_path / "b")
    assert m1 == m2
    for name in ("node-table.jsonl", "node-table.csv", "node-table.manifest.json"):
        assert (tmp_path / "a" / name).read_bytes() == (tmp_path / "b" / name).read_bytes()
    jl = [json.loads(x) for x in (tmp_path / "a" / "node-table.jsonl").read_text(encoding="utf-8").splitlines()]
    raw = (tmp_path / "a" / "node-table.csv").read_text(encoding="utf-8")
    rows = list(csv.DictReader(io.StringIO(raw)))
    assert len(rows) == len(jl) == m1["rows"]
    for c, j in zip(rows, jl):
        assert c["node_id"] == j["node_id"] and c["name"] == j["name"]
        assert json.loads(c["textbook_projections"]) == j["textbook_projections"]
        assert c["taggable"] == ("true" if j["taggable"] else "false")
        assert c["parent_node_id"] == (j["parent_node_id"] or "")
    for name, digest in m1["files"].items():
        assert hashlib.sha256((tmp_path / "a" / name).read_bytes()).hexdigest() == digest
    assert m1["format"] == nt.FORMAT and m1["taggable_rows"] == sum(1 for j in jl if j["taggable"])
    assert m1["projections"] == 3 and m1["publishers_filled"] is False
    assert str(tmp_path) not in (tmp_path / "a" / "node-table.manifest.json").read_text(encoding="utf-8")


def test_cli(tmp_path, capsys):
    snap = tmp_path / "map.yaml"
    snap.write_text(yaml.safe_dump(doc_with_extras(), allow_unicode=True), encoding="utf-8")
    pubs = tmp_path / "pubs.yaml"
    pubs.write_text(yaml.safe_dump({"publishers": {"syn-ed": "合成出版社甲", "syn-ed2": "合成出版社乙"}}, allow_unicode=True),
                    encoding="utf-8")
    assert nt.main(["--map-snapshot", str(snap), "--out", str(tmp_path / "o"), "--publishers", str(pubs)]) == 0
    assert "可打标" in capsys.readouterr().out
    assert json.loads((tmp_path / "o" / "node-table.manifest.json").read_text(encoding="utf-8"))["publishers_filled"] is True
    assert nt.main(["--map-snapshot", str(tmp_path / "missing.yaml"), "--out", str(tmp_path / "x")]) == 2
    d = doc_with_extras()
    d["status"] = "draft"
    snap.write_text(yaml.safe_dump(d, allow_unicode=True), encoding="utf-8")
    assert nt.main(["--map-snapshot", str(snap), "--out", str(tmp_path / "y")]) == 2
    assert "节点表导出失败" in capsys.readouterr().err and not (tmp_path / "y").exists()
