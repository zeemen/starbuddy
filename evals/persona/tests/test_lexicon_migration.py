"""词表收拢（冲刺 3-F 设计要点 2、待人确认 #14）：迁移前后同一批输入的命中逐条一致。

“迁移前”用旧位置 diandian/lexicon/forbidden.yaml（内容原样保留的只读镜像）按迁移前的解析方式加载；
“迁移后”用评分器现在的 load_lexicon()（读 packages/personas/）。输入覆盖：120 条用例的全部字符串、评分器自检夹具、
新词表里的正例与反例。比对每条输入上的 (rule_id, start, end) 列表与规则模式的命中。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))

from scorer.cases import load_cases  # noqa: E402
from scorer.judge import load_lexicon  # noqa: E402

BASE = HERE / "diandian"
LEGACY = BASE / "lexicon" / "forbidden.yaml"
NEW_DIR = HERE.parents[1] / "packages" / "personas"


def _legacy():
    """迁移前（冲刺 1-I）的加载方式，逐字保留"""
    doc = yaml.safe_load(LEGACY.read_text(encoding="utf-8"))
    forb = tuple((e["id"], e["class"], re.compile(e["regex"])) for e in doc["forbidden"])
    pats = {name: {k: re.compile(v) for k, v in p.items() if k in ("regex", "mention", "allowed")} for name, p in doc["patterns"].items()}
    return str(doc["version"]), bool(doc["reviewed"]), forb, pats


def _strings(obj) -> list[str]:
    if isinstance(obj, str):
        return [obj]
    if isinstance(obj, dict):
        return [s for v in obj.values() for s in _strings(v)]
    if isinstance(obj, list):
        return [s for v in obj for s in _strings(v)]
    return []


def _inputs() -> list[str]:
    texts = [s for c in load_cases(BASE / "cases") for s in _strings(c)]
    texts += _strings(yaml.safe_load((BASE / "fixtures" / "selftest.yaml").read_text(encoding="utf-8")))
    for c in "ABCDE":
        doc = yaml.safe_load((NEW_DIR / "forbidden" / f"{c}.yaml").read_text(encoding="utf-8"))
        texts += [s for r in doc["rules"] for s in _strings(r.get("examples_positive")) + _strings(r.get("examples_negative"))]
    return list(dict.fromkeys(texts))


def _hits(forb, text: str):
    return [(rid, m.start(), m.end()) for rid, _, rx in forb for m in rx.finditer(text)]


def test_same_hits_before_and_after_migration():
    old_version, old_reviewed, old_forb, old_pats = _legacy()
    new = load_lexicon()
    assert (new.version, new.reviewed) == (old_version, old_reviewed)
    assert [(i, c, r.pattern) for i, c, r in new.forbidden] == [(i, c, r.pattern) for i, c, r in old_forb], "规则顺序、类与正则逐条一致"
    inputs = _inputs()
    assert len(inputs) > 300
    fired = 0
    for text in inputs:
        before, after = _hits(old_forb, text), _hits(new.forbidden, text)
        assert before == after, text
        fired += bool(before)
        for name, pats in old_pats.items():
            for k, rx in pats.items():
                assert [m.span() for m in rx.finditer(text)] == [m.span() for m in new.patterns[name][k].finditer(text)], (name, k, text)
    assert fired >= 35, "输入里要有足够多的命中，否则一致性比对没有意义"


def test_legacy_file_is_marked_moved():
    doc = yaml.safe_load(LEGACY.read_text(encoding="utf-8"))
    assert doc["moved_to"] == "packages/personas/forbidden/"
