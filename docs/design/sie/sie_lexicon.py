#!/usr/bin/env python3
"""禁用推断词表的加载与匹配（specs/sie-00-field-dictionary/spec.md 设计要点 5）。

用在三处：字段字典检查、判断模板登记前的检查、SIE-03 的禁用推断自动化测试与陈述—判断一致性检查。
词表本身是草稿，定稿须人签字（CLAUDE.md 必停项）。

命令行：
  python docs/design/sie/sie_lexicon.py "要检查的文本"    命中时列出类别并以退出码 1 结束
  python docs/design/sie/sie_lexicon.py --self-test       校验词表的正反例（make sie-dictionary-check 也会跑）
缺 PyYAML 时退出码 3。
"""
from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path

LEXICON_PATH = Path(__file__).resolve().parent / "forbidden-inference.v0.yaml"

try:
    import yaml
except ImportError:  # pragma: no cover
    print("sie-lexicon: 跳过（缺少 PyYAML；pip install -r packages/contracts/requirements-dev.txt）")
    sys.exit(3)


@dataclass(frozen=True)
class Hit:
    category: str
    name: str
    match: str


@dataclass(frozen=True)
class Category:
    id: str
    name: str
    patterns: tuple[re.Pattern[str], ...]
    forbidden_examples: tuple[str, ...]


@dataclass(frozen=True)
class Lexicon:
    version: str
    categories: tuple[Category, ...]
    allowed_examples: tuple[str, ...]

    def find(self, text: str) -> list[Hit]:
        hits: list[Hit] = []
        for c in self.categories:
            for p in c.patterns:
                m = p.search(text)
                if m:
                    hits.append(Hit(c.id, c.name, m.group(0)))
                    break
        return hits


def load(path: Path = LEXICON_PATH) -> Lexicon:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    cats = tuple(
        Category(
            id=c["id"],
            name=c["name"],
            patterns=tuple(re.compile(p, re.IGNORECASE) for p in c["patterns"]),
            forbidden_examples=tuple(c.get("forbidden_examples") or ()),
        )
        for c in data["categories"]
    )
    allowed = tuple(data.get("allowed_examples") or ()) + tuple(
        e for c in data["categories"] for e in (c.get("allowed_examples") or ())
    )
    return Lexicon(version=str(data["version"]), categories=cats, allowed_examples=allowed)


# CLAUDE.md 规则 12 与 SIE 第十章的每一类都要在词表里（验收：覆盖每一类）
REQUIRED_CATEGORIES = {
    "intelligence", "personality", "mental_health", "emotion_state", "family", "biometric",
    "group_to_individual", "level_label", "learning_style", "qualitative_negation",
}


def self_test(lex: Lexicon) -> list[str]:
    """词表自检：必备类别齐全；每类至少一个禁止示例且全部被本类命中；允许示例全部不命中任何类。"""
    errors: list[str] = []
    ids = [c.id for c in lex.categories]
    if len(ids) != len(set(ids)):
        errors.append("词表类别 id 重复")
    for missing in sorted(REQUIRED_CATEGORIES - set(ids)):
        errors.append(f"词表缺少类别 {missing}")
    for c in lex.categories:
        if not c.forbidden_examples:
            errors.append(f"类别 {c.id} 没有禁止示例")
        for e in c.forbidden_examples:
            if not any(h.category == c.id for h in lex.find(e)):
                errors.append(f"类别 {c.id} 的禁止示例没有被本类命中：{e}")
    if len(lex.allowed_examples) < 10:
        errors.append("允许示例少于 10 条，不足以防误伤")
    for e in lex.allowed_examples:
        hits = lex.find(e)
        if hits:
            errors.append(f"允许示例被误伤：{e}（{', '.join(f'{h.category}:{h.match}' for h in hits)}）")
    return errors


def main(argv: list[str]) -> int:
    lex = load()
    if argv[1:] == ["--self-test"]:
        errors = self_test(lex)
        for e in errors:
            print(f"sie-lexicon: {e}")
        print(f"sie-lexicon: {'失败' if errors else '通过'}（v{lex.version}，{len(lex.categories)} 类，允许示例 {len(lex.allowed_examples)} 条）")
        return 1 if errors else 0
    if len(argv) != 2:
        print(__doc__)
        return 2
    hits = lex.find(argv[1])
    for h in hits:
        print(f"命中 {h.category}（{h.name}）：{h.match}")
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
