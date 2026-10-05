"""再练候选的来源：知识服务 item.similar / item.search（packages/contracts/openapi/knowledge-service.v0.9.yaml），调用方身份 learning。

与诊断的知识服务来源同一做法：Bearer 服务间令牌、不跟随重定向、不读代理环境变量（共用学习服务的 httpx 客户端），
响应按契约严格校验（ItemSimilarResponse / ItemSearchResponse），不合格、不可用一律 SourceError（next 回 503，可重试）。
请求发送前也按契约（ItemSimilarRequest / ItemSearchRequest）自检，不合格不发出（RequestMismatch，next 回 500）。
请求不带学生标识（公共层，规则 13）；context.map_version 取当前已发布版本，避免读到另一版的打标。
"""
from __future__ import annotations

import uuid

import httpx

from ..contract import knowledge_contract
from ..diagnosis.source import SourceError
from .queue import Unit
from .select import SEARCH_LIMIT, SIMILAR_LIMIT, Candidate, from_view

SCHEMAS = {"item.similar": "ItemSimilarResponse", "item.search": "ItemSearchResponse"}
REQUESTS = {"item.similar": "ItemSimilarRequest", "item.search": "ItemSearchRequest"}
TIMEOUT = 10.0


class RequestMismatch(Exception):
    """组装的知识服务请求不合契约（本服务的缺陷，不是知识服务不可用）：不发出，next 回 500。"""


class KnowledgeCandidates:
    def __init__(self, client: httpx.AsyncClient, base_url: str, token: str) -> None:
        self._client = client
        self._base = base_url.rstrip("/")
        self._token = token

    async def _call(self, tool: str, body: dict, map_version: str | None) -> dict:
        ctx = {"request_id": str(uuid.uuid4()), "contract_version": "0.9.0", "caller": "learning", "data_class": "L1"}
        if map_version:
            ctx["map_version"] = map_version
        req = {"context": ctx, **body}
        # 发送前按契约自检（审查 S8）：不合契约的请求不发出
        if knowledge_contract().validate(REQUESTS[tool], req):
            raise RequestMismatch(tool)
        try:
            r = await self._client.post(f"{self._base}/v0/{tool}", json=req,
                                        headers={"authorization": f"Bearer {self._token}"}, timeout=TIMEOUT)
        except httpx.HTTPError:
            raise SourceError(f"{tool} unavailable") from None
        if r.status_code != 200:
            raise SourceError(f"{tool} status {r.status_code}")
        try:
            out = r.json()
        except ValueError:
            raise SourceError(f"{tool} malformed") from None
        if knowledge_contract().validate(SCHEMAS[tool], out):
            raise SourceError(f"{tool} response does not match the contract")
        return out["result"]

    async def similar(self, subject: str, item_id: str, map_version: str | None) -> list[Candidate]:
        res = await self._call("item.similar", {"item_ref": f"{subject}:{item_id}", "limit": SIMILAR_LIMIT}, map_version)
        return [c for c in (from_view(subject, x["item"]) for x in res["items"]) if c is not None]

    async def search(self, subject: str, unit: Unit, map_version: str | None) -> list[Candidate]:
        res = await self._call("item.search", {"subject": subject, "node_id": unit.node_id, "cognitive_level": unit.cognitive_level,
                                               "limit": SEARCH_LIMIT}, map_version)
        return [c for c in (from_view(subject, x) for x in res["items"]) if c is not None]
