"""进程内 BM25（spec 待人确认 #5 的首选实现：不依赖 Postgres 扩展，便于确定性测试）。

打分（Okapi BM25，idf 取 Lucene 的非负写法）：
    idf(t)      = ln(1 + (N − df(t) + 0.5) / (df(t) + 0.5))
    score(d, q) = Σ_{t ∈ set(q)} idf(t) · tf(t, d) · (k1 + 1) / (tf(t, d) + k1 · (1 − b + b · |d| / avgdl))
查询词去重后求和（同一个词在问题里说两遍不加倍）。k1 = 1.2，b = 0.75。
分数四舍五入到 6 位小数后排序：分数降序，同分按文档键升序——结果与输入顺序无关、逐次相同。
"""
from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass
from typing import Callable, Generic, Hashable, Iterable, Sequence, TypeVar

K1 = 1.2
B = 0.75
ROUND = 6

K = TypeVar("K", bound=Hashable)


@dataclass(frozen=True)
class Hit(Generic[K]):
    key: K
    score: float


class BM25(Generic[K]):
    def __init__(self, docs: Iterable[tuple[K, Sequence[str]]], *, k1: float = K1, b: float = B) -> None:
        self.k1, self.b = k1, b
        self.keys: list[K] = []
        self.tf: list[Counter[str]] = []
        self.length: list[int] = []
        seen: set[K] = set()
        for key, tokens in sorted(docs, key=lambda kv: str(kv[0])):
            if key in seen:
                raise ValueError("BM25 文档键重复")
            seen.add(key)
            self.keys.append(key)
            self.tf.append(Counter(tokens))
            self.length.append(len(tokens))
        self.n = len(self.keys)
        self.avgdl = (sum(self.length) / self.n) if self.n else 0.0
        df: Counter[str] = Counter()
        for c in self.tf:
            df.update(c.keys())
        self.df = df

    def idf(self, term: str) -> float:
        d = self.df.get(term, 0)
        return math.log(1 + (self.n - d + 0.5) / (d + 0.5))

    def score(self, i: int, terms: Iterable[str]) -> float:
        tf, dl = self.tf[i], self.length[i]
        s = 0.0
        for t in sorted(set(terms)):
            f = tf.get(t, 0)
            if f == 0:
                continue
            norm = self.k1 * (1 - self.b + self.b * dl / self.avgdl) if self.avgdl else self.k1
            s += self.idf(t) * f * (self.k1 + 1) / (f + norm)
        return round(s, ROUND)

    def search(self, terms: Sequence[str], top_k: int, *, where: Callable[[K], bool] | None = None) -> list[Hit[K]]:
        if top_k <= 0 or not terms:
            return []
        hits = []
        for i, key in enumerate(self.keys):
            if where is not None and not where(key):
                continue
            s = self.score(i, terms)
            if s > 0:
                hits.append(Hit(key, s))
        hits.sort(key=lambda h: (-h.score, str(h.key)))
        return hits[:top_k]

    def coverage(self, terms: Sequence[str], doc_terms: Iterable[str]) -> float:
        """按 idf 加权的查询词覆盖率：Σ idf(命中的查询词) / Σ idf(全部查询词)；没见过的词按 df = 0 计 idf"""
        q = sorted(set(terms))
        if not q:
            return 0.0
        have = set(doc_terms)
        total = sum(self.idf(t) for t in q)
        got = sum(self.idf(t) for t in q if t in have)
        return round(got / total, ROUND) if total else 0.0
