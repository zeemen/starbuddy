"""地图版本快照：把 kge.* 的行转成与版本 ID、行 UUID 无关的键值（两个版本之间才能按键比较）。

键的写法：
- 节点：节点 ID；
- 边：`from -type-> to`，跨版本边在 to 后加 `@目标版本`；
- 教材投影：`节点|版次|册|章`，同键多行按起始页、ID 排序后加 `#2`、`#3`；
- 地区投影：`节点|地区|年份`；能力视图挂接：`节点|学科|local_id`；
- 教材投影的键含原句哈希前 12 位（没有原句为 `-`）：只改页码时仍对得上，改原句显示为删一条、加一条（审查 S5）；
- 旧编码迁移：`旧编码|新 ID`（撤销为 `旧编码|-`）；
- 题目族：`code`；题目标签：`学科|题目`；采分点：`rubric_version|学科|题目|采分点`（按评分版本分组）；
- 时政：`来源|生效日|失效日|节点`，同键多行加 `#2`（库里这几列没有唯一约束，审查 A-2）。
快照必须覆盖 kge.map_version_digest 的每一类内容，签字人签的不能多于看到的（契约审查 B1，单测核对）。
`refs` 把 kge.validate_map_version 返回的 object_id 映射到差异条目（审查 S6）。
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
TAG_SOURCE_FIELDS = ("tag_source", "tag_confidence", "tagger_version", "tag_model_version")
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
    # 版本元数据：摘要里有学科、版本号、课标年份。学科不会变，版本号本来就要变（发布规则单独检查），差异只比课标年份
    meta: dict[str, dict] = field(default_factory=dict)
    nodes: dict[str, dict] = field(default_factory=dict)
    edges: dict[str, dict] = field(default_factory=dict)
    textbook: dict[str, dict] = field(default_factory=dict)
    region: dict[str, dict] = field(default_factory=dict)
    skill_links: dict[str, dict] = field(default_factory=dict)
    id_migrations: dict[str, dict] = field(default_factory=dict)
    item_families: dict[str, dict] = field(default_factory=dict)
    item_tags: dict[str, dict] = field(default_factory=dict)
    scoring_points: dict[str, dict] = field(default_factory=dict)
    current_affairs: dict[str, dict] = field(default_factory=dict)
    refs: dict[str, list[tuple[str, str]]] = field(default_factory=dict)

    @staticmethod
    def empty() -> "Snapshot":
        return Snapshot(version_id="")

    def _ref(self, ref: str, obj: str, key: str) -> None:
        self.refs.setdefault(ref, []).append((obj, key))

    @staticmethod
    def _unique(target: dict, base: str) -> str:
        key, n = base, 1
        while key in target:
            n += 1
            key = f"{base}#{n}"
        return key

    @staticmethod
    def from_rows(version_id: str, rows: Mapping[str, Iterable[Mapping[str, Any]]]) -> "Snapshot":
        """rows 的键是表名（不带 schema），值是该版本的行。item_tag 行需带 `family`（题目族 code，没有为 None）。"""
        s = Snapshot(version_id=str(version_id))
        for r in rows.get("map_version", ()):
            s.meta["map_version"] = {"standard_year": norm(r["standard_year"])}
        for r in rows.get("knowledge_node", ()):
            s.nodes[r["id"]] = {f: norm(r[f]) for f in NODE_FIELDS}
            s._ref(r["id"], "node", r["id"])
        for r in rows.get("knowledge_edge", ()):
            to = r["to_id"] if str(r["to_map_version_id"]) == s.version_id else f'{r["to_id"]}@{r["to_map_version_id"]}'
            key = f'{r["from_id"]} -{r["type"]}-> {to}'
            s.edges[key] = {f: norm(r[f]) for f in ("strength", "evidence_ref", "status")}
            if r.get("id") is not None:
                s._ref(f'knowledge_edge:{r["id"]}', "edge", key)
            if r["type"] == "prerequisite":
                s._ref(f'{r["from_id"]} -> {r["to_id"]}', "edge", key)  # 传递边问题的写法
        tb = sorted(rows.get("textbook_projection", ()),
                    key=lambda r: (r["node_id"], r["edition"], r["volume"], r["chapter"], norm(r["quote_hash"]) or "",
                                   r["page_from"], r["page_to"], str(r.get("id"))))
        for r in tb:
            qh = norm(r["quote_hash"])
            key = s._unique(s.textbook, f'{r["node_id"]}|{r["edition"]}|{r["volume"]}|{r["chapter"]}|{qh[:12] if qh else "-"}')
            s.textbook[key] = {f: norm(r.get(f)) for f in ("edition_year", "printing", "page_from", "page_to", "quote_text", "quote_hash",
                                                            "verified_by", "verified_at", "mapping_state")}
            if r.get("id") is not None:
                s._ref(f'textbook_projection:{r["id"]}', "textbook", key)
        for r in rows.get("region_projection", ()):
            key = f'{r["node_id"]}|{r["region"]}|{r["exam_year"]}'
            s.region[key] = {f: norm(r[f]) for f in ("weight", "typical_slots", "style_tags", "source_ref")}
            if r.get("id") is not None:
                s._ref(f'region_projection:{r["id"]}', "region", key)
        for r in rows.get("skill_link", ()):
            key = f'{r["node_id"]}|{r["subject"]}|{r["local_id"]}'
            s.skill_links[key] = {"weight": norm(r["weight"])}
            s._ref(f'skill_link:{r["subject"]}:{r["local_id"]}', "skill_link", key)
        for r in rows.get("id_migration", ()):
            s.id_migrations[f'{r["old_id"]}|{r["new_id"] or "-"}'] = {"rule": r["rule"], "change_id": norm(r.get("change_id"))}
        for r in rows.get("item_family", ()):
            s.item_families[r["code"]] = {"main_node_id": r["main_node_id"]}
        for r in rows.get("item_tag", ()):
            key = f'{r["subject"]}|{r["item_id"]}'
            s.item_tags[key] = {
                f: norm(r[f]) for f in ("main_node_id", "node_ids", "cognitive_level", "prior_difficulty", "family", "source_kind",
                                        "license_state", "quality_grade", "tag_review_status")}
            # 0024 起的打标来源、置信度、版本与依据（审查建议：审核员要看得到来源变化；0024 之前的行没有这些列）
            s.item_tags[key].update({f: norm(r.get(f)) for f in TAG_SOURCE_FIELDS})
            s.item_tags[key]["tag_basis"] = _json(r.get("tag_basis", []))
            s._ref(f'item_tag:{r["subject"]}:{r["item_id"]}', "item_tag", key)
        for r in rows.get("scoring_point", ()):
            d = {f: norm(r[f]) for f in ("score", "node_ids", "subject_dimension", "required_logic", "material_linkage")}
            d.update({f: _json(r[f]) for f in JSON_FIELDS})
            key = f'{r["rubric_version"]}|{r["subject"]}|{r["item_id"]}|{r["point_id"]}'
            s.scoring_points[key] = d
            if r.get("id") is not None:
                s._ref(f'scoring_point:{r["id"]}', "scoring_point", key)
        ca = sorted(rows.get("current_affair", ()),
                    key=lambda r: (r["source"], norm(r["valid_from"]), norm(r["valid_until"]), sorted(r["node_ids"]),
                                   r["review_status"], str(r.get("id"))))
        for r in ca:
            key = s._unique(s.current_affairs, f'{r["source"]}|{norm(r["valid_from"])}|{norm(r["valid_until"])}|{",".join(sorted(r["node_ids"]))}')
            s.current_affairs[key] = {f: norm(r[f]) for f in ("node_ids", "valid_until", "review_status")}
            if r.get("id") is not None:
                s._ref(f'current_affair:{r["id"]}', "current_affair", key)
                s._ref(str(r["id"]), "current_affair", key)  # 过期时政问题用裸 ID
        return s
