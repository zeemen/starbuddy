"""成本护栏（LT-02 范围第 5 条、决定记录第 2 条）：按任务计每个自然日（UTC+8）的调用次数与 token，超过路由表里配的上限就拒绝。

- 进程内计数：多实例时上限按实例计（用户决定 2026-10-21）；重启清零。
- 次数按每次真实的供应商调用计（安全审查建议 1）：每次尝试（含重试与切到备选）发出前预留一次，成败都算；没有候选、熔断跳过、
  超过总时限而没发出的不算。同一事件循环里预留没有 await，并发请求不会多放；另加一把线程锁，防同一个路由对象被多个线程共用。
- token 按供应商报告的用量累加：成功的调用累加；失败的调用只有供应商报告了用量（200 但内容不合格式）才累加——
  HTTP 错误、超时、连接失败没有用量可读，只计次数、不计 token。并发在途的调用可能让当日 token 略超上限
  （至多超出在途调用的用量），之后的调用一律拒绝。
- 只决定“这次放不放行”，不碰计量与扣次（规则 4：预留、结算、回滚仍由 BFF 与计量负责）。
"""
from __future__ import annotations

import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from .config import Budget
from .types import Task

UTC8_OFFSET_SECONDS = 8 * 3600
DAY_SECONDS = 86_400


def day_of(epoch_seconds: float) -> int:
    """UTC+8 自然日的序号：北京时间 0 点切换"""
    return int((epoch_seconds + UTC8_OFFSET_SECONDS) // DAY_SECONDS)


def seconds_until_reset(epoch_seconds: float) -> int:
    """距下一个北京时间 0 点的整秒数（至少 1）"""
    next_reset = (day_of(epoch_seconds) + 1) * DAY_SECONDS - UTC8_OFFSET_SECONDS
    return max(1, int(next_reset - epoch_seconds + 0.999))


@dataclass(frozen=True)
class Reservation:
    task: Task
    day: int


class DailyBudget:
    def __init__(self, limits: Mapping[Task, Budget], wall_clock: Callable[[], float] = time.time) -> None:
        self._limits = dict(limits)
        self._wall = wall_clock
        self._day: int | None = None
        self._calls: dict[Task, int] = {}
        self._tokens: dict[Task, int] = {}
        self._lock = threading.Lock()

    def _roll(self, day: int) -> None:
        if day != self._day:
            self._day = day
            self._calls.clear()
            self._tokens.clear()

    def reserve(self, task: Task) -> Reservation | None:
        """每次供应商调用前：放行就记一次并返回预留；当日次数或 token 已到上限返回 None。没有配上限的任务总是放行"""
        now = self._wall()
        day = day_of(now)
        with self._lock:
            self._roll(day)
            limit = self._limits.get(task)
            if limit is not None:
                if self._calls.get(task, 0) >= limit.daily_calls or self._tokens.get(task, 0) >= limit.daily_tokens:
                    return None
            self._calls[task] = self._calls.get(task, 0) + 1
        return Reservation(task, day)

    def settle(self, reservation: Reservation, tokens: int) -> None:
        """调用结束后累加供应商报告的 token；跨过 0 点才结束的调用不计入新的一天（预留时的那一天已经清零）"""
        if tokens <= 0:
            return
        with self._lock:
            self._roll(day_of(self._wall()))
            if reservation.day == self._day:
                self._tokens[reservation.task] = self._tokens.get(reservation.task, 0) + int(tokens)

    def retry_after(self) -> int:
        return seconds_until_reset(self._wall())

    def remaining(self) -> dict[Task, tuple[int, int, Budget]]:
        """配了上限的任务 →（当日剩余次数，当日剩余 token，上限）；只读，供指标采集（LT-05）"""
        with self._lock:
            self._roll(day_of(self._wall()))
            return {t: (max(0, b.daily_calls - self._calls.get(t, 0)), max(0, b.daily_tokens - self._tokens.get(t, 0)), b)
                    for t, b in self._limits.items()}

    def used(self, task: Task) -> tuple[int, int]:
        """（当日已用次数，当日已用 token）；只读，供测试与运维核对"""
        with self._lock:
            self._roll(day_of(self._wall()))
            return self._calls.get(task, 0), self._tokens.get(task, 0)
