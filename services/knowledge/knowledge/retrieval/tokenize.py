"""全文检索的分词（spec 设计要点 6；待人确认 #5：jieba，MIT，固定 0.42.1）。

- 用独立的 `jieba.Tokenizer` 实例（不碰全局默认分词器：别处加的用户词典不会改变这里的结果）；
- 只用词典切分（HMM=False）+ 搜索引擎模式（长词再切出短词），不做新词发现，同一输入的结果只取决于 jieba 版本与词典；
- 先做 NFKC 与小写；去掉页面标记（原子引用、页面链接、原句占位）；只保留含汉字、字母或数字的词；去停用词。
分词器与停用词表的任何改动都改变检索结果：改动时升 TOKENIZER_VERSION，并重跑对照评测。
"""
from __future__ import annotations

import logging
import re
import threading
import unicodedata
import warnings

TOKENIZER_VERSION = "jieba-0.42.1+search-nohmm+sw1"
# 停用词：只收虚词与问句套话（问句里常见、页面里常见、对区分知识点没有帮助）。改动须升 TOKENIZER_VERSION
STOPWORDS = frozenset({
    "的", "了", "是", "和", "与", "及", "或", "在", "把", "被", "对", "从", "到", "为", "以", "就", "都", "也", "还", "而",
    "吗", "呢", "吧", "啊", "呀", "么", "个", "这", "那", "这个", "那个", "一个", "什么", "怎么", "怎样", "如何", "为什么",
    "哪些", "有", "有些", "要", "会", "能", "可以", "请问", "一下", "用", "我", "你", "他", "她", "我们", "你们", "时候", "时",
})
_MARKUP = re.compile(r"\[@[a-z]+:[^\]\s]+\]|\{\{quote:[^}\s]+\}\}|\[\[[^\]\s|]+\]\]|<!--.*?-->")
_WORDY = re.compile(r"[0-9a-z㐀-䶿一-鿿]")

_lock = threading.Lock()
_tokenizer = None


def _jieba():
    global _tokenizer
    with _lock:
        if _tokenizer is None:
            with warnings.catch_warnings():  # jieba 0.42.1 导入时用 pkg_resources（有弃用警告，功能不受影响）
                warnings.simplefilter("ignore", DeprecationWarning)
                import jieba  # 延迟导入：只有用到全文检索时才加载词典（约 1 秒）

            jieba.setLogLevel(logging.WARNING)
            t = jieba.Tokenizer()
            t.initialize()
            _tokenizer = t
        return _tokenizer


def strip_markup(text: str) -> str:
    """去掉页面里的机器标记（引用、链接、占位、段落标记），只留给人读的文字"""
    return _MARKUP.sub("", text)


def normalize(text: str) -> str:
    return unicodedata.normalize("NFKC", strip_markup(text)).lower()


def tokenize(text: str) -> list[str]:
    """分词：返回词序列（保留重复，供词频计算）。同一输入逐次结果相同"""
    t = normalize(text)
    if not t.strip():
        return []
    out: list[str] = []
    for w in _jieba().lcut_for_search(t, HMM=False):
        w = w.strip()
        if w and w not in STOPWORDS and _WORDY.search(w):
            out.append(w)
    return out
