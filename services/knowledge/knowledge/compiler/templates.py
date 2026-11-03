"""锁定段与渲染段的模板（spec 设计要点 3“渲染重建”）：只从公共层原子生成，同样的快照总是生成同样的文本。

每个生成的自然段都带原子引用 `[@type:id]`（KGE 第九章“引用完整率 100%”）。教材原句不写进页面，只放占位
`{{quote:<投影 ID>}}`，读取时按学生版次渲染（KGE V1.1 修订 3）。
"""
from __future__ import annotations

from collections.abc import Iterable

from ..atoms import VERB_LEVELS, Atom, Snapshot
from ..pages.format import SECTIONS_BY_TYPE, AtomRef, Section

EMPTY = "（暂无）"


def _title(sid: str, page_type: str = "kp") -> str:
    return next(s.title for s in SECTIONS_BY_TYPE[page_type] if s.id == sid)


def _cite(atom: Atom) -> str:
    return f"[@{atom.type}:{atom.id}]"


def _name(snapshot: Snapshot, node_id: str, known_pages: Iterable[str]) -> str:
    node = snapshot.node(node_id)
    name = node["name"] if node else node_id
    return f"[[{node_id}]] {name}" if node_id in set(known_pages) else name


def _node(snapshot: Snapshot, node_id: str) -> Atom:
    atom = snapshot.get("node", node_id)
    if atom is None:
        raise KeyError(f"快照里没有节点 {node_id}")
    return atom


def definition(snapshot: Snapshot, node_id: str) -> tuple[str, list[Atom]]:
    atom = _node(snapshot, node_id)
    n = atom.record
    body = [f"## {_title('definition')}", "", f"**{n['name']}** {_cite(atom)}"]
    scope = []
    if n.get("includes"):
        scope.append("包括：" + "；".join(n["includes"]) + "。")
    if n.get("excludes"):
        scope.append("不包括：" + "；".join(n["excludes"]) + "。")
    if scope:
        body += ["", "".join(scope) + f" {_cite(atom)}"]
    return "\n".join(body), [atom]


def curriculum(snapshot: Snapshot, node_id: str) -> tuple[str, list[Atom]]:
    items = snapshot.curriculum_of(node_id)
    body = [f"## {_title('curriculum')}", ""]
    if not items:
        node = _node(snapshot, node_id)
        body.append(f"{EMPTY} {_cite(node)}")
        return "\n".join(body), [node]
    for a in items:
        c = a.record
        body.append(f"- 【{VERB_LEVELS[c['verb_level']]}】{c['text']} {_cite(a)}")
    return "\n".join(body), items


def relations(snapshot: Snapshot, node_id: str, known_pages: Iterable[str]) -> tuple[str, list[Atom]]:
    known = list(known_pages)
    # 注意（KGE-07）：这里不分强弱。语文的层次递进写成 prerequisite + weak，编能力点（ability_kp）页之前须区分递进与前置，
    # 否则递进的上一级会被写成“前置”。本步不编能力点页，未改
    pre = snapshot.edges_to(node_id, "prerequisite")
    post = snapshot.edges_from(node_id, "prerequisite")
    node = _node(snapshot, node_id)
    body = [f"## {_title('relations')}", "", "前置："]
    body += [f"- {_name(snapshot, e.record['from_id'], known)} {_cite(e)}" for e in pre] or [f"- {EMPTY} {_cite(node)}"]
    body += ["", "后续："]
    body += [f"- {_name(snapshot, e.record['to_id'], known)} {_cite(e)}" for e in post] or [f"- {EMPTY} {_cite(node)}"]
    used = pre + post if pre and post else [*pre, *post, node]
    return "\n".join(body), used


def textbook(snapshot: Snapshot, node_id: str) -> tuple[str, list[Atom]]:
    items = sorted(snapshot.projections_of(node_id, "projection"),
                   key=lambda a: (a.record["edition"], a.record["edition_year"], a.record["volume"], a.record["page_from"], a.id))
    body = [f"## {_title('textbook')}", ""]
    if not items:
        node = _node(snapshot, node_id)
        body.append(f"{EMPTY} {_cite(node)}")
        return "\n".join(body), [node]
    for a in items:
        p = a.record
        pages = f"第 {p['page_from']} 页" if p["page_from"] == p["page_to"] else f"第 {p['page_from']}–{p['page_to']} 页"
        body.append(f"- {p['edition']}（{p['edition_year']}）{p['volume']} {p['chapter']}，{pages} {{{{quote:{a.id}}}}} {_cite(a)}")
    return "\n".join(body), items


def region(snapshot: Snapshot, node_id: str) -> tuple[str, list[Atom]]:
    items = sorted(snapshot.projections_of(node_id, "region"), key=lambda a: (a.record["region"], -int(a.record["exam_year"]), a.id))
    body = [f"## {_title('region')}", ""]
    if not items:
        node = _node(snapshot, node_id)
        body.append(f"{EMPTY} {_cite(node)}")
        return "\n".join(body), [node]
    for a in items:
        r = a.record
        slots = "、".join(r.get("typical_slots") or []) or "—"
        body.append(f"- {r['region']} {r['exam_year']}：权重 {float(r['weight']):.3f}；常见题位：{slots} {_cite(a)}")
    return "\n".join(body), items


# ---------- 素材卡（KGE-07 第一步；KGE 分册 :443） ----------

def _projections(snapshot: Snapshot, node_id: str) -> list[Atom]:
    return sorted(snapshot.projections_of(node_id, "projection"),
                  key=lambda a: (a.record["edition"], a.record["edition_year"], a.record["volume"], a.record["chapter"], a.record["page_from"], a.id))


def entry(snapshot: Snapshot, node_id: str) -> tuple[str, list[Atom]]:
    """词条（锁定段）：名称、释义要点（节点 includes）、不包括（节点 excludes）"""
    atom = _node(snapshot, node_id)
    n = atom.record
    body = [f"## {_title('entry', 'material')}", "", f"**{n['name']}** {_cite(atom)}"]
    if n.get("includes"):
        body += ["", "释义要点：" + "；".join(n["includes"]) + f"。 {_cite(atom)}"]
    if n.get("excludes"):
        body += ["", "不包括：" + "；".join(n["excludes"]) + f"。 {_cite(atom)}"]
    return "\n".join(body), [atom]


def source_text(snapshot: Snapshot, node_id: str) -> tuple[str, list[Atom]]:
    """原文（渲染段）：只放教材原句占位，原文不写进页面；没有投影时写暂无"""
    items = _projections(snapshot, node_id)
    body = [f"## {_title('source_text', 'material')}", ""]
    if not items:
        node = _node(snapshot, node_id)
        body.append(f"{EMPTY} {_cite(node)}")
        return "\n".join(body), [node]
    body += [f"- {{{{quote:{a.id}}}}} {_cite(a)}" for a in items]
    return "\n".join(body), items


def provenance(snapshot: Snapshot, node_id: str) -> tuple[str, list[Atom]]:
    """出处（渲染段）：版次、册、课、页码（册-课-页）"""
    items = _projections(snapshot, node_id)
    body = [f"## {_title('provenance', 'material')}", ""]
    if not items:
        node = _node(snapshot, node_id)
        body.append(f"{EMPTY} {_cite(node)}")
        return "\n".join(body), [node]
    for a in items:
        p = a.record
        pages = f"第 {p['page_from']} 页" if p["page_from"] == p["page_to"] else f"第 {p['page_from']}–{p['page_to']} 页"
        body.append(f"- {p['edition']}（{p['edition_year']}）{p['volume']}-{p['chapter']}-{pages} {_cite(a)}")
    return "\n".join(body), items


def material_examples(snapshot: Snapshot, node_id: str) -> tuple[str, list[Atom]]:
    """例句（渲染段）：节点 examples"""
    atom = _node(snapshot, node_id)
    body = [f"## {_title('examples', 'material')}", ""]
    body += [f"- {x} {_cite(atom)}" for x in atom.record.get("examples") or []] or [f"{EMPTY} {_cite(atom)}"]
    return "\n".join(body), [atom]


def _other_end(e: Atom, node_id: str) -> str:
    return e.record["to_id"] if e.record["from_id"] == node_id else e.record["from_id"]


def confusables(snapshot: Snapshot, node_id: str, known_pages: Iterable[str]) -> tuple[str, list[Atom]]:
    """易混项与关联（渲染段）：confusable 与 related 边无方向，列出另一端"""
    known = list(known_pages)
    node = _node(snapshot, node_id)
    out: list[Atom] = []
    body = [f"## {_title('confusables', 'material')}"]
    for label, etype in (("易混：", "confusable"), ("关联：", "related")):
        edges = sorted(snapshot.edges_to(node_id, etype) + snapshot.edges_from(node_id, etype), key=lambda a: (_other_end(a, node_id), a.id))
        body += ["", label]
        body += [f"- {_name(snapshot, _other_end(e, node_id), known)} {_cite(e)}" for e in edges] or [f"- {EMPTY} {_cite(node)}"]
        out += edges or [node]
    return "\n".join(body), out


def render_sections(snapshot: Snapshot, node_id: str, known_pages: Iterable[str], page_type: str = "kp") -> tuple[dict[str, Section], list[Atom]]:
    """锁定段与渲染段：段落 ID → 段落；以及这些段落引用的全部原子（去重、保序）"""
    known = list(known_pages)
    out: dict[str, Section] = {}
    used: list[Atom] = []
    gens = {"definition": lambda: definition(snapshot, node_id), "curriculum": lambda: curriculum(snapshot, node_id),
            "relations": lambda: relations(snapshot, node_id, known), "textbook": lambda: textbook(snapshot, node_id),
            "region": lambda: region(snapshot, node_id)}
    if page_type == "material":
        gens = {"entry": lambda: entry(snapshot, node_id), "source_text": lambda: source_text(snapshot, node_id),
                "provenance": lambda: provenance(snapshot, node_id), "examples": lambda: material_examples(snapshot, node_id),
                "confusables": lambda: confusables(snapshot, node_id, known)}
    for spec in SECTIONS_BY_TYPE[page_type]:
        if spec.kind == "narrative":
            continue
        text, atoms = gens[spec.id]()
        out[spec.id] = Section(spec.id, spec.kind, text)
        used += atoms
    seen: dict[str, Atom] = {}
    for a in used:
        seen.setdefault(a.key, a)
    return out, list(seen.values())


def refs_for(atoms: Iterable[Atom]) -> list[AtomRef]:
    return [AtomRef(a.type, a.id, a.hash) for a in atoms]
