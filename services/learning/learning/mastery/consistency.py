"""学情档案页的陈述—判断一致性检查：确定性规则引擎（spec 第 14 节；KGE 第八章质量门槛，通过率要求 100%）。

不调模型（待人确认 #19 默认）；程度词表、结论词表、衔接短语表、人设词表都是配置位，现为合成占位，
待数学教研与 SIE 负责人起草、产品负责人审核（待人确认 #20）。将来增加模型辅助检查只能追加阻断条件。

逐条检查，任何一条不过就阻断：
  0. connective_unapproved   衔接语不在人审过的固定衔接短语表里
  1. no_claim / claim_unreadable / claim_status
                             结论句必须恰好引用一条可读的判断，且只能是“成立”或“假设”
  2. degree_escalation       衔接语里的程度词比同组所引判断的模板程度更重（“尚不稳定”→“薄弱”）
  3. new_conclusion          衔接语里的结论性表述（程度词、掌握词、评价词）同组没有对应判断
  4. number_in_connective    衔接语不得包含数字
  5. forbidden_inference     全文命中禁用推断词表（docs/design/sie/sie_lexicon.py）
  6. persona_language        衔接语出现人设名或口头禅
阻断后不发布衔接语：页面降级为纯模板版本（check_status = blocked_fallback），并返回阻断记录。
"""
from __future__ import annotations

import importlib.util
import json
import re
import sys
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from .config import TEST_ENV, ConfigError

SCHEMA_VERSION = "kge-profile-check/0.1"
VISIBLE_STATUSES = ("established", "hypothesis")  # 规则 11：只有“成立”“假设”能作陈述
_INVISIBLE = re.compile(r"[\s​-‏⁠﻿]+")
_DIGITS = re.compile(r"[0-9%]|[零〇一二两三四五六七八九十百千万]+\s*(个|道|次|题|分|成|倍|天|周|%)")

Forbidden = Callable[[str], Sequence[str]]


def normalize(text: str) -> str:
    return _INVISIBLE.sub("", unicodedata.normalize("NFKC", text))


@dataclass(frozen=True)
class CheckConfig:
    degree_words: Mapping[str, int]
    template_degrees: Mapping[str, int]
    conclusion_words: tuple[str, ...]
    template_conclusions: Mapping[str, tuple[str, ...]]
    connective_phrases: frozenset[str]
    persona_terms: tuple[str, ...]


def parse_check_config(doc: Mapping[str, Any], *, env: str) -> CheckConfig:
    keys = {"$comment", "schema_version", "synthetic", "signed_by", "signed_at", "degree_words", "template_degrees",
            "conclusion_words", "template_conclusions", "connective_phrases", "persona_terms"}
    if set(doc) - keys or (keys - {"$comment"}) - set(doc):
        raise ConfigError("档案页检查配置的配置位不对")
    if doc["schema_version"] != SCHEMA_VERSION:
        raise ConfigError(f"schema_version 必须是 {SCHEMA_VERSION}")
    signed = bool(str(doc["signed_by"]).strip() and str(doc["signed_at"]).strip())
    if env != TEST_ENV and (doc["synthetic"] or not signed):
        raise ConfigError("未签字或合成的档案页检查词表只允许 LEARNING_ENV=test 加载（待人确认 #20）")
    deg = doc["degree_words"]
    if not all(isinstance(k, str) and k and isinstance(v, int) and not isinstance(v, bool) and v >= 1 for k, v in deg.items()):
        raise ConfigError("degree_words：程度词 → 正整数程度（越大越重）")
    tdeg = doc["template_degrees"]
    if not all(isinstance(v, int) and not isinstance(v, bool) and v >= 0 for v in tdeg.values()):
        raise ConfigError("template_degrees：模板 → 非负整数程度")
    for name in ("conclusion_words", "connective_phrases", "persona_terms"):
        if not (isinstance(doc[name], list) and all(isinstance(x, str) and x.strip() for x in doc[name])):
            raise ConfigError(f"{name} 必须是非空字符串列表")
    tcon = doc["template_conclusions"]
    if not all(isinstance(v, list) and set(v) <= set(doc["conclusion_words"]) for v in tcon.values()):
        raise ConfigError("template_conclusions 只能引用 conclusion_words 里的词")
    for phrase in doc["connective_phrases"]:
        p = normalize(phrase)
        if any(normalize(w) in p for w in list(deg) + doc["conclusion_words"] + doc["persona_terms"]) or _DIGITS.search(p):
            raise ConfigError("固定衔接短语不能含程度词、结论词、人设词或数字")
    return CheckConfig(
        degree_words={normalize(k): v for k, v in deg.items()},
        template_degrees=dict(tdeg),
        conclusion_words=tuple(normalize(w) for w in doc["conclusion_words"]),
        template_conclusions={k: tuple(normalize(w) for w in v) for k, v in tcon.items()},
        connective_phrases=frozenset(normalize(p) for p in doc["connective_phrases"]),
        persona_terms=tuple(normalize(w) for w in doc["persona_terms"]),
    )


def load_check_config(path: str | Path, *, env: str) -> CheckConfig:
    with open(path, encoding="utf-8") as f:
        return parse_check_config(json.load(f), env=env)


def default_forbidden_matcher() -> Forbidden:
    """复用 SIE 的禁用推断匹配函数（同一个词表、同一套规范化），不另写一套。"""
    here = Path(__file__).resolve()
    for parent in here.parents:
        cand = parent / "docs" / "design" / "sie" / "sie_lexicon.py"
        if cand.exists():
            spec = importlib.util.spec_from_file_location("sie_lexicon", cand)
            assert spec and spec.loader
            mod = sys.modules.get("sie_lexicon")
            if mod is None:
                mod = importlib.util.module_from_spec(spec)
                sys.modules["sie_lexicon"] = mod
                spec.loader.exec_module(mod)
            lex = mod.load()
            return lambda text: [h.category for h in lex.find(text)]
    raise ConfigError("找不到 docs/design/sie/sie_lexicon.py：禁用推断检查不能缺席")


@dataclass(frozen=True)
class ClaimView:
    """编译时读到的判断（insight.claim 的 agent 可见视图）。"""
    claim_id: str
    status: str
    template_key: str
    readable: bool = True


@dataclass(frozen=True)
class Statement:
    claim_id: str | None
    text: str  # 由判断模板渲染（RenderedStatement 同一套规则），不落库


@dataclass(frozen=True)
class Section:
    key: str
    connective: str
    statements: tuple[Statement, ...] = ()


@dataclass(frozen=True)
class Violation:
    code: str
    section: str
    claim_ids: tuple[str, ...]
    fragment: str


@dataclass(frozen=True)
class CheckResult:
    violations: tuple[Violation, ...] = ()

    @property
    def passed(self) -> bool:
        return not self.violations

    @property
    def codes(self) -> set[str]:
        return {v.code for v in self.violations}


def _scan(text: str, words: Sequence[str]) -> tuple[list[str], str]:
    """最长优先找出词表里的词，并把命中处遮掉（避免“尚不稳定”里的“稳定”再被当成结论词）。"""
    hits: list[str] = []
    for w in sorted(set(words), key=lambda x: (-len(x), x)):
        if w and w in text:
            hits.append(w)
            text = text.replace(w, "\u0000" * len(w))
    return hits, text


def check_page(sections: Sequence[Section], claims: Mapping[str, ClaimView], cfg: CheckConfig,
               forbidden: Forbidden) -> CheckResult:
    out: list[Violation] = []
    full_text: list[str] = []
    for sec in sections:
        valid: list[ClaimView] = []
        for st in sec.statements:
            full_text.append(st.text)
            if not st.claim_id:
                out.append(Violation("no_claim", sec.key, (), ""))
                continue
            cv = claims.get(st.claim_id)
            if cv is None or not cv.readable:
                out.append(Violation("claim_unreadable", sec.key, (st.claim_id,), ""))
                continue
            if cv.status not in VISIBLE_STATUSES:
                out.append(Violation("claim_status", sec.key, (st.claim_id,), ""))
                continue
            valid.append(cv)
        ids = tuple(c.claim_id for c in valid)
        conn = normalize(sec.connective)
        full_text.append(sec.connective)
        if not conn:
            continue
        if conn not in cfg.connective_phrases:
            out.append(Violation("connective_unapproved", sec.key, ids, sec.connective))
        degree_hits, masked = _scan(conn, list(cfg.degree_words))
        group_degree = max((cfg.template_degrees.get(c.template_key, 0) for c in valid), default=None)
        for w in degree_hits:
            if group_degree is None:
                out.append(Violation("new_conclusion", sec.key, ids, w))
            elif cfg.degree_words[w] > group_degree:
                out.append(Violation("degree_escalation", sec.key, ids, w))
        allowed = {w for c in valid for w in cfg.template_conclusions.get(c.template_key, ())}
        concl_hits, _ = _scan(masked, cfg.conclusion_words)
        for w in concl_hits:
            if w not in allowed:
                out.append(Violation("new_conclusion", sec.key, ids, w))
        if _DIGITS.search(conn):
            out.append(Violation("number_in_connective", sec.key, ids, sec.connective))
        for term in cfg.persona_terms:
            if term in conn:
                out.append(Violation("persona_language", sec.key, ids, term))
    hits = forbidden("\n".join(full_text))
    if hits:
        out.append(Violation("forbidden_inference", "*", (), ",".join(sorted(set(hits)))))
    return CheckResult(tuple(out))


@dataclass(frozen=True)
class CompiledPage:
    sections: tuple[Section, ...]
    check_status: str  # passed / blocked_fallback
    violations: tuple[Violation, ...] = field(default_factory=tuple)


def finalize(sections: Sequence[Section], claims: Mapping[str, ClaimView], cfg: CheckConfig,
             forbidden: Forbidden) -> CompiledPage:
    """检查通过就原样发布；否则降级为纯模板版本：去掉全部衔接语，只留引用了可见判断的陈述，并返回阻断记录。"""
    res = check_page(sections, claims, cfg, forbidden)
    if res.passed:
        return CompiledPage(tuple(sections), "passed")
    fallback = tuple(
        Section(s.key, "", tuple(st for st in s.statements if st.claim_id and st.claim_id in claims
                                 and claims[st.claim_id].readable and claims[st.claim_id].status in VISIBLE_STATUSES))
        for s in sections)
    return CompiledPage(fallback, "blocked_fallback", res.violations)
