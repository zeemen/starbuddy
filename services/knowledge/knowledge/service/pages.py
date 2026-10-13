"""已发布页面快照（spec 待人确认 #7）：知识服务只读编译器导出的快照目录，不在线访问 Git。

快照目录（knowledge.compiler.snapshot 导出，只读）：
    manifest.json              {"format": "kge-page-snapshot/1", "commit": <40 位提交号>, "exported_at": ..., "pages": [...]}
    pages/kp/<节点 ID>.md       页面原文（与页面仓库该提交逐字节相同）
manifest 的每页：page_id（页面仓库里的页面 ID = 节点 ID）、path、page_version、map_version、sha256；
页面对账失败被扣、沿用上一份快照的页面另有 source_commit（它原来的 40 位提交号，page.get 的 source_version 用它）。
启动时逐页核对哈希、头部与 manifest 一致，任何一处不符就拒绝加载（fail closed）；只有 manifest 列出的页面算“已发布”。
载入时还要：
- 快照目录、pages/、pages/kp/、manifest.json 与每个页面文件都不能是符号链接（lstat），也不能对属组或其他用户可写；
- 对每页再跑一遍程序体检里不依赖地图快照的检查（头部含原子 ID 白名单、原子引用登记、缺引用、个人信息、人设中立与禁语，
  knowledge.lint.checks.published_page_checks）：导出后被人改过、或绕过导出直接拼出来的快照，读取时渲染也绕不过体检；
  有阻断即拒绝加载（知识服务拒绝启动）。
manifest 的签名（HMAC）：KGE-06 只留接口（hotswap.py 的 ManifestVerifier；密钥 KNOWLEDGE_SNAPSHOT_HMAC_KEY，未配置时不验签并记录），
签名格式与密钥管理待人确认。运行中换快照见 hotswap.py（全部核对通过才原子替换）。
page.get 只服务 map_version 与当前已发布地图版本一致的页面（tools.page_get）：升版到重编合并之间，落后的页面回 404。

契约的 PageId 是小写编码（`math.func.quad.vertex-form`）：节点 ID 转小写、下划线换连字符（节点 ID 里没有连字符，
所以可逆）。page_version 取页面头部的语义化版本；提交号按 ADR 12 映射进 source_version（`graph:kge-pages@<提交号前 12 位>`）。

读取时按 edition、region 渲染（KGE V1.1 修订 3：教材原句不锁在页面里）：锁定段、叙述段、前置与后续逐字取页面；
教材位置段与地区权重段按库里当前已核对的投影重新生成（与编译器同一模板），原句只在给了 edition 时按版次填入。
"""
from __future__ import annotations

import errno
import hashlib
import json
import os
import re
import stat
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


def _safe_entry(path: Path, *, directory: bool) -> None:
    """不是符号链接、类型对、属组与其他用户不可写"""
    try:
        st = os.lstat(path)
    except OSError:
        raise SnapshotLoadError("页面快照缺少必要的文件或目录") from None
    if stat.S_ISLNK(st.st_mode):
        raise SnapshotLoadError("页面快照里不能有符号链接")
    if directory != stat.S_ISDIR(st.st_mode) or (not directory and not stat.S_ISREG(st.st_mode)):
        raise SnapshotLoadError("页面快照里的文件类型不对")
    if st.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        raise SnapshotLoadError("页面快照的文件或目录对属组或其他用户可写")


def read_safe(path: Path, missing: str = "页面快照缺少必要的文件或目录") -> bytes:
    """读一个普通文件：O_NOFOLLOW 打开（最后一段是符号链接即失败），对打开的描述符 fstat 核对类型与权限，再从同一个描述符读。
    检查与读取是同一个文件对象，lstat 与 open 之间被换成符号链接也读不到别处（KGE-06）。"""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NONBLOCK", 0))
    except OSError as e:
        if e.errno == errno.ELOOP:
            raise SnapshotLoadError("页面快照里不能有符号链接") from None
        raise SnapshotLoadError(missing) from None
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise SnapshotLoadError("页面快照里的文件类型不对")
        if st.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
            raise SnapshotLoadError("页面快照的文件或目录对属组或其他用户可写")
        chunks = []
        while True:
            b = os.read(fd, 1 << 20)
            if not b:
                break
            chunks.append(b)
        return b"".join(chunks)
    except OSError:
        raise SnapshotLoadError(missing) from None
    finally:
        os.close(fd)


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
    source_commit: str | None = None  # 对账失败被扣、沿用上一份快照的页面：它原来的提交号（其余页面为空，用快照的 commit）


@dataclass(frozen=True)
class PageSnapshot:
    commit: str
    pages: Mapping[str, PublishedPage]  # 契约 PageId → 页面

    @property
    def short_commit(self) -> str:
        return self.commit[:12]

    def get(self, page_id: str) -> PublishedPage | None:
        return self.pages.get(page_id)


def load_snapshot_dir(root: str | Path, lexicon: Any = None, manifest_raw: bytes | None = None) -> PageSnapshot:
    """lexicon：人设词表（缺省从 docs/personas 只读抽取，与体检同一份）。
    manifest_raw：调用方已读到的 manifest.json 原始字节（KGE-06：验签、解析与摘要共用同一份字节，不再从磁盘读第二次）；
    不给时这里用 read_safe 读一次。"""
    from ..lint.checks import PageFile, published_page_checks
    from ..lint.lexicon import LexiconError, default_lexicon

    d = Path(root)
    if not d.is_dir():
        raise SnapshotLoadError("页面快照目录不存在")
    _safe_entry(d, directory=True)
    raw_manifest = manifest_raw if manifest_raw is not None else read_safe(d / "manifest.json", "页面快照缺少 manifest.json 或无法解析")
    try:
        manifest = json.loads(raw_manifest.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise SnapshotLoadError("页面快照缺少 manifest.json 或无法解析") from None
    if not isinstance(manifest, dict) or manifest.get("format") != FORMAT:
        raise SnapshotLoadError("manifest 格式不对")
    commit = manifest.get("commit")
    if not isinstance(commit, str) or not COMMIT.fullmatch(commit):
        raise SnapshotLoadError("manifest 缺少 40 位提交号")
    entries = manifest.get("pages")
    if not isinstance(entries, list):
        raise SnapshotLoadError("manifest.pages 必须是数组")
    if entries:
        _safe_entry(d / "pages", directory=True)
        _safe_entry(d / "pages" / "kp", directory=True)
    try:
        lex = lexicon if lexicon is not None else default_lexicon()
    except LexiconError:
        raise SnapshotLoadError("人设词表无法载入，不能核对页面") from None
    pages: dict[str, PublishedPage] = {}
    for i, e in enumerate(entries):
        if not isinstance(e, dict) or set(e) - {"source_commit"} != {"page_id", "path", "page_version", "map_version", "sha256"}:
            raise SnapshotLoadError(f"manifest.pages[{i}] 字段不对")
        source_commit = e.get("source_commit")
        if source_commit is not None and (not isinstance(source_commit, str) or not COMMIT.fullmatch(source_commit)):
            raise SnapshotLoadError(f"manifest.pages[{i}] 的 source_commit 不是 40 位提交号")
        nid, rel, ver, digest = e["page_id"], e["path"], e["page_version"], e["sha256"]
        if not is_node_id(nid) or rel != f"pages/kp/{nid}.md" or not SEMVER.fullmatch(str(ver)) or not SHA256.fullmatch(str(digest)):
            raise SnapshotLoadError(f"manifest.pages[{i}] 取值不合规范")
        path = d / rel
        raw = read_safe(path, f"manifest.pages[{i}] 指向的文件不存在")
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
        blocks = [i for i in published_page_checks(PageFile(rel, raw.decode("utf-8"), page), lex) if i.severity == "block"]
        if blocks:
            kinds = "、".join(sorted({i.check_type for i in blocks}))
            raise SnapshotLoadError(f"manifest.pages[{i}] 的页面有体检阻断（{kinds}），拒绝加载")
        pid = contract_page_id(nid)
        if not CONTRACT_PAGE_ID.fullmatch(pid) or pid in pages:
            raise SnapshotLoadError(f"manifest.pages[{i}] 的页面 ID 重复或不合契约")
        pages[pid] = PublishedPage(pid, nid, ver, str(e["map_version"]), page, source_commit)
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
