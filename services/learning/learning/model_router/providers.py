"""供应商适配器（spec 设计要点 1、待人确认 #4）：只有“OpenAI 兼容协议”一种通用适配器，外加测试用的进程内假供应商。

通用适配器是代码，不是具体供应商：地址与 Key 从路由表登记的环境变量名读取，仓库里没有任何值。
构造时核对实际地址（安全审查应改 1、2）：登记了 allowed_hosts 的，主机名必须在里面；dev、test 以外必须是 https。
错误只带类别，不带供应商返回的正文（可能回显请求内容）；响应体有大小上限。
"""
from __future__ import annotations

import json
import os
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import urlsplit

import httpx

from .config import SYNTHETIC_ENVS, Target, Vendor
from .types import ConfigError, Usage

RETRYABLE = frozenset({"timeout", "rate_limited", "server"})
# 计入熔断的失败：供应商侧问题；client（请求被供应商判为不合法）不计入熔断，也不重试，直接换下一家
BREAKER_FAILURES = frozenset({"timeout", "rate_limited", "server", "auth", "bad_response"})
MAX_RESPONSE_BYTES = 1 << 20  # 1 MiB
MAX_RETRY_AFTER = 30.0


class ProviderError(Exception):
    def __init__(self, kind: str, retry_after: float | None = None, usage: Usage | None = None) -> None:
        super().__init__(kind)
        self.kind = kind
        self.retry_after = retry_after
        # 失败时供应商报告的用量（只有 200 且带 usage、但内容不合格式时才有；HTTP 错误响应没有用量，不计 token，只计次数）
        self.usage = usage

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


def check_endpoint(vendor: Vendor, url: str, env: str) -> None:
    """地址核对：只看主机名与协议，错误信息不带地址本身"""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname or parts.username or parts.password:
        raise ConfigError(f"供应商 {vendor.vendor_id}: 环境变量 {vendor.base_url_env} 没有设置合法地址")
    if env not in SYNTHETIC_ENVS and parts.scheme != "https":
        raise ConfigError(f"供应商 {vendor.vendor_id}: {env} 环境只接受 https 地址")
    if vendor.allowed_hosts and parts.hostname.lower() not in vendor.allowed_hosts:
        raise ConfigError(f"供应商 {vendor.vendor_id}: 地址的主机名不在登记的 allowed_hosts 里")


def _retry_after(value: str | None) -> float | None:
    try:
        return max(0.0, min(float(value), MAX_RETRY_AFTER)) if value else None
    except ValueError:
        return None


class OpenAICompatProvider:
    """POST {base}/chat/completions；Idempotency-Key 头随每次重试原样带上"""

    def __init__(self, vendor: Vendor, environ: Mapping[str, str] | None = None, *, env: str = "test",
                 transport: httpx.AsyncBaseTransport | None = None) -> None:
        source = os.environ if environ is None else environ
        base = source.get(vendor.base_url_env, "")
        check_endpoint(vendor, base, env)
        key = source.get(vendor.api_key_env, "") if vendor.api_key_env else ""
        if vendor.api_key_env and not key:
            raise ConfigError(f"供应商 {vendor.vendor_id}: 环境变量 {vendor.api_key_env} 没有设置")
        headers = {"Authorization": f"Bearer {key}"} if key else {}
        self._client = httpx.AsyncClient(base_url=base.rstrip("/"), headers=headers, transport=transport,
                                         follow_redirects=False, trust_env=False)

    async def complete(self, target: Target, messages: Sequence[Mapping[str, Any]], *, idempotency_key: str,
                       timeout: float) -> ProviderReply:
        body = {"model": target.api_name, "messages": list(messages), **target.params}
        try:
            async with self._client.stream("POST", "/chat/completions", json=body, headers={"Idempotency-Key": idempotency_key},
                                           timeout=timeout) as r:
                if r.status_code == 429:
                    raise ProviderError("rate_limited", _retry_after(r.headers.get("retry-after")))
                if r.status_code in (401, 403):
                    raise ProviderError("auth")
                if r.status_code >= 500:
                    raise ProviderError("server", _retry_after(r.headers.get("retry-after")))
                if r.status_code != 200:
                    raise ProviderError("client")
                buf = bytearray()
                async for chunk in r.aiter_bytes():
                    buf += chunk
                    if len(buf) > MAX_RESPONSE_BYTES:
                        raise ProviderError("bad_response")
        except httpx.TimeoutException:
            raise ProviderError("timeout") from None
        except httpx.HTTPError:
            raise ProviderError("server") from None
        try:
            data = json.loads(bytes(buf))
        except ValueError:
            raise ProviderError("bad_response") from None
        try:
            u = data.get("usage") or {}
            usage = Usage(int(u.get("prompt_tokens", 0)), int(u.get("completion_tokens", 0)))
        except (AttributeError, ValueError, TypeError):
            raise ProviderError("bad_response") from None
        try:
            content = data["choices"][0]["message"]["content"]
            if not isinstance(content, str):
                raise TypeError
            reported = data.get("model")
        except (KeyError, IndexError, TypeError):
            # 内容不合格式但供应商报了用量：交给成本护栏累加 token
            raise ProviderError("bad_response", usage=usage if usage.input_tokens or usage.output_tokens else None) from None
        return ProviderReply(output=content, usage=usage, reported_model=reported if isinstance(reported, str) else None)

    async def aclose(self) -> None:
        await self._client.aclose()


Behavior = Callable[[Target, Sequence[Mapping[str, Any]], str], Awaitable[ProviderReply]]


class FakeProvider:
    """进程内假供应商（只用于 dev、test）：行为由测试注入；默认返回固定文本"""

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


def build_provider(vendor: Vendor, environ: Mapping[str, str] | None = None, env: str = "test") -> Provider:
    if vendor.protocol == "openai_compat":
        return OpenAICompatProvider(vendor, environ, env=env)
    if vendor.protocol == "fake" and env in SYNTHETIC_ENVS:
        return FakeProvider(vendor)
    raise ConfigError(f"供应商 {vendor.vendor_id}: {env} 环境没有 {vendor.protocol} 协议的适配器")
