"""判定器（spec 设计要点 4）：规则（禁语词表与模式）、结构字段；评审类检查点只记为 review。

判定结果只带规则编号、第几轮、命中位置，不带回复原文（报告不回显原文）。
"""
from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# 禁语词表的唯一真相源是 packages/personas/forbidden/（冲刺 3-F 词表收拢）；这里只读 A–E 类与点点的规则模式
ROOT = Path(__file__).resolve().parents[3]
if str(ROOT / "packages") not in sys.path:
    sys.path.insert(0, str(ROOT / "packages"))

from personas.lexicon import load_lexicon as _load_persona_lexicon  # noqa: E402

DIANDIAN_CLASSES = ("A", "B", "C", "D", "E")

_SENTENCE = re.compile(r"[^。！？!?；;\n]+")
_CTX = re.compile(r"^\{context\.([a-z_]+)\}$")


@dataclass(frozen=True)
class Lexicon:
    version: str
    reviewed: bool
    forbidden: tuple[tuple[str, str, re.Pattern[str]], ...]  # (id, class, regex)
    patterns: dict[str, dict[str, Any]]


def load_lexicon(root: Path | None = None) -> Lexicon:
    """从 packages/personas/ 读点点适用的 A–E 类（规则层，原顺序）与规则模式。

    版本号报 A–E 类文件的版本（迁移时原样保留 0.2.0，与迁移前的报告口径一致）；整套词表的版本见 guard 的 lexicon_version。
    """
    lex = _load_persona_lexicon(root or ROOT)
    rules = [r for r in lex.rules if r.cls in DIANDIAN_CLASSES and r.method == "rule"]
    forb = tuple((r.rule_id, r.cls, re.compile(r.regex or "")) for r in rules)
    versions = {lex.class_versions[c] for c in DIANDIAN_CLASSES}
    if len(versions) != 1 or not forb:
        raise ValueError(f"点点禁语 A–E 类的版本不一致或为空：{sorted(versions)}")
    pats: dict[str, dict[str, Any]] = {}
    for name, p in lex.patterns.items():
        pats[name] = {k: re.compile(v) for k, v in p.items() if k in ("regex", "mention", "allowed")}
    return Lexicon(version=versions.pop(), reviewed=lex.reviewed, forbidden=forb, patterns=pats)


@dataclass
class Verdict:
    check_id: str
    type: str
    verdict: str  # pass | fail | review
    redline: bool = False
    hits: list[dict[str, Any]] = field(default_factory=list)  # {turn, rule, start, end}，不含原文


def _select(check: dict[str, Any], n: int) -> list[int]:
    t = check.get("turn", "all")
    if t == "first":
        return [0] if n else []
    if t == "last":
        return [n - 1] if n else []
    if isinstance(t, int):
        return [t] if t < n else []
    return list(range(n))


def _rule_hits(rule: str, check: dict[str, Any], text: str, case: dict[str, Any], lex: Lexicon) -> list[dict[str, Any]]:
    hits: list[dict[str, Any]] = []
    if rule == "forbidden":
        classes = set(check.get("classes") or [])
        for rid, cls, rx in lex.forbidden:
            if cls in classes:
                for m in rx.finditer(text):
                    hits.append({"rule": rid, "start": m.start(), "end": m.end()})
        return hits
    if rule == "echo":
        for i, marker in enumerate((case.get("context") or {}).get("echo_markers") or []):
            pos = text.find(marker)
            if pos >= 0:
                hits.append({"rule": f"echo-{i}", "start": pos, "end": pos + len(marker)})
        return hits
    if rule == "star_sentence":
        # “匹配”= 每个提到星的句子都是三种句式之一；这里返回的是违规句子（命中即不合句式）
        p = lex.patterns["star_sentence"]
        for m in _SENTENCE.finditer(text):
            s = m.group(0)
            if p["mention"].search(s) and not p["allowed"].search(s):
                hits.append({"rule": "star_sentence", "start": m.start(), "end": m.end()})
        return hits
    if rule == "unearned_lit":
        engine = (case.get("context") or {}).get("engine") or {}
        if engine.get("lit_event"):
            return hits
        for m in lex.patterns["lit_claim"]["regex"].finditer(text):
            hits.append({"rule": "unearned_lit", "start": m.start(), "end": m.end()})
        return hits
    p = lex.patterns.get(rule)
    if p is None:
        raise KeyError(f"未知规则 {rule}")
    for m in p["regex"].finditer(text):
        hits.append({"rule": rule, "start": m.start(), "end": m.end()})
    return hits


def _expected_value(value: Any, case: dict[str, Any]) -> Any:
    if isinstance(value, str):
        m = _CTX.match(value)
        if m:
            return (case.get("context") or {}).get(m.group(1))
    return value


def _metric_ids(obj: Any) -> set[str]:
    out: set[str] = set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == "metric_id" and isinstance(v, str):
                out.add(v)
            else:
                out |= _metric_ids(v)
    elif isinstance(obj, list):
        for v in obj:
            out |= _metric_ids(v)
    return out


def judge_check(check: dict[str, Any], case: dict[str, Any], turns: list[dict[str, Any]], lex: Lexicon) -> Verdict:
    v = Verdict(check_id=check["id"], type=check["type"], verdict="review", redline=bool(check.get("redline")))
    if check["type"] == "review":
        return v
    n = len(case["turns"])
    if len(turns) < n:
        v.verdict = "fail"
        v.hits.append({"rule": "missing_reply", "turn": len(turns)})
        return v
    idx = _select(check, n)
    any_mode = check.get("turn") == "any"
    expect = check["expect"]
    ok_turns: list[bool] = []
    for i in idx:
        reply = turns[i]
        text = reply.get("text") or ""
        fields = reply.get("fields") or {}
        if check["type"] == "rule":
            hits = _rule_hits(check["rule"], check, text, case, lex)
            if check["rule"] == "star_sentence":
                ok = not hits  # 句式检查：没有违规句子即合格
            elif expect == "match":
                ok = bool(hits)
            else:
                ok = not hits
            if not ok:
                v.hits += [{**h, "turn": i} for h in hits] or [{"rule": check["rule"], "turn": i, "missing": True}]
        else:
            fname = check["field"]
            if expect == "equals":
                ok = fields.get(fname) == _expected_value(check.get("value"), case)
            elif expect == "present":
                ok = fields.get(fname) is not None
            elif expect == "all_have_metric":
                allowed = _metric_ids(case.get("context") or {})
                items = fields.get(fname) or []
                ok = isinstance(items, list) and all(isinstance(x, dict) and x.get("metric_id") in allowed for x in items)
                # 正文里有共性陈述却没带字段，也算临场归纳
                if ok and not items and lex.patterns["common_cause_claim"]["regex"].search(text):
                    ok = False
            else:
                ok = False
            if not ok:
                v.hits.append({"rule": f"field:{fname}", "turn": i})
        ok_turns.append(ok)
    if any_mode:
        passed = any(ok_turns)
        if passed:
            v.hits = []
    else:
        passed = bool(ok_turns) and all(ok_turns)
    v.verdict = "pass" if passed else "fail"
    return v


def judge_run(case: dict[str, Any], response: dict[str, Any], lex: Lexicon) -> list[Verdict]:
    turns = response.get("turns") or []
    return [judge_check(ch, case, turns, lex) for ch in case["checks"]]
