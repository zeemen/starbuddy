"""审核台差异与风险分类单测（spec 冲刺 1 验收“差异与分类”）。全部合成数据。"""
from __future__ import annotations

import copy
from datetime import datetime, timezone
from decimal import Decimal

from review_console.diff import canonical_json, diff, touched_nodes
from review_console.risk import classify, classify_change_type, summarize
from review_console.snapshot import Snapshot

V1, V2 = "11111111-0000-4000-8000-000000000001", "11111111-0000-4000-8000-000000000002"
OTHER = "22222222-0000-4000-8000-000000000009"


def node(i, **kw):
    d = {"id": i, "level": "node", "parent_id": "MATH.F.T", "kind": "kp", "name": "合成节点", "includes": ["a"], "excludes": ["b"],
         "examples": ["c"], "scope": "required", "cognitive_levels": ["understand"], "error_type": None, "status": "active"}
    d.update(kw)
    return d


def rows(version, **over):
    r = {
        "knowledge_node": [node("MATH.F.T.A"), node("MATH.F.T.B"), node("MATH.F.T.C")],
        "knowledge_edge": [
            {"from_id": "MATH.F.T.B", "to_map_version_id": version, "to_id": "MATH.F.T.A", "type": "prerequisite",
             "strength": "strong", "evidence_ref": None, "status": "active"},
            {"from_id": "MATH.F.T.C", "to_map_version_id": version, "to_id": "MATH.F.T.A", "type": "related",
             "strength": "weak", "evidence_ref": None, "status": "active"},
            {"from_id": "MATH.F.T.C", "to_map_version_id": OTHER, "to_id": "PHY.X.T.A", "type": "aligned_to",
             "strength": "weak", "evidence_ref": None, "status": "active"},
        ],
        "textbook_projection": [
            {"id": f"{version[:8]}-tb1", "node_id": "MATH.F.T.A", "edition": "syn-2024", "edition_year": 2024, "printing": None,
             "volume": "v9b", "chapter": "c5", "page_from": 10, "page_to": 11, "quote_hash": bytes.fromhex("ab" * 32),
             "verified_by": "staff-001", "verified_at": datetime(2027, 1, 1, tzinfo=timezone.utc), "mapping_state": "verified"},
        ],
        "region_projection": [],
        "skill_link": [{"node_id": "MATH.F.T.A", "subject": "aya", "local_id": "K-SYN-0001", "weight": Decimal("1.000")}],
        "id_migration": [{"old_id": "K-SYN-0001", "new_id": "MATH.F.T.A", "rule": "rename"}],
        "item_tag": [], "scoring_point": [], "current_affair": [],
    }
    r.update(over)
    return r


def snap(version, **over):
    return Snapshot.from_rows(version, rows(version, **over))


def test_identical_versions_have_no_diff():
    # 行 UUID 与版本 ID 不同，但内容相同：没有差异
    assert diff(snap(V1), snap(V2)) == []


def test_no_base_lists_everything_as_added_and_structural():
    ch = diff(None, snap(V2))
    assert {c["op"] for c in ch} == {"added"}
    assert [c["key"] for c in ch if c["object"] == "node"] == ["MATH.F.T.A", "MATH.F.T.B", "MATH.F.T.C"]
    s = summarize(ch)
    assert s["structural"] and s["required_signers"] == 2 and s["level"] == "high"


def test_node_field_change_is_per_field_with_old_and_new():
    r = rows(V2)
    r["knowledge_node"][0] = node("MATH.F.T.A", excludes=["b", "d"])
    ch = diff(snap(V1), Snapshot.from_rows(V2, r))
    assert ch == [{"object": "node", "key": "MATH.F.T.A", "op": "changed", "field": "excludes", "old": ["b"], "new": ["b", "d"]}]
    assert classify(ch[0]).as_dict() == {"level": "medium", "reason": "boundary_supplement", "structural": False}
    assert summarize(ch)["required_signers"] == 1


def test_node_deprecated_is_structural():
    r = rows(V2)
    r["knowledge_node"][2] = node("MATH.F.T.C", status="deprecated")
    ch = diff(snap(V1), Snapshot.from_rows(V2, r))
    assert [(c["key"], c["op"]) for c in ch] == [("MATH.F.T.C", "deprecated")]
    assert classify(ch[0]).structural and classify(ch[0]).level == "high"


def test_edges_added_removed_and_strength():
    r = rows(V2)
    r["knowledge_edge"] = [dict(r["knowledge_edge"][0], strength="weak"), r["knowledge_edge"][2],
                           {"from_id": "MATH.F.T.C", "to_map_version_id": V2, "to_id": "MATH.F.T.B", "type": "prerequisite",
                            "strength": "strong", "evidence_ref": None, "status": "active"}]
    ch = diff(snap(V1), Snapshot.from_rows(V2, r))
    got = [(c["key"], c["op"], c["field"], classify(c).level, classify(c).structural) for c in ch]
    assert got == [
        ("MATH.F.T.B -prerequisite-> MATH.F.T.A", "changed", "strength", "high", False),
        ("MATH.F.T.C -prerequisite-> MATH.F.T.B", "added", None, "high", True),
        ("MATH.F.T.C -related-> MATH.F.T.A", "removed", None, "medium", False),
    ]


def test_cross_version_edge_key_keeps_target_version():
    keys = set(snap(V2).edges)
    assert f"MATH.F.T.C -aligned_to-> PHY.X.T.A@{OTHER}" in keys


def test_textbook_page_and_quote_change_are_high():
    r = rows(V2)
    r["textbook_projection"] = [dict(r["textbook_projection"][0], page_to=12, quote_hash=bytes.fromhex("cd" * 32))]
    ch = diff(snap(V1), Snapshot.from_rows(V2, r))
    assert [(c["field"], c["old"], c["new"]) for c in ch] == [("page_to", 11, 12), ("quote_hash", "ab" * 32, "cd" * 32)]
    assert all(classify(c).as_dict() == {"level": "high", "reason": "textbook_quote_page", "structural": False} for c in ch)
    assert touched_nodes(ch) == ["MATH.F.T.A"]


def test_duplicate_textbook_keys_get_ordinal():
    r = rows(V2)
    r["textbook_projection"].append(dict(r["textbook_projection"][0], id="zz", page_from=30, page_to=31))
    assert sorted(Snapshot.from_rows(V2, r).textbook) == ["MATH.F.T.A|syn-2024|v9b|c5", "MATH.F.T.A|syn-2024|v9b|c5#2"]


def test_id_retire_flags_retest():
    r = rows(V2, id_migration=[{"old_id": "K-SYN-0001", "new_id": "MATH.F.T.A", "rule": "rename"},
                               {"old_id": "K-SYN-0009", "new_id": None, "rule": "retire"}])
    ch = diff(snap(V1), Snapshot.from_rows(V2, r))
    assert [(c["key"], c["op"]) for c in ch] == [("K-SYN-0009|-", "added")]
    assert classify(ch[0]).reason == "id_retire_retest"


def test_atoms():
    tag = {"subject": "aya", "item_id": "SYN-1", "main_node_id": "MATH.F.T.A", "node_ids": [], "cognitive_level": None,
           "prior_difficulty": None, "family": None, "source_kind": "original", "license_state": "original", "quality_grade": None,
           "tag_review_status": "approved"}
    sp = {"subject": "aya", "item_id": "SYN-1", "rubric_version": "r1", "point_id": "p1", "score": Decimal("2.00"),
          "node_ids": ["MATH.F.T.A"], "subject_dimension": None, "keyword_family": "{}", "required_logic": None,
          "material_linkage": None, "partial_credit_rule": {}, "common_errors": "[]"}
    base = snap(V1, item_tag=[tag])
    draft = snap(V2, item_tag=[dict(tag, family="fam-01")], scoring_point=[sp])
    ch = diff(base, draft)
    got = [(c["object"], c["op"], c["field"], classify(c).level) for c in ch]
    assert got == [("item_tag", "changed", "family", "low"), ("scoring_point", "added", None, "high")]
    assert ch[1]["new"]["score"] == "2.00" and ch[1]["new"]["keyword_family"] == {}


def test_unknown_changes_are_high():
    assert classify({"object": "node", "op": "changed", "field": "mystery"}).as_dict() == {"level": "high", "reason": "unclassified", "structural": False}
    assert classify({"object": "wiki_page", "op": "added"}).level == "high"
    assert classify({"object": "edge", "key": "garbled", "op": "added"}).level == "high"
    assert classify({"object": "region", "key": "x|y|2027", "op": "changed", "field": "weight"}).level == "high"
    assert classify({"object": "item_tag", "op": "changed", "field": "main_node_id"}).level == "high"


def test_change_types_match_db_signer_rule():
    assert [ct for ct in ("node_add", "node_split", "node_merge", "node_retire", "node_revise", "edge_add", "edge_remove",
                          "projection_fix", "other") if classify_change_type(ct).structural] == \
        ["node_add", "node_split", "node_merge", "node_retire", "edge_add", "edge_remove"]
    assert classify_change_type("bogus").reason == "unclassified"


def test_output_is_deterministic():
    r = rows(V2)
    r2 = copy.deepcopy(r)
    r2["knowledge_node"].reverse()
    r2["knowledge_edge"].reverse()
    r["knowledge_node"][1] = node("MATH.F.T.B", name="改名")
    r2["knowledge_node"][1] = node("MATH.F.T.B", name="改名")
    a = canonical_json(diff(snap(V1), Snapshot.from_rows(V2, r)))
    b = canonical_json(diff(snap(V1), Snapshot.from_rows(V2, r2)))
    assert a == b and a.encode() == canonical_json(diff(snap(V1), Snapshot.from_rows(V2, r))).encode()
