"""页面格式（解析、序列化、往返）、节点 ID 规范一致性、地图快照（白名单、哈希、差异）。"""
from __future__ import annotations

import importlib.util
import subprocess
import sys

import pytest

from conftest import FIXTURES, LINEAR, MAP_V1, PAGES, ROOT, map_doc, node_rec
from knowledge.atoms import SnapshotError, atom_hash, diff_snapshots, parse_snapshot
from knowledge.nodeid import NODE_ID_REGEX, SUBJECT_PREFIX
from knowledge.pages.format import KP_SECTIONS, PageFormatError, parse, serialize, serialize_header


def _load_check_node_id():
    spec = importlib.util.spec_from_file_location("check_node_id", ROOT / "docs/design/kge/check_node_id.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def test_node_id_regex_matches_doc_migration_and_contract():
    """直接复用 docs/design/kge/check_node_id.py 的解析：本包固化的正则与文档、迁移、知识服务契约逐字一致"""
    mod = _load_check_node_id()
    import re
    doc = mod.DOC.read_text(encoding="utf-8")
    sql = mod.MIGRATION.read_text(encoding="utf-8")
    doc_regex = re.search(r"`([^`]+)`", mod.section(doc, "node-id-regex")).group(1)
    sql_regex = re.search(r"CREATE DOMAIN kge\.node_id AS text\s+CHECK \(VALUE ~ '([^']+)'\)", sql).group(1)
    assert NODE_ID_REGEX == doc_regex == sql_regex == mod.contract_regex(mod.CONTRACT.read_text(encoding="utf-8"))
    doc_prefix = {r[0]: r[1] for r in mod.table_rows(mod.section(doc, "subject-prefix"))}
    assert SUBJECT_PREFIX == doc_prefix


@pytest.mark.parametrize("path", sorted(PAGES.rglob("*.md")), ids=lambda p: p.name)
def test_fixture_pages_round_trip_byte_stable(path):
    text = path.read_text(encoding="utf-8")
    page = parse(text)
    assert serialize(page) == text
    assert parse(serialize(page)) == page
    assert [s.id for s in page.sections] == [s.id for s in KP_SECTIONS]


def test_fixtures_match_generator():
    r = subprocess.run([sys.executable, str(FIXTURES / "make_pages.py"), "--check"], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


def test_non_canonical_header_reserializes_stably():
    text = (PAGES / "kp" / f"{LINEAR}.md").read_text(encoding="utf-8")
    shuffled = text.replace("page_type: kp\nsubject: aya\n", "subject: 'aya'\npage_type: kp\n")
    page = parse(shuffled)
    assert serialize(page) != shuffled
    assert serialize(page) == text  # 规范化后回到规范写法
    assert serialize(parse(serialize(page))) == serialize(page)


def test_scalars_that_yaml_would_retype_are_quoted():
    out = serialize_header({"page_id": "yes", "page_version": "1.0", "map_version": "0.9.0", "schema_version": 1,
                            "atom_refs": [{"type": "node", "id": "a: b", "hash": "sha256:00"}]})
    assert 'page_id: "yes"' in out and 'page_version: "1.0"' in out and "map_version: 0.9.0" in out
    assert 'id: "a: b"' in out
    import yaml
    loaded = yaml.safe_load(out.strip("-\n"))
    assert loaded["page_id"] == "yes" and loaded["page_version"] == "1.0" and loaded["atom_refs"][0]["id"] == "a: b"


@pytest.mark.parametrize("bad,line", [
    ("no header\n", 1),
    ("---\npage_id: x\n", 1),
    ("---\npage_id: [x\n---\n# t\n", None),
    ("---\npage_id: x\n---\n# t\n\n<!-- kge:section id=definition kind=locked -->\n## 定义\n", 6),
    ("---\npage_id: x\n---\n# t\n\n<!-- kge:section id=definition kind=locked -->\n## 定义\n<!-- kge:end id=curriculum -->\n", 8),
    ("---\npage_id: x\n---\n# t\n<!-- kge:end id=definition -->\n", 5),
    ("---\r\npage_id: x\r\n---\r\n", None),
])
def test_parse_errors_are_reported_with_line(bad, line):
    with pytest.raises(PageFormatError) as e:
        parse(bad)
    if line is not None:
        assert e.value.line == line


def test_stray_text_outside_sections_is_kept_for_lint():
    text = (PAGES / "kp" / f"{LINEAR}.md").read_text(encoding="utf-8")
    page = parse(text.replace("# 一元一次方程\n", "# 一元一次方程\n游离的一行\n"))
    assert page.stray and page.stray[0][1] == "游离的一行"


def test_pages_never_contain_textbook_quotes():
    doc = map_doc()
    quotes = [p["quote_text"] for p in doc["textbook_projections"]]
    for path in PAGES.rglob("*.md"):
        text = path.read_text(encoding="utf-8")
        assert not any(q in text for q in quotes)
        assert "{{quote:" in text or "（暂无）" in text


# ---------- 快照 ----------

def test_snapshot_rejects_unknown_and_personal_fields():
    doc = map_doc()
    node_rec(doc, LINEAR)["student_id"] = "synthetic-student-1"
    with pytest.raises(SnapshotError, match="未知字段"):
        parse_snapshot(doc)
    doc = map_doc()
    doc["attempts"] = []
    with pytest.raises(SnapshotError, match="顶层未知字段"):
        parse_snapshot(doc)


@pytest.mark.parametrize("mutate,msg", [
    (lambda d: node_rec(d, LINEAR).update(id="MATH.SYN.EQ.linear"), "节点 ID"),
    (lambda d: node_rec(d, LINEAR).update(id="CHN.SYN.EQ.LINEAR"), "学科前缀"),
    (lambda d: node_rec(d, LINEAR).pop("name"), "缺字段"),
    (lambda d: d["edges"].append({"from_id": LINEAR, "to_id": "MATH.SYN.EQ.NOPE", "type": "prerequisite"}), "不存在的节点"),
    (lambda d: d["edges"].append({"from_id": LINEAR, "to_id": "MATH.SYN.EQ.APPLY", "type": "prerequisite"}), "原子重复"),
    (lambda d: d["edges"].append({"from_id": LINEAR, "to_id": "MATH.SYN.EQ.APPLY", "type": "sibling"}), "边类型"),
    (lambda d: d.update(subject="math"), "学科编码"),
])
def test_snapshot_validation(mutate, msg):
    doc = map_doc()
    mutate(doc)
    with pytest.raises(SnapshotError, match=msg):
        parse_snapshot(doc)


def test_atom_hash_is_stable_and_sensitive():
    a = parse_snapshot(map_doc())
    b = parse_snapshot(map_doc())
    assert a.get("node", LINEAR).hash == b.get("node", LINEAR).hash
    assert atom_hash({"a": 1, "b": 2}) == atom_hash({"b": 2, "a": 1})
    doc = map_doc()
    doc["textbook_projections"][0]["quote_text"] = "合成教材原句：改过的一句。"
    c = parse_snapshot(doc)
    assert diff_snapshots(a, c) == {"projection:syn-tp-001": "modified"}


def test_curriculum_links_are_part_of_node_atom():
    doc = map_doc()
    doc["curriculum_links"].append({"node_id": LINEAR, "standard_year": 2022, "code": "SYN-EQ-02"})
    changes = diff_snapshots(parse_snapshot(map_doc()), parse_snapshot(doc))
    assert changes == {f"node:{LINEAR}": "modified"}


def test_map_fixture_is_synthetic_only():
    text = MAP_V1.read_text(encoding="utf-8")
    assert "合成" in text and "SYN" in text
