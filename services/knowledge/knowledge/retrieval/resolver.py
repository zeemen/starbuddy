"""node.resolve 的全文检索（PR ③）：经 PR ② 的 `Resolver` 接口接入，契约不变。

FullTextResolver：对当次传入的节点（已发布版本的有效节点，由调用方从 kge.* 只读取得）建 BM25 索引，
节点文档 = 名称 + 包括 + 例子（不含“不包括”：写在不包括里的词不该把问题引到这个节点）。
    排序：BM25 分数降序，同分按节点 ID；
    置信度 = 0.6 × 按 idf 加权的查询词覆盖率（覆盖率低于 0.34 不算命中）；
    全文检索单独给出的置信度不超过 0.6——只有覆盖率 ≥ 0.834 才越过复核线（LOW_CONFIDENCE = 0.5）。
CombinedResolver：规则解析（节点 ID 直引、名称整体出现）与全文检索取并集，每个节点取两者的较高置信度；
    低置信（< LOW_CONFIDENCE）仍只标复核（tools.py 不变）。
输入文本只在内存里用完即弃：不落库、不写日志、不进异常消息（规则 13）。
"""
from __future__ import annotations

import hashlib
import json
import threading
from typing import Any, Iterable, Mapping, Sequence

from ..service.resolve import Candidate, Resolver, RuleResolver, _SYNTHETIC_PREFIX
from .bm25 import BM25
from .tokenize import TOKENIZER_VERSION, tokenize

FULLTEXT_WEIGHT = 0.6
MIN_COVERAGE = 0.34
RESOLVER_VERSION = "algo:kge-node-resolve@0.2.0"  # 0.1.0 = 只有规则；0.2.0 = 规则 + 全文（jieba + BM25）


def node_document(n: Mapping[str, Any]) -> str:
    parts = [_SYNTHETIC_PREFIX.sub("", str(n["name"]))]
    parts += [_SYNTHETIC_PREFIX.sub("", str(x)) for x in (n.get("includes") or [])]
    parts += [str(x) for x in (n.get("examples") or [])]
    return "\n".join(parts)


class FullTextResolver:
    version = RESOLVER_VERSION

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cache: tuple[str, BM25[str], dict[str, tuple[str, ...]], dict[str, Mapping[str, Any]]] | None = None

    def _index(self, nodes: Sequence[Mapping[str, Any]]):
        usable = sorted((n for n in nodes if n["level"] in ("node", "detail")), key=lambda n: str(n["id"]))
        sig = hashlib.sha256(json.dumps([[str(n["id"]), node_document(n)] for n in usable] + [TOKENIZER_VERSION],
                                        ensure_ascii=False).encode("utf-8")).hexdigest()
        with self._lock:
            if self._cache is not None and self._cache[0] == sig:
                return self._cache[1:]
        docs = {str(n["id"]): tuple(tokenize(node_document(n))) for n in usable}
        index = BM25(docs.items())
        by_id = {str(n["id"]): n for n in usable}
        with self._lock:
            self._cache = (sig, index, docs, by_id)
        return index, docs, by_id

    def resolve(self, text: str, nodes: Iterable[Mapping[str, Any]], max_candidates: int) -> list[Candidate]:
        terms = tokenize(text)
        if not terms or max_candidates <= 0:
            return []
        index, docs, by_id = self._index(list(nodes))
        out: list[Candidate] = []
        for hit in index.search(terms, top_k=len(index.keys)):
            cov = index.coverage(terms, docs[hit.key])
            if cov < MIN_COVERAGE:
                continue
            n = by_id[hit.key]
            out.append(Candidate(hit.key, str(n["level"]), n.get("kind"), round(FULLTEXT_WEIGHT * cov, 3)))
        # 输出按 BM25 分数排（search 已排好），截断前不改顺序
        return out[:max_candidates]


class CombinedResolver:
    """多个解析器取并集，每个节点取最高置信度；按置信度降序、节点 ID 升序"""
    version = RESOLVER_VERSION

    def __init__(self, resolvers: Sequence[Resolver] | None = None) -> None:
        self.resolvers: tuple[Resolver, ...] = tuple(resolvers) if resolvers else (RuleResolver(), FullTextResolver())

    def resolve(self, text: str, nodes: Iterable[Mapping[str, Any]], max_candidates: int) -> list[Candidate]:
        pool = list(nodes)
        best: dict[str, Candidate] = {}
        for r in self.resolvers:
            for c in r.resolve(text, pool, max(len(pool), max_candidates)):
                if c.node_id not in best or c.confidence > best[c.node_id].confidence:
                    best[c.node_id] = c
        ranked = sorted(best.values(), key=lambda c: (-c.confidence, c.node_id))
        return ranked[:max_candidates]
