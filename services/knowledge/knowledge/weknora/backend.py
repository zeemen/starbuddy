"""WeKnora 后端（KGE-12 C）：相似题（item.similar 的 WeKnora 实现）与标准知识检索（契约 v0.10 候选版的 standard.search）。

数据流（ADR 16 第 3.4 节）：WeKnora 文档 ID →（交付清单）中台内容 ID →（交付清单的 item_ref）StarBuddy 题目引用；
题目再由 tools.py 经 kge.item_tag 核对“已发布版本、打标审核通过”（第二道防线，与 SQL 实现同一组条件），WeKnora 只给候选与排序。

- 先过滤后检索：过滤由 filters.py 的策略落到 WeKnora（允许清单），检索结果再核对一遍是否在允许集合里，不在的丢掉并计数；
- 查询文本（standard.search 的 query 可能来自学生；相似题的查询是题目本身的正文）只在内存里用：不落库、不进日志、不进异常消息
  （ADR 16 第 3.5 节）。WeKnora 侧是否留日志由技术验证 V17 核对；
- WeKnora 的命中正文是讲解与检索素材，不是判分依据（规则 3）：命中里不带答案、采分点类字段；
- WeKnora 出错一律转成 UpstreamError（只带是否超时与状态码）。

standard.search 在 v0.10 签字、知识服务切到 v0.10 之前不对外暴露（服务端仍按 v0.9 的工具清单建路由）；
这里先实现，测试按 v0.10 的 schema 校验它的输出。
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Mapping

from . import STANDARD_MODEL, UpstreamError
from .client import WeKnoraClient, WeKnoraError
from .filters import AllowlistStrategy, FilterStrategy, FilterTooBroad, SearchPlan
from .manifest import DeliveryManifest, Entry

MAX_QUERY_CHARS = 500     # 与契约 StandardSearchRequest.query 的上限一致；相似题的查询也截到这么长
MAX_TEXT_CHARS = 4000     # 契约 StandardHit.text 的上限
MAX_MATCH = 50            # 一次向 WeKnora 要的切块数上限
OVERFETCH = 3             # 同一文档的多个切块会合并成一条命中，按 top_k 的几倍去要


@dataclass(frozen=True)
class Similar:
    """相似题的结果：refs 为 None 表示这道题没交付进 WeKnora（或取不到正文），调用方走 SQL；reason 进访问日志"""
    refs: list[str] | None
    reason: str
    dropped: int = 0


def _score(v: float | None) -> float:
    if v is None or v != v:  # None 或 NaN
        return 0.0
    return round(min(1.0, max(0.0, float(v))), 6)


class WeKnoraBackend:
    def __init__(self, client: WeKnoraClient, manifest: DeliveryManifest, strategy: FilterStrategy | None = None) -> None:
        self.client = client
        self.manifest = manifest
        self.strategy = strategy or AllowlistStrategy(manifest)

    # ---------- 底层：同步客户端放到线程里跑；错误只留编码化原因 ----------
    async def _run(self, fn, *args, **kw):
        try:
            return await asyncio.to_thread(fn, *args, **kw)
        except WeKnoraError as e:
            raise UpstreamError(timeout="Timeout" in str(e) or "timeout" in str(e), status=e.status) from None

    async def _search(self, query: str, plan: SearchPlan, match_count: int):
        return await self._run(self.client.knowledge_search, query[:MAX_QUERY_CHARS], kb_ids=plan.kb_ids,
                               knowledge_ids=plan.knowledge_ids, tag_ids=plan.tag_ids, match_count=match_count)

    def _admitted(self, plan: SearchPlan, hits) -> tuple[list[tuple[Entry, Any]], int]:
        """按命中顺序，留下在允许集合里的文档（同一文档只留分数最高的第一条切块）；返回（条目与命中，丢掉的个数）"""
        out: list[tuple[Entry, Any]] = []
        seen: set[str] = set()
        dropped = 0
        for h in hits:
            e = self.strategy.admits(plan, h.knowledge_id)
            if e is None:
                dropped += 1
                continue
            if e.weknora_id in seen:
                continue
            seen.add(e.weknora_id)
            out.append((e, h))
        return out, dropped

    # ---------- 相似题 ----------
    async def similar_items(self, item_ref: str, limit: int) -> Similar:
        me = self.manifest.by_item_ref.get(item_ref)
        if me is None:
            return Similar(None, "not_delivered")
        doc = await self._run(self.client.get_knowledge, me.weknora_id)
        text = doc.get("content") if isinstance(doc, dict) else None
        if not isinstance(text, str) or not text.strip():
            return Similar(None, "no_content")
        try:
            plan = self.strategy.plan({"subject": me.subject}, ["item"], exclude=[me.weknora_id])
        except FilterTooBroad:  # 一个学科交付的题多于允许清单上限：允许清单这种策略做不了，走 SQL（lessons 待人确认第 11 条）
            return Similar(None, "too_broad")
        if plan.empty:
            return Similar([], "weknora")
        res = await self._search(text.strip(), plan, min(MAX_MATCH, max(limit, 1) * OVERFETCH * 2))
        admitted, dropped = self._admitted(plan, res.hits)
        refs = [e.item_ref for e, _ in admitted if e.item_ref is not None]  # 自己已在 plan 里排除
        return Similar(refs, "weknora", dropped)

    # ---------- 标准知识检索（契约 v0.10 候选版 standard.search） ----------
    async def standard_search(self, body: Mapping[str, Any]) -> tuple[dict, dict]:
        """body 已按 v0.10 的 StandardSearchRequest 校验过。返回（result, meta）：meta 同 tools.py 的约定
        （source_version、confidence、model_version，另有 dropped：被丢掉的允许集合外命中数，只进访问日志）"""
        top_k = int(body.get("top_k", 5))
        plan = self.strategy.plan(body["filters"], list(body["content_types"]))
        hits: list[dict[str, Any]] = []
        dropped = 0
        if not plan.empty:
            res = await self._search(body["query"], plan, min(MAX_MATCH, top_k * OVERFETCH))
            admitted, dropped = self._admitted(plan, res.hits)
            for e, h in admitted:
                text = (h.content or "").strip()[:MAX_TEXT_CHARS]
                if not text:
                    continue
                hit: dict[str, Any] = {"content_id": e.content_id, "content_version": e.content_version, "weknora_id": e.weknora_id,
                                       "content_type": e.content_type, "score": _score(h.score), "text": text, "source": dict(e.source)}
                if e.node_ids:
                    hit["node_ids"] = list(e.node_ids)
                if e.item_ref is not None:
                    hit["item_ref"] = e.item_ref
                if e.page_ref is not None:
                    hit["page_ref"] = dict(e.page_ref)
                hits.append(hit)
                if len(hits) >= top_k:
                    break
        meta = {"source_version": self.manifest.source_version, "confidence": hits[0]["score"] if hits else 0,
                "model_version": STANDARD_MODEL, "dropped": dropped}
        return {"hits": hits}, meta
