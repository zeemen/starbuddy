"""向量检索：只留接口，不引入（spec 待人确认 #5）。

候选实现（引入需人确认：新增 Postgres 扩展与本地模型，属必停项“新增第三方服务 / 依赖”）：
pgvector（Postgres 扩展）+ BGE-M3（向量模型）+ bge-reranker（重排）。实现时须满足：
- 只对 chunks.build_chunks 产出的公共层切块建向量（同一份切块、同一套来源），不对任何个人层数据建向量；
- 向量模型在境内部署（规则 13），不外发切块；
- 返回与 Corpus.search 相同形状：[(Chunk, 分数)]，同分按切块键排序，结果确定；
- 与 BM25 的融合方式（例如倒数排名融合）与权重进对照评测再定。
"""
from __future__ import annotations

from typing import Protocol

from .chunks import Chunk


class VectorSearch(Protocol):
    version: str

    def search(self, text: str, top_k: int, exclude: set[str] | None = None) -> list[tuple[Chunk, float]]: ...


class VectorSearchNotConfigured:
    """占位：调用即报错，防止误以为向量检索在工作"""
    version = "none"

    def search(self, text: str, top_k: int, exclude: set[str] | None = None) -> list[tuple[Chunk, float]]:
        raise NotImplementedError("向量检索未引入（spec 待人确认 #5）；首期只用全文检索")
