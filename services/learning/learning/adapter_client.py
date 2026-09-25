"""调用学科适配器（契约 servers：`<base>/plugin/v0/<method>`）。

只在连接失败，或适配器返回 retryable: true 时，用同一个 idempotency_key 重试一次（spec 范围第 3 条）。
适配器的 Problem.detail 不往上转发：只转编码。
"""
from __future__ import annotations

import httpx

from .config import AdapterTarget
from .errors import IngestError


async def call_adapter(client: httpx.AsyncClient, target: AdapterTarget, method: str, body: dict, timeout: float) -> dict:
    url = f"{target.base_url}/plugin/v0/{method}"
    headers = {"authorization": f"Bearer {target.credential}", "content-type": "application/json"}
    last: IngestError | None = None
    for _ in range(2):
        try:
            r = await client.post(url, json=body, headers=headers, timeout=timeout)
        except httpx.TimeoutException:
            raise IngestError(504, "UPSTREAM_TIMEOUT", "adapter timed out", retryable=True) from None
        except httpx.TransportError:
            last = IngestError(503, "UPSTREAM_UNAVAILABLE", "adapter unavailable", retryable=True)
            continue
        if r.status_code == 200:
            try:
                data = r.json()
            except ValueError:
                raise IngestError(502, "ADAPTER_RESULT_REJECTED", "adapter returned invalid JSON") from None
            if not isinstance(data, dict):
                raise IngestError(502, "ADAPTER_RESULT_REJECTED", "adapter returned a non-object body")
            return data
        if r.status_code == 202:
            raise IngestError(501, "ASYNC_NOT_SUPPORTED", "asynchronous adapter jobs are not supported in sprint 1")
        try:
            problem = r.json()
        except ValueError:
            problem = {}
        code = problem.get("code") if isinstance(problem, dict) else None
        retryable = bool(problem.get("retryable")) if isinstance(problem, dict) else False
        err = IngestError(
            r.status_code if 400 <= r.status_code < 500 else 502,
            "UPSTREAM_REJECTED" if 400 <= r.status_code < 500 else "UPSTREAM_UNAVAILABLE",
            "adapter rejected the request" if 400 <= r.status_code < 500 else "adapter failed",
            retryable=retryable,
            upstream_code=code if isinstance(code, str) and code.isupper() and len(code) <= 48 else None,
        )
        if not retryable:
            raise err
        last = err
    assert last is not None
    raise last
