"""熔断器：按（供应商，任务）计连续失败；打开 → 冷却 → 半开放一个试探 → 成功关闭 / 失败再打开。

半开时只放一个试探，试探名额由令牌持有：只有拿到试探令牌的那个请求能结算或释放（安全审查应改 4）。
时钟可注入（测试用），默认 time.monotonic。单进程内状态；多实例部署时各实例独立熔断（lessons 待人确认 #1）。
"""
from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass


@dataclass
class _State:
    failures: int = 0
    opened_at: float | None = None
    probe: object | None = None  # 当前试探令牌


@dataclass(frozen=True, eq=False)
class Ticket:
    """一次放行；is_probe 表示它持有半开状态的试探名额"""

    vendor: str
    task: str
    is_probe: bool


class CircuitBreaker:
    def __init__(self, failure_threshold: int, cooldown_seconds: float, clock: Callable[[], float] = time.monotonic) -> None:
        if failure_threshold < 1 or cooldown_seconds <= 0:
            raise ValueError("breaker parameters out of range")
        self._threshold = failure_threshold
        self._cooldown = cooldown_seconds
        self._clock = clock
        self._states: dict[tuple[str, str], _State] = {}

    def _get(self, vendor: str, task: str) -> _State:
        return self._states.setdefault((vendor, task), _State())

    def state(self, vendor: str, task: str) -> str:
        s = self._get(vendor, task)
        if s.opened_at is None:
            return "closed"
        if self._clock() - s.opened_at >= self._cooldown:
            return "half_open"
        return "open"

    def acquire(self, vendor: str, task: str) -> Ticket | None:
        """关闭时放行；打开时拒绝；冷却结束后只把试探令牌发给一个请求"""
        s = self._get(vendor, task)
        st = self.state(vendor, task)
        if st == "closed":
            return Ticket(vendor, task, False)
        if st == "half_open" and s.probe is None:
            t = Ticket(vendor, task, True)
            s.probe = t
            return t
        return None

    def _holds_probe(self, t: Ticket) -> bool:
        return t.is_probe and self._get(t.vendor, t.task).probe is t

    def success(self, t: Ticket) -> None:
        s = self._get(t.vendor, t.task)
        if t.is_probe and not self._holds_probe(t):
            return
        if s.opened_at is not None and not t.is_probe:
            return  # 熔断打开期间迟到的普通请求结果不改变状态
        s.failures, s.opened_at, s.probe = 0, None, None

    def failure(self, t: Ticket) -> None:
        s = self._get(t.vendor, t.task)
        if t.is_probe:
            if self._holds_probe(t):
                s.opened_at, s.probe = self._clock(), None  # 试探失败：重新打开，冷却重新计时
            return
        if s.opened_at is not None:
            return
        s.failures += 1
        if s.failures >= self._threshold:
            s.opened_at = self._clock()

    def release(self, t: Ticket) -> None:
        """没有得到结论（被取消、请求本身不合法）：试探令牌放回；普通令牌无事可做"""
        if self._holds_probe(t):
            self._get(t.vendor, t.task).probe = None
