"""编译层页面格式（spec 设计要点 2；KGE V1.1 第四章“页面体系、三种段落”）。

页面 = YAML 头部 + Markdown 正文。正文只有一个一级标题，其余内容全部在带固定标记的段落里：

    ---
    <头部，键顺序固定，见 HEADER_KEYS>
    ---
    # <节点名称>

    <!-- kge:section id=definition kind=locked -->
    ## 定义
    ...
    <!-- kge:end id=definition -->

    <!-- kge:section id=relations kind=render -->
    ...

段落三种：locked（锁定段：定义、课标要求，教研确认后重编不可改写）、render（渲染段：由骨架与投影生成）、
narrative（叙述段：由模型编写、教研审核）。教材原句不写进页面：教材位置段只放投影引用与占位 `{{quote:<投影 ID>}}`，
读取时按学生版次渲染（PR ②）。

解析与序列化：`serialize(parse(text)) == text` 对规范写法逐字节成立；非规范写法（键顺序、引号、空行）解析后
序列化会变成规范写法，体检报 `format` 警告。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from typing import Any

import yaml

# 首期实现知识点页；KGE-07 第一步加素材卡（material，语文积累训练）；其余六类留类型不实现（KGE 第四章页面体系）
PAGE_TYPES = ("kp", "material")
RESERVED_PAGE_TYPES = ("viewpoint", "method", "pitfall", "unit_overview", "exam_profile", "edition_diff")
RISK_LEVELS = ("low", "medium", "high")
REBUILD_KINDS = ("render", "full")
SECTION_KINDS = ("locked", "render", "narrative")
SCHEMA_VERSION = 1

HEADER_KEYS = ("page_id", "page_type", "subject", "node_id", "map_version", "schema_version", "page_version", "risk_level",
               "rebuild_kind", "locked_sections", "atom_refs")
ATOM_REF_KEYS = ("type", "id", "hash")


@dataclass(frozen=True)
class SectionSpec:
    id: str
    kind: str
    title: str


# 知识点页的段落，顺序固定
KP_SECTIONS: tuple[SectionSpec, ...] = (
    SectionSpec("definition", "locked", "定义"),
    SectionSpec("curriculum", "locked", "课标要求"),
    SectionSpec("relations", "render", "前置与后续"),
    SectionSpec("textbook", "render", "教材位置"),
    SectionSpec("region", "render", "地区权重"),
    SectionSpec("key_points", "narrative", "讲解要点"),
    SectionSpec("pitfalls", "narrative", "易错"),
    SectionSpec("frequent_questions", "narrative", "高频设问"),
)
# 素材卡的段落，顺序固定（KGE 分册 :443 素材卡：篇目或词条原文、释义、例句、出处（册-课-页）、易混项）。
# 原文段只放教材原句占位 {{quote:<投影 ID>}}（原文不写进页面，读取时按版次渲染）；释义要点取节点 includes，例句取节点 examples，
# 出处取教材投影的版次、册、课、页码，易混项取 confusable 边（关联节点取 related 边）。只有“积累要点”是叙述段
MATERIAL_SECTIONS: tuple[SectionSpec, ...] = (
    SectionSpec("entry", "locked", "词条"),
    SectionSpec("source_text", "render", "原文"),
    SectionSpec("provenance", "render", "出处"),
    SectionSpec("examples", "render", "例句"),
    SectionSpec("confusables", "render", "易混项与关联"),
    SectionSpec("study_notes", "narrative", "积累要点"),
)
SECTIONS_BY_TYPE: dict[str, tuple[SectionSpec, ...]] = {"kp": KP_SECTIONS, "material": MATERIAL_SECTIONS}

BEGIN = re.compile(r"^<!-- kge:section id=([a-z_]+) kind=([a-z]+) -->$")
END = re.compile(r"^<!-- kge:end id=([a-z_]+) -->$")
SEMVER = re.compile(r"^(0|[1-9]\d{0,3})\.(0|[1-9]\d{0,3})\.(0|[1-9]\d{0,5})$")
MAP_VERSION = re.compile(r"^\d{1,3}\.\d{1,3}\.\d{1,4}$")
# 正文里的原子引用 [@type:id]、页面链接 [[页面 ID]]、教材原句占位 {{quote:投影 ID}}
CITATION = re.compile(r"\[@([a-z]+):([^\]\s]+)\]")
WIKILINK = re.compile(r"\[\[([^\]\s|]+)\]\]")
QUOTE_SLOT = re.compile(r"\{\{quote:([^}\s]+)\}\}")


class PageFormatError(ValueError):
    """页面无法解析（头部缺失、YAML 语法错误、段落标记不成对）"""

    def __init__(self, message: str, line: int | None = None) -> None:
        super().__init__(message)
        self.line = line


@dataclass(frozen=True)
class AtomRef:
    type: str
    id: str
    hash: str

    @property
    def key(self) -> str:
        return f"{self.type}:{self.id}"


@dataclass(frozen=True)
class Section:
    id: str
    kind: str
    content: str  # 起止标记之间的原文（含二级标题），不含首尾换行
    line: int = field(default=0, compare=False)  # 起始标记所在行（1 起），只用于体检定位


@dataclass(frozen=True)
class Page:
    header: dict[str, Any]
    title: str
    sections: tuple[Section, ...]
    stray: tuple[tuple[int, str], ...] = field(default=())  # 段落之外的非空行（行号，原文）：体检阻断
    title_line: int = field(default=0, compare=False)

    @property
    def page_id(self) -> str:
        return str(self.header.get("page_id", ""))

    @property
    def page_version(self) -> str:
        return str(self.header.get("page_version", ""))

    @property
    def atom_refs(self) -> tuple[AtomRef, ...]:
        refs = []
        for r in self.header.get("atom_refs") or []:
            if isinstance(r, dict):
                refs.append(AtomRef(str(r.get("type", "")), str(r.get("id", "")), str(r.get("hash", ""))))
        return tuple(refs)

    def section(self, sid: str) -> Section | None:
        return next((s for s in self.sections if s.id == sid), None)

    def with_sections(self, sections: tuple[Section, ...]) -> "Page":
        return replace(self, sections=sections)

    def with_header(self, **changes: Any) -> "Page":
        return replace(self, header={**self.header, **changes})


# ---------- 头部：规范写法 ----------

def _scalar(value: Any, flow: bool = False) -> str:
    if isinstance(value, bool) or value is None:
        raise PageFormatError("头部不收布尔与空值")
    if isinstance(value, int):
        return str(value)
    s = str(value)
    probe = "{k: %s}" if flow else "k: %s"
    try:
        loaded = yaml.safe_load(probe % s)
        if isinstance(loaded, dict) and loaded.get("k") == s and isinstance(loaded.get("k"), str) and s.strip() == s and s:
            return s
    except yaml.YAMLError:
        pass
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def serialize_header(header: dict[str, Any]) -> str:
    lines = ["---"]
    for key in HEADER_KEYS:
        if key not in header:
            continue
        v = header[key]
        if key == "locked_sections":
            lines.append(f"{key}: [" + ", ".join(_scalar(x, flow=True) for x in v) + "]")
        elif key == "atom_refs":
            if not v:
                lines.append(f"{key}: []")
                continue
            lines.append(f"{key}:")
            for r in v:
                parts = ", ".join(f"{k}: {_scalar(r[k], flow=True)}" for k in ATOM_REF_KEYS if k in r)
                lines.append(f"  - {{{parts}}}")
        else:
            lines.append(f"{key}: {_scalar(v)}")
    for key in sorted(set(header) - set(HEADER_KEYS)):  # 未知键原样放最后（体检会报 header 阻断）
        lines.append(f"{key}: {_scalar(header[key])}")
    lines.append("---")
    return "\n".join(lines) + "\n"


def serialize(page: Page) -> str:
    parts = [serialize_header(page.header), f"# {page.title}\n"]
    for s in page.sections:
        parts.append(f"\n<!-- kge:section id={s.id} kind={s.kind} -->\n{s.content}\n<!-- kge:end id={s.id} -->\n")
    return "".join(parts)


# ---------- 解析 ----------

def parse(text: str) -> Page:
    if "\r" in text:
        raise PageFormatError("页面只用 LF 换行")
    lines = text.split("\n")
    if not lines or lines[0] != "---":
        raise PageFormatError("页面必须以 YAML 头部（---）开头", 1)
    try:
        close = lines.index("---", 1)
    except ValueError:
        raise PageFormatError("YAML 头部没有结束标记 ---", 1) from None
    try:
        header = yaml.safe_load("\n".join(lines[1:close])) or {}
    except yaml.YAMLError as e:
        mark = getattr(e, "problem_mark", None)
        raise PageFormatError("YAML 头部语法错误", (mark.line + 2) if mark else 1) from None
    if not isinstance(header, dict):
        raise PageFormatError("YAML 头部必须是键值对象", 2)
    header = {str(k): v for k, v in header.items()}

    title = ""
    title_line = 0
    sections: list[Section] = []
    stray: list[tuple[int, str]] = []
    i = close + 1
    current: tuple[str, str, int, list[str]] | None = None
    while i < len(lines):
        ln, lineno = lines[i], i + 1
        if current is None:
            m = BEGIN.match(ln)
            if m:
                current = (m.group(1), m.group(2), lineno, [])
            elif END.match(ln):
                raise PageFormatError("段落结束标记没有对应的起始标记", lineno)
            elif ln.startswith("# ") and not title and not sections:
                title, title_line = ln[2:].strip(), lineno
            elif ln.strip():
                stray.append((lineno, ln))
        else:
            m = END.match(ln)
            if m:
                if m.group(1) != current[0]:
                    raise PageFormatError(f"段落 {current[0]} 的结束标记写成了 {m.group(1)}", lineno)
                sections.append(Section(current[0], current[1], "\n".join(current[3]), current[2]))
                current = None
            elif BEGIN.match(ln):
                raise PageFormatError(f"段落 {current[0]} 没有结束就开始了下一段", lineno)
            else:
                current[3].append(ln)
        i += 1
    if current is not None:
        raise PageFormatError(f"段落 {current[0]} 没有结束标记", current[2])
    return Page(header, title, tuple(sections), tuple(stray), title_line)


def paragraphs(content: str) -> list[tuple[int, str]]:
    """段落内容按空行切成自然段：（相对行号，文本）。标题行、`$$` 公式块单独算，不算事实性自然段"""
    out: list[tuple[int, str]] = []
    buf: list[str] = []
    start = 0
    in_math = False
    for idx, ln in enumerate(content.split("\n")):
        if ln.strip() == "$$":
            in_math = not in_math
            if buf and in_math:
                out.append((start, "\n".join(buf)))
                buf = []
            continue
        if in_math:
            continue
        if not ln.strip() or ln.startswith("#"):
            if buf:
                out.append((start, "\n".join(buf)))
                buf = []
            continue
        if not buf:
            start = idx
        buf.append(ln)
    if buf:
        out.append((start, "\n".join(buf)))
    return out
