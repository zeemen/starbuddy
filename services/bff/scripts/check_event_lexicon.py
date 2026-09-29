#!/usr/bin/env python3
"""行为事件与探针接口的禁用推断词表检查（冲刺 2-L，specs/behavior-events-sprint2/spec.md 设计要点 4；CLAUDE.md 规则 12）。

从 services/bff/openapi/events.v0.yaml 抽出学生端能写进库的全部编码：
  - kinds        行为事件类型（BehaviorEventInput 各分支的 kind 的 const / enum）
  - payload_keys 各类型 payload 的键名
  - entries      session_start 的 entry 取值
  - predictions  探针的预测取值（顺带查）
用 docs/design/sie/sie_lexicon.py 的匹配规则逐个扫描（与 packages/contracts/scripts/check_services.py 同一做法：
原文查一次，把 _ . - 换成空格再查一次，词表按词边界匹配）。只读词表，不改 docs/design/sie/ 下的任何文件。

命令行：
  python services/bff/scripts/check_event_lexicon.py            扫描 events.v0.yaml；命中时列出并以退出码 1 结束
  python services/bff/scripts/check_event_lexicon.py --json     另在最后一行输出抽出的编码（JSON），供 BFF 单测与代码里的白名单对照
  python services/bff/scripts/check_event_lexicon.py --self-test  反例自检：往抽出的编码里塞入会命中词表的合成编码，必须被发现
  另可加 --openapi <路径> 扫描别的文件（单测用它扫一份塞了反例的副本）
缺 PyYAML 时退出码 3（与 sie_lexicon.py 一致）。
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent.parent
OPENAPI = HERE.parent / "openapi" / "events.v0.yaml"
LEXICON_DIR = ROOT / "docs" / "design" / "sie"

try:
    import yaml
except ImportError:  # pragma: no cover
    print("event-lexicon: 跳过（缺少 PyYAML；用 services/learning/.venv/bin/python 或 pip install -r packages/contracts/requirements-dev.txt）")
    sys.exit(3)

sys.path.insert(0, str(LEXICON_DIR))
import sie_lexicon  # noqa: E402


def _values(node: dict) -> list[str]:
    """schema 节点的 const 或 enum 里的字符串。非字符串一律报错：YAML 1.1 会把裸的 yes / no 读成布尔值，静默丢掉就漏查了"""
    vals = [node["const"]] if "const" in node else list(node.get("enum", []))
    bad = [v for v in vals if not isinstance(v, str)]
    if bad:
        raise ValueError(f"枚举里有非字符串取值 {bad!r}（YAML 里的 yes / no 要加引号）")
    return vals


def extract(doc: dict) -> dict[str, list[str]]:
    schemas = doc["components"]["schemas"]
    kinds: list[str] = []
    payload_keys: list[str] = []
    entries: list[str] = []
    for branch in schemas["BehaviorEventInput"]["oneOf"]:
        props = branch["properties"]
        kinds += _values(props["kind"])
        payload = props["payload"].get("properties", {})
        payload_keys += list(payload.keys())
        if "entry" in payload:
            entries += _values(payload["entry"])
    predictions = _values(schemas["ConfidenceProbeInput"]["properties"]["prediction"])
    return {
        "kinds": sorted(set(kinds)),
        "payload_keys": sorted(set(payload_keys)),
        "entries": sorted(set(entries)),
        "predictions": sorted(set(predictions)),
    }


def scan(lex: sie_lexicon.Lexicon, words: dict[str, list[str]]) -> list[str]:
    errors: list[str] = []
    for group, items in words.items():
        for w in items:
            for h in lex.find(w) + lex.find(re.sub(r"[_.\-]+", " ", w)):
                errors.append(f"{group} 的 {w!r} 命中禁用推断词表 {h.category}（{h.name}：{h.match}）")
    return errors


# 反例：每组各塞一个合成编码，必须全部被发现（证明检查在工作，而不是“什么都没查”）
COUNTEREXAMPLES = {
    "kinds": "exit_when_anxious",
    "payload_keys": "is_lazy",
    "entries": "gifted_track",
    "predictions": "frustrated",
}


def self_test(lex: sie_lexicon.Lexicon, words: dict[str, list[str]]) -> list[str]:
    errors: list[str] = []
    for group, items in words.items():
        if not items:
            errors.append(f"没有从 events.v0.yaml 抽出任何 {group}，检查不到东西")
    for group, bad in COUNTEREXAMPLES.items():
        hits = scan(lex, {group: [bad]})
        if not hits:
            errors.append(f"反例没有被发现：{group} 里的 {bad!r}")
    return errors


def main(argv: list[str]) -> int:
    argv = list(argv)
    path = OPENAPI
    if "--openapi" in argv:  # 单测用：扫一份塞了反例的副本，证明整条链路能发现
        i = argv.index("--openapi")
        path = Path(argv[i + 1])
        del argv[i:i + 2]
    lex = sie_lexicon.load()
    words = extract(yaml.safe_load(path.read_text(encoding="utf-8")))
    if argv[1:] == ["--self-test"]:
        errors = self_test(lex, words)
        for e in errors:
            print(f"event-lexicon: {e}")
        print(f"event-lexicon: 自检{'失败' if errors else '通过'}（{len(COUNTEREXAMPLES)} 个反例）")
        return 1 if errors else 0
    if argv[1:] not in ([], ["--json"]):
        print(__doc__)
        return 2
    errors = scan(lex, words)
    for e in errors:
        print(f"event-lexicon: {e}")
    total = sum(len(v) for v in words.values())
    print(f"event-lexicon: {'失败' if errors else '通过'}（{total} 个编码，词表 v{lex.version}）")
    if argv[1:] == ["--json"]:
        print(json.dumps(words, ensure_ascii=False, sort_keys=True))
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
