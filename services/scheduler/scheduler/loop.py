"""常驻循环：按时间表把到期的任务交给运行器。

- 每个任务记下一次运行时刻（next_run，纯函数）；到期即启动并立刻算下一次（从“现在”往后算，错过的轮次不补跑）；
- 同一任务上一轮还没结束：不重叠，记 skipped_running（alert：任务跑得比间隔还久）；跨实例的重叠由运行器的咨询锁挡住；
- 启动时补跑只给声明了 catch_up 的任务（周报）：第一次 tick 立即跑一次；其他任务一律从下一个时刻开始；
- 不同任务互不等待（每个任务一个 asyncio 任务、一个子进程）；
- 收到 SIGTERM / SIGINT：不再启动新任务，等正在跑的结束（各自有任务级超时）后退出。
"""
from __future__ import annotations

import asyncio
import contextlib
import signal
from datetime import datetime, timezone
from typing import Any, Callable, Mapping

from .config import Job
from .runner import EXIT, RunResult, Runner, _iso
from .schedule import next_run

MAX_SLEEP_SECONDS = 30.0


class Loop:
    def __init__(self, jobs: Mapping[str, Job], runner: Runner, clock: Callable[[], datetime] | None = None) -> None:
        self.jobs = dict(jobs)
        self.runner = runner
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        now = self.clock()
        # 启动时补跑：只有声明了 catch_up 的任务（配置只允许周报）在第一次 tick 立即跑一次，靠子命令幂等兜底（已生成的周不重写）
        self.next_due = {name: now if j.catch_up else next_run(j.schedule, now) for name, j in self.jobs.items()}
        self.running: dict[str, asyncio.Task[RunResult]] = {}

    def due(self, now: datetime) -> tuple[list[Job], list[Job]]:
        """到期的任务分两类：(要启动的, 上一轮还没结束而跳过的)；两类都把下一次运行时刻推到 now 之后"""
        start: list[Job] = []
        busy: list[Job] = []
        for name, job in self.jobs.items():
            if self.next_due[name] > now:
                continue
            t = self.running.get(name)
            (busy if t is not None and not t.done() else start).append(job)
            self.next_due[name] = next_run(job.schedule, now)
        return start, busy

    def tick(self) -> list[asyncio.Task[RunResult]]:
        now = self.clock()
        start, busy = self.due(now)
        for job in busy:
            t = _iso(now)
            self.runner.emit(RunResult(job.name, "skipped_running", EXIT["skipped_running"], t, t, 0))
        for job in start:
            self.running[job.name] = asyncio.ensure_future(self.runner.run(job))
        return [self.running[j.name] for j in start]

    def sleep_seconds(self) -> float:
        now = self.clock()
        soonest = min(self.next_due.values())
        return max(0.05, min(MAX_SLEEP_SECONDS, (soonest - now).total_seconds()))

    async def serve(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            self.tick()
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=self.sleep_seconds())
        pending = [t for t in self.running.values() if not t.done()]
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)


async def serve_forever(loop_: Loop, health: Any = None) -> None:
    """health：可选的健康监听（scheduler/health.py 的 HealthServer，LT-03）。先起监听，循环开始时标记就绪；
    收到信号即标记未就绪（不再启动新任务），等正在跑的任务结束后关监听"""
    stop = asyncio.Event()
    ev = asyncio.get_running_loop()

    def on_signal() -> None:
        if health is not None:
            health.running = False
        stop.set()
    for sig in (signal.SIGTERM, signal.SIGINT):
        with contextlib.suppress(NotImplementedError):
            ev.add_signal_handler(sig, on_signal)
    if health is not None:
        await health.start()
        health.running = True
    try:
        await loop_.serve(stop)
    finally:
        if health is not None:
            await health.stop()
