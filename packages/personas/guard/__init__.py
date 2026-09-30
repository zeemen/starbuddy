"""禁语分类器（spec 设计要点 2）：按登记表的 forbidden_classes 判定一段角色输出命中哪些禁语类。

三层判定：
  - 规则层（method=rule）：词表 + 正则、句长、疑问句；确定性，可以阻断 CI；
  - 结构上下文层（method=context）：需要引擎字段（allowed_hint_level、hint_level_used、verified_claims、grading_moment、
    moment、citations、generated_passage）；只有调用方给了 context 才判定；确定性，可以阻断 CI；
  - 模型判定层（method=model）：本冲刺只登记、不执行（pending_model_rules 列出），不阻断。
同一句话在不同角色下结论可以不同（例如“差一点”只对考官型是 G 类）；平台级条目（platform.yaml）按角色类型生效。

输出每条命中：class、rule_id、span、method、confidence、lexicon_version。不回显原文（调用方用 span 自取）。
评测现在用它；以后学习服务的 teach 输出过滤复用同一份代码。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from .. import GUARD_VERSION, REGISTRY_REL, REPO_ROOT, PersonaConfigError
from ..lexicon import Lexicon, Rule, load_lexicon
from ..registry import load_registry, personas_by_id

__all__ = ["GUARD_VERSION", "Hit", "classify", "classify_detail", "applicable_rules", "persona_profile", "export_kge",
           "load_context", "Context", "CORRECTNESS", "STAR_MENTION", "split_sentences", "sentence_chars"]

_SENT = re.compile(r"[^。！？!?；;\n]+[。！？!?；;]?")
_PUNCT = re.compile(r"[\s，。！？；：、,.!?;:\"'“”‘’（）()《》【】\[\]…—-]")
# 对错陈述（结构层 unverified_claim）：命中即要求本轮带 verified 的判定（含考官型的命中 / 达标判定句）
CORRECTNESS = re.compile(r"是对的|是错的|对了|错了|不正确|正确|错误|答对|答错|做对|做错|部分命中|未命中|命中|部分达标|未达标|达标")
# 星的提及（与点点评分器 star_sentence.mention 同口径：不含星期、星座、星球、明星等）
STAR_MENTION = re.compile(r"(?<![明火卫行恒彗零])星(?!期|座|球)")
STAR_MOMENTS = ("opening", "threshold")
FULL_TEXT_MIN_CHARS = 150  # 结构层 full_generation：单段连续正文超过这个字数按“整段生成”（待教研校准，lessons）


def split_sentences(text: str) -> list[tuple[int, int, str]]:
    return [(m.start(), m.end(), m.group(0)) for m in _SENT.finditer(text) if m.group(0).strip()]


def sentence_chars(s: str) -> int:
    """句长按“字”计：不算空白与标点"""
    return len(_PUNCT.sub("", s))


@dataclass(frozen=True)
class Hit:
    cls: str
    rule_id: str
    span: tuple[int, int]
    method: str
    confidence: float
    lexicon_version: str

    def to_dict(self) -> dict[str, Any]:
        return {"class": self.cls, "rule_id": self.rule_id, "span": list(self.span), "method": self.method,
                "confidence": self.confidence, "lexicon_version": self.lexicon_version}


@dataclass(frozen=True)
class Context:
    registry: dict[str, Any]
    lexicon: Lexicon
    root: Path


@lru_cache(maxsize=16)
def _cached(root: str, registry_rel: str, stamp: tuple[tuple[str, int, int], ...]) -> Context:
    reg = load_registry(Path(root), registry_rel)
    lex = load_lexicon(Path(root), str(reg.get("lexicon") or "packages/personas/forbidden/manifest.yaml"))
    return Context(reg, lex, Path(root))


def _stamp(root: Path, registry_rel: str) -> tuple[tuple[str, int, int], ...]:
    """缓存键带上登记表与词表文件的修改时间和大小：文件一改就重新加载（测试与变异会原地改副本）"""
    files = [root / registry_rel]
    for d in ("packages/personas/forbidden", "packages/personas/patterns"):
        if (root / d).is_dir():
            files += sorted((root / d).glob("*.yaml"))
    out = []
    for f in files:
        try:
            st = f.stat()
            out.append((str(f), st.st_mtime_ns, st.st_size))
        except OSError:
            out.append((str(f), -1, -1))
    return tuple(out)


def load_context(root: Path | str = REPO_ROOT, registry_rel: str = REGISTRY_REL) -> Context:
    r = Path(root).resolve()
    return _cached(str(r), registry_rel, _stamp(r, registry_rel))


def persona_profile(ctx: Context, persona_id: str) -> tuple[str, tuple[str, ...]]:
    """（角色类型, 适用禁语类）；persona_id 也可以是登记表 surfaces 里的平台文案面（例如 platform）"""
    p = personas_by_id(ctx.registry).get(persona_id)
    if p is not None:
        return str(p["type"]), tuple(p.get("forbidden_classes") or ())
    s = (ctx.registry.get("surfaces") or {}).get(persona_id)
    if s is not None:
        return "surface", tuple(s.get("forbidden_classes") or ())
    raise PersonaConfigError(f"登记表里没有角色或文案面 {persona_id!r}")


def applicable_rules(ctx: Context, persona_id: str) -> list[Rule]:
    ptype, classes = persona_profile(ctx, persona_id)
    out: list[Rule] = []
    for r in ctx.lexicon.rules:
        if r.applies_to_types:
            if ptype in r.applies_to_types:
                out.append(r)
        elif r.cls in classes:
            out.append(r)
    return out


def _rule_layer(r: Rule, text: str, lex: Lexicon) -> list[tuple[int, int]]:
    if r.kind == "sentence_length":
        return [(s, e) for s, e, t in split_sentences(text) if sentence_chars(t) > int(r.max_chars or 0)]
    if r.kind == "question":
        return [(s, e) for s, e, t in split_sentences(text) if t.rstrip().endswith(("？", "?"))]
    return [(m.start(), m.end()) for m in lex.rx(r).finditer(text) if m.end() > m.start()]


def _int(v: Any) -> int | None:
    return v if isinstance(v, int) and not isinstance(v, bool) else None


def _context_layer(r: Rule, text: str, c: dict[str, Any]) -> list[tuple[int, int]]:
    whole = [(0, len(text))]
    if r.check == "hint_overreach":
        allowed, used = _int(c.get("allowed_hint_level")), _int(c.get("hint_level_used"))
        return whole if allowed is not None and used is not None and used > allowed else []
    if r.check == "unverified_claim":
        m = CORRECTNESS.search(text)
        if not m:
            return []
        claims = c.get("verified_claims") or []
        verified = any(isinstance(x, dict) and x.get("verified") is True for x in claims)
        return [] if verified else [(m.start(), m.end())]
    if r.check == "off_moment":
        return whole if c.get("grading_moment") is False else []
    if r.check == "star_outside":
        m = STAR_MENTION.search(text)
        return [(m.start(), m.end())] if m and c.get("moment") not in STAR_MOMENTS else []
    if r.check == "uncited_quote":
        cites = c.get("citations") or []
        return whole if any(isinstance(x, dict) and x.get("corpus_hit") is not True for x in cites) else []
    if r.check == "full_generation":
        if c.get("generated_passage") is True:
            return whole
        pos = 0
        for para in text.split("\n"):
            if sentence_chars(para) >= FULL_TEXT_MIN_CHARS:
                return [(pos, pos + len(para))]
            pos += len(para) + 1
        return []
    raise PersonaConfigError(f"{r.rule_id}: 未实现的结构检查 {r.check}")


def classify_detail(persona_id: str, text: str, context: dict[str, Any] | None = None, history: list[Any] | None = None, *,
                    ctx: Context | None = None, classes: tuple[str, ...] | None = None) -> dict[str, Any]:
    """history 目前只作记录（多轮判定在漂移检查器里做）；classes 可再收窄到某几类（点点评分器按检查点的 classes）"""
    ctx = ctx or load_context()
    rules = applicable_rules(ctx, persona_id)
    if classes is not None:
        rules = [r for r in rules if r.cls in classes]
    hits: list[Hit] = []
    pending: list[str] = []
    ver = ctx.lexicon.version
    for r in rules:
        if r.method == "rule":
            spans = _rule_layer(r, text, ctx.lexicon)
        elif r.method == "context":
            spans = [] if context is None else _context_layer(r, text, context)
        else:
            pending.append(r.rule_id)
            continue
        hits += [Hit(r.cls, r.rule_id, sp, r.method, 1.0, ver) for sp in spans]
    hits.sort(key=lambda h: (h.span[0], h.span[1], h.rule_id))
    return {"persona_id": persona_id, "lexicon_version": ver, "guard_version": GUARD_VERSION,
            "classes": sorted({r.cls for r in rules}), "hits": [h.to_dict() for h in hits], "pending_model_rules": pending,
            "history_turns": len(history or [])}


def classify(persona_id: str, text: str, context: dict[str, Any] | None = None, history: list[Any] | None = None, *,
             ctx: Context | None = None, classes: tuple[str, ...] | None = None) -> list[dict[str, Any]]:
    return classify_detail(persona_id, text, context, history, ctx=ctx, classes=classes)["hits"]


# ---------------------------------------------------------------- 导出给 KGE-03 页面人设中立检查

KGE_CLASS = {"A": "A", "B": "B", "C": "C", "D": "D", "E": "E", "F:math": "F", "F:chinese": "F", "G": "G"}


def export_kge(ctx: Context | None = None) -> dict[str, Any]:
    """角色名、称呼、口头禅与 A–G 类词面规则（KGE 的 Rule：id、cls、regex、source、subjects）。

    页面不属于任何角色：A–E、G 对所有页面生效（subjects 为空），F:math / F:chinese 只对登记表 kge_subject 对应的页面。
    平台级条目（platform 类）不导出（页面检查 KGE-03 没有这一类，口径不变，lessons 说明）；结构层与模型层条目不导出。
    """
    ctx = ctx or load_context()
    reg = ctx.registry
    active = [p for p in reg.get("personas") or [] if p.get("status") == "active"]
    names: list[str] = []
    for p in active:
        names.append(p["display_name"])
    names += list((reg.get("type_labels") or {}).values())
    address: list[str] = []
    catch: list[str] = []
    for p in active:
        address += [str(v) for v in (p.get("address_terms") or {}).values()]
        catch += [str(c["text"]) for c in p.get("catchphrases") or []]
    subj_by_class: dict[str, tuple[str, ...]] = {}
    for p in active:
        for c in p.get("forbidden_classes") or []:
            if c.startswith("F:") and p.get("kge_subject"):
                subj_by_class[c] = tuple(sorted(set(subj_by_class.get(c, ())) | {p["kge_subject"]}))
    forbidden = []
    for r in ctx.lexicon.rules:
        if r.method != "rule" or r.kind is not None or r.cls not in KGE_CLASS:
            continue
        subjects = subj_by_class.get(r.cls, ()) if r.cls.startswith("F:") else ()
        if r.cls.startswith("F:") and not subjects:
            raise PersonaConfigError(f"{r.cls} 没有对应的 kge_subject（登记表）")
        forbidden.append({"id": r.rule_id, "cls": KGE_CLASS[r.cls], "regex": r.regex, "source": f"{r.file} {r.rule_id}（{r.source}）",
                          "subjects": list(subjects)})
    aya_world = (ctx.lexicon.patterns.get("aya_world") or {}).get("regex")
    if not aya_world:
        raise PersonaConfigError("规则模式里没有 aya_world（阿鸦设定元素）")
    setting = [{"id": "SET-aya-world", "cls": "setting", "regex": aya_world,
                "source": "packages/personas/patterns/diandian.yaml patterns.aya_world", "subjects": []}]
    uniq = lambda xs: list(dict.fromkeys(x for x in xs if x))  # noqa: E731
    return {"role_names": uniq(names), "address_terms": uniq(address), "catchphrases": uniq(catch), "setting_patterns": setting,
            "forbidden": forbidden, "lexicon_version": ctx.lexicon.version,
            "sources": [REGISTRY_REL, *ctx.lexicon.files]}
