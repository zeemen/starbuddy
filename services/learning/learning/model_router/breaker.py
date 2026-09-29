"""熔断器：按（供应商，任务）计连续失败；打开 → 冷却 → 半开放一个试探 → 成功关闭 / 失败再打开。

时钟可注入（测试用），默认 time.monotonic。单进程内状态；多实例部署时各实例独立熔断（lessons）。
"""
from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass


@dataclass
class _State:
    failures: int = 0
    opened_at: float | None = None
    probing: bool = False


class CircuitBreaker:
    def __init__(self, failure_threshold: int, cooldown_seconds: float, clock: Callable[[], float] = time.monotonic) -> None:
        if failure_threshold < 1 or cooldown_seconds <= 0:
            raise ValueError("breaker parameters out of range")
        self._threshold = failure_threshold
        self._cooldown = cooldown_seconds
        self._clock = clock
        self._states: dict[tuple[str, str], _State] = {}

    def _get(self, key: tuple[str, str]) -> _State:
        return self._states.setdefault(key, _State())

    def state(self, vendor: str, task: str) -> str:
        s = self._get((vendor, task))
        if s.opened_at is None:
            return "closed"
        if self._clock() - s.opened_at >= self._cooldown:
            return "half_open"
        return "open"

    def allow(self, vendor: str, task: str) -> bool:
        """关闭时放行；打开时拒绝；冷却结束后只放一个试探请求"""
        s = self._get((vendor, task))
        st = self.state(vendor, task)
        if st == "closed":
            return True
        if st == "half_open" and not s.probing:
            s.probing = True
            return True
        return False

    def record_success(self, vendor: str, task: str) -> None:
        s = self._get((vendor, task))
        s.failures, s.opened_at, s.probing = 0, None, False

    def record_failure(self, vendor: str, task: str) -> None:
        s = self._get((vendor, task))
        if s.probing or s.opened_at is not None:
            # 半开试探失败：重新打开，冷却重新计时
            s.opened_at, s.probing = self._clock(), False
            return
        s.failures += 1
        if s.failures >= self._threshold:
            s.opened_at = self._clock()

    def release_probe(self, vendor: str, task: str) -> None:
        """试探请求没有得到结论（例如被调用方取消）：放回试探名额"""
        self._get((vendor, task)).probing = False
