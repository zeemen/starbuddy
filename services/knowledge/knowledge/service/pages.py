"""已发布页面快照（spec 待人确认 #7）：知识服务只读编译器导出的快照目录，不在线访问 Git。

快照目录（knowledge.compiler.snapshot 导出，只读）：
    manifest.json              {"format": "kge-page-snapshot/1", "commit": <40 位提交号>, "exported_at": ..., "pages": [...]}
    pages/kp/<节点 ID>.md       页面原文（与页面仓库该提交逐字节相同）
manifest 的每页：page_id（页面仓库里的页面 ID = 节点 ID）、path、page_version、map_version、sha256。
启动时逐页核对哈希、头部与 manifest 一致，任何一处不符就拒绝加载（fail closed）；只有 manifest 列出的页面算“已发布”。

契约的 PageId 是小写编码（`math.func.quad.vertex-form`）：节点 ID 转小写、下划线换连字符（节点 ID 里没有连字符，
所以可逆）。page_version 取页面头部的语义化版本；提交号按 ADR 12 映射进 source_version（`graph:kge-pages@<提交号前 12 位>`）。

读取时按 edition、region 渲染（KGE V1.1 修订 3：教材原句不锁在页面里）：锁定段、叙述段、前置与后续逐字取页面；
教材位置段与地区权重段按库里当前已核对的投影重新生成（与编译器同一模板），原句只在给了 edition 时按版次填入。
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from ..atoms import parse_snapshot
from ..compiler import templates
from ..nodeid import is_node_id
from ..pages.format import QUOTE_SLOT, SEMVER, Page, PageFormatError, Section, parse, serialize

FORMAT = "kge-page-snapshot/1"
COMMIT = re.compile(r"^[0-9a-f]{40}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
CONTRACT_PAGE_ID = re.compile(r"^[a-z0-9][a-z0-9_.\-]{0,127}$")
ATOM_REF = re.compile(r"^(node|item|scoring_point|current_affair|textbook_projection):[A-Za-z0-9][A-Za-z0-9_.:\-]{0,127}$")
# 页面里的原子类型 → 契约 PageGetResponse.atom_refs 的类型（边、课标、地区投影在契约里没有对应类型，见 lessons 待人确认）
REF_TYPES = {"node": "node", "projection": "textbook_projection", "affair": "current_affair"}
MAX_ATOM_REFS = 200


class SnapshotLoadError(RuntimeError):
    """快照目录不合格：拒绝加载"""


def contract_page_id(node_id: str) -> str:
    return node_id.lower().replace("_", "-")


def node_id_of(page_id: str) -> str | None:
    nid = page_id.upper().replace("-", "_")
    return nid if is_node_id(nid) and contract_page_id(nid) == page_id else None


@dataclass(frozen=True)
class PublishedPage:
    page_id: str      # 契约 PageId
    node_id: str
    page_version: str
    map_version: str
    page: Page


@dataclass(frozen=True)
class PageSnapshot:
    commit: str
    pages: Mapping[str, PublishedPage]  # 契约 PageId → 页面

    @property
    def short_commit(self) -> str:
        return self.commit[:12]

    def get(self, page_id: str) -> PublishedPage | None:
        return self.pages.get(page_id)


def load_snapshot_dir(root: str | Path) -> PageSnapshot:
    d = Path(root)
    if not d.is_dir():
        raise SnapshotLoadError("页面快照目录不存在")
    try:
        manifest = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise SnapshotLoadError("页面快照缺少 manifest.json 或无法解析") from None
    if not isinstance(manifest, dict) or manifest.get("format") != FORMAT:
        raise SnapshotLoadError("manifest 格式不对")
    commit = manifest.get("commit")
    if not isinstance(commit, str) or not COMMIT.fullmatch(commit):
        raise SnapshotLoadError("manifest 缺少 40 位提交号")
    entries = manifest.get("pages")
    if not isinstance(entries, list):
        raise SnapshotLoadError("manifest.pages 必须是数组")
    pages: dict[str, PublishedPage] = {}
    for i, e in enumerate(entries):
        if not isinstance(e, dict) or set(e) != {"page_id", "path", "page_version", "map_version", "sha256"}:
            raise SnapshotLoadError(f"manifest.pages[{i}] 字段不对")
        nid, rel, ver, digest = e["page_id"], e["path"], e["page_version"], e["sha256"]
        if not is_node_id(nid) or rel != f"pages/kp/{nid}.md" or not SEMVER.fullmatch(str(ver)) or not SHA256.fullmatch(str(digest)):
            raise SnapshotLoadError(f"manifest.pages[{i}] 取值不合规范")
        path = d / rel
        try:
            raw = path.read_bytes()
        except OSError:
            raise SnapshotLoadError(f"manifest.pages[{i}] 指向的文件不存在") from None
        if hashlib.sha256(raw).hexdigest() != digest:
            raise SnapshotLoadError(f"manifest.pages[{i}] 的哈希与文件不符")
        try:
            page = parse(raw.decode("utf-8"))
        except (PageFormatError, UnicodeDecodeError):
            raise SnapshotLoadError(f"manifest.pages[{i}] 无法解析") from None
        h = page.header
        if h.get("page_id") != nid or h.get("node_id") != nid or h.get("page_type") != "kp" or str(h.get("page_version")) != ver \
                or str(h.get("map_version")) != str(e["map_version"]):
            raise SnapshotLoadError(f"manifest.pages[{i}] 与页面头部不一致")
        pid = contract_page_id(nid)
        if not CONTRACT_PAGE_ID.fullmatch(pid) or pid in pages:
            raise SnapshotLoadError(f"manifest.pages[{i}] 的页面 ID 重复或不合契约")
        pages[pid] = PublishedPage(pid, nid, ver, str(e["map_version"]), page)
    return PageSnapshot(commit, pages)


def _mini_snapshot(map_version: str, subject: str, standard_year: int, node: Mapping[str, Any],
                   projections: Iterable[Mapping[str, Any]], regions: Iterable[Mapping[str, Any]]):
    return parse_snapshot({"map_version": map_version, "subject": subject, "status": "published", "standard_year": standard_year,
                           "nodes": [dict(node)], "textbook_projections": [dict(p) for p in projections],
                           "region_projections": [dict(r) for r in regions]})


def render_page(pp: PublishedPage, *, subject: str, map_version: str, standard_year: int, node: Mapping[str, Any],
                projections: list[Mapping[str, Any]], regions: list[Mapping[str, Any]],
                edition: str | None) -> tuple[str, list[str]]:
    """按版次与地区渲染：返回（body_md，atom_refs）。projections / regions 已按 edition / region 过滤、只含已核对的"""
    snap = _mini_snapshot(map_version, subject, standard_year, node, projections, regions)
    quotes = {str(p["id"]): str(p["quote_text"]) for p in projections if p.get("quote_text")}
    sections: list[Section] = []
    for s in pp.page.sections:
        if s.id == "textbook":
            text, _ = templates.textbook(snap, pp.node_id)
            if edition is None:
                text = re.sub(r" ?\{\{quote:[^}\s]+\}\}", "", text)
            else:
                text = QUOTE_SLOT.sub(lambda m: f"“{quotes[m.group(1)]}”" if m.group(1) in quotes else "", text)
            s = Section(s.id, s.kind, text)
        elif s.id == "region":
            text, _ = templates.region(snap, pp.node_id)
            s = Section(s.id, s.kind, text)
        elif QUOTE_SLOT.search(s.content):  # 原句占位只允许出现在教材位置段；别处出现一律去掉，不按版次填
            s = Section(s.id, s.kind, QUOTE_SLOT.sub("", s.content))
        sections.append(s)
    full = serialize(pp.page.with_sections(tuple(sections)))
    body = full.split("\n---\n", 1)[1].lstrip("\n")
    refs: list[str] = []
    for r in pp.page.atom_refs:
        ctype = REF_TYPES.get(r.type)
        if ctype and ctype != "textbook_projection":
            refs.append(f"{ctype}:{r.id}")
    refs += [f"textbook_projection:{p['id']}" for p in projections]
    seen: dict[str, None] = {}
    for ref in refs:
        if ATOM_REF.fullmatch(ref):
            seen.setdefault(ref, None)
    return body, list(seen)[:MAX_ATOM_REFS]
