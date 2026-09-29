"""公共层原子与地图快照（spec 设计要点 3：编译器输入）。

本 PR 的“kge 数据”由 JSON / YAML 快照喂入（合成夹具）；`AtomSource` 是数据源接口，PR ② 接数据库（只读 kge.*）。

原子键（页面头部 atom_refs 的 type + id，正文引用写成 `[@type:id]`）：
  node:<节点 ID>                       kge.knowledge_node（含快照里的课标承接 curriculum_links）
  edge:<from_id>><type>><to_id>         kge.knowledge_edge（prerequisite：from 是 to 的前置）
  curriculum:<课标年份>/<code>          kge.curriculum_item
  projection:<id>                       kge.textbook_projection（原句 quote_text 只参与哈希，不写进页面）
  region:<id>                           kge.region_projection
  affair:<id>                           kge.current_affair（带有效期）

快照只收下面白名单里的字段，任何未知字段（包括任何个人层字段）一律拒收（fail closed，规则 13）。
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping, Protocol

import yaml

from .nodeid import SUBJECT_PREFIX, is_node_id

ATOM_TYPES = ("node", "edge", "curriculum", "projection", "region", "affair")
HASH_PREFIX = "sha256:"
HASH_HEX_LEN = 16  # 截断到 16 位十六进制：只用于变更检测，不作安全用途

_FIELDS: dict[str, dict[str, bool]] = {  # 字段 → 是否必填
    "node": {"id": True, "level": True, "parent_id": False, "kind": False, "name": True, "includes": False, "excludes": False,
             "examples": False, "scope": False, "cognitive_levels": False, "status": False},
    "edge": {"from_id": True, "to_id": True, "type": True, "strength": False, "status": False},
    "curriculum": {"standard_year": True, "code": True, "domain": True, "text": True, "verb_level": True, "source_ref": True},
    "curriculum_link": {"node_id": True, "standard_year": True, "code": True},
    "projection": {"id": True, "node_id": True, "edition": True, "edition_year": True, "printing": False, "volume": True,
                   "chapter": True, "page_from": True, "page_to": True, "quote_text": False, "mapping_state": False},
    "region": {"id": True, "node_id": True, "region": True, "exam_year": True, "weight": True, "typical_slots": False,
               "style_tags": False, "source_ref": True},
    "affair": {"id": True, "node_ids": True, "source": True, "valid_from": True, "valid_until": True, "review_status": False},
}
_TOP = {"map_version": True, "subject": True, "status": True, "standard_year": True, "nodes": True, "edges": False,
        "curriculum_items": False, "curriculum_links": False, "textbook_projections": False, "region_projections": False,
        "current_affairs": False}
_LISTS = {"nodes": "node", "edges": "edge", "curriculum_items": "curriculum", "curriculum_links": "curriculum_link",
          "textbook_projections": "projection", "region_projections": "region", "current_affairs": "affair"}
EDGE_TYPES = ("prerequisite", "part_of", "related", "confusable", "uses_method", "has_pitfall", "aligned_to")
VERB_LEVELS = {"know": "知道", "understand": "理解", "apply": "运用", "synthesize": "综合"}


class SnapshotError(ValueError):
    """快照不合格式；消息只含字段名与编号"""


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def atom_hash(record: Mapping[str, Any]) -> str:
    return HASH_PREFIX + hashlib.sha256(canonical_json(dict(record)).encode("utf-8")).hexdigest()[:HASH_HEX_LEN]


def edge_key(from_id: str, etype: str, to_id: str) -> str:
    return f"{from_id}>{etype}>{to_id}"


@dataclass(frozen=True)
class Atom:
    type: str
    id: str
    record: Mapping[str, Any] = field(repr=False)
    hash: str = ""

    @property
    def key(self) -> str:
        return f"{self.type}:{self.id}"


@dataclass(frozen=True)
class Snapshot:
    map_version: str
    subject: str
    status: str
    standard_year: int
    atoms: Mapping[str, Atom]  # key = "type:id"

    def get(self, atype: str, aid: str) -> Atom | None:
        return self.atoms.get(f"{atype}:{aid}")

    def of_type(self, atype: str) -> list[Atom]:
        return sorted((a for a in self.atoms.values() if a.type == atype), key=lambda a: a.id)

    def node(self, node_id: str) -> Mapping[str, Any] | None:
        a = self.get("node", node_id)
        return a.record if a else None

    def edges_to(self, node_id: str, etype: str) -> list[Atom]:
        return [a for a in self.of_type("edge") if a.record["to_id"] == node_id and a.record["type"] == etype
                and a.record.get("status", "active") == "active"]

    def edges_from(self, node_id: str, etype: str) -> list[Atom]:
        return [a for a in self.of_type("edge") if a.record["from_id"] == node_id and a.record["type"] == etype
                and a.record.get("status", "active") == "active"]

    def curriculum_of(self, node_id: str) -> list[Atom]:
        node = self.node(node_id)
        keys = [f"{c['standard_year']}/{c['code']}" for c in (node or {}).get("curriculum", [])]
        return [a for k in keys if (a := self.get("curriculum", k)) is not None]

    def projections_of(self, node_id: str, atype: str) -> list[Atom]:
        return [a for a in self.of_type(atype) if a.record["node_id"] == node_id]

    def affairs_of(self, node_id: str) -> list[Atom]:
        return [a for a in self.of_type("affair") if node_id in a.record["node_ids"]]

    def quote_texts(self) -> list[tuple[str, str]]:
        """（投影 ID，教材原句）——体检用来拦“教材原句写进页面”"""
        return [(a.id, a.record["quote_text"]) for a in self.of_type("projection") if a.record.get("quote_text")]


class AtomSource(Protocol):
    """编译器与体检的数据源接口：FileSnapshotSource（文件快照）；knowledge.service.reader.DbAtomSource（只读 kge.* 已发布版本）"""

    def load(self) -> Snapshot: ...


def _pick(kind: str, raw: Any, where: str) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise SnapshotError(f"{where}: 必须是对象")
    spec = _FIELDS[kind]
    unknown = sorted(set(raw) - set(spec))
    if unknown:
        raise SnapshotError(f"{where}: 未知字段 {unknown}（快照只收公共层白名单字段）")
    missing = [k for k, req in spec.items() if req and raw.get(k) in (None, "")]
    if missing:
        raise SnapshotError(f"{where}: 缺字段 {missing}")
    out = {k: raw[k] for k in spec if k in raw}
    for k, v in list(out.items()):
        if isinstance(v, dt.date):
            out[k] = v.isoformat()
    return out


def parse_snapshot(doc: Any) -> Snapshot:
    if not isinstance(doc, dict):
        raise SnapshotError("快照必须是对象")
    unknown = sorted(set(doc) - set(_TOP))
    if unknown:
        raise SnapshotError(f"快照顶层未知字段 {unknown}")
    missing = [k for k, req in _TOP.items() if req and doc.get(k) in (None, "")]
    if missing:
        raise SnapshotError(f"快照顶层缺字段 {missing}")
    subject = doc["subject"]
    if subject not in SUBJECT_PREFIX:
        raise SnapshotError("subject 只收平台学科编码")
    lists: dict[str, list[dict[str, Any]]] = {}
    for key, kind in _LISTS.items():
        raw = doc.get(key) or []
        if not isinstance(raw, list):
            raise SnapshotError(f"{key}: 必须是数组")
        lists[key] = [_pick(kind, r, f"{key}[{i}]") for i, r in enumerate(raw)]

    links: dict[str, list[dict[str, Any]]] = {}
    for ln in lists["curriculum_links"]:
        links.setdefault(ln["node_id"], []).append({"standard_year": ln["standard_year"], "code": ln["code"]})

    atoms: dict[str, Atom] = {}

    def add(atype: str, aid: str, record: dict[str, Any]) -> None:
        key = f"{atype}:{aid}"
        if key in atoms:
            raise SnapshotError(f"原子重复：{key}")
        atoms[key] = Atom(atype, aid, MappingProxyType(record), atom_hash({"type": atype, **record}))

    prefix = SUBJECT_PREFIX[subject]
    for n in lists["nodes"]:
        if not is_node_id(n["id"]) or n["id"].split(".")[0] != prefix:
            raise SnapshotError(f"节点 ID 不合规范或学科前缀不符：{n['id']!r}")
        rec = dict(n)
        rec["curriculum"] = sorted(links.get(n["id"], []), key=lambda c: (c["standard_year"], c["code"]))
        add("node", n["id"], rec)
    for e in lists["edges"]:
        if e["type"] not in EDGE_TYPES:
            raise SnapshotError(f"边类型不合法：{e['type']!r}")
        add("edge", edge_key(e["from_id"], e["type"], e["to_id"]), e)
    for c in lists["curriculum_items"]:
        if c["verb_level"] not in VERB_LEVELS:
            raise SnapshotError(f"课标条目 {c['code']} 的 verb_level 不合法")
        add("curriculum", f"{c['standard_year']}/{c['code']}", c)
    for p in lists["textbook_projections"]:
        add("projection", str(p["id"]), p)
    for r in lists["region_projections"]:
        add("region", str(r["id"]), r)
    for a in lists["current_affairs"]:
        add("affair", str(a["id"]), a)
    for key, a in atoms.items():  # 引用闭合：边、投影、承接都必须指向快照里的节点
        refs = {"edge": ("from_id", "to_id"), "projection": ("node_id",), "region": ("node_id",)}.get(a.type, ())
        for f in refs:
            if f"node:{a.record[f]}" not in atoms:
                raise SnapshotError(f"{key}: {f} 指向不存在的节点")
    for nid in links:
        if f"node:{nid}" not in atoms:
            raise SnapshotError(f"curriculum_links: 节点 {nid} 不存在")
    return Snapshot(str(doc["map_version"]), subject, str(doc["status"]), int(doc["standard_year"]), MappingProxyType(atoms))


def read_structured(path: str | Path) -> Any:
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    return json.loads(text) if p.suffix == ".json" else yaml.safe_load(text)


@dataclass(frozen=True)
class FileSnapshotSource:
    path: Path

    def load(self) -> Snapshot:
        return parse_snapshot(read_structured(self.path))


def load_snapshot(path: str | Path) -> Snapshot:
    return FileSnapshotSource(Path(path)).load()


def diff_snapshots(old: Snapshot, new: Snapshot) -> dict[str, str]:
    """原子键 → added | removed | modified"""
    out: dict[str, str] = {}
    for k in sorted(set(old.atoms) | set(new.atoms)):
        a, b = old.atoms.get(k), new.atoms.get(k)
        if a is None:
            out[k] = "added"
        elif b is None:
            out[k] = "removed"
        elif a.hash != b.hash:
            out[k] = "modified"
    return out
