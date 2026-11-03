"""假供应商的故障注入（spec 设计要点 2）：整家不可用、超时、5xx 比例、慢响应、限流；另有可配置的基础延迟分布。

同一套判定既给本机栈里的 HTTP 假供应商（fake_provider.py）用，也给进程内的 FakeProvider 行为用（测试）：
- decide() 只依据注入规则与一个带种子的随机数源，给出“这一次怎么答”，不看请求内容；
- 进程内行为 faulty_behavior() 把判定翻译成路由认得的 ProviderError 类别（server / rate_limited）或挂起（超时由路由的时限截断）。
不改 model_router/providers.py：FakeProvider 本来就接受注入的行为（向后兼容，零改动）。
"""
from __future__ import annotations

import asyncio
import math
import random
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

MODES = ("ok", "unavailable", "timeout", "error_ratio", "slow", "rate_limited")


@dataclass(frozen=True)
class LatencyModel:
    """对数正态延迟：给中位数与 P90（毫秒）。median_ms = 0 表示不加基础延迟。"""

    median_ms: float = 0.0
    p90_ms: float = 0.0

    def sample(self, rng: random.Random) -> float:
        if self.median_ms <= 0:
            return 0.0
        if self.p90_ms <= self.median_ms:
            return self.median_ms
        sigma = math.log(self.p90_ms / self.median_ms) / 1.2815515655446004  # z(0.9)
        return rng.lognormvariate(math.log(self.median_ms), sigma)


@dataclass(frozen=True)
class FaultSpec:
    """一家供应商当前的注入规则。

    mode：
      ok            正常（只有基础延迟）
      unavailable   整家不可用：每次都是服务端错误（HTTP 503）
      timeout       超时：挂起 hang_s 秒（远超路由时限，由路由截断）
      error_ratio   按 error_ratio 的比例返回 5xx（HTTP 500），其余正常
      slow          慢响应：基础延迟之外再加 extra_latency_ms
      rate_limited  限流：HTTP 429，带 Retry-After（retry_after_s）
    """

    mode: str = "ok"
    error_ratio: float = 0.0
    extra_latency_ms: float = 0.0
    hang_s: float = 3600.0
    retry_after_s: float | None = 1.0

    def __post_init__(self) -> None:
        if self.mode not in MODES:
            raise ValueError(f"unknown fault mode {self.mode!r}")
        if not 0.0 <= self.error_ratio <= 1.0 or self.extra_latency_ms < 0 or self.hang_s <= 0:
            raise ValueError("fault parameters out of range")

    @classmethod
    def parse(cls, raw: Mapping[str, Any]) -> "FaultSpec":
        allowed = {"mode", "error_ratio", "extra_latency_ms", "hang_s", "retry_after_s"}
        if set(raw) - allowed:
            raise ValueError("unknown fault keys")
        return cls(**{k: raw[k] for k in raw})


@dataclass(frozen=True)
class Decision:
    """kind：None = 正常答复；server / rate_limited = 返回错误；timeout = 挂起 delay_s 秒"""

    kind: str | None
    delay_s: float
    retry_after_s: float | None = None


def decide(spec: FaultSpec, latency: LatencyModel, rng: random.Random) -> Decision:
    base = latency.sample(rng) / 1000.0
    if spec.mode == "unavailable":
        return Decision("server", 0.0)
    if spec.mode == "timeout":
        return Decision("timeout", spec.hang_s)
    if spec.mode == "rate_limited":
        return Decision("rate_limited", 0.0, spec.retry_after_s)
    if spec.mode == "error_ratio" and rng.random() < spec.error_ratio:
        return Decision("server", base)
    if spec.mode == "slow":
        return Decision(None, base + spec.extra_latency_ms / 1000.0)
    return Decision(None, base)


@dataclass
class FaultController:
    """按供应商登记注入规则；每家一个带种子的随机数源（同种子同序列）。计数只有类别，没有内容。"""

    seed: int = 20260930
    latency: LatencyModel = field(default_factory=LatencyModel)
    specs: dict[str, FaultSpec] = field(default_factory=dict)
    counts: dict[str, dict[str, int]] = field(default_factory=dict)
    _rngs: dict[str, random.Random] = field(default_factory=dict)

    def set(self, vendor: str, spec: FaultSpec | None = None, **kw: Any) -> None:
        self.specs[vendor] = replace(spec or FaultSpec(), **kw) if kw else (spec or FaultSpec())

    def clear(self, vendor: str | None = None) -> None:
        if vendor is None:
            self.specs.clear()
        else:
            self.specs.pop(vendor, None)

    def spec(self, vendor: str) -> FaultSpec:
        return self.specs.get(vendor, FaultSpec())

    def decide(self, vendor: str) -> Decision:
        rng = self._rngs.setdefault(vendor, random.Random(f"{self.seed}:{vendor}"))
        d = decide(self.spec(vendor), self.latency, rng)
        bucket = self.counts.setdefault(vendor, {})
        key = d.kind or "ok"
        bucket[key] = bucket.get(key, 0) + 1
        return d


def faulty_behavior(controller: FaultController, vendor: str,
                    reply: Callable[[Any, Sequence[Mapping[str, Any]], str], Awaitable[Any]]):
    """给 learning.model_router.FakeProvider 的行为：先按注入规则判定，正常时交给 reply 生成答复。"""
    from learning.model_router import ProviderError  # 延迟导入：本模块的纯函数部分不依赖学习服务

    async def behavior(target, messages, key):
        d = controller.decide(vendor)
        if d.kind == "timeout":
            await asyncio.sleep(d.delay_s)  # 由路由的 asyncio.wait_for 截断，记为 timeout
            raise ProviderError("timeout")
        if d.delay_s > 0:
            await asyncio.sleep(d.delay_s)
        if d.kind == "server":
            raise ProviderError("server")
        if d.kind == "rate_limited":
            raise ProviderError("rate_limited", d.retry_after_s)
        return await reply(target, messages, key)

    return behavior
