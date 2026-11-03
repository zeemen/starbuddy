"""查阅路径（spec 设计要点 6）：定位节点 → 读已发布页面 → 按需查原子 → 兜底检索 → 带引用的上下文包。

PageFirstRetriever（主路径）：
  1. 定位：调用方给了节点 ID 就用它（置信度 1.0）；否则经 `Resolver`（缺省规则 + 全文）在已发布地图的有效节点里解析；
  2. 读页面：该节点在已发布页面快照里有页面 → 取 PAGE_SECTIONS 各段（每段一条，来源 = 页面 ID + page_version + 段落）；
  3. 按需查原子：节点没有页面 → 取节点原子（名称、包括、不包括）与承接的课标条目（来源 = 原子键 + 原子哈希）；
  4. 兜底：没定位到节点、定位置信度低于 LOW_CONFIDENCE、节点没有页面、或页面对查询词的覆盖率低于 PAGE_COVERAGE_MIN 时，
     在公共层切块（chunks.py）上做 BM25，取前 FALLBACK_TOP_K 块（已在包里的来源不重复），并发出补页面信号（signals.py）；
  5. 上下文包：段落按上面的顺序排列，总字数不超过 max_chars（超出的段整段丢弃，不截半段）。
ChunkRetriever（对照路径，也是 M1 未达标时的主路径）：只在同一套公共层切块上做 BM25，取前 top_k 块。

两条路径都只读公共层：页面快照 + 已发布原子；没有任何学生参数。查询文本只在内存里用。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from ..atoms import Snapshot
from ..service.pages import PageSnapshot
from ..service.resolve import LOW_CONFIDENCE, Resolver
from .bm25 import BM25
from .chunks import Chunk, Source, active_nodes, build_chunks, clean, definition_text
from .resolver import CombinedResolver
from .signals import GapSignal, MemorySink, QueryDigester, SignalSink
from .tokenize import tokenize

PAGE_SECTIONS = ("definition", "curriculum", "key_points", "pitfalls", "frequent_questions")
PAGE_COVERAGE_MIN = 0.5
FALLBACK_TOP_K = 3
DEFAULT_TOP_K = 5
DEFAULT_MAX_CHARS = 4000
RETRIEVAL_VERSION = "algo:kge-retrieval@0.1.0"


@dataclass(frozen=True)
class Segment:
    text: str
    source: Source
    via: str  # page | atom | fallback | chunk


@dataclass(frozen=True)
class ContextPack:
    path: str                              # page_first | chunk
    node_id: str | None
    confidence: float
    segments: tuple[Segment, ...]
    fallback_reason: str | None = None
    snapshot_commit: str | None = None
    map_version: str | None = None
    steps: tuple[str, ...] = field(default=())

    @property
    def text(self) -> str:
        return "\n\n".join(s.text for s in self.segments)

    @property
    def source_keys(self) -> tuple[str, ...]:
        return tuple(s.source.key for s in self.segments)

    def to_dict(self) -> dict[str, Any]:
        return {"path": self.path, "node_id": self.node_id, "confidence": self.confidence, "fallback_reason": self.fallback_reason,
                "snapshot_commit": self.snapshot_commit, "map_version": self.map_version, "steps": list(self.steps),
                "retrieval_version": RETRIEVAL_VERSION,
                "segments": [{"text": s.text, "via": s.via, "source": s.source.to_dict()} for s in self.segments]}


class Corpus:
    """公共层语料：已发布页面快照 + 已发布原子 + 两者切块上的 BM25 索引（建一次，多次查）"""

    def __init__(self, pages: PageSnapshot | None, atoms: Snapshot) -> None:
        self.chunks: list[Chunk] = build_chunks(pages, atoms)
        self.pages = pages
        self.atoms = atoms
        self.nodes = active_nodes(atoms)
        self.by_key = {c.key: c for c in self.chunks}
        self.index: BM25[str] = BM25((c.key, c.tokens) for c in self.chunks)
        self.page_of = {pp.node_id: pp for pp in (pages.pages.values() if pages else []) if pp.node_id in self.nodes}

    def search(self, terms: Sequence[str], top_k: int, exclude: set[str] | None = None) -> list[tuple[Chunk, float]]:
        ex = exclude or set()
        hits = self.index.search(terms, top_k, where=lambda k: self.by_key[k].source.key not in ex)
        return [(self.by_key[h.key], h.score) for h in hits]


def _fit(segments: list[Segment], max_chars: int) -> tuple[Segment, ...]:
    out, used = [], 0
    for s in segments:
        if used + len(s.text) > max_chars:
            continue
        out.append(s)
        used += len(s.text)
    return tuple(out)


class ChunkRetriever:
    path = "chunk"

    def __init__(self, corpus: Corpus, top_k: int = DEFAULT_TOP_K) -> None:
        self.corpus, self.top_k = corpus, top_k

    def lookup(self, text: str, *, max_chars: int = DEFAULT_MAX_CHARS) -> ContextPack:
        hits = self.corpus.search(tokenize(text), self.top_k)
        segs = [Segment(c.text, c.source, "chunk") for c, _ in hits]
        top = hits[0][1] if hits else 0.0
        return ContextPack("chunk", hits[0][0].node_id if hits else None, top, _fit(segs, max_chars),
                           snapshot_commit=self.corpus.pages.commit if self.corpus.pages else None,
                           map_version=self.corpus.atoms.map_version, steps=("chunk_search",))


class PageFirstRetriever:
    path = "page_first"

    def __init__(self, corpus: Corpus, *, resolver: Resolver | None = None, sink: SignalSink | None = None,
                 digester: QueryDigester | None = None) -> None:
        self.corpus = corpus
        self.resolver = resolver or CombinedResolver()
        self.sink = sink if sink is not None else MemorySink()
        self.digester = digester or QueryDigester()

    def _node_list(self) -> list[Mapping[str, Any]]:
        return [self.corpus.nodes[k] for k in sorted(self.corpus.nodes)]

    def _page_segments(self, node_id: str) -> tuple[list[Segment], set[str]]:
        pp = self.corpus.page_of[node_id]
        segs, toks = [], set()
        for sid in PAGE_SECTIONS:
            s = pp.page.section(sid)
            if s is None:
                continue
            text = clean(s.content)
            if not text:
                continue
            segs.append(Segment(text, Source("page", page_id=pp.node_id, page_version=pp.page_version, section=sid), "page"))
            toks.update(tokenize(text))
        return segs, toks

    def _atom_segments(self, node_id: str) -> list[Segment]:
        a = self.corpus.atoms.get("node", node_id)
        if a is None:
            return []
        segs = [Segment(definition_text(dict(a.record)), Source("atom", atom_ref=a.key, atom_hash=a.hash), "atom")]
        for c in self.corpus.atoms.curriculum_of(node_id):
            segs.append(Segment(clean(str(c.record["text"])), Source("atom", atom_ref=c.key, atom_hash=c.hash), "atom"))
        return segs

    def lookup(self, text: str, *, node_id: str | None = None, max_chars: int = DEFAULT_MAX_CHARS) -> ContextPack:
        terms = tokenize(text)
        steps: list[str] = []
        if node_id is not None and node_id in self.corpus.nodes:
            nid, conf = node_id, 1.0
            steps.append("node_given")
        else:
            cands = self.resolver.resolve(text, self._node_list(), 1)
            nid, conf = (cands[0].node_id, cands[0].confidence) if cands else (None, 0.0)
            steps.append("resolve")
        segs: list[Segment] = []
        reason: str | None = None
        if nid is None:
            reason = "no_node"
        elif nid in self.corpus.page_of:
            steps.append("page")
            segs, page_tokens = self._page_segments(nid)
            if conf < LOW_CONFIDENCE:
                reason = "low_confidence"
            elif terms and self.corpus.index.coverage(terms, page_tokens) < PAGE_COVERAGE_MIN:
                reason = "page_low_coverage"
        else:
            steps.append("atoms")
            segs = self._atom_segments(nid)
            reason = "low_confidence" if conf < LOW_CONFIDENCE else "no_page"
        if reason is not None:
            steps.append("fallback")
            have = {s.source.key for s in segs}
            hits = self.corpus.search(terms, FALLBACK_TOP_K, exclude=have)
            segs += [Segment(c.text, c.source, "fallback") for c, _ in hits]
            self.sink.emit(GapSignal(nid, reason, self.digester.digest(text), len(terms),
                                     tuple(c.source.key for c, _ in hits), hits[0][1] if hits else 0.0))
        return ContextPack("page_first", nid, conf, _fit(segs, max_chars), reason,
                           self.corpus.pages.commit if self.corpus.pages else None, self.corpus.atoms.map_version, tuple(steps))
