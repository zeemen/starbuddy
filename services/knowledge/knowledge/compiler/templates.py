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


def render_sections(snapshot: Snapshot, node_id: str, known_pages: Iterable[str]) -> tuple[dict[str, Section], list[Atom]]:
    """锁定段与渲染段：段落 ID → 段落；以及这些段落引用的全部原子（去重、保序）"""
    known = list(known_pages)
    out: dict[str, Section] = {}
    used: list[Atom] = []
    gens = {"definition": lambda: definition(snapshot, node_id), "curriculum": lambda: curriculum(snapshot, node_id),
            "relations": lambda: relations(snapshot, node_id, known), "textbook": lambda: textbook(snapshot, node_id),
            "region": lambda: region(snapshot, node_id)}
    for spec in SECTIONS_BY_TYPE["kp"]:
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
