"""把走查产物里每次运行都会变的值换成按出现顺序编号的占位符，得到可以入库、逐字节比对的样例。

会变的值：库里生成的 UUID（审核记录、内容版本 ID）、时间戳、SHA-256 摘要（含题目登记摘要，它包含内容版本 ID）、
导出批次编号、内容包版本号里的决定摘要段（.d<8 位>）。固定的合成 ID（合成 staff、合成地图版本）原样保留。
UUID、摘要等同一个值在所有产物里换成同一个占位符（按第一次出现的顺序编号），所以样例里仍能看出“这条执行记录指回那条审核记录”；
时间戳一律换成 <time>，不编号。
"""
from __future__ import annotations

import re
from typing import Any

UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
TIME = re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})")
SHA = re.compile(r"(?<![0-9a-f])[0-9a-f]{64}(?![0-9a-f])")
EXPORT_REF = re.compile(r"AYA-ADM-\d{8}T\d{6}Z")
PACK_DIGEST = re.compile(r"(\.d)[0-9a-f]{8}\b")


class Normalizer:
    def __init__(self, keep: set[str]) -> None:
        self.keep = {k.lower() for k in keep}
        self.seen: dict[tuple[str, str], str] = {}
        self.count: dict[str, int] = {}

    def _label(self, kind: str, value: str) -> str:
        key = (kind, value)
        if key not in self.seen:
            self.count[kind] = self.count.get(kind, 0) + 1
            self.seen[key] = f"<{kind}-{self.count[kind]}>"
        return self.seen[key]

    def text(self, s: str) -> str:
        s = TIME.sub("<time>", s)  # 不编号：两次签字可能落在同一微秒，编号会随运行变
        s = UUID.sub(lambda m: m.group(0) if m.group(0).lower() in self.keep else self._label("uuid", m.group(0)), s)
        s = SHA.sub(lambda m: self._label("sha256", m.group(0)), s)
        s = EXPORT_REF.sub(lambda m: self._label("export-ref", m.group(0)), s)
        return PACK_DIGEST.sub(lambda m: m.group(1) + self._label("pack-digest", m.group(0)[2:]), s)

    def value(self, v: Any) -> Any:
        if isinstance(v, str):
            return self.text(v)
        if isinstance(v, list):
            return [self.value(x) for x in v]
        if isinstance(v, dict):
            return {self.text(k) if isinstance(k, str) else k: self.value(x) for k, x in v.items()}
        return v
