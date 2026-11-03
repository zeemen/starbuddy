"""环境变量名在源码里是否真实存在：字面量，或源码里按学科 / 调用方拼出的模板（f-string 的 PREFIX{…}SUFFIX，模板段的小写取值
在同一批源码里以带引号的字符串出现）。清单里的模板名（含 <SUBJECT> 之类）按前缀与后缀核对。"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_TEMPLATE = re.compile(r"([A-Z][A-Z0-9_]*_)\{[^}]*\}([A-Z0-9_]*)")


def read_sources(sources: list[str]) -> str:
    out = []
    for s in sources:
        p = ROOT / s
        assert p.is_file(), f"源码文件不存在：{s}"
        out.append(p.read_text(encoding="utf-8"))
    return "\n".join(out)


def name_exists(name: str, text: str) -> bool:
    if "<" in name:
        prefix, rest = name.split("<", 1)
        suffix = rest.split(">", 1)[1]
        return any(m.group(1) == prefix and m.group(2) == suffix for m in _TEMPLATE.finditer(text))
    if re.search(rf"(?<![A-Z0-9_]){re.escape(name)}(?![A-Z0-9_])", text):
        return True
    for m in _TEMPLATE.finditer(text):
        prefix, suffix = m.group(1), m.group(2)
        if name.startswith(prefix) and name.endswith(suffix) and len(name) > len(prefix) + len(suffix):
            middle = name[len(prefix): len(name) - len(suffix)] if suffix else name[len(prefix):]
            if re.fullmatch(r"[A-Z0-9]+", middle) and re.search(rf"[\"']{middle.lower()}[\"']", text):
                return True
    return False
