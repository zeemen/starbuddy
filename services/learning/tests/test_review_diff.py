"""审核台差异与风险分类单测（spec 冲刺 1 验收“差异与分类”）。全部合成数据。"""
from __future__ import annotations

import copy
import re
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

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
    # 阶段 0 中风险按高风险审（spec 待人确认 #7）
    assert classify(ch[0]).as_dict() == {"level": "medium", "review_level": "high", "reason": "boundary_supplement", "structural": False}
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


def test_textbook_page_change_is_high_and_quote_change_is_remove_add():
    r = rows(V2)
    r["textbook_projection"] = [dict(r["textbook_projection"][0], page_to=12)]
    ch = diff(snap(V1), Snapshot.from_rows(V2, r))
    assert [(c["field"], c["old"], c["new"]) for c in ch] == [("page_to", 11, 12)]
    assert classify(ch[0]).as_dict() == {"level": "high", "review_level": "high", "reason": "textbook_quote_page", "structural": False}
    assert touched_nodes(ch) == (["MATH.F.T.A"], [])
    r["textbook_projection"] = [dict(r["textbook_projection"][0], quote_hash=bytes.fromhex("cd" * 32), quote_text="新原句")]
    ch = diff(snap(V1), Snapshot.from_rows(V2, r))
    assert [(c["op"], (c["old"] or c["new"])["quote_hash"][:4]) for c in ch] == [("removed", "abab"), ("added", "cdcd")]
    assert ch[1]["new"]["quote_text"] == "新原句"  # 原句本身也给审核人看（审查 B-2）


def test_duplicate_textbook_rows_keep_pairing_when_one_page_moves():
    # 同节点同章两条投影：只挪一条的页码，另一条不应显示为变化（契约审查 S5）
    r1, r2 = rows(V1), rows(V2)
    second = dict(r1["textbook_projection"][0], id="zz", page_from=20, page_to=21, quote_hash=bytes.fromhex("ef" * 32))
    r1["textbook_projection"].append(second)
    r2["textbook_projection"].append(dict(second, page_from=30, page_to=31))
    ch = diff(Snapshot.from_rows(V1, r1), Snapshot.from_rows(V2, r2))
    assert [(c["key"][-12:], c["field"], c["old"], c["new"]) for c in ch] == [("efefefefefef", "page_from", 20, 30), ("efefefefefef", "page_to", 21, 31)]
    r2["textbook_projection"].append(dict(second, id="yy"))
    assert sorted(Snapshot.from_rows(V2, r2).textbook)[-1].endswith("#2")


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
    assert classify({"object": "node", "op": "changed", "field": "mystery"}).as_dict() == {"level": "high", "review_level": "high", "reason": "unclassified", "structural": False}
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


def test_structural_only_when_an_active_side_is_touched():
    # 本来停用的前置边被删、新增一条停用节点：与 kge.structural_diff 一致，不算结构性（契约审查 S1）
    r1, r2 = rows(V1), rows(V2)
    r1["knowledge_edge"][0] = dict(r1["knowledge_edge"][0], status="deprecated")
    r2["knowledge_edge"] = r2["knowledge_edge"][1:]
    r2["knowledge_node"].append(node("MATH.F.T.D", status="deprecated"))
    ch = diff(Snapshot.from_rows(V1, r1), Snapshot.from_rows(V2, r2))
    assert [(c["object"], c["op"], classify(c).structural) for c in ch] == [("node", "added", False), ("edge", "removed", False)]
    assert not summarize(ch)["structural"]


def test_prerequisite_evidence_ref_is_high():
    r = rows(V2)
    r["knowledge_edge"][0] = dict(r["knowledge_edge"][0], evidence_ref="syn-evidence-1")
    ch = diff(snap(V1), Snapshot.from_rows(V2, r))
    assert [(c["field"], classify(c).level) for c in ch] == [("evidence_ref", "high")]


def test_duplicate_current_affairs_are_all_listed():
    ca = {"id": "c1", "node_ids": ["MATH.F.T.A"], "source": "syn-source", "valid_from": date(2027, 1, 1),
          "valid_until": date(2027, 6, 1), "review_status": "pending"}
    s = snap(V2, current_affair=[ca, dict(ca, id="c2")])
    assert len(s.current_affairs) == 2  # 同来源同生效日两行都在（安全审查 A-2）
    ch = diff(snap(V1), s)
    assert [(c["object"], c["op"], classify(c).level) for c in ch] == [("current_affair", "added", "medium")] * 2
    assert s.refs["c1"] == [("current_affair", "syn-source|2027-01-01|2027-06-01|MATH.F.T.A")]


def test_item_family_and_id_migration_basis_are_in_diff():
    fam = [{"code": "fam-01", "main_node_id": "MATH.F.T.A"}]
    ch = diff(snap(V1), snap(V2, item_family=fam, id_migration=[{"old_id": "K-SYN-0001", "new_id": "MATH.F.T.A", "rule": "rename",
                                                              "change_id": uuid.UUID("33333333-0000-4000-8000-000000000003")}]))
    assert [(c["object"], c["op"], c["field"], classify(c).level) for c in ch] == [
        ("id_migration", "changed", "change_id", "high"), ("item_family", "added", None, "low")]
    ch = diff(snap(V1, item_family=fam), snap(V2, item_family=[dict(fam[0], main_node_id="MATH.F.T.B")]))
    assert [(c["field"], classify(c).level) for c in ch] == [("main_node_id", "high")]


def test_snapshot_covers_every_digest_section():
    # 签字人签的不能多于看到的：kge.map_version_digest 的每一节都要在快照里（契约审查 B1）
    sql = (Path(__file__).resolve().parents[3] / "db/migrations/0019_kge__review_signoff.up.sql").read_text(encoding="utf-8")
    body = sql[sql.index("CREATE FUNCTION kge.map_version_digest"):sql.index("ALTER FUNCTION kge.map_version_digest")]
    sections = set(re.findall(r"'(\w+)', \(SELECT", body))
    covered = {"version": None, "nodes": "nodes", "edges": "edges", "textbook": "textbook", "region": "region", "skill_links": "skill_links",
               "id_migrations": "id_migrations", "item_families": "item_families", "item_tags": "item_tags",
               "scoring_points": "scoring_points", "current_affairs": "current_affairs"}
    assert sections == set(covered)
    for attr in filter(None, covered.values()):
        assert hasattr(Snapshot.empty(), attr)


def test_validation_issue_refs():
    s = snap(V2, knowledge_edge=[dict(rows(V2)["knowledge_edge"][0], id="e1")])
    assert s.refs["knowledge_edge:e1"] == s.refs["MATH.F.T.B -> MATH.F.T.A"] == [("edge", "MATH.F.T.B -prerequisite-> MATH.F.T.A")]
    assert s.refs["skill_link:aya:K-SYN-0001"] == [("skill_link", "MATH.F.T.A|aya|K-SYN-0001")]
    assert s.refs["MATH.F.T.A"] == [("node", "MATH.F.T.A")]


def test_touched_nodes_include_edge_targets_and_removed():
    r = rows(V2)
    r["knowledge_node"] = r["knowledge_node"][:2]
    r["knowledge_edge"] = [r["knowledge_edge"][0], {"from_id": "MATH.F.T.A", "to_map_version_id": V2, "to_id": "MATH.F.T.B",
                                                    "type": "related", "strength": "weak", "evidence_ref": None, "status": "active"}]
    ch = diff(snap(V1), Snapshot.from_rows(V2, r))
    assert touched_nodes(ch) == (["MATH.F.T.A", "MATH.F.T.B"], ["MATH.F.T.C"])
