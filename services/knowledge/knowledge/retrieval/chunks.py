"""兜底检索的切块（spec 设计要点 6）：切块对象只能是公共层内容。

只收两种输入，类型写死（传别的一律 TypeError，fail closed；规则 13：作答、笔记、错因、学情档案永远不进这里）：
- `PageSnapshot`（knowledge.service.pages）：已发布页面快照——manifest 列出、哈希核对过的页面；
- `Snapshot`（knowledge.atoms）且 status = published：已发布地图版本的公共层原子（白名单字段）。

切法：
- 页面：按段落（空行分隔）切；跳过“教材位置”段（只有投影占位，原句按版次在读取时渲染，不进检索）；
  节点不在该地图版本有效节点里的页面整页跳过（节点已撤下）。
- 原子：知识节点与细目（名称 + 包括 + 不包括）、课标条目（条目原文）。教材投影的原句不切（KGE V1.1 修订 3：
  教材原句不锁在页面里，按学生版次渲染），地区投影、边不切（没有可检索的叙述）。
每块带来源：页面 = 页面 ID + page_version + 段落；原子 = 原子键 + 原子哈希。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from ..atoms import Snapshot
from ..service.pages import PageSnapshot, PublishedPage
from .tokenize import strip_markup, tokenize

SKIP_SECTIONS = frozenset({"textbook"})
_HEADING = re.compile(r"^#{1,6} ")
_SPACES = re.compile(r"[ \t]+")


@dataclass(frozen=True)
class Source:
    kind: str                       # page | atom
    page_id: str | None = None      # 页面仓库里的页面 ID（= 节点 ID）
    page_version: str | None = None
    section: str | None = None
    atom_ref: str | None = None     # 原子键 type:id
    atom_hash: str | None = None

    @property
    def key(self) -> str:
        if self.kind == "page":
            return f"page:{self.page_id}@{self.page_version}#{self.section}"
        return f"atom:{self.atom_ref}"

    def to_dict(self) -> dict[str, str]:
        d = {"kind": self.kind, "page_id": self.page_id, "page_version": self.page_version, "section": self.section,
             "atom_ref": self.atom_ref, "atom_hash": self.atom_hash}
        return {k: v for k, v in d.items() if v is not None}


@dataclass(frozen=True)
class Chunk:
    key: str                  # 唯一：来源键 + 段内序号
    source: Source
    node_id: str | None
    text: str
    tokens: tuple[str, ...]


def clean(text: str) -> str:
    """给人读的文字：去标记、去行首尾空白、合并空格，保留换行"""
    lines = [_SPACES.sub(" ", strip_markup(ln)).strip() for ln in text.split("\n")]
    return "\n".join(ln for ln in lines if ln and ln != "$$").strip()


def active_nodes(atoms: Snapshot) -> dict[str, dict]:
    return {a.id: dict(a.record) for a in atoms.of_type("node") if a.record.get("status", "active") == "active"}


def _require(pages: PageSnapshot | None, atoms: Snapshot) -> None:
    if not isinstance(atoms, Snapshot):
        raise TypeError("切块只收已发布地图版本的公共层快照（knowledge.atoms.Snapshot）")
    if atoms.status != "published":
        raise ValueError("切块只收已发布（published）的地图版本")
    if pages is not None and not isinstance(pages, PageSnapshot):
        raise TypeError("切块只收已发布页面快照（knowledge.service.pages.PageSnapshot）")


def page_chunks(pp: PublishedPage) -> list[Chunk]:
    out: list[Chunk] = []
    for s in pp.page.sections:
        if s.id in SKIP_SECTIONS:
            continue
        paras = [p for p in re.split(r"\n\s*\n", s.content) if p.strip()]
        n = 0
        for para in paras:
            body = "\n".join(ln for ln in para.split("\n") if not _HEADING.match(ln))
            text = clean(body)
            toks = tuple(tokenize(text))
            if not toks:
                continue
            src = Source("page", page_id=pp.node_id, page_version=pp.page_version, section=s.id)
            out.append(Chunk(f"{src.key}/{n}", src, pp.node_id, text, toks))
            n += 1
    return out


def atom_chunks(atoms: Snapshot) -> list[Chunk]:
    out: list[Chunk] = []
    nodes = active_nodes(atoms)
    linked: dict[str, list[str]] = {}
    for nid, rec in sorted(nodes.items()):
        for c in rec.get("curriculum") or []:
            linked.setdefault(f"{c['standard_year']}/{c['code']}", []).append(nid)
    for a in atoms.of_type("node"):
        rec = nodes.get(a.id)
        if rec is None or rec.get("level") not in ("node", "detail"):
            continue
        text = definition_text(rec)
        src = Source("atom", atom_ref=a.key, atom_hash=a.hash)
        out.append(Chunk(src.key, src, a.id, text, tuple(tokenize(text))))
    for a in atoms.of_type("curriculum"):
        text = clean(str(a.record["text"]))
        src = Source("atom", atom_ref=a.key, atom_hash=a.hash)
        owners = linked.get(a.id) or [None]
        out.append(Chunk(src.key, src, owners[0], text, tuple(tokenize(text))))
    return [c for c in out if c.tokens]


def definition_text(rec: dict) -> str:
    parts = [str(rec["name"])]
    if rec.get("includes"):
        parts.append("包括：" + "；".join(str(x) for x in rec["includes"]))
    if rec.get("excludes"):
        parts.append("不包括：" + "；".join(str(x) for x in rec["excludes"]))
    return "。".join(parts) + "。"


def build_chunks(pages: PageSnapshot | None, atoms: Snapshot) -> list[Chunk]:
    """公共层切块：已发布页面快照的段落 + 已发布原子。键唯一、顺序确定"""
    _require(pages, atoms)
    nodes = active_nodes(atoms)
    out: list[Chunk] = []
    if pages is not None:
        for pid in sorted(pages.pages):
            pp = pages.pages[pid]
            if pp.node_id in nodes:
                out.extend(page_chunks(pp))
    out.extend(atom_chunks(atoms))
    out.sort(key=lambda c: c.key)
    keys = [c.key for c in out]
    if len(keys) != len(set(keys)):
        raise ValueError("切块键重复")
    return out
