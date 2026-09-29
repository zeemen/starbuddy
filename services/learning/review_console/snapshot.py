"""地图版本快照：把 kge.* 的行转成与版本 ID、行 UUID 无关的键值（两个版本之间才能按键比较）。

键的写法：
- 节点：节点 ID；
- 边：`from -type-> to`，跨版本边在 to 后加 `@目标版本`；
- 教材投影：`节点|版次|册|章`，同键多行按起始页、ID 排序后加 `#2`、`#3`；
- 地区投影：`节点|地区|年份`；能力视图挂接：`节点|学科|local_id`；
- 旧编码迁移：`旧编码|新 ID`（撤销为 `旧编码|-`）；
- 题目标签：`学科|题目`；采分点：`学科|题目|rubric_version|采分点`；时政：`来源|生效日`。
值只含 str / int / list / dict / None：小数转字符串、日期转 ISO、哈希转十六进制，保证序列化稳定。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Iterable, Mapping
from uuid import UUID

NODE_FIELDS = ("level", "parent_id", "kind", "name", "includes", "excludes", "examples", "scope", "cognitive_levels", "error_type", "status")
JSON_FIELDS = ("keyword_family", "partial_credit_rule", "common_errors")


def norm(v: Any) -> Any:
    if isinstance(v, UUID):
        return str(v)
    if isinstance(v, Decimal):
        return format(v, "f")
    if isinstance(v, datetime):
        return v.astimezone(timezone.utc).isoformat()
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, (bytes, bytearray, memoryview)):
        return bytes(v).hex()
    if isinstance(v, (list, tuple)):
        return [norm(x) for x in v]
    if isinstance(v, dict):
        return {str(k): norm(x) for k, x in v.items()}
    return v


def _json(v: Any) -> Any:
    return json.loads(v) if isinstance(v, str) else norm(v)


@dataclass
class Snapshot:
    version_id: str
    digest: str = ""
    nodes: dict[str, dict] = field(default_factory=dict)
    edges: dict[str, dict] = field(default_factory=dict)
    textbook: dict[str, dict] = field(default_factory=dict)
    region: dict[str, dict] = field(default_factory=dict)
    skill_links: dict[str, dict] = field(default_factory=dict)
    id_migrations: dict[str, dict] = field(default_factory=dict)
    item_tags: dict[str, dict] = field(default_factory=dict)
    scoring_points: dict[str, dict] = field(default_factory=dict)
    current_affairs: dict[str, dict] = field(default_factory=dict)

    @staticmethod
    def empty() -> "Snapshot":
        return Snapshot(version_id="")

    @staticmethod
    def from_rows(version_id: str, rows: Mapping[str, Iterable[Mapping[str, Any]]]) -> "Snapshot":
        """rows 的键是表名（不带 schema），值是该版本的行。item_tag 行需带 `family`（题目族 code，没有为 None）。"""
        s = Snapshot(version_id=str(version_id))
        for r in rows.get("knowledge_node", ()):
            s.nodes[r["id"]] = {f: norm(r[f]) for f in NODE_FIELDS}
        for r in rows.get("knowledge_edge", ()):
            to = r["to_id"] if str(r["to_map_version_id"]) == s.version_id else f'{r["to_id"]}@{r["to_map_version_id"]}'
            s.edges[f'{r["from_id"]} -{r["type"]}-> {to}'] = {f: norm(r[f]) for f in ("strength", "evidence_ref", "status")}
        tb = sorted(rows.get("textbook_projection", ()),
                    key=lambda r: (r["node_id"], r["edition"], r["volume"], r["chapter"], r["page_from"], str(r["id"])))
        for r in tb:
            base = f'{r["node_id"]}|{r["edition"]}|{r["volume"]}|{r["chapter"]}'
            key, n = base, 1
            while key in s.textbook:
                n += 1
                key = f"{base}#{n}"
            s.textbook[key] = {f: norm(r[f]) for f in ("edition_year", "printing", "page_from", "page_to", "quote_hash",
                                                        "verified_by", "verified_at", "mapping_state")}
        for r in rows.get("region_projection", ()):
            s.region[f'{r["node_id"]}|{r["region"]}|{r["exam_year"]}'] = {
                f: norm(r[f]) for f in ("weight", "typical_slots", "style_tags", "source_ref")}
        for r in rows.get("skill_link", ()):
            s.skill_links[f'{r["node_id"]}|{r["subject"]}|{r["local_id"]}'] = {"weight": norm(r["weight"])}
        for r in rows.get("id_migration", ()):
            s.id_migrations[f'{r["old_id"]}|{r["new_id"] or "-"}'] = {"rule": r["rule"]}
        for r in rows.get("item_tag", ()):
            s.item_tags[f'{r["subject"]}|{r["item_id"]}'] = {
                f: norm(r[f]) for f in ("main_node_id", "node_ids", "cognitive_level", "prior_difficulty", "family", "source_kind",
                                        "license_state", "quality_grade", "tag_review_status")}
        for r in rows.get("scoring_point", ()):
            d = {f: norm(r[f]) for f in ("score", "node_ids", "subject_dimension", "required_logic", "material_linkage")}
            d.update({f: _json(r[f]) for f in JSON_FIELDS})
            s.scoring_points[f'{r["subject"]}|{r["item_id"]}|{r["rubric_version"]}|{r["point_id"]}'] = d
        for r in rows.get("current_affair", ()):
            s.current_affairs[f'{r["source"]}|{norm(r["valid_from"])}'] = {
                f: norm(r[f]) for f in ("node_ids", "valid_until", "review_status")}
        return s
