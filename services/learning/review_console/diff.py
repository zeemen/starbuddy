"""地图版本差异：纯函数，同一输入逐字节相同的输出（spec 设计要点 4）。

输入是两份“快照”（见 snapshot.py 的 Snapshot），基准为同学科当前已发布版本；没有已发布版本时 base 为 None，
全部按新增列出。输出是按固定顺序排好的差异条目列表，每条是只含 str / int / list / None 的字典，可直接 JSON 序列化。
"""
from __future__ import annotations

import json
from typing import Any

from .snapshot import NODE_FIELDS, Snapshot

# 对象顺序：骨架在前，原子在后（与审核界面的分组一致）
OBJECT_ORDER = ("version", "node", "edge", "textbook", "region", "skill_link", "id_migration", "item_family", "item_tag", "scoring_point",
                "current_affair")

Change = dict[str, Any]


def _change(obj: str, key: str, op: str, field: str | None = None, old: Any = None, new: Any = None) -> Change:
    return {"object": obj, "key": key, "op": op, "field": field, "old": old, "new": new}


def _keyed(obj: str, base: dict[str, dict], draft: dict[str, dict], fields: tuple[str, ...], status_field: str | None = None) -> list[Change]:
    out: list[Change] = []
    for key in sorted(set(base) | set(draft)):
        a, b = base.get(key), draft.get(key)
        if a is None:
            out.append(_change(obj, key, "added", new=b))
            continue
        if b is None:
            out.append(_change(obj, key, "removed", old=a))
            continue
        if status_field and a.get(status_field) != b.get(status_field):
            op = "deprecated" if b.get(status_field) == "deprecated" else "reactivated"
            out.append(_change(obj, key, op, status_field, a.get(status_field), b.get(status_field)))
        for f in fields:
            if f != status_field and a.get(f) != b.get(f):
                out.append(_change(obj, key, "changed", f, a.get(f), b.get(f)))
    return out


def diff(base: Snapshot | None, draft: Snapshot) -> list[Change]:
    empty = Snapshot.empty()
    b = base or empty
    out: list[Change] = []
    out += _keyed("version", b.meta, draft.meta, ("standard_year",))
    out += _keyed("node", b.nodes, draft.nodes, NODE_FIELDS, "status")
    out += _keyed("edge", b.edges, draft.edges, ("strength", "evidence_ref", "status"), "status")
    out += _keyed("textbook", b.textbook, draft.textbook,
                  ("edition_year", "printing", "page_from", "page_to", "quote_text", "quote_hash", "verified_by", "verified_at",
                   "mapping_state"))
    out += _keyed("region", b.region, draft.region, ("weight", "typical_slots", "style_tags", "source_ref"))
    out += _keyed("skill_link", b.skill_links, draft.skill_links, ("weight",))
    out += _keyed("id_migration", b.id_migrations, draft.id_migrations, ("rule", "change_id"))
    out += _keyed("item_family", b.item_families, draft.item_families, ("main_node_id",))
    out += _keyed("item_tag", b.item_tags, draft.item_tags,
                  ("main_node_id", "node_ids", "cognitive_level", "prior_difficulty", "family", "source_kind", "license_state",
                   "quality_grade", "tag_review_status", "tag_source", "tag_confidence", "tagger_version", "tag_model_version",
                   "tag_basis"))
    out += _keyed("scoring_point", b.scoring_points, draft.scoring_points,
                  ("score", "node_ids", "subject_dimension", "keyword_family", "required_logic", "material_linkage",
                   "partial_credit_rule", "common_errors"))
    out += _keyed("current_affair", b.current_affairs, draft.current_affairs, ("node_ids", "valid_until", "review_status"))
    order = {o: i for i, o in enumerate(OBJECT_ORDER)}
    out.sort(key=lambda c: (order[c["object"]], c["key"], c["field"] or "", c["op"]))
    return out


def canonical_json(value: Any) -> str:
    """差异与报告的规范序列化：键排序、无多余空白、保留中文。"""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def touched_nodes(changes: list[Change]) -> tuple[list[str], list[str]]:
    """差异涉及的节点 ID（影响分析用）：（草稿里仍在的，只在基准版本里的）。
    节点本身的变化、边的两端、投影与挂接所在的节点都算（审查 S7）。"""
    ids: set[str] = set()
    removed: set[str] = set()
    for c in changes:
        if c["object"] == "node":
            (removed if c["op"] == "removed" else ids).add(c["key"])
        elif c["object"] == "edge":
            parts = c["key"].split(" ")
            ids.add(parts[0])
            if len(parts) == 3 and "@" not in parts[2]:
                ids.add(parts[2])
        elif c["object"] in ("textbook", "region", "skill_link"):
            ids.add(c["key"].split("|", 1)[0])
    return sorted(ids - removed), sorted(removed)
