"""语义体检的输入词表（KGE-11 范围 1）。三种文件，全部是 JSON，格式不对一律拒收（fail closed）：

- 超纲词表 kge-semantic-scope/1：按学科列“监测术语”及其所属学段；学段不在该学科适用学段里的术语出现在页面即超纲，
  个别节点可在 node_allow 里放行（如拓展节点）。
- 适龄文风词表 kge-semantic-style/1：逐条规则（字面或正则），命中即按分级配置报阻断或提示。
- 术语表 kge-semantic-terms/1：节点别名（相邻页同一术语写法一致性用；节点名称取自地图快照）与版次专属表述（版本差异误判用；
  教材投影的册名、章名已自动算作所属版次的专属表述）。

仓库里只有合成样例（services/knowledge/tests/fixtures/semantic/），正式词表由教研提供（spec 待人确认 #2）。
词表内容（术语、规则说明）会出现在体检消息里；页面原文不回显（规则 13）。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

from ..lint.binding import sha256_json
from ..nodeid import SUBJECT_PREFIX, is_node_id

SCOPE_FORMAT = "kge-semantic-scope/1"
STYLE_FORMAT = "kge-semantic-style/1"
TERMS_FORMAT = "kge-semantic-terms/1"
MAX_INPUT_BYTES = 1024 * 1024
MAX_TERM_CHARS = 32
MAX_PATTERN_CHARS = 200
_STAGE = re.compile(r"^[a-z0-9_]{1,32}$")
_RULE_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_EDITION = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
RESERVED_STYLE_IDS = ("default", "no_lexicon")  # 分级配置里 age_style.default / age_style.no_lexicon 已有含义


class InputError(ValueError):
    pass


def _read(path: str | Path, fmt: str, keys: set[str]) -> dict[str, Any]:
    data = Path(path).read_bytes()
    if len(data) > MAX_INPUT_BYTES:
        raise InputError(f"{fmt} 文件超过 1 MB")
    raw = json.loads(data.decode("utf-8"))
    if not isinstance(raw, dict) or raw.get("format") != fmt:
        raise InputError(f"文件格式必须是 {fmt}")
    unknown = sorted(set(raw) - keys - {"format", "synthetic", "note"})
    if unknown:
        raise InputError(f"{fmt} 有未登记的字段：{unknown}")
    if not isinstance(raw.get("synthetic"), bool):
        raise InputError(f"{fmt} 必须用 synthetic（true / false）标明是否合成样例")
    return raw


def _term(value: Any, where: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > MAX_TERM_CHARS or re.search(r"\s", value):
        raise InputError(f"{where}：术语必须是 1–{MAX_TERM_CHARS} 字、不含空白的字符串")
    return value


# ---------- 超纲词表 ----------

@dataclass(frozen=True)
class SubjectScope:
    stages: frozenset[str]
    terms: Mapping[str, str]  # 术语 → 所属学段
    node_allow: Mapping[str, frozenset[str]]

    def beyond(self, node_id: str) -> list[tuple[str, str]]:
        """（术语，学段）：该节点页面里不该出现的术语，按术语排序"""
        allow = self.node_allow.get(node_id, frozenset())
        return sorted((t, s) for t, s in self.terms.items() if s not in self.stages and t not in allow)


@dataclass(frozen=True)
class ScopeVocab:
    subjects: Mapping[str, SubjectScope]
    synthetic: bool = True
    digest: str | None = None  # 输入文件内容的摘要（报告 inputs 里核对用）


def parse_scope(raw: dict[str, Any]) -> ScopeVocab:
    subjects = raw.get("subjects")
    if not isinstance(subjects, dict) or not subjects:
        raise InputError("超纲词表的 subjects 必须是非空对象")
    out: dict[str, SubjectScope] = {}
    for subj, body in subjects.items():
        if subj not in SUBJECT_PREFIX:
            raise InputError("超纲词表的学科只收平台学科编码")
        if not isinstance(body, dict) or set(body) - {"stages", "terms", "node_allow"}:
            raise InputError(f"超纲词表 {subj}：只收 stages、terms、node_allow")
        stages = body.get("stages")
        if not isinstance(stages, list) or not stages or any(not isinstance(s, str) or not _STAGE.match(s) for s in stages):
            raise InputError(f"超纲词表 {subj}：stages 必须是非空的学段编码数组")
        terms: dict[str, str] = {}
        for i, t in enumerate(body.get("terms") or []):
            if not isinstance(t, dict) or set(t) != {"term", "stage"}:
                raise InputError(f"超纲词表 {subj}.terms[{i}]：只收 term、stage")
            term = _term(t["term"], f"超纲词表 {subj}.terms[{i}]")
            if not isinstance(t["stage"], str) or not _STAGE.match(t["stage"]):
                raise InputError(f"超纲词表 {subj}.terms[{i}]：stage 不合规范")
            if term in terms:
                raise InputError(f"超纲词表 {subj}：术语 {term} 重复")
            terms[term] = t["stage"]
        allow: dict[str, frozenset[str]] = {}
        node_allow = body.get("node_allow") or {}
        if not isinstance(node_allow, dict):
            raise InputError(f"超纲词表 {subj}：node_allow 必须是对象")
        for nid, ts in node_allow.items():
            if not is_node_id(nid) or not isinstance(ts, list) or any(t not in terms for t in ts):
                raise InputError(f"超纲词表 {subj}：node_allow 的键必须是节点 ID、值只能是已登记的术语")
            allow[nid] = frozenset(ts)
        out[subj] = SubjectScope(frozenset(stages), MappingProxyType(terms), MappingProxyType(allow))
    return ScopeVocab(MappingProxyType(out), raw.get("synthetic") is not False, sha256_json(raw))


def load_scope(path: str | Path) -> ScopeVocab:
    return parse_scope(_read(path, SCOPE_FORMAT, {"subjects"}))


# ---------- 适龄文风词表 ----------

@dataclass(frozen=True)
class StyleRule:
    id: str
    kind: str  # literal | regex
    pattern: str
    message: str

    def search(self, text: str) -> bool:
        return (self.pattern in text) if self.kind == "literal" else re.search(self.pattern, text) is not None


@dataclass(frozen=True)
class StyleLexicon:
    rules: tuple[StyleRule, ...]
    synthetic: bool = True
    digest: str | None = None

    @property
    def ids(self) -> frozenset[str]:
        return frozenset(r.id for r in self.rules)


def parse_style(raw: dict[str, Any]) -> StyleLexicon:
    rules = raw.get("rules")
    if not isinstance(rules, list) or not rules:
        raise InputError("文风词表的 rules 必须是非空数组")
    out: list[StyleRule] = []
    seen: set[str] = set()
    for i, r in enumerate(rules):
        if not isinstance(r, dict) or set(r) != {"id", "kind", "pattern", "message"}:
            raise InputError(f"文风词表 rules[{i}]：只收 id、kind、pattern、message")
        if not isinstance(r["id"], str) or not _RULE_ID.match(r["id"]) or r["id"] in seen or r["id"] in RESERVED_STYLE_IDS:
            raise InputError(f"文风词表 rules[{i}]：id 不合规范、重复或用了保留名（default、no_lexicon）")
        seen.add(r["id"])
        if r["kind"] not in ("literal", "regex"):
            raise InputError(f"文风词表 rules[{i}]：kind 只能是 literal / regex")
        if not isinstance(r["pattern"], str) or not r["pattern"] or len(r["pattern"]) > MAX_PATTERN_CHARS:
            raise InputError(f"文风词表 rules[{i}]：pattern 必须是 1–{MAX_PATTERN_CHARS} 字符")
        if r["kind"] == "regex":
            try:
                compiled = re.compile(r["pattern"])
            except re.error as e:
                raise InputError(f"文风词表 rules[{i}]：正则不合法") from e
            if compiled.search(""):
                raise InputError(f"文风词表 rules[{i}]：正则能匹配空串")
        if not isinstance(r["message"], str) or not r["message"].strip() or len(r["message"]) > 200:
            raise InputError(f"文风词表 rules[{i}]：message 必须是 1–200 字的说明")
        out.append(StyleRule(r["id"], r["kind"], r["pattern"], r["message"]))
    return StyleLexicon(tuple(out), raw.get("synthetic") is not False, sha256_json(raw))


def load_style(path: str | Path) -> StyleLexicon:
    return parse_style(_read(path, STYLE_FORMAT, {"rules"}))


# ---------- 术语表：别名与版次专属表述 ----------

@dataclass(frozen=True)
class TermTable:
    aliases: Mapping[str, tuple[str, ...]] = field(default_factory=lambda: MappingProxyType({}))
    edition_terms: Mapping[str, tuple[str, ...]] = field(default_factory=lambda: MappingProxyType({}))
    synthetic: bool = True
    digest: str | None = None


EMPTY_TERMS = TermTable(synthetic=False)  # 没给术语表：没有别名与版次专属表述（不是合成样例，也不算跳过检查）


def parse_terms(raw: dict[str, Any]) -> TermTable:
    if not isinstance(raw.get("aliases") or {}, dict) or not isinstance(raw.get("edition_terms") or {}, dict):
        raise InputError("术语表 aliases、edition_terms 必须是对象")
    aliases: dict[str, tuple[str, ...]] = {}
    seen: set[str] = set()
    for nid, xs in (raw.get("aliases") or {}).items():
        if not is_node_id(nid) or not isinstance(xs, list) or not xs:
            raise InputError("术语表 aliases：键必须是节点 ID，值是非空数组")
        for k, a in enumerate(xs):
            a = _term(a, f"术语表 aliases.{nid}[{k}]")
            if a in seen:
                raise InputError(f"术语表 aliases：别名 {a} 重复（一个写法只能属于一个节点）")
            seen.add(a)
        aliases[nid] = tuple(xs)
    editions: dict[str, tuple[str, ...]] = {}
    owner: dict[str, str] = {}
    for ed, xs in (raw.get("edition_terms") or {}).items():
        if not isinstance(ed, str) or not _EDITION.match(ed) or not isinstance(xs, list) or not xs:
            raise InputError("术语表 edition_terms：键必须是版次编码，值是非空数组")
        for k, t in enumerate(xs):
            t = _term(t, f"术语表 edition_terms.{ed}[{k}]")
            if t in owner:
                raise InputError(f"术语表 edition_terms：{t} 同时登记在两个版次下（不是版次专属表述）")
            owner[t] = ed
        editions[ed] = tuple(xs)
    return TermTable(MappingProxyType(aliases), MappingProxyType(editions), raw.get("synthetic") is not False, sha256_json(raw))


def load_terms(path: str | Path) -> TermTable:
    return parse_terms(_read(path, TERMS_FORMAT, {"aliases", "edition_terms"}))
