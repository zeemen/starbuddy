"""规则式语义体检的各项检查（KGE-11 范围 1）。确定性、不调模型、不连库；只看叙述段（锁定段与渲染段由程序体检的模板检查逐字核对）。

每项检查一个函数，返回 LintIssue（layer = semantic）。消息只给定位与结论；回显的只有节点 ID、原子键、地图快照里的值
和词表内容，不回显页面原文（规则 13，与程序体检同口径）。
"""
from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from functools import cached_property

from ..atoms import VERB_LEVELS, Snapshot, edge_key
from ..lint import mathcheck
from ..lint.checks import PageFile
from ..lint.issues import LintIssue
from ..lint.lexicon import Lexicon
from ..pages.format import CITATION, QUOTE_SLOT, WIKILINK, Page, paragraphs
from .config import DEFAULT_LEVELS, level_of
from .inputs import EMPTY_TERMS, ScopeVocab, StyleLexicon, TermTable

LAYER = "semantic"
_SENTENCE_SPLIT = re.compile(r"[。！？!?；;]")
_INEQ = re.compile(r"\\(?:le|ge|leq|geq|ne|neq|lt|gt|approx)\b|[<>]")
SELF_WORDS = ("本知识点", "这一知识点", "本节", "本课")
# 跨页前置方向：节点提及先换成占位 \ue000<序号>\ue001，再按句式取（前置，后续）
_T = "\ue000(\\d+)\ue001"
_LEARN = "(?:学习|学会|学|掌握|理解)?(?:好)?"
DIRECTION_PATTERNS: tuple[tuple[re.Pattern[str], bool], ...] = (  # （句式，True = 第一个是前置）
    (re.compile(rf"先{_LEARN}{_T}[，,、\s]*(?:再|才能|然后|之后再){_LEARN}{_T}"), True),
    (re.compile(rf"{_T}(?:是|为)(?:学习)?{_T}的(?:前置知识|前置|基础|前提)"), True),
    (re.compile(rf"{_T}(?:要|需要|须|必须)?以{_T}为(?:前置|基础|前提)"), False),
    (re.compile(rf"(?:学习|学)?{_T}(?:之前|以前|前)[，,\s]*(?:要|需要|须|必须|应该|应)?先{_LEARN}{_T}"), False),
)
_WEIGHT = re.compile(r"权重\s*(?:约为|约|为|是|：|:)?\s*(\d+(?:\.\d+)?)(?![\d.]|\s*[%％])")
_SHARE = re.compile(r"(?:分值占比|占比|权重|占)\s*(?:约为|约|为|是|：|:)?\s*(\d+(?:\.\d+)?)\s*[%％]")
_EXAM_YEAR = re.compile(r"(20\d{2})\s*年?\s*的?(?:中考|考试|考情|试卷)")
_VERB_ZH = {v: k for k, v in VERB_LEVELS.items()}
_CURRICULUM_LEVEL = re.compile(r"(?:课标|课程标准)[^。；;]{0,12}?(?:要求|层次)[^。；;]{0,4}?(知道|理解|运用|综合)|【(知道|理解|运用|综合)】")
_PAGE_REF = re.compile(r"第\s*(\d{1,4})\s*(?:[–—\-~～至到]\s*(\d{1,4})\s*)?页")
_INCLUDE_MARK = re.compile(r"属于|包括|包含|算作|也是|是一种|也叫")
_NEGATION = re.compile(r"不|没有|并非|非|无")


@dataclass
class SemanticContext:
    files: list[PageFile]
    snapshot: Snapshot
    lexicon: Lexicon
    scope_vocab: ScopeVocab | None = None
    style: StyleLexicon | None = None
    terms: TermTable = EMPTY_TERMS
    levels: Mapping[str, str] = field(default_factory=lambda: DEFAULT_LEVELS)
    affected: frozenset[str] | None = None  # 本次受影响页（编入清单的页面 ID）；None = 每日全量（所有页都算受影响）

    @cached_property
    def pages(self) -> dict[str, PageFile]:
        return {pf.page.page_id: pf for pf in self.files if pf.page is not None}

    @cached_property
    def matcher(self) -> "TermMatcher":
        return TermMatcher.build(self.snapshot, self.terms)

    @cached_property
    def edition_markers(self) -> dict[str, str]:
        """版次专属表述 → 版次。
        - 快照里至少有两个版次时，教材投影的册名、章名只出现在一个版次下的才算该版次的专属表述；只有一个版次时不从投影推断
          （单版次下“专属”无从谈起，章名常常就是知识点名，推断只会误报）；
        - 加术语表里的 edition_terms（与投影推断冲突的去掉）；
        - 与任何节点名称、别名重合（相等或互为子串）的词一律剔除：那是知识点本身的说法，不是版次专属表述。"""
        projections = self.snapshot.of_type("projection")
        out: dict[str, str] = {}
        if len({str(a.record["edition"]) for a in projections}) >= 2:
            seen: dict[str, set[str]] = {}
            for a in projections:
                for f in ("volume", "chapter"):
                    v = str(a.record.get(f) or "").strip()
                    if len(v) >= 2:
                        seen.setdefault(v, set()).add(str(a.record["edition"]))
            out = {v: next(iter(eds)) for v, eds in seen.items() if len(eds) == 1}
        for ed, xs in self.terms.edition_terms.items():
            for t in xs:
                if t in out and out[t] != ed:
                    out.pop(t)
                else:
                    out[t] = ed
        names = {n for n in set(self.matcher.owner) | set(self.matcher.canonical.values()) if len(n) >= 2}
        return {w: ed for w, ed in out.items() if not any(w == n or w in n or n in w for n in names)}


# ---------- 术语识别 ----------

@dataclass(frozen=True)
class TermMatcher:
    """节点名称与别名的最长匹配。一个写法属于多个节点（重名）时不参与比对（判不出指哪个节点）"""
    pattern: re.Pattern[str] | None
    owner: Mapping[str, str]  # 写法 → 节点 ID
    canonical: Mapping[str, str]  # 节点 ID → 节点名称

    @staticmethod
    def build(snap: Snapshot, terms: TermTable) -> "TermMatcher":
        owners: dict[str, set[str]] = {}
        canonical: dict[str, str] = {}
        for a in snap.of_type("node"):
            if a.record.get("status", "active") != "active" or a.record.get("level") not in ("node", "detail"):
                continue
            canonical[a.id] = str(a.record["name"])
            owners.setdefault(str(a.record["name"]), set()).add(a.id)
        for nid, xs in terms.aliases.items():
            if nid not in canonical:
                continue
            for x in xs:
                owners.setdefault(x, set()).add(nid)
        owner = {w: next(iter(ns)) for w, ns in owners.items() if len(ns) == 1 and len(w) >= 2}
        words = sorted(owner, key=lambda w: (-len(w), w))
        pat = re.compile("|".join(re.escape(w) for w in words)) if words else None
        return TermMatcher(pat, owner, canonical)

    def forms(self, text: str) -> Iterator[tuple[str, str]]:
        """（节点 ID，写法）"""
        if self.pattern is None:
            return
        for m in self.pattern.finditer(text):
            yield self.owner[m.group(0)], m.group(0)


def strip_refs(text: str) -> str:
    return QUOTE_SLOT.sub(" ", WIKILINK.sub(" ", CITATION.sub(" ", text)))


def narrative_paragraphs(page: Page) -> Iterator[tuple[int, str, str]]:
    """（文件行号，段落 ID，自然段原文）：只取叙述段；`$$` 公式块不算自然段"""
    for s in page.sections:
        if s.kind != "narrative":
            continue
        for rel, para in paragraphs(s.content):
            yield s.line + 1 + rel, s.id, para


def narrative_lines(page: Page) -> Iterator[tuple[int, str, str]]:
    for line, sid, para in narrative_paragraphs(page):
        for k, ln in enumerate(para.split("\n")):
            yield line + k, sid, ln


def _cited(para: str, atype: str) -> list[str]:
    return [m.group(2) for m in CITATION.finditer(para) if m.group(1) == atype]


def _sentences(text: str) -> list[str]:
    return [s for s in _SENTENCE_SPLIT.split(text) if s.strip()]


def _issue(ctx: SemanticContext, pf: PageFile, check: str, rule: str, message: str, line: int | None = None, section: str | None = None,
           **detail: object) -> LintIssue:
    page = pf.page
    assert page is not None
    key = f"{check}.{rule}"
    return LintIssue(check, level_of(ctx.levels, key), page.page_id, page.page_version, pf.rel, message, line, section,
                     {"rule": key, **detail}, layer=LAYER)


def global_issue(ctx: SemanticContext, check: str, rule: str, message: str, page_id: str = "*", **detail: object) -> LintIssue:
    key = f"{check}.{rule}"
    return LintIssue(check, level_of(ctx.levels, key), page_id, "", "", message, None, None, {"rule": key, **detail}, layer=LAYER)


# ---------- 与原子冲突 ----------

def _equation(src: str) -> tuple[str, str] | None:
    """恰好一个等号、不含不等号的等式：（左，右）——LaTeX 源，交给 mathcheck 验算"""
    if _INEQ.search(src) or src.count("=") != 1:
        return None
    left, right = (p.strip() for p in src.split("="))
    return (left, right) if left and right else None


def _equations(text: str) -> list[tuple[str, str]]:
    formulas, _ = mathcheck.extract(text)
    return [eq for f in formulas if (eq := _equation(f.source)) is not None]


def _same(a: str, b: str) -> mathcheck.CheckOutcome:
    return mathcheck.verify_chain(f"{a} = {b}", None)


def check_atom_conflict(ctx: SemanticContext, pf: PageFile) -> list[LintIssue]:
    page = pf.page
    assert page is not None
    snap = ctx.snapshot
    out: list[LintIssue] = []
    node_atoms = [a for r in page.atom_refs if r.type == "node" and (a := snap.get("node", r.id)) is not None]
    # 公式：所引节点原子（includes、examples）里的等式 vs 叙述段里的等式；左边等价而右边不等价即冲突（SymPy，复用程序体检的受限验算）
    atom_eqs: list[tuple[str, str, str]] = []
    for a in node_atoms:
        for fld in ("includes", "examples"):
            for text in a.record.get(fld) or []:
                atom_eqs += [(a.key, lhs, rhs) for lhs, rhs in _equations(str(text))]
    if atom_eqs:
        for s in page.sections:
            if s.kind != "narrative":
                continue
            formulas, _ = mathcheck.extract(s.content)
            for f in formulas:
                eq = _equation(f.source)
                if eq is None:
                    continue
                left, right = eq
                for key, lhs, rhs in atom_eqs:
                    for mine, theirs in ((left, right), (right, left)):
                        if not _same(mine, lhs).ok:
                            continue
                        res = _same(theirs, rhs)
                        if not res.ok and res.verifiable:
                            out.append(_issue(ctx, pf, "atom_conflict", "formula",
                                              f"叙述段的等式与所引原子 {key} 里的同一等式不符（SymPy：左边等价、右边不等价）",
                                              s.line + 1 + f.line, s.id, atom=key))
                        break
    own = snap.node(str(page.header.get("node_id")))
    excludes = [str(x) for x in (own or {}).get("excludes") or []]
    for line, sid, para in narrative_paragraphs(page):
        text = strip_refs(para)
        # 地区投影：权重、占比、考试年份
        regions = [a for rid in _cited(para, "region") if (a := snap.get("region", rid)) is not None]
        if regions:
            weights = [float(a.record["weight"]) for a in regions]
            years = {int(a.record["exam_year"]) for a in regions}
            keys = ", ".join(a.key for a in regions)
            for m in _WEIGHT.finditer(text):
                if not any(abs(float(m.group(1)) - w) < 5e-4 for w in weights):
                    out.append(_issue(ctx, pf, "atom_conflict", "region_number",
                                      f"写的权重与所引原子 {keys} 不符（原子：{'、'.join(f'{w:.3f}' for w in weights)}）", line, sid, atom=keys))
            for m in _SHARE.finditer(text):
                if not any(abs(float(m.group(1)) / 100 - w) < 5e-4 for w in weights):
                    out.append(_issue(ctx, pf, "atom_conflict", "region_number",
                                      f"写的占比与所引原子 {keys} 的权重不符（原子：{'、'.join(f'{w:.3f}' for w in weights)}）", line, sid, atom=keys))
            for m in _EXAM_YEAR.finditer(text):
                if int(m.group(1)) not in years:
                    out.append(_issue(ctx, pf, "atom_conflict", "region_number",
                                      f"写的考试年份与所引原子 {keys} 不符（原子：{'、'.join(str(y) for y in sorted(years))}）", line, sid, atom=keys))
        # 课标条目：要求层次
        currs = [a for cid in _cited(para, "curriculum") if (a := snap.get("curriculum", cid)) is not None]
        if currs:
            allowed = {str(a.record["verb_level"]) for a in currs}
            for m in _CURRICULUM_LEVEL.finditer(text):
                zh = m.group(1) or m.group(2)
                if _VERB_ZH[zh] not in allowed:
                    keys = ", ".join(a.key for a in currs)
                    want = "、".join(VERB_LEVELS[v] for v in sorted(allowed))
                    out.append(_issue(ctx, pf, "atom_conflict", "curriculum_level",
                                      f"写的课标要求层次与所引原子 {keys} 不符（原子：{want}）", line, sid, atom=keys))
        # 教材投影：页码
        projs = [a for pid in _cited(para, "projection") if (a := snap.get("projection", pid)) is not None]
        if projs:
            for m in _PAGE_REF.finditer(text):
                lo = int(m.group(1))
                hi = int(m.group(2) or lo)
                if not any(int(a.record["page_from"]) <= lo <= hi <= int(a.record["page_to"]) for a in projs):
                    keys = ", ".join(a.key for a in projs)
                    rng = "、".join(f"{a.record['page_from']}–{a.record['page_to']}" for a in projs)
                    out.append(_issue(ctx, pf, "atom_conflict", "page_range", f"写的页码超出所引原子 {keys} 的页码范围（{rng}）",
                                      line, sid, atom=keys))
        # 节点“不包括”的内容被说成属于本节点
        if excludes:
            node_id = str(page.header.get("node_id"))
            names = [ctx.matcher.canonical.get(node_id, ""), *ctx.terms.aliases.get(node_id, ()), *SELF_WORDS]
            for sent in _sentences(text):
                for x in excludes:
                    if x not in sent or _NEGATION.search(sent) or not _INCLUDE_MARK.search(sent):
                        continue
                    rest = sent.replace(x, " ")
                    if any(n and n in rest for n in names):
                        out.append(_issue(ctx, pf, "atom_conflict", "excluded_scope",
                                          f"把节点 {node_id} 明确不包括的“{x}”说成属于本节点", line, sid, atom=f"node:{node_id}"))
    return out


# ---------- 跨页矛盾：前置方向 ----------

def direction_claims(ctx: SemanticContext, page: Page) -> list[tuple[int, str, str, str]]:
    """（行号，段落 ID，前置节点，后续节点）：叙述段里关于前置关系的表述"""
    out = []
    own = str(page.header.get("node_id"))
    m = ctx.matcher
    alts = [r"\[\[([^\]\s|]+)\]\]", "(" + "|".join(SELF_WORDS) + ")"]
    if m.pattern is not None:
        alts.append(f"({m.pattern.pattern})")
    combined = re.compile("|".join(alts))
    for line, sid, para in narrative_paragraphs(page):
        text = QUOTE_SLOT.sub(" ", CITATION.sub(" ", para))
        nodes: list[str] = []

        def token(mt: re.Match[str]) -> str:
            if mt.group(1) is not None:
                nid = mt.group(1)
                if nid not in m.canonical:
                    return " "
            elif mt.group(2) is not None:
                nid = own
            else:
                nid = m.owner[mt.group(3)]
            nodes.append(nid)
            return f"\ue000{len(nodes) - 1}\ue001"

        tok = combined.sub(token, text)
        found: set[tuple[str, str]] = set()
        for sent in _sentences(tok):
            for pat, first_is_pre in DIRECTION_PATTERNS:
                for mt in pat.finditer(sent):
                    a, b = nodes[int(mt.group(1))], nodes[int(mt.group(2))]
                    pre, post = (a, b) if first_is_pre else (b, a)
                    if pre != post:
                        found.add((pre, post))
        out += [(line, sid, pre, post) for pre, post in sorted(found)]
    return out


def _edge(snap: Snapshot, pre: str, post: str) -> bool:
    a = snap.get("edge", edge_key(pre, "prerequisite", post))
    return a is not None and a.record.get("status", "active") == "active"


def check_prerequisite_direction(ctx: SemanticContext, pf: PageFile) -> list[LintIssue]:
    page = pf.page
    assert page is not None
    out = []
    for line, sid, pre, post in direction_claims(ctx, page):
        if _edge(ctx.snapshot, pre, post):
            continue
        if _edge(ctx.snapshot, post, pre):
            out.append(_issue(ctx, pf, "prerequisite_direction", "reversed",
                              f"说 {pre} 是 {post} 的前置，与骨架里的前置边方向相反（骨架：{post} 是 {pre} 的前置）", line, sid,
                              claimed=[pre, post]))
        else:
            out.append(_issue(ctx, pf, "prerequisite_direction", "no_edge", f"说 {pre} 是 {post} 的前置，骨架里没有这条前置边",
                              line, sid, claimed=[pre, post]))
    return out


def check_pair_direction(ctx: SemanticContext, a: PageFile, b: PageFile) -> list[LintIssue]:
    """骨架里没有边、两张相邻页的说法又相反：两边都报"""
    assert a.page is not None and b.page is not None
    ca, cb = direction_claims(ctx, a.page), direction_claims(ctx, b.page)
    out = []
    for la, sa, pre, post in ca:
        if _edge(ctx.snapshot, pre, post) or _edge(ctx.snapshot, post, pre):
            continue
        for lb, sb, pre2, post2 in cb:
            if (pre2, post2) == (post, pre):
                out.append(_issue(ctx, a, "prerequisite_direction", "pages_disagree",
                                  f"与相邻页 {b.page.page_id} 对 {pre}、{post} 的前置方向说法相反（骨架里没有这条边）", la, sa,
                                  other_page=b.page.page_id))
                out.append(_issue(ctx, b, "prerequisite_direction", "pages_disagree",
                                  f"与相邻页 {a.page.page_id} 对 {pre}、{post} 的前置方向说法相反（骨架里没有这条边）", lb, sb,
                                  other_page=a.page.page_id))
    return out


# ---------- 跨页矛盾：相邻页术语写法 ----------

def term_forms(ctx: SemanticContext, page: Page) -> dict[str, dict[str, tuple[int, str]]]:
    """节点 ID → {写法: 首次出现的（行号，段落 ID）}"""
    out: dict[str, dict[str, tuple[int, str]]] = {}
    for line, sid, ln in narrative_lines(page):
        for nid, form in ctx.matcher.forms(strip_refs(ln)):
            out.setdefault(nid, {}).setdefault(form, (line, sid))
    return out


def check_pair_terms(ctx: SemanticContext, a: PageFile, b: PageFile) -> list[LintIssue]:
    assert a.page is not None and b.page is not None
    fa, fb = term_forms(ctx, a.page), term_forms(ctx, b.page)
    out = []
    for nid in sorted(set(fa) & set(fb)):
        if set(fa[nid]) == set(fb[nid]):
            continue
        name = ctx.matcher.canonical[nid]
        for me, mine, theirs, other in ((a, fa[nid], fb[nid], b), (b, fb[nid], fa[nid], a)):
            odd = sorted(f for f in mine if f != name and f not in theirs)
            if not odd:
                continue
            line, sid = mine[odd[0]]
            assert other.page is not None
            out.append(_issue(ctx, me, "term_consistency", "variant",
                              f"节点 {nid}（{name}）在本页写作“{'、'.join(odd)}”，相邻页 {other.page.page_id} 写作“{'、'.join(sorted(theirs))}”",
                              line, sid, node=nid, forms=odd, other_page=other.page.page_id))
    return out


# ---------- 版本差异误判 ----------

def check_edition_scope(ctx: SemanticContext, pf: PageFile) -> list[LintIssue]:
    page = pf.page
    assert page is not None
    out = []
    markers = ctx.edition_markers
    words = sorted(markers, key=lambda w: (-len(w), w))
    pat = re.compile("|".join(re.escape(w) for w in words)) if words else None
    for line, sid, para in narrative_paragraphs(page):
        text = strip_refs(para)
        editions = {str(a.record["edition"]) for pid in _cited(para, "projection") if (a := ctx.snapshot.get("projection", pid)) is not None}
        hits: list[tuple[str, str]] = []  # （表述，版次）
        if pat is not None:
            hits += [(m.group(0), markers[m.group(0)]) for m in pat.finditer(text)]
        for m in _PAGE_REF.finditer(text):
            if not editions:
                out.append(_issue(ctx, pf, "edition_scope", "unmarked", "写了教材页码（页码随版次不同），但本段没有引用任何教材投影（未标版次）",
                                  line, sid))
                break
        for expr, ed in sorted(set(hits)):
            if not editions:
                out.append(_issue(ctx, pf, "edition_scope", "unmarked", f"出现版次 {ed} 的专属表述“{expr}”，但本段没有引用该版次的教材投影",
                                  line, sid, edition=ed, expression=expr))
            elif ed not in editions:
                out.append(_issue(ctx, pf, "edition_scope", "mismatch",
                                  f"出现版次 {ed} 的专属表述“{expr}”，而本段引用的教材投影是 {'、'.join(sorted(editions))}", line, sid,
                                  edition=ed, expression=expr))
    return out


# ---------- 超纲 ----------

def check_out_of_scope(ctx: SemanticContext, pf: PageFile) -> list[LintIssue]:
    page = pf.page
    assert page is not None
    if ctx.scope_vocab is None:
        return []
    scope = ctx.scope_vocab.subjects.get(str(page.header.get("subject")))
    if scope is None:
        return []
    beyond = scope.beyond(str(page.header.get("node_id")))
    out = []
    for line, sid, ln in narrative_lines(page):
        text = strip_refs(ln)
        for term, stage in beyond:
            if term in text:
                out.append(_issue(ctx, pf, "out_of_scope", "term", f"出现超出本节点适用学段的术语“{term}”（词表学段：{stage}）", line, sid,
                                  term=term, stage=stage))
    return out


# ---------- 适龄文风 ----------

def check_age_style(ctx: SemanticContext, pf: PageFile) -> list[LintIssue]:
    page = pf.page
    assert page is not None
    if ctx.style is None:
        return []
    out = []
    for line, sid, ln in narrative_lines(page):
        text = re.sub(r"\$[^$]*\$", "式", strip_refs(ln))
        for rule in ctx.style.rules:
            if rule.search(text):
                out.append(_issue(ctx, pf, "age_style", rule.id, f"适龄文风（{rule.id}）：{rule.message}", line, sid, style_rule=rule.id))
    return out


# ---------- 角色语言混入 ----------

def normalize(text: str) -> str:
    """NFKC 后去掉空白、标点与符号：拆开写、夹标点、全半角混写的角色名与口头禅也能比上"""
    return "".join(ch for ch in unicodedata.normalize("NFKC", text) if unicodedata.category(ch)[0] not in "PSZC")


def check_persona_mix(ctx: SemanticContext, pf: PageFile) -> list[LintIssue]:
    """程序体检逐行字面比对人设词表；这里按自然段归一化后再比，只报程序层比不上的（不重复报）"""
    page = pf.page
    assert page is not None
    lex = ctx.lexicon
    words = [("角色名", w) for w in lex.role_names] + [("称呼", w) for w in lex.address_terms] + [("口头禅或固定句式", w) for w in lex.catchphrases]
    out = []
    for line, sid, para in narrative_paragraphs(page):
        lines = [strip_refs(ln) for ln in para.split("\n")]
        flat = normalize(strip_refs(para))
        for label, word in words:
            nw = normalize(word)
            if nw and nw in flat and not any(word in ln for ln in lines):
                out.append(_issue(ctx, pf, "persona_mix", "normalized", f"页面须人设中立：去掉空白与标点后出现了{label}“{word}”", line, sid,
                                  term=word))
        for rule in lex.setting_patterns:
            if re.search(rule.regex, flat) and not any(re.search(rule.regex, ln) for ln in lines):
                out.append(_issue(ctx, pf, "persona_mix", "normalized", f"页面须人设中立：去掉空白与标点后出现了角色设定元素（{rule.id}）",
                                  line, sid, rule_id=rule.id))
    return out


PAGE_CHECKS = (check_atom_conflict, check_prerequisite_direction, check_edition_scope, check_out_of_scope, check_age_style,
               check_persona_mix)
PAIR_CHECKS = (check_pair_direction, check_pair_terms)


def run_page_checks(ctx: SemanticContext, pf: PageFile) -> list[LintIssue]:
    out: list[LintIssue] = []
    for chk in PAGE_CHECKS:
        out += chk(ctx, pf)
    return out


def run_pair_checks(ctx: SemanticContext, pairs: Iterable[tuple[str, str]]) -> list[LintIssue]:
    out: list[LintIssue] = []
    for a, b in pairs:
        for chk in PAIR_CHECKS:
            out += chk(ctx, ctx.pages[a], ctx.pages[b])
    return out
