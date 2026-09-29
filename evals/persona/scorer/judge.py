"""判定器（spec 设计要点 4）：规则（禁语词表与模式）、结构字段；评审类检查点只记为 review。

判定结果只带规则编号、第几轮、命中位置，不带回复原文（报告不回显原文）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

_SENTENCE = re.compile(r"[^。！？!?；;\n]+")
_CTX = re.compile(r"^\{context\.([a-z_]+)\}$")


@dataclass(frozen=True)
class Lexicon:
    version: str
    reviewed: bool
    forbidden: tuple[tuple[str, str, re.Pattern[str]], ...]  # (id, class, regex)
    patterns: dict[str, dict[str, Any]]


def load_lexicon(path: Path) -> Lexicon:
    doc = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    forb = tuple((e["id"], e["class"], re.compile(e["regex"])) for e in doc["forbidden"])
    pats: dict[str, dict[str, Any]] = {}
    for name, p in doc["patterns"].items():
        pats[name] = {k: re.compile(v) for k, v in p.items() if k in ("regex", "mention", "allowed")}
    return Lexicon(version=str(doc["version"]), reviewed=bool(doc["reviewed"]), forbidden=forb, patterns=pats)


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
