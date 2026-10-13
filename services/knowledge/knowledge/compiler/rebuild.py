"""两种重建（spec 设计要点 3；KGE 第四章“两种重建”“分级发布闸门”）。

渲染重建：从公共层原子重新生成锁定段模板位与渲染段；已审核的叙述段逐字保留；记录所引原子哈希；按改动类型判风险。
全量重编：叙述段由模型经模型路由重写（见 full.py）；一律高风险，必须教研审核；旧版本在新版发布前继续服务。

风险（KGE 第四章分级发布闸门）：
  高：锁定段（定义、课标要求）变更；前置关系表述变更；教材投影（原句与页码）变更；全量重编
  低：其余渲染段变化（后续节点、地区权重等考情数值）、只有原子哈希更新
页面版本：低风险升修订号，高风险升次版本号，全量重编升主版本号。
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Mapping

from ..atoms import Snapshot
from ..pages.format import CITATION, SECTIONS_BY_TYPE, AtomRef, Page, Section, serialize
from .jobs import CompileJob
from .templates import render_sections

CST = dt.timezone(dt.timedelta(hours=8), "Asia/Shanghai")  # 合并窗口按北京时间换日
# 由教材投影渲染的段落：知识点页的“教材位置”，素材卡的“原文”（原句占位）与“出处”（册-课-页）——变更一律高风险
TEXTBOOK_SECTIONS = ("textbook", "source_text", "provenance")


class CompileError(RuntimeError):
    pass


@dataclass(frozen=True)
class BasisAtom:
    key: str
    change: str  # added | removed | modified
    old_hash: str | None
    new_hash: str | None


@dataclass(frozen=True)
class CompileResult:
    job: CompileJob
    rel_path: str
    old_page: Page
    new_page: Page
    old_text: str
    new_text: str
    risk: str
    reasons: tuple[str, ...]
    changed_sections: tuple[str, ...]
    basis: tuple[BasisAtom, ...]
    compiled_at: dt.datetime
    rebuild_kind: str = "render"
    source_version: str | None = None  # 全量重编的 prompt 版本（模型标识只记在模型路由的调用记录里，不进页面仓库）

    @property
    def page_id(self) -> str:
        return self.new_page.page_id

    @property
    def unchanged(self) -> bool:
        return self.old_text == self.new_text

    @property
    def day(self) -> dt.date:
        return self.compiled_at.astimezone(CST).date()


def _cited(sections: Mapping[str, Section]) -> list[str]:
    keys: list[str] = []
    for s in sections.values():
        keys += [f"{m.group(1)}:{m.group(2)}" for m in CITATION.finditer(s.content)]
    return list(dict.fromkeys(keys))


def assemble(old: Page, snapshot: Snapshot, narrative: Mapping[str, Section], known_pages: set[str]) -> Page:
    """用快照渲染锁定段与渲染段，拼上给定的叙述段；头部更新地图版本与所引原子哈希（版本号、风险由调用方填）"""
    ptype = str(old.header.get("page_type"))
    node_id = str(old.header.get("node_id"))
    if ptype not in SECTIONS_BY_TYPE:
        raise CompileError(f"页面类型 {ptype} 首期未实现")
    node = snapshot.node(node_id)
    if node is None:
        raise CompileError(f"节点 {node_id} 不在地图快照里（孤立页由体检报出，编译器不重建）")
    templ, atoms = render_sections(snapshot, node_id, known_pages, ptype)
    sections = []
    for spec in SECTIONS_BY_TYPE[ptype]:
        if spec.kind == "narrative":
            if spec.id not in narrative:
                raise CompileError(f"缺叙述段 {spec.id}")
            sections.append(narrative[spec.id])
        else:
            sections.append(templ[spec.id])
    refs: dict[str, AtomRef] = {a.key: AtomRef(a.type, a.id, a.hash) for a in atoms}
    old_refs = {r.key: r for r in old.atom_refs}
    for key in _cited(narrative):
        atype, _, aid = key.partition(":")
        atom = snapshot.get(atype, aid)
        if atom is not None:
            refs.setdefault(key, AtomRef(atom.type, atom.id, atom.hash))
        elif key in old_refs:
            refs.setdefault(key, old_refs[key])  # 原子已删除：保留旧哈希，体检报失效引用，由人改叙述段
    header = {**old.header, "map_version": snapshot.map_version,
              "atom_refs": [{"type": r.type, "id": r.id, "hash": r.hash} for r in refs.values()]}
    return Page(header, str(node["name"]), tuple(sections))


def _prerequisite_block(content: str) -> str:
    return content.split("前置：", 1)[-1].split("后续：", 1)[0]


def classify(old: Page, new: Page) -> tuple[str, tuple[str, ...], tuple[str, ...]]:
    """（风险，原因，变化的段落）"""
    reasons: list[str] = []
    changed: list[str] = []
    high = False
    for spec in SECTIONS_BY_TYPE[str(new.header.get("page_type"))]:
        a, b = old.section(spec.id), new.section(spec.id)
        if (a.content if a else None) == (b.content if b else None):
            continue
        changed.append(spec.id)
        if spec.kind == "locked":
            high = True
            reasons.append(f"锁定段 {spec.id} 变更（定义与课标要求：高风险）")
        elif spec.id == "relations" and _prerequisite_block(a.content if a else "") != _prerequisite_block(b.content if b else ""):
            high = True
            reasons.append("前置关系表述变更（高风险）")
        elif spec.id in TEXTBOOK_SECTIONS:
            high = True
            reasons.append(f"教材投影（{'原句占位、' if spec.id == 'source_text' else ''}页码或版次）变更（高风险）")
        elif spec.kind == "narrative":
            high = True
            reasons.append(f"叙述段 {spec.id} 改写（高风险）")
        else:
            reasons.append(f"渲染段 {spec.id} 变化（低风险）")
    old_h = {r.key: r.hash for r in old.atom_refs}
    new_h = {r.key: r.hash for r in new.atom_refs}
    moved = sorted(k for k in set(old_h) | set(new_h) if old_h.get(k) != new_h.get(k))
    if any(k.startswith("projection:") for k in moved) and not set(TEXTBOOK_SECTIONS) & set(changed):
        high = True
        reasons.append("教材投影原句变更（原句不在页面里，但属高风险，须核对）")
    if moved and not changed:
        reasons.append("只有所引原子的哈希更新（低风险）")
    return ("high" if high else "low"), tuple(reasons), tuple(changed)


def basis_atoms(old: Page, new: Page) -> tuple[BasisAtom, ...]:
    old_h = {r.key: r.hash for r in old.atom_refs}
    new_h = {r.key: r.hash for r in new.atom_refs}
    out = []
    for k in sorted(set(old_h) | set(new_h)):
        a, b = old_h.get(k), new_h.get(k)
        if a != b:
            out.append(BasisAtom(k, "added" if a is None else "removed" if b is None else "modified", a, b))
    return tuple(out)


def bump(version: str, risk: str, kind: str) -> str:
    major, minor, patch = (int(x) for x in version.split("."))
    if kind == "full":
        return f"{major + 1}.0.0"
    if risk == "high":
        return f"{major}.{minor + 1}.0"
    return f"{major}.{minor}.{patch + 1}"


def finish(job: CompileJob, rel_path: str, old: Page, old_text: str, new: Page, kind: str, compiled_at: dt.datetime,
           extra_reasons: tuple[str, ...] = (), source_version: str | None = None) -> CompileResult:
    risk, reasons, changed = classify(old, new)
    if kind == "full":
        risk = "high"
    reasons = extra_reasons + reasons
    candidate = new.with_header(page_version=old.page_version, risk_level=old.header.get("risk_level"),
                                rebuild_kind=old.header.get("rebuild_kind"))
    if kind == "render" and serialize(candidate) == old_text:
        return CompileResult(job, rel_path, old, old, old_text, old_text, "low", ("无变化",), (), (), compiled_at, kind)
    new = new.with_header(page_version=bump(old.page_version, risk, kind), risk_level=risk, rebuild_kind=kind)
    return CompileResult(job, rel_path, old, new, old_text, serialize(new), risk, reasons, changed, basis_atoms(old, new), compiled_at,
                         kind, source_version)


def render_rebuild(job: CompileJob, rel_path: str, old: Page, old_text: str, snapshot: Snapshot, known_pages: set[str],
                   compiled_at: dt.datetime) -> CompileResult:
    narrative = {s.id: s for s in old.sections if s.kind == "narrative"}
    new = assemble(old, snapshot, narrative, known_pages)
    for sid, sec in narrative.items():  # 已审核的叙述段逐字保留（防御：模板改动不得波及叙述段）
        got = new.section(sid)
        if got is None or got.content != sec.content:
            raise CompileError(f"渲染重建改动了叙述段 {sid}")
    return finish(job, rel_path, old, old_text, new, "render", compiled_at)
