"""文本规范化与切分：中文 CER 按字，英文 WER 按词。规则可配置，缺省值写进报告。

只处理参考文本与转写文本；不接触音频。
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import asdict, dataclass
from typing import Any

_CJK = re.compile(r"[㐀-䶿一-鿿豈-﫿\U00020000-\U0002ffff]")
_EN_DASHES = re.compile(r"[-‐‑‒–—]")
_APOSTROPHES = str.maketrans({"’": "'", "‘": "'", "＇": "'"})


@dataclass(frozen=True)
class ZhNorm:
    """中文（CER）规范化。"""
    nfkc: bool = True            # 全角转半角（NFKC）：“ＡＢ１”与“AB1”视为相同
    strip_punct: bool = True     # 去掉标点与符号（Unicode 类别 P*、S*）；为 False 时标点逐个计入
    lowercase: bool = True       # 夹在中文里的拉丁字母统一小写
    latin_as_word: bool = False  # 夹在中文里的连续拉丁字母与数字按一个单位计（缺省逐字符计）

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class EnNorm:
    """英文（WER）规范化。"""
    nfkc: bool = True             # 全角转半角
    lowercase: bool = True        # 大小写不敏感
    strip_punct: bool = True      # 去掉词里的标点与符号
    keep_apostrophe: bool = True  # 保留词内撇号：don't 仍是 don't（为 False 时变成 dont）
    split_hyphen: bool = True     # 连字符拆词：well-known → well known

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _is_punct(ch: str) -> bool:
    return unicodedata.category(ch)[0] in "PS"


def zh_units(text: str, cfg: ZhNorm = ZhNorm()) -> list[str]:
    """中文按字切分。空白只作分隔，不计入。"""
    if cfg.nfkc:
        text = unicodedata.normalize("NFKC", text)
    if cfg.lowercase:
        text = text.lower()
    units: list[str] = []
    buf: list[str] = []

    def flush() -> None:
        if buf:
            units.append("".join(buf))
            buf.clear()

    for ch in text:
        if ch.isspace():
            flush()
        elif _is_punct(ch):
            flush()
            if not cfg.strip_punct:
                units.append(ch)
        elif cfg.latin_as_word and ch.isalnum() and not _CJK.match(ch):
            buf.append(ch)
        else:
            flush()
            units.append(ch)
    flush()
    return units


def en_words(text: str, cfg: EnNorm = EnNorm()) -> list[str]:
    """英文按词切分（空白分词）。"""
    if cfg.nfkc:
        text = unicodedata.normalize("NFKC", text)
    text = text.translate(_APOSTROPHES)
    if cfg.lowercase:
        text = text.casefold()
    if cfg.split_hyphen:
        text = _EN_DASHES.sub(" ", text)
    words: list[str] = []
    for tok in text.split():
        if cfg.strip_punct:
            tok = "".join(c for c in tok if not _is_punct(c) or (cfg.keep_apostrophe and c == "'")).strip("'")
        if tok:
            words.append(tok)
    return words


def units_for(lang: str, text: str, zh: ZhNorm, en: EnNorm) -> list[str]:
    if lang == "zh":
        return zh_units(text, zh)
    if lang == "en":
        return en_words(text, en)
    raise ValueError(f"不支持的语言：{lang}")
