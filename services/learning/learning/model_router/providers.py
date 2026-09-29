"""供应商适配器（spec 设计要点 1、待人确认 #4）：只有“OpenAI 兼容协议”一种通用适配器，外加测试用的进程内假供应商。

通用适配器是代码，不是具体供应商：地址与 Key 从路由表登记的环境变量名读取，仓库里没有任何值。
错误只带类别，不带供应商返回的正文（可能回显请求内容）。
"""
from __future__ import annotations

import os
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from .config import Target, Vendor
from .types import ConfigError, Usage

RETRYABLE = frozenset({"timeout", "rate_limited", "server"})
# 计入熔断的失败：供应商侧问题；client（请求被供应商判为不合法）不计入熔断，也不重试，直接换下一家
BREAKER_FAILURES = frozenset({"timeout", "rate_limited", "server", "auth", "bad_response"})


class ProviderError(Exception):
    def __init__(self, kind: str) -> None:
        super().__init__(kind)
        self.kind = kind

    @property
    def retryable(self) -> bool:
        return self.kind in RETRYABLE


@dataclass(frozen=True)
class ProviderReply:
    output: str
    usage: Usage
    reported_model: str | None


class Provider(Protocol):
    async def complete(self, target: Target, messages: Sequence[Mapping[str, Any]], *, idempotency_key: str,
                       timeout: float) -> ProviderReply: ...

    async def aclose(self) -> None: ...


class OpenAICompatProvider:
    """POST {base}/chat/completions；Idempotency-Key 头随每次重试原样带上"""

    def __init__(self, vendor: Vendor, environ: Mapping[str, str] | None = None,
                 transport: httpx.AsyncBaseTransport | None = None) -> None:
        env = os.environ if environ is None else environ
        base = env.get(vendor.base_url_env, "")
        if not base.startswith(("http://", "https://")):
            raise ConfigError(f"供应商 {vendor.vendor_id}: 环境变量 {vendor.base_url_env} 没有设置合法地址")
        key = env.get(vendor.api_key_env, "") if vendor.api_key_env else ""
        if vendor.api_key_env and not key:
            raise ConfigError(f"供应商 {vendor.vendor_id}: 环境变量 {vendor.api_key_env} 没有设置")
        headers = {"Authorization": f"Bearer {key}"} if key else {}
        self._client = httpx.AsyncClient(base_url=base.rstrip("/"), headers=headers, transport=transport,
                                         follow_redirects=False, trust_env=False)

    async def complete(self, target: Target, messages: Sequence[Mapping[str, Any]], *, idempotency_key: str,
                       timeout: float) -> ProviderReply:
        body = {"model": target.api_name, "messages": list(messages), **target.params}
        try:
            r = await self._client.post("/chat/completions", json=body, headers={"Idempotency-Key": idempotency_key}, timeout=timeout)
        except httpx.TimeoutException:
            raise ProviderError("timeout") from None
        except httpx.HTTPError:
            raise ProviderError("server") from None
        if r.status_code == 429:
            raise ProviderError("rate_limited")
        if r.status_code in (401, 403):
            raise ProviderError("auth")
        if r.status_code >= 500:
            raise ProviderError("server")
        if r.status_code != 200:
            raise ProviderError("client")
        try:
            data = r.json()
            content = data["choices"][0]["message"]["content"]
            if not isinstance(content, str):
                raise TypeError
            u = data.get("usage") or {}
            usage = Usage(int(u.get("prompt_tokens", 0)), int(u.get("completion_tokens", 0)))
            reported = data.get("model")
        except (ValueError, KeyError, IndexError, TypeError):
            raise ProviderError("bad_response") from None
        return ProviderReply(output=content, usage=usage, reported_model=reported if isinstance(reported, str) else None)

    async def aclose(self) -> None:
        await self._client.aclose()


Behavior = Callable[[Target, Sequence[Mapping[str, Any]], str], Awaitable[ProviderReply]]


class FakeProvider:
    """进程内假供应商（只用于 dev、test）：行为由测试注入；默认原样返回固定文本"""

    def __init__(self, vendor: Vendor, behavior: Behavior | None = None) -> None:
        self.vendor = vendor
        self.calls: list[tuple[str, str]] = []  # (api_name, idempotency_key)
        self._behavior = behavior

    async def complete(self, target: Target, messages: Sequence[Mapping[str, Any]], *, idempotency_key: str,
                       timeout: float) -> ProviderReply:
        self.calls.append((target.api_name, idempotency_key))
        if self._behavior is not None:
            return await self._behavior(target, messages, idempotency_key)
        return ProviderReply(output=f"fake:{self.vendor.vendor_id}", usage=Usage(1, 1), reported_model=target.api_name)

    async def aclose(self) -> None:
        return None


def build_provider(vendor: Vendor, environ: Mapping[str, str] | None = None) -> Provider:
    if vendor.protocol == "openai_compat":
        return OpenAICompatProvider(vendor, environ)
    if vendor.protocol == "fake":
        return FakeProvider(vendor)
    raise ConfigError(f"供应商 {vendor.vendor_id}: 没有 {vendor.protocol} 协议的适配器")
