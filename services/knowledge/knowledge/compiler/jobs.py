"""编入（spec 设计要点 3“编入”；KGE 第四章“五个操作”）：按“页面—原子”引用索引找出受影响页面，生成编译任务。

受影响 = 页面头部 atom_refs 引用了变更的原子，或者变更的原子挂在页面的节点上（新增的前置边、投影、时政等）。
优先级 = 风险 × 影响：风险按触发原子预估（定义、课标、前置关系及前置节点的表述、教材投影 = 高，其余 = 低；
渲染重建后按实际改动再判一次），
影响 = 1 + 以该节点为前置的后续节点数（改动会沿骨架传给多少页面）。
"""
from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from ..atoms import Atom, Snapshot, diff_snapshots
from ..pages.format import Page

RISK_WEIGHT = {"low": 1, "medium": 2, "high": 3}


@dataclass(frozen=True)
class Trigger:
    key: str  # 原子键 type:id
    change: str  # added | removed | modified


@dataclass(frozen=True)
class CompileJob:
    job_id: str
    page_id: str
    reason: str  # atom_changed：原子变更触发；codification_changed 等全量重编原因由调用方给
    triggers: tuple[Trigger, ...]
    rebuild_kind: str  # render | full
    risk_estimate: str
    impact: int
    priority: int
    status: str = "queued"


def _nodes_touched(atom: Atom | None, snap: Snapshot) -> set[str]:
    if atom is None:
        return set()
    r = atom.record
    if atom.type == "node":
        return {atom.id}
    if atom.type == "edge":
        return {r["from_id"], r["to_id"]}
    if atom.type in ("projection", "region"):
        return {r["node_id"]}
    if atom.type == "affair":
        return set(r["node_ids"])
    if atom.type == "curriculum":
        return {a.id for a in snap.of_type("node") if atom.id in {f"{c['standard_year']}/{c['code']}" for c in a.record.get("curriculum", [])}}
    return set()


def _name(snap: Snapshot, key: str) -> str | None:
    atom = snap.atoms.get(key)
    return None if atom is None else str(atom.record["name"])


def _neighbors(snap: Snapshot, node_id: str) -> set[str]:
    return ({e.record["from_id"] for e in snap.edges_to(node_id, "prerequisite")}
            | {e.record["to_id"] for e in snap.edges_from(node_id, "prerequisite")})


def _estimate_risk(node_id: str, trig: Iterable[Trigger], old: Snapshot, new: Snapshot) -> str:
    for t in trig:
        atype, _, aid = t.key.partition(":")
        atom = new.atoms.get(t.key) or old.atoms.get(t.key)
        if atype in ("curriculum", "projection"):
            return "high"
        if atype == "node" and (aid == node_id or any(e.record["from_id"] == aid for e in new.edges_to(node_id, "prerequisite"))):
            return "high"  # 本节点定义变更，或前置节点的表述变更
        if atype == "edge" and atom is not None and atom.record["type"] == "prerequisite" and atom.record["to_id"] == node_id:
            return "high"
    return "low"


def impact_of(node_id: str, snap: Snapshot) -> int:
    return 1 + len(snap.edges_from(node_id, "prerequisite"))


def _job_id(page_id: str, triggers: tuple[Trigger, ...], map_version: str, kind: str) -> str:
    raw = "|".join([page_id, map_version, kind, *(f"{t.key}={t.change}" for t in triggers)])
    return "cj-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


def plan_jobs(pages: Mapping[str, Page], old: Snapshot, new: Snapshot) -> list[CompileJob]:
    """pages：页面 ID → 当前（上一审核版）页面。返回按优先级从高到低排好的编译任务（同优先级按页面 ID）"""
    changes = diff_snapshots(old, new)
    index: dict[str, set[str]] = {}  # 原子键 → 引用它的页面
    by_node: dict[str, str] = {}
    for pid, page in pages.items():
        by_node[str(page.header.get("node_id"))] = pid
        for r in page.atom_refs:
            index.setdefault(r.key, set()).add(pid)
    hits: dict[str, list[Trigger]] = {}
    for key, change in changes.items():
        affected = set(index.get(key, set()))
        touched = _nodes_touched(old.atoms.get(key), old) | _nodes_touched(new.atoms.get(key), new)
        if key.startswith("node:") and _name(old, key) != _name(new, key):  # 节点名称会渲染进相邻页面的“前置与后续”
            touched |= _neighbors(old, key[5:]) | _neighbors(new, key[5:])
        for nid in touched:
            if nid in by_node:
                affected.add(by_node[nid])
        for pid in affected:
            hits.setdefault(pid, []).append(Trigger(key, change))
    jobs = []
    for pid, trig in hits.items():
        triggers = tuple(sorted(trig, key=lambda t: t.key))
        node_id = str(pages[pid].header.get("node_id"))
        risk = _estimate_risk(node_id, triggers, old, new)
        impact = impact_of(node_id, new)
        jobs.append(CompileJob(_job_id(pid, triggers, new.map_version, "render"), pid, "atom_changed", triggers, "render", risk, impact,
                               RISK_WEIGHT[risk] * impact))
    jobs.sort(key=lambda j: (-j.priority, j.page_id))
    return jobs


def full_job(page: Page, snap: Snapshot, reason: str) -> CompileJob:
    """全量重编任务（原子大改、编纂规范变更、发现错误复利时，由人发起）：一律高风险"""
    node_id = str(page.header.get("node_id"))
    impact = impact_of(node_id, snap)
    return CompileJob(_job_id(page.page_id, (), f"{snap.map_version}:{reason}:{page.page_version}", "full"), page.page_id, reason, (),
                      "full", "high", impact, RISK_WEIGHT["high"] * impact)
