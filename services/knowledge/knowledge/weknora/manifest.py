"""WeKnora 交付清单（KGE-12 C）：WeKnora 文档 ID → 中台内容 ID → StarBuddy 的题目引用 / 编译页的对照表，以及过滤用的元数据。

来源：中台的交付清单接口（中台需求单 ZT-08；字段是附录 A.1 的子集），由 StarBuddy 侧的同步步骤导出成本文件，题目一并带上
`item_ref`（同步步骤按 platform.item 的来源字段 source_ref 对上中台内容 ID，ADR 16 第 3.4 节）。知识服务的只读角色不读
platform.item（迁移 0026 只授 kge.*），所以知识服务不自己去库里对照，只读这份文件（同页面快照的做法，lessons 待人确认第 4 条）。
清单本身不是题目身份的真相源（真相源仍是 platform.item），命中的题目还要再经 kge.item_tag 的已发布、审核通过条件核对。

文件（JSON，UTF-8，≤ 64 MiB）：
    {"format": "kge-weknora-delivery/1",
     "kb": {"name": <知识库名>, "version": <交付版本>, "ids": [<WeKnora 知识库 ID>, ...]},
     "entries": [{"content_id", "content_version", "weknora_id", "content_type", "status", "subject", "grade", "province",
                  "publisher"?, "edition"?, "semester"?, "exam_year"?, "exam_region"?, "exam_type"?, "node_ids"?,
                  "item_ref"?（content_type = item 时必填）, "page_ref"?（content_type = compiled_page 时必填）,
                  "source": {"title", "org"?, "year"?, "locator"?}}, ...]}
载入时逐条严格校验（未知字段、取值不合格、ID 重复都拒绝载入，fail closed）；只有 status = active 的条目参与检索。
清单里只有公共层元数据，没有正文、没有个人数据（规则 13）；错误消息只含字段名与序号，不含取值。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

FORMAT = "kge-weknora-delivery/1"
MAX_BYTES = 64 * 1024 * 1024
CONTENT_TYPES = ("textbook_section", "knowledge_point", "item", "exam_paper", "reference", "compiled_page")
STATUSES = ("active", "superseded", "offline")
SEMESTERS = ("first", "second")
EXAM_TYPES = ("zhongkao", "gaokao", "midterm", "final", "other")
SUBJECTS = ("wenli", "aya", "zhixing", "echo")

# 与契约同一套写法（knowledge-service.v0.10 / plugin-api.v0.9）
KB_NAME = re.compile(r"^[a-z0-9][a-z0-9\-]{0,47}$")
KB_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\-]{0,31}$")
OPAQUE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:\-]{0,127}$")
CODE = re.compile(r"^[a-z][a-z0-9\-]{1,31}$")
EDITION = re.compile(r"^[a-z0-9][a-z0-9\-]{1,31}$")
ITEM_REF = re.compile(r"^(wenli|aya|zhixing|echo):[A-Za-z0-9][A-Za-z0-9_.\-]{0,63}$")
NODE_ID = re.compile(r"^[A-Z][A-Z0-9]{1,9}(\.[A-Z0-9][A-Z0-9_]{0,31}){1,4}$")
PAGE_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\-]{0,63}$")

ENTRY_KEYS = frozenset({"content_id", "content_version", "weknora_id", "content_type", "status", "subject", "grade", "province",
                        "publisher", "edition", "semester", "exam_year", "exam_region", "exam_type", "node_ids", "item_ref",
                        "page_ref", "source"})
ENTRY_REQUIRED = frozenset({"content_id", "content_version", "weknora_id", "content_type", "status", "subject", "grade",
                            "province", "source"})
SOURCE_KEYS = frozenset({"title", "org", "year", "locator"})


class ManifestError(ValueError):
    """交付清单不合格：拒绝载入（消息只含字段名与条目序号）"""


@dataclass(frozen=True)
class Entry:
    content_id: str
    content_version: int
    weknora_id: str
    content_type: str
    status: str
    subject: str
    grade: int
    province: str
    publisher: str | None = None
    edition: str | None = None
    semester: str | None = None
    exam_year: int | None = None
    exam_region: str | None = None
    exam_type: str | None = None
    node_ids: tuple[str, ...] = ()
    item_ref: str | None = None
    page_ref: Mapping[str, str] | None = None
    source: Mapping[str, Any] = field(default_factory=dict)

    @property
    def active(self) -> bool:
        return self.status == "active"


@dataclass(frozen=True)
class DeliveryManifest:
    kb_name: str
    kb_version: str
    kb_ids: tuple[str, ...]
    entries: tuple[Entry, ...]
    by_weknora_id: Mapping[str, Entry] = field(repr=False, default_factory=dict)
    by_item_ref: Mapping[str, Entry] = field(repr=False, default_factory=dict)  # 只含 active

    @property
    def source_version(self) -> str:
        """契约 SourceVersion 的 items 类别：items:<知识库名>@<交付版本>"""
        return f"items:{self.kb_name}@{self.kb_version}"

    def active(self) -> list[Entry]:
        return [e for e in self.entries if e.active]


def _str(v: Any, pat: re.Pattern[str], where: str) -> str:
    if not isinstance(v, str) or not pat.fullmatch(v):
        raise ManifestError(f"{where} 不合格")
    return v


def _int(v: Any, lo: int, hi: int, where: str) -> int:
    if not isinstance(v, int) or isinstance(v, bool) or not lo <= v <= hi:
        raise ManifestError(f"{where} 不合格")
    return v


def _opt(e: Mapping[str, Any], key: str, check, where: str):
    return None if e.get(key) is None else check(e[key], f"{where}.{key}")


def _text(v: Any, max_len: int, where: str) -> str:
    if not isinstance(v, str) or not v.strip() or len(v) > max_len:
        raise ManifestError(f"{where} 不合格")
    return v


def _entry(e: Any, i: int) -> Entry:
    where = f"entries[{i}]"
    if not isinstance(e, dict):
        raise ManifestError(f"{where} 必须是对象")
    unknown, missing = set(e) - ENTRY_KEYS, ENTRY_REQUIRED - set(e)
    if unknown:
        raise ManifestError(f"{where} 有未定义字段：{'、'.join(sorted(unknown))}")
    if missing:
        raise ManifestError(f"{where} 缺字段：{'、'.join(sorted(missing))}")
    ctype = e["content_type"]
    if ctype not in CONTENT_TYPES:
        raise ManifestError(f"{where}.content_type 不合格")
    if e["status"] not in STATUSES:
        raise ManifestError(f"{where}.status 不合格")
    subject = e["subject"]
    if subject not in SUBJECTS:
        raise ManifestError(f"{where}.subject 不合格")
    src = e["source"]
    if not isinstance(src, dict) or set(src) - SOURCE_KEYS or "title" not in src:
        raise ManifestError(f"{where}.source 必须是对象、有 title、没有未定义字段")
    source: dict[str, Any] = {"title": _text(src["title"], 200, f"{where}.source.title")}
    if src.get("org") is not None:
        source["org"] = _text(src["org"], 100, f"{where}.source.org")
    if src.get("year") is not None:
        source["year"] = _int(src["year"], 1949, 2100, f"{where}.source.year")
    if src.get("locator") is not None:
        source["locator"] = _text(src["locator"], 100, f"{where}.source.locator")
    nodes = e.get("node_ids") or []
    if not isinstance(nodes, list) or len(nodes) > 16 or len(set(nodes)) != len(nodes):
        raise ManifestError(f"{where}.node_ids 必须是至多 16 个不重复的节点 ID")
    for n in nodes:
        _str(n, NODE_ID, f"{where}.node_ids")
    item_ref = _opt(e, "item_ref", lambda v, w: _str(v, ITEM_REF, w), where)
    if (ctype == "item") != (item_ref is not None):
        raise ManifestError(f"{where}.item_ref 只有题目有、且题目必须有")
    if item_ref is not None and item_ref.split(":", 1)[0] != subject:
        raise ManifestError(f"{where}.item_ref 的学科与 subject 不一致")
    page_ref = None
    if e.get("page_ref") is not None:
        pr = e["page_ref"]
        if not isinstance(pr, dict) or set(pr) != {"node_id", "page_version"}:
            raise ManifestError(f"{where}.page_ref 必须正好有 node_id、page_version")
        page_ref = {"node_id": _str(pr["node_id"], NODE_ID, f"{where}.page_ref.node_id"),
                    "page_version": _str(pr["page_version"], PAGE_VERSION, f"{where}.page_ref.page_version")}
    if (ctype == "compiled_page") != (page_ref is not None):
        raise ManifestError(f"{where}.page_ref 只有编译页有、且编译页必须有")
    if e.get("exam_type") is not None and e["exam_type"] not in EXAM_TYPES:
        raise ManifestError(f"{where}.exam_type 不合格")
    if e.get("semester") is not None and e["semester"] not in SEMESTERS:
        raise ManifestError(f"{where}.semester 不合格")
    return Entry(
        content_id=_str(e["content_id"], OPAQUE_ID, f"{where}.content_id"),
        content_version=_int(e["content_version"], 1, 2**31 - 1, f"{where}.content_version"),
        weknora_id=_str(e["weknora_id"], OPAQUE_ID, f"{where}.weknora_id"),
        content_type=ctype, status=e["status"], subject=subject,
        grade=_int(e["grade"], 7, 12, f"{where}.grade"),
        province=_str(e["province"], CODE, f"{where}.province"),
        publisher=_opt(e, "publisher", lambda v, w: _str(v, CODE, w), where),
        edition=_opt(e, "edition", lambda v, w: _str(v, EDITION, w), where),
        semester=e.get("semester"),
        exam_year=_opt(e, "exam_year", lambda v, w: _int(v, 2000, 2100, w), where),
        exam_region=_opt(e, "exam_region", lambda v, w: _str(v, CODE, w), where),
        exam_type=e.get("exam_type"),
        node_ids=tuple(nodes), item_ref=item_ref, page_ref=page_ref, source=source,
    )


def parse_manifest(doc: Any) -> DeliveryManifest:
    if not isinstance(doc, dict) or set(doc) != {"format", "kb", "entries"}:
        raise ManifestError("交付清单必须正好有 format、kb、entries")
    if doc["format"] != FORMAT:
        raise ManifestError(f"交付清单格式必须是 {FORMAT}")
    kb = doc["kb"]
    if not isinstance(kb, dict) or set(kb) != {"name", "version", "ids"}:
        raise ManifestError("kb 必须正好有 name、version、ids")
    ids = kb["ids"]
    if not isinstance(ids, list) or not ids or len(set(ids)) != len(ids):
        raise ManifestError("kb.ids 必须是非空、不重复的知识库 ID 列表")
    for k in ids:
        _str(k, OPAQUE_ID, "kb.ids")
    if not isinstance(doc["entries"], list):
        raise ManifestError("entries 必须是数组")
    entries = tuple(_entry(e, i) for i, e in enumerate(doc["entries"]))
    by_wid: dict[str, Entry] = {}
    by_ref: dict[str, Entry] = {}
    active_content: set[str] = set()
    for i, e in enumerate(entries):
        if e.weknora_id in by_wid:
            raise ManifestError(f"entries[{i}].weknora_id 重复")
        by_wid[e.weknora_id] = e
        if e.active:
            if e.content_id in active_content:
                raise ManifestError(f"entries[{i}].content_id 在有效条目里重复")
            active_content.add(e.content_id)
            if e.item_ref is not None:
                if e.item_ref in by_ref:
                    raise ManifestError(f"entries[{i}].item_ref 在有效条目里重复")
                by_ref[e.item_ref] = e
    return DeliveryManifest(kb_name=_str(kb["name"], KB_NAME, "kb.name"), kb_version=_str(kb["version"], KB_VERSION, "kb.version"),
                            kb_ids=tuple(ids), entries=entries, by_weknora_id=by_wid, by_item_ref=by_ref)


def load_manifest(path: str | Path) -> DeliveryManifest:
    p = Path(path)
    try:
        if p.is_symlink() or not p.is_file():
            raise ManifestError("交付清单必须是普通文件（不能是符号链接）")
        if p.stat().st_size > MAX_BYTES:
            raise ManifestError("交付清单超过 64 MiB")
        doc = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        raise ManifestError("交付清单不是可读的 UTF-8 JSON") from None
    return parse_manifest(doc)
