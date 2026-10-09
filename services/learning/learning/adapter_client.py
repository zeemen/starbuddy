"""调用学科适配器（契约 servers：`<base>/plugin/v0/<method>`）。

只在连接失败，或适配器返回 retryable: true 时，用同一个 idempotency_key 重试一次（spec 范围第 3 条）。
适配器的 Problem.detail 不往上转发：只转编码。
"""
from __future__ import annotations

import asyncio
import json
import re

import httpx

from .config import AdapterTarget
from .errors import IngestError


CODE_RE = re.compile(r"[A-Z][A-Z0-9_]{0,47}")
MAX_RESPONSE_BYTES = 5 * 1024 * 1024


def _reject_constant(name: str):
    raise ValueError(f"non-finite number {name}")


def strict_json(raw: bytes) -> object:
    """拒绝 NaN / Infinity：它们能穿过 schema 的 minimum/maximum，写进 numeric 列（安全审查 S4）。"""
    return json.loads(raw, parse_constant=_reject_constant)


async def _post(client: httpx.AsyncClient, url: str, body: dict, headers: dict, timeout: float) -> tuple[int, bytes]:
    """单次调用，响应体限 5 MB（安全审查 S6）。"""
    async with client.stream("POST", url, json=body, headers=headers, timeout=timeout) as r:
        chunks, size = [], 0
        async for chunk in r.aiter_bytes():
            size += len(chunk)
            if size > MAX_RESPONSE_BYTES:
                raise IngestError(502, "ADAPTER_RESULT_REJECTED", "adapter response too large")
            chunks.append(chunk)
        return r.status_code, b"".join(chunks)


async def call_adapter(client: httpx.AsyncClient, target: AdapterTarget, method: str, body: dict, timeout: float) -> dict:
    """timeout 是单次调用的上限；整个调用（含一次重试）的总预算是 2 × timeout + 1 秒，用 asyncio.timeout 兜住，
    因为 httpx 的 timeout 只管单次读写，适配器每隔一会儿送一点数据就能一直拖着（安全审查 S6）。"""
    try:
        async with asyncio.timeout(2 * timeout + 1):
            return await _call(client, target, method, body, timeout)
    except TimeoutError:
        raise IngestError(504, "UPSTREAM_TIMEOUT", "adapter timed out", retryable=True) from None


async def _call(client: httpx.AsyncClient, target: AdapterTarget, method: str, body: dict, timeout: float) -> dict:
    url = f"{target.base_url}/plugin/v0/{method}"
    headers = {"authorization": f"Bearer {target.credential}", "content-type": "application/json"}
    last: IngestError | None = None
    for _ in range(2):
        try:
            status, raw = await _post(client, url, body, headers, timeout)
        except httpx.TimeoutException:
            raise IngestError(504, "UPSTREAM_TIMEOUT", "adapter timed out", retryable=True) from None
        except httpx.TransportError:
            last = IngestError(503, "UPSTREAM_UNAVAILABLE", "adapter unavailable", retryable=True)
            continue
        if status == 200:
            try:
                data = strict_json(raw)
            except ValueError:
                raise IngestError(502, "ADAPTER_RESULT_REJECTED", "adapter returned invalid JSON") from None
            if not isinstance(data, dict):
                raise IngestError(502, "ADAPTER_RESULT_REJECTED", "adapter returned a non-object body")
            return data
        if status == 202:
            raise IngestError(501, "ASYNC_NOT_SUPPORTED", "asynchronous adapter jobs are not supported in sprint 1")
        try:
            problem = strict_json(raw)
        except ValueError:
            problem = {}
        code = problem.get("code") if isinstance(problem, dict) else None
        retryable = problem.get("retryable") is True if isinstance(problem, dict) else False
        # 只转发编码形状的 code（安全审查 B1：isupper() 会放过“张三A”这类文本）；detail 一律不转发
        upstream_code = code if isinstance(code, str) and CODE_RE.fullmatch(code) else None
        # 适配器的 401/403 是学习服务与适配器之间的问题，不能让 BFF 误以为是它自己的令牌：统一 502
        client_error = 400 <= status < 500 and status not in (401, 403, 407)
        err = IngestError(
            status if client_error else 502,
            "UPSTREAM_REJECTED" if client_error else "UPSTREAM_UNAVAILABLE",
            "adapter rejected the request" if client_error else "adapter failed",
            retryable=retryable,
            upstream_code=upstream_code,
            upstream_status=status,
        )
        if not retryable:
            raise err
        last = err
    assert last is not None
    raise last
