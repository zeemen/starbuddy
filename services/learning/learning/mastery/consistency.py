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
  引语（specs/quote-verbatim-guard，OpenMAIC 借鉴第 3 点；只追加阻断，不放宽上面任何一条）：
  7. unmarked_quote          衔接语的自由文字部分出现引号类字符：引语只能结构化标出（Section.quotes + 占位 {q:<key>}）
  8. quote_placeholder       占位与 quotes 对不上（未声明、未使用、重复、key 格式不对）
  9. quote_too_long / too_many_quotes
                             一段引语规范化后为空或超过 QUOTE_MAX_CHARS 个码点；一节超过 QUOTES_PER_SECTION 段
  10. quote_not_cited        引语的判断不是本节所引的可见判断，或证据不在那条判断的 evidence_ids 里
  11. quote_unverified       原文核对没有给出 ok（没有核对结果也算：fail closed）。核对由学习服务查证据原文完成
                             （learning.mastery.quotes），本引擎不读原文
  12. quote_audience         调用方没有声明“受众只有学生本人”（quotes_allowed=False，缺省）却出现引语或占位
  占位以外的衔接语照常跑第 2–6 条；第 0 条按含占位的整句比对；禁用推断查全文，引语文字一起查。
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

# ---- 引语（specs/quote-verbatim-guard 第二节；待人确认 #1–#5 按默认）
QUOTE_MAX_CHARS = 30          # 一段引语规范化后的码点上限（待人确认 #4，数字待 SIE 负责人与教研定）
QUOTES_PER_SECTION = 1
QUOTE_MARKS = frozenset("\"'“”‘’「」『』＂＇﹁﹂﹃﹄〝〞〟‟„‚‛‹›«»")
QUOTE_KEY_RE = re.compile(r"^[a-z][a-z_]{0,15}$")   # 不含数字：占位不该触发“衔接语无数字”
_PLACEHOLDER = re.compile(r"\{q:([^{}]*)\}")
QUOTE_OK = "ok"


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
        p = normalize(_PLACEHOLDER.sub("", phrase))
        if any(normalize(w) in p for w in list(deg) + doc["conclusion_words"] + doc["persona_terms"]) or _DIGITS.search(p):
            raise ConfigError("固定衔接短语不能含程度词、结论词、人设词或数字")
        if any(ch in QUOTE_MARKS for ch in _PLACEHOLDER.sub("", phrase)):
            raise ConfigError("固定衔接短语不能含引号：引语只能经占位 {q:<key>} 结构化标出")
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
    evidence_ids: tuple[str, ...] = ()   # 判断的依据（insight.claim.evidence_ids）；引语只能出自这里


@dataclass(frozen=True)
class Statement:
    claim_id: str | None
    text: str  # 由判断模板渲染（RenderedStatement 同一套规则），不落库


@dataclass(frozen=True)
class Quote:
    """衔接语里 {q:<key>} 处的一段学生原话：出自所引判断 claim_id 的证据 evidence_id，文字须逐字是该证据原文的连续子串。
    引号由显示程序加，text 本身不带引号。"""
    key: str
    claim_id: str
    evidence_id: str
    text: str


@dataclass(frozen=True)
class Section:
    key: str
    connective: str
    statements: tuple[Statement, ...] = ()
    quotes: tuple[Quote, ...] = ()


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


QuoteVerdicts = Mapping[tuple[str, str], str]   # (节, 引语 key) → "ok" 或原因编码（learning.mastery.quotes）


def quote_requests(sections: Sequence[Section]) -> list[tuple[str, Quote]]:
    """要交给原文核对的引语（节, 引语）；核对结果按 (节, key) 传回 check_page 的 quote_verdicts"""
    return [(sec.key, q) for sec in sections for q in sec.quotes]


def _check_quotes(sec: Section, raw_free: str, valid: Sequence[ClaimView], *, quotes_allowed: bool,
                  verdicts: QuoteVerdicts) -> list[Violation]:
    ids = tuple(c.claim_id for c in valid)
    out: list[Violation] = []
    if any(ch in QUOTE_MARKS for ch in raw_free) or any(ch in QUOTE_MARKS for ch in unicodedata.normalize("NFKC", raw_free)):
        out.append(Violation("unmarked_quote", sec.key, ids, ""))
    used = _PLACEHOLDER.findall(sec.connective)
    if not used and not sec.quotes:
        return out
    if not quotes_allowed:
        out.append(Violation("quote_audience", sec.key, ids, ""))
    declared = [q.key for q in sec.quotes]
    if (len(set(used)) != len(used) or len(set(declared)) != len(declared) or set(used) != set(declared)
            or not all(QUOTE_KEY_RE.fullmatch(k) for k in used + declared)):
        out.append(Violation("quote_placeholder", sec.key, ids, ""))
    if len(sec.quotes) > QUOTES_PER_SECTION:
        out.append(Violation("too_many_quotes", sec.key, ids, ""))
    by_id = {c.claim_id: c for c in valid}
    for q in sec.quotes:
        if not 1 <= len(normalize(q.text)) <= QUOTE_MAX_CHARS:
            out.append(Violation("quote_too_long", sec.key, ids, ""))
        cv = by_id.get(q.claim_id)
        if cv is None or q.evidence_id not in cv.evidence_ids:
            out.append(Violation("quote_not_cited", sec.key, ids, ""))
        if verdicts.get((sec.key, q.key)) != QUOTE_OK:
            out.append(Violation("quote_unverified", sec.key, ids, ""))  # 片段不记引语文字（规则 8）
    return out


def check_page(sections: Sequence[Section], claims: Mapping[str, ClaimView], cfg: CheckConfig,
               forbidden: Forbidden, *, quotes_allowed: bool = False,
               quote_verdicts: QuoteVerdicts | None = None) -> CheckResult:
    """quotes_allowed：调用方确认这份输出的受众只有学生本人（家长版、学生与家长共用的视图一律 False）。
    quote_verdicts：原文核对结果；没有某段引语的结果即视为未通过。"""
    out: list[Violation] = []
    full_text: list[str] = []
    verdicts = quote_verdicts or {}
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
        raw_free = _PLACEHOLDER.sub("", sec.connective)   # 占位以外的自由文字
        out.extend(_check_quotes(sec, raw_free, valid, quotes_allowed=quotes_allowed, verdicts=verdicts))
        full_text.append(raw_free)
        full_text.extend(q.text for q in sec.quotes)
        if not normalize(sec.connective):
            continue
        if normalize(sec.connective) not in cfg.connective_phrases:   # 第 0 条按含占位的整句比对
            out.append(Violation("connective_unapproved", sec.key, ids, raw_free))
        conn = normalize(raw_free)
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
            out.append(Violation("number_in_connective", sec.key, ids, raw_free))
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
             forbidden: Forbidden, *, quotes_allowed: bool = False,
             quote_verdicts: QuoteVerdicts | None = None) -> CompiledPage:
    """检查通过就原样发布；否则降级为纯模板版本：去掉全部衔接语（连同引语），只留引用了可见判断的陈述，并返回阻断记录。"""
    res = check_page(sections, claims, cfg, forbidden, quotes_allowed=quotes_allowed, quote_verdicts=quote_verdicts)
    if res.passed:
        return CompiledPage(tuple(sections), "passed")
    fallback = tuple(
        Section(s.key, "", tuple(st for st in s.statements if st.claim_id and st.claim_id in claims
                                 and claims[st.claim_id].readable and claims[st.claim_id].status in VISIBLE_STATUSES))
        for s in sections)
    return CompiledPage(fallback, "blocked_fallback", res.violations)
