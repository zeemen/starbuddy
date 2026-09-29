"""程序体检的各项检查（spec 设计要点 4）。每项检查一个函数，返回 LintIssue 列表；阻断即不能合并。

语义体检（与原子冲突、跨页矛盾、超纲、角色语言混入的语义层）不在本任务：阶段 0 中风险按高风险处理。
"""
from __future__ import annotations

import datetime as dt
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from ..atoms import ATOM_TYPES, HASH_HEX_LEN, HASH_PREFIX, Snapshot
from ..compiler.templates import render_sections
from ..nodeid import SUBJECT_PREFIX, is_node_id
from ..pages.format import (CITATION, HEADER_KEYS, MAP_VERSION, PAGE_TYPES, QUOTE_SLOT, REBUILD_KINDS, RESERVED_PAGE_TYPES,
                            RISK_LEVELS, SCHEMA_VERSION, SECTIONS_BY_TYPE, SEMVER, WIKILINK, Page, PageFormatError, paragraphs,
                            serialize)
from . import mathcheck
from .issues import LintIssue
from .lexicon import Lexicon

DEFAULT_MIN_GROUP = 30  # 群体统计最小人数门槛（spec 待人确认 #6，默认 30）
KP_NODE_KINDS = ("kp", "ability_kp")
KP_NODE_LEVELS = ("node", "detail")
_HASH = re.compile(rf"^{re.escape(HASH_PREFIX)}[0-9a-f]{{{HASH_HEX_LEN}}}$")
_MD_LINK = re.compile(r"\]\(([^)\s]+)\)")

# ---------- 规则 13：个人信息与学生标识 ----------
_SURNAMES = ("王李张刘陈杨黄赵吴周徐孙马朱胡郭何高林罗郑梁谢宋唐许韩冯邓曹彭曾肖田董袁潘于蒋蔡余杜叶程苏魏吕丁任沈姚卢姜崔"
             "钟谭陆汪范金石廖贾夏韦付方白邹孟熊秦邱江尹薛闫段雷侯龙史陶黎贺顾毛郝龚邵万钱严覃武戴莫孔向汤")
_SCHOOL = r"(?:中学|小学|附中|初级中学|外国语学校|实验学校)"
PII_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("手机号", re.compile(r"(?<!\d)1[3-9]\d[- ]?\d{4}[- ]?\d{4}(?!\d)")),
    ("身份证号", re.compile(r"(?<![0-9A-Za-z])[1-9]\d{5}(?:18|19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx](?![0-9A-Za-z])")),
    ("学号式编号", re.compile(r"(?:学号|考号|准考证号|学籍号)\s*[:：]?\s*[A-Za-z0-9]{4,}|(?<![A-Za-z0-9])[A-Za-z]{1,4}\d{6,12}(?![A-Za-z0-9])")),
    ("UUID", re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")),
    ("电子邮箱", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")),
    ("姓名 + 学校", re.compile(rf"[{_SURNAMES}][一-龥]{{1,2}}(?:同学|学生)?[，,、\s（(]{{0,2}}[一-龥]{{0,10}}?{_SCHOOL}"
                           rf"|{_SCHOOL}[一-龥]{{0,6}}?的?[{_SURNAMES}][一-龥]{{1,2}}(?:同学|学生)")),
)
# ---------- 规则 13：群体统计 ----------
_STAT = re.compile(r"正确率|得分率|错误率|错选率|失分率|通过率|及格率|平均分|\d+(?:\.\d+)?\s*[%％]")
_GROUP = re.compile(r"学生|同学|考生|作答|样本|人次|\d+\s*人")
_N = re.compile(r"[nN]\s*[=＝]\s*(\d+)|(\d+)\s*(?:名|位)?(?:学生|同学|考生|人)|样本[量数]?\s*[:：为]?\s*(\d+)")
_SENTENCE_SPLIT = re.compile(r"[。！？!?；;]")


@dataclass(frozen=True)
class PageFile:
    rel: str
    text: str
    page: Page | None
    error: PageFormatError | None = None


@dataclass
class LintContext:
    files: list[PageFile]
    lexicon: Lexicon
    snapshot: Snapshot | None = None
    baseline: dict[str, Page] = field(default_factory=dict)
    today: dt.date = field(default_factory=dt.date.today)
    min_group: int = DEFAULT_MIN_GROUP

    @property
    def page_ids(self) -> set[str]:
        """页面 ID 集合；解析失败的页面按文件名计入，免得一页坏了连带别的页面报失效链接（坏页本身由 format 阻断）"""
        return {pf.page.page_id if pf.page is not None else PurePosixPath(pf.rel).stem for pf in self.files}


def _issue(pf: PageFile, check: str, severity: str, message: str, line: int | None = None, section: str | None = None,
           **detail: object) -> LintIssue:
    page_id = pf.page.page_id if pf.page else PurePosixPath(pf.rel).stem
    version = pf.page.page_version if pf.page else ""
    return LintIssue(check, severity, page_id, version, pf.rel, message, line, section, dict(detail))


def _strip_refs(text: str) -> str:
    return QUOTE_SLOT.sub(" ", WIKILINK.sub(" ", CITATION.sub(" ", text)))


def body_lines(page: Page) -> Iterator[tuple[int, str | None, str]]:
    """（文件行号，段落 ID，原文）：段落内容逐行；标题行的段落 ID 为 None"""
    if page.title:
        yield page.title_line, None, page.title
    for s in page.sections:
        for k, ln in enumerate(s.content.split("\n")):
            yield s.line + 1 + k, s.id, ln


def _cited_keys(text: str) -> set[str]:
    return {f"{m.group(1)}:{m.group(2)}" for m in CITATION.finditer(text)}


# ---------- 结构 ----------

def check_format(pf: PageFile) -> list[LintIssue]:
    if pf.error is not None:
        return [_issue(pf, "format", "block", f"页面无法解析：{pf.error}", pf.error.line)]
    assert pf.page is not None
    if serialize(pf.page) != pf.text:
        return [_issue(pf, "format", "warn", "不是规范写法（头部键顺序、引号或空行与序列化结果不同），合并前请用编译器重新序列化")]
    return []


def check_header(pf: PageFile) -> list[LintIssue]:
    page = pf.page
    assert page is not None
    h = page.header
    out: list[LintIssue] = []
    bad = lambda msg, **d: out.append(_issue(pf, "header", "block", msg, 2, **d))  # noqa: E731
    missing = [k for k in HEADER_KEYS if k not in h]
    if missing:
        bad(f"头部缺字段：{', '.join(missing)}", fields=missing)
    unknown = sorted(set(h) - set(HEADER_KEYS))
    if unknown:
        bad(f"头部有未登记字段：{', '.join(unknown)}", fields=unknown)
    ptype = h.get("page_type")
    if ptype in RESERVED_PAGE_TYPES:
        bad(f"页面类型 {ptype} 已预留但首期未实现（只做知识点页 kp）")
    elif ptype not in PAGE_TYPES:
        bad(f"页面类型不合法：{ptype!r}")
    subject, node_id = h.get("subject"), h.get("node_id")
    if subject not in SUBJECT_PREFIX:
        bad(f"subject 只收平台学科编码：{subject!r}")
    if not is_node_id(node_id):
        bad(f"node_id 不合节点 ID 规范：{node_id!r}")
    elif subject in SUBJECT_PREFIX and str(node_id).split(".")[0] != SUBJECT_PREFIX[subject]:
        bad(f"node_id 的学科前缀与 subject 不符（{subject} 应为 {SUBJECT_PREFIX[subject]}）")
    if h.get("page_id") != node_id:
        bad("page_id 必须等于 node_id（KGE 第二章：页面 ID = 节点 ID）")
    rel = PurePosixPath(pf.rel)
    if rel.name != f"{h.get('page_id')}.md" or rel.parent.name != str(ptype):
        bad(f"文件位置应为 {ptype}/{h.get('page_id')}.md")
    if not isinstance(h.get("map_version"), str) or not MAP_VERSION.match(h["map_version"]):
        bad("map_version 应为 主.次.修订")
    sv = h.get("schema_version")
    if not isinstance(sv, int) or isinstance(sv, bool) or sv != SCHEMA_VERSION:
        bad(f"schema_version 应为 {SCHEMA_VERSION}")
    if not isinstance(h.get("page_version"), str) or not SEMVER.match(h["page_version"]):
        bad("page_version 应为 semver（主.次.修订）")
    if h.get("risk_level") not in RISK_LEVELS:
        bad(f"risk_level 只收 {'/'.join(RISK_LEVELS)}")
    if h.get("rebuild_kind") not in REBUILD_KINDS:
        bad(f"rebuild_kind 只收 {'/'.join(REBUILD_KINDS)}")
    elif h.get("rebuild_kind") == "full" and h.get("risk_level") != "high":
        bad("全量重编的页面 risk_level 必须是 high")
    if ptype in SECTIONS_BY_TYPE:
        want = [s.id for s in SECTIONS_BY_TYPE[ptype] if s.kind == "locked"]
        if h.get("locked_sections") != want:
            bad(f"locked_sections 应为 {want}")
    refs = h.get("atom_refs")
    if not isinstance(refs, list) or not refs:
        bad("atom_refs 必须是非空数组")
    else:
        seen: set[str] = set()
        for i, r in enumerate(refs):
            if not isinstance(r, dict) or set(r) != {"type", "id", "hash"}:
                bad(f"atom_refs[{i}] 必须只有 type、id、hash 三个字段")
                continue
            if r["type"] not in ATOM_TYPES:
                bad(f"atom_refs[{i}] 的 type 不合法：{r['type']!r}")
            if not isinstance(r["hash"], str) or not _HASH.match(r["hash"]):
                bad(f"atom_refs[{i}] 的 hash 应为 {HASH_PREFIX}<{HASH_HEX_LEN} 位十六进制>")
            key = f"{r['type']}:{r['id']}"
            if key in seen:
                bad(f"atom_refs 重复：{key}")
            seen.add(key)
    return out


def check_node(pf: PageFile, ctx: LintContext) -> list[LintIssue]:
    page, snap = pf.page, ctx.snapshot
    assert page is not None
    if snap is None:
        return []
    out = []
    node_id = page.header.get("node_id")
    if snap.node(str(node_id)) is None:
        out.append(_issue(pf, "node_id", "block", f"节点 {node_id} 不在地图快照 {snap.subject} {snap.map_version} 里", 2))
    if page.header.get("map_version") != snap.map_version:
        out.append(_issue(pf, "node_id", "block", f"页面的 map_version {page.header.get('map_version')} 与指定地图快照 {snap.map_version} 不一致", 2))
    if page.header.get("subject") != snap.subject:
        out.append(_issue(pf, "node_id", "block", "页面的 subject 与地图快照不一致", 2))
    return out


def check_sections(pf: PageFile) -> list[LintIssue]:
    page = pf.page
    assert page is not None
    out: list[LintIssue] = []
    specs = SECTIONS_BY_TYPE.get(str(page.header.get("page_type")))
    if not page.title:
        out.append(_issue(pf, "sections", "block", "缺一级标题（# 节点名称）"))
    for lineno, text in page.stray:
        out.append(_issue(pf, "sections", "block", f"段落标记之外有内容：{text[:40]}", lineno))
    if specs is None:
        return out
    got = [(s.id, s.kind) for s in page.sections]
    want = [(s.id, s.kind) for s in specs]
    if got != want:
        missing = [f"{i}({k})" for i, k in want if (i, k) not in got]
        extra = [f"{i}({k})" for i, k in got if (i, k) not in want]
        dup = sorted({i for i, _ in got if [g[0] for g in got].count(i) > 1})
        msg = "段落标记不齐全或顺序不对"
        if missing:
            msg += f"；缺：{', '.join(missing)}"
        if extra:
            msg += f"；多出或种类不对：{', '.join(extra)}"
        if dup:
            msg += f"；重复：{', '.join(dup)}"
        out.append(_issue(pf, "sections", "block", msg, missing=missing, extra=extra))
    for s in page.sections:
        spec = next((x for x in specs if x.id == s.id), None)
        if spec and s.content.split("\n", 1)[0] != f"## {spec.title}":
            out.append(_issue(pf, "sections", "block", f"段落 {s.id} 的第一行应为“## {spec.title}”", s.line + 1, s.id))
    return out


def check_template(pf: PageFile, ctx: LintContext) -> list[LintIssue]:
    """锁定段与渲染段必须与地图快照渲染出的模板逐字一致（KGE 第四章“模板违规”）：人手改动这两类段落即阻断"""
    page, snap = pf.page, ctx.snapshot
    assert page is not None
    node_id = str(page.header.get("node_id"))
    if snap is None or snap.node(node_id) is None or page.header.get("page_type") not in SECTIONS_BY_TYPE:
        return []
    expected, _ = render_sections(snap, node_id, ctx.page_ids)
    out = []
    if page.title != snap.node(node_id)["name"]:  # type: ignore[index]
        out.append(_issue(pf, "template", "block", "一级标题应为节点名称"))
    for sid, sec in expected.items():
        cur = page.section(sid)
        if cur is not None and cur.content != sec.content:
            out.append(_issue(pf, "template", "block", f"{'锁定段' if sec.kind == 'locked' else '渲染段'} {sid} 与地图快照渲染的模板不一致"
                              "（这两类段落只能由编译器从原子生成）", cur.line + 1, sid))
    return out


def check_locked_baseline(pf: PageFile, ctx: LintContext) -> list[LintIssue]:
    """锁定段与上一审核版一致（重编不可改写）；只有它所引原子的哈希变了（原子本身变更）才允许随之变化"""
    page = pf.page
    assert page is not None
    base = ctx.baseline.get(page.page_id)
    specs = SECTIONS_BY_TYPE.get(str(page.header.get("page_type")))
    if base is None or specs is None:
        return []
    old_hash = {r.key: r.hash for r in base.atom_refs}
    new_hash = {r.key: r.hash for r in page.atom_refs}
    out = []
    for spec in specs:
        if spec.kind != "locked":
            continue
        cur, old = page.section(spec.id), base.section(spec.id)
        if cur is None or old is None or cur.content == old.content:
            continue
        keys = _cited_keys(cur.content) | _cited_keys(old.content)
        if not any(old_hash.get(k) != new_hash.get(k) for k in keys):
            out.append(_issue(pf, "locked_baseline", "block", f"锁定段 {spec.id} 与上一审核版不一致，而它引用的原子没有变化（重编不可改写锁定段）",
                              cur.line + 1, spec.id))
    return out


# ---------- 引用 ----------

def check_links(pf: PageFile, ctx: LintContext) -> list[LintIssue]:
    page = pf.page
    assert page is not None
    refs = {r.key for r in page.atom_refs}
    out = []
    for lineno, sid, ln in body_lines(page):
        for m in WIKILINK.finditer(ln):
            if m.group(1) not in ctx.page_ids:
                out.append(_issue(pf, "link", "block", f"失效链接：页面 {m.group(1)} 不存在", lineno, sid))
        for m in CITATION.finditer(ln):
            key = f"{m.group(1)}:{m.group(2)}"
            if m.group(1) not in ATOM_TYPES:
                out.append(_issue(pf, "link", "block", f"引用的原子类型不合法：{key}", lineno, sid))
            elif key not in refs:
                out.append(_issue(pf, "link", "block", f"引用 {key} 没有登记在头部 atom_refs", lineno, sid))
            elif ctx.snapshot is not None and ctx.snapshot.get(m.group(1), m.group(2)) is None:
                out.append(_issue(pf, "link", "block", f"失效引用：原子 {key} 不在地图快照里", lineno, sid))
        for m in QUOTE_SLOT.finditer(ln):
            if f"projection:{m.group(1)}" not in refs:
                out.append(_issue(pf, "link", "block", f"教材原句占位引用的投影 {m.group(1)} 没有登记在 atom_refs", lineno, sid))
        for m in _MD_LINK.finditer(ln):
            target = m.group(1)
            if re.match(r"^[a-z]+:", target):
                out.append(_issue(pf, "link", "warn", f"页面里有外部链接：{target[:60]}", lineno, sid))
            elif not target.startswith("#"):
                out.append(_issue(pf, "link", "block", f"用 [[页面 ID]] 链接其他页面，不用相对路径：{target[:60]}", lineno, sid))
    return out


def check_orphans(ctx: LintContext) -> list[LintIssue]:
    """孤立页：没有被任何现行节点挂载（节点已撤下、节点类型挂不了该类页面）、同一节点挂了两个页面"""
    out: list[LintIssue] = []
    by_node: dict[str, list[PageFile]] = {}
    for pf in ctx.files:
        if pf.page is None:
            continue
        nid = str(pf.page.header.get("node_id"))
        by_node.setdefault(nid, []).append(pf)
        node = ctx.snapshot.node(nid) if ctx.snapshot else None
        if node is None:
            continue
        if node.get("status", "active") != "active":
            out.append(_issue(pf, "orphan", "block", f"孤立页：节点 {nid} 已撤下（deprecated）"))
        elif pf.page.header.get("page_type") == "kp" and (node.get("kind") not in KP_NODE_KINDS or node.get("level") not in KP_NODE_LEVELS):
            out.append(_issue(pf, "orphan", "block", f"孤立页：节点 {nid} 的类型（{node.get('level')}/{node.get('kind')}）挂不了知识点页"))
    for nid, pfs in by_node.items():
        if len(pfs) > 1:
            for pf in pfs:
                out.append(_issue(pf, "orphan", "block", f"节点 {nid} 挂了 {len(pfs)} 个页面：{', '.join(p.rel for p in pfs)}"))
    return out


def check_citations(pf: PageFile) -> list[LintIssue]:
    """缺引用：事实性段落每个自然段至少一个原子引用（KGE 第九章“引用完整率 100%”）。标题行与独立公式块不算自然段"""
    page = pf.page
    assert page is not None
    out = []
    for s in page.sections:
        for rel, para in paragraphs(s.content):
            if not CITATION.search(para):
                out.append(_issue(pf, "citation", "block", f"自然段缺原子引用：{para.strip()[:30]}", s.line + 1 + rel, s.id))
    return out


def check_atom_hash(pf: PageFile, ctx: LintContext) -> list[LintIssue]:
    page, snap = pf.page, ctx.snapshot
    assert page is not None
    if snap is None:
        return []
    out = []
    for r in page.atom_refs:
        atom = snap.get(r.type, r.id)
        if atom is None:
            out.append(_issue(pf, "atom_hash", "block", f"引用的原子 {r.key} 已不在地图快照里", 2, atom=r.key))
        elif atom.hash != r.hash:
            out.append(_issue(pf, "atom_hash", "block", f"原子 {r.key} 的哈希已过期（页面 {r.hash}，快照 {atom.hash}），需要重编",
                              2, atom=r.key, page_hash=r.hash, snapshot_hash=atom.hash))
    return out


def check_expiry(pf: PageFile, ctx: LintContext) -> list[LintIssue]:
    """带有效期的原子（时政）过期即阻断：页面引用的，以及挂在本节点上的"""
    page, snap = pf.page, ctx.snapshot
    assert page is not None
    if snap is None:
        return []
    atoms = {a.key: a for a in snap.affairs_of(str(page.header.get("node_id")))}
    for r in page.atom_refs:
        if r.type == "affair" and (a := snap.get("affair", r.id)) is not None:
            atoms[a.key] = a
    out = []
    for key, a in sorted(atoms.items()):
        until = dt.date.fromisoformat(str(a.record["valid_until"]))
        if until < ctx.today or a.record.get("review_status") == "retired":
            out.append(_issue(pf, "atom_expired", "block", f"带有效期的原子 {key} 已过期（失效日 {until.isoformat()}）或已下架", 2, atom=key))
    return out


# ---------- 公式 ----------

def check_formulas(pf: PageFile) -> list[LintIssue]:
    page = pf.page
    assert page is not None
    out = []
    for s in page.sections:
        formulas, problems = mathcheck.extract(s.content)
        for rel, msg in problems:
            out.append(_issue(pf, "latex", "warn", msg, s.line + 1 + rel, s.id))
        for f in formulas:
            line = s.line + 1 + f.line
            syntax = mathcheck.syntax_problems(f.source)
            if f.check is None:
                if syntax:
                    out.append(_issue(pf, "latex", "warn", f"公式无法解析：{'；'.join(syntax)}", line, s.id, formula=f.source))
                continue
            if syntax:
                out.append(_issue(pf, "sympy", "block", f"标了 % check 的式子无法解析：{'；'.join(syntax)}", line, s.id, formula=f.source))
                continue
            res = mathcheck.verify_chain(f.source, f.check or None)
            if not res.ok:
                out.append(_issue(pf, "sympy", "block", f"SymPy 验算不通过：{res.message}", line, s.id, formula=f.source,
                                  verifiable=res.verifiable))
    return out


# ---------- 规则 13 ----------

def check_pii(pf: PageFile) -> list[LintIssue]:
    page = pf.page
    assert page is not None
    out = []
    for lineno, sid, ln in body_lines(page):
        text = _strip_refs(ln)
        for label, pat in PII_PATTERNS:
            if pat.search(text):
                # 定位里不回显命中的原文（可能就是个人信息）
                out.append(_issue(pf, "pii", "block", f"疑似个人信息或学生标识：{label}", lineno, sid, pattern=label))
    return out


def check_group_stats(pf: PageFile, ctx: LintContext) -> list[LintIssue]:
    page = pf.page
    assert page is not None
    out = []
    for s in page.sections:
        for rel, para in paragraphs(s.content):
            text = _strip_refs(para)
            if not (_STAT.search(text) and _GROUP.search(text)):
                continue
            ns = [int(next(g for g in m.groups() if g)) for m in _N.finditer(text)]
            if not ns:
                out.append(_issue(pf, "group_stats", "block", "引用群体统计必须写明人数（如 n=120）", s.line + 1 + rel, s.id))
            elif min(ns) < ctx.min_group:
                out.append(_issue(pf, "group_stats", "block", f"群体统计的人数 {min(ns)} 低于门槛 {ctx.min_group}", s.line + 1 + rel, s.id,
                                  n=min(ns), threshold=ctx.min_group))
    return out


# ---------- 人设中立与适龄 ----------

def check_persona(pf: PageFile, lex: Lexicon) -> list[LintIssue]:
    page = pf.page
    assert page is not None
    subject = str(page.header.get("subject"))
    out = []
    literal = [("角色名", w) for w in lex.role_names] + [("称呼", w) for w in lex.address_terms] + [("口头禅或固定句式", w) for w in lex.catchphrases]
    for lineno, sid, ln in body_lines(page):
        text = _strip_refs(ln)
        for label, word in literal:
            if word in text:
                out.append(_issue(pf, "persona", "block", f"页面须人设中立，出现了{label}“{word}”", lineno, sid, term=word))
        for rule in lex.setting_patterns:
            if re.search(rule.regex, text):
                out.append(_issue(pf, "persona", "block", f"页面须人设中立，出现了角色设定元素（{rule.id}）", lineno, sid, rule=rule.id))
        for rule in lex.forbidden:
            if rule.applies(subject) and re.search(rule.regex, text):
                out.append(_issue(pf, "forbidden_phrase", "block", f"禁语 {rule.cls} 类（{rule.id}）", lineno, sid, rule=rule.id,
                                  source=rule.source))
    return out


def check_sentence_length(pf: PageFile, lex: Lexicon) -> list[LintIssue]:
    page = pf.page
    assert page is not None
    out = []
    for s in page.sections:
        if s.kind != "narrative":
            continue
        for rel, para in paragraphs(s.content):
            for k, ln in enumerate(para.split("\n")):
                text = re.sub(r"\$[^$]*\$", "式", _strip_refs(ln))
                text = re.sub(r"^\s*(?:[-*+]|\d+[.、])\s*", "", text)
                for sent in _SENTENCE_SPLIT.split(text):
                    n = len(re.sub(r"\s+", "", sent))
                    if n > lex.max_sentence_chars:
                        out.append(_issue(pf, "sentence_length", "block", f"句子 {n} 字，超过上限 {lex.max_sentence_chars} 字",
                                          s.line + 1 + rel + k, s.id, chars=n))
    return out


def check_textbook_quote(pf: PageFile, ctx: LintContext) -> list[LintIssue]:
    """教材原句不写进页面（KGE V1.1 修订 3）：页面正文不得含任何投影原句中连续 12 字以上的片段"""
    page, snap = pf.page, ctx.snapshot
    assert page is not None
    if snap is None:
        return []
    window = 12
    out = []
    lines = list(body_lines(page))
    for pid, quote in snap.quote_texts():
        q = re.sub(r"\s+", "", quote)
        pieces = {q[i:i + window] for i in range(max(1, len(q) - window + 1))} if len(q) >= window else {q}
        for lineno, sid, ln in lines:
            flat = re.sub(r"\s+", "", _strip_refs(ln))
            if len(q) >= 6 and any(p in flat for p in pieces):
                out.append(_issue(pf, "textbook_quote", "block", f"页面里写进了教材原句（投影 {pid}），只能放占位 {{{{quote:{pid}}}}}",
                                  lineno, sid, projection=pid))
                break
    return out


def load_files(pages_dir: Path) -> list[PageFile]:
    from ..pages.format import parse
    files = []
    for p in sorted(pages_dir.rglob("*.md")):
        if p.name.lower() == "readme.md":
            continue
        rel = p.relative_to(pages_dir).as_posix()
        text = p.read_text(encoding="utf-8")
        try:
            files.append(PageFile(rel, text, parse(text)))
        except PageFormatError as e:
            files.append(PageFile(rel, text, None, e))
    return files
