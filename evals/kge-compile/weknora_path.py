"""对照评测的第三条路径：WeKnora 检索（技术验证计划 V12）。

做法：
1. 导入：把同一份公共层语料的每个切块（已发布页面段落 + 已发布原子，与切块路径同一套）作为一篇手工录入的知识写进
   **测试知识库**，标题是 `sbeval-<run_id>-<切块键>`，打上本次运行专用的标签；
2. 检索：只按这个标签过滤（不会混进测试库里的其他内容），取前 top_k 个结果；
3. 换回来源：按标题里的切块键找回原来的来源（页面 ID + 版本 + 段落，或原子键），这样“同题讲法一致性”与另两条路径同口径；
4. 结束时删除本次导入的文档与标签（--weknora-keep 时保留）。

只对 WEKNORA_TARGET=test 的测试库运行（客户端在 tools/weknora-probe，只用标准库）；查询文本只在内存里用。
真实结论要等 WeKnora 部署到集群、教研金标到位后再跑，结论由人定（验证计划第四节 V12）。
"""
from __future__ import annotations

import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

HERE = Path(__file__).resolve().parent
PROBE = HERE.parents[1] / "tools" / "weknora-probe"
if str(PROBE) not in sys.path:
    sys.path.insert(0, str(PROBE))

from knowledge.retrieval.pipeline import DEFAULT_MAX_CHARS, DEFAULT_TOP_K, ContextPack, Corpus, Segment  # noqa: E402
from weknora_probe.client import WeKnoraClient, WeKnoraError, wait_until  # noqa: E402

PATH = "weknora"
TITLE_PREFIX = "sbeval-"
DONE = {"completed", "done", "success", "finished", "indexed"}


@dataclass
class WeKnoraCorpus:
    """导入到测试库的一份语料：知识 ID ↔ 切块键"""
    client: WeKnoraClient
    kb_id: str
    corpus: Corpus
    run_id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    tag_id: str | None = None
    by_knowledge: dict[str, str] = field(default_factory=dict)
    by_title: dict[str, str] = field(default_factory=dict)

    @property
    def prefix(self) -> str:
        return f"{TITLE_PREFIX}{self.run_id}-"

    def ingest(self, *, index_timeout: float = 600.0, poll: float = 5.0,
               sleep: Callable[[float], None] | None = None) -> bool:
        """导入全部切块；返回是否在超时内全部建好索引"""
        self.tag_id = self.client.create_tag(self.kb_id, self.prefix + "run")
        for c in self.corpus.chunks:
            title = self.prefix + c.key
            kid = self.client.create_manual(self.kb_id, title, c.text, tag_ids=[self.tag_id])
            self.by_knowledge[kid] = c.key
            self.by_title[title] = c.key
        kw = {"sleep": sleep} if sleep else {}
        return wait_until(self._indexed, timeout=index_timeout, interval=poll, **kw)

    def _indexed(self) -> bool:
        items = self.client.list_knowledge(self.kb_id, tag_ids=[self.tag_id] if self.tag_id else ())
        mine = [x for x in items if str(x.get("id") or x.get("knowledge_id")) in self.by_knowledge]
        if len(mine) < len(self.by_knowledge):
            return False
        return all(str(x.get("parse_status", "completed")).lower() in DONE for x in mine)

    def chunk_key(self, knowledge_id: str | None, title: str | None) -> str | None:
        if knowledge_id and knowledge_id in self.by_knowledge:
            return self.by_knowledge[knowledge_id]
        if title and title in self.by_title:
            return self.by_title[title]
        return None

    def cleanup(self) -> int:
        failed = 0
        for kid in list(self.by_knowledge):
            try:
                self.client.delete_knowledge(kid)
            except WeKnoraError as e:
                failed += e.status != 404
        if self.tag_id:
            try:
                self.client.delete_tag(self.kb_id, self.tag_id)
            except WeKnoraError as e:
                failed += e.status != 404
        self.by_knowledge.clear()
        self.by_title.clear()
        return failed


class WeKnoraRetriever:
    path = PATH

    def __init__(self, wc: WeKnoraCorpus, top_k: int = DEFAULT_TOP_K) -> None:
        self.wc, self.top_k = wc, top_k
        self.unmapped = 0     # 检索结果里认不出来源的条数（不应出现：只按本次运行的标签检索）

    def lookup(self, text: str, *, max_chars: int = DEFAULT_MAX_CHARS, **_: object) -> ContextPack:
        r = self.wc.client.knowledge_search(text, kb_ids=[self.wc.kb_id], tag_ids=[self.wc.tag_id] if self.wc.tag_id else (),
                                            match_count=self.top_k)
        segs: list[Segment] = []
        seen: set[str] = set()
        used = 0
        node_id, top = None, 0.0
        for h in r.hits:
            key = self.wc.chunk_key(h.knowledge_id, h.title)
            chunk = self.wc.corpus.by_key.get(key) if key else None
            if chunk is None:
                self.unmapped += 1
                continue
            if not segs:
                node_id, top = chunk.node_id, h.score or 0.0
            if chunk.source.key in seen or used + len(h.content) > max_chars:
                continue
            seen.add(chunk.source.key)
            segs.append(Segment(h.content, chunk.source, PATH))
            used += len(h.content)
        corpus = self.wc.corpus
        return ContextPack(PATH, node_id, top, tuple(segs), snapshot_commit=corpus.pages.commit if corpus.pages else None,
                           map_version=corpus.atoms.map_version, steps=("weknora_search",))
