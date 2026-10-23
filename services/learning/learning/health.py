"""HTTP 健康端点（LT-03 范围第 4 条，specs/launch-lt03-cloud-staging）：学习服务与审核台后端共用（review_console 也从这里取）。

- GET /healthz：存活。不访问任何依赖（库、适配器、知识服务），进程能应答就是 200 {"status": "ok"}；
- GET /readyz：就绪。启动前置（契约加载、迁移 0016 / 0037 / 0058 存在与执行权等）在 lifespan 里已核对通过（started 为真），
  且连接池能在 READY_TIMEOUT 秒内完成 SELECT 1：200 {"status": "ready"}；否则 503 {"status": "not_ready"}。
- 无需鉴权（k8s / compose 探针不带令牌），所以响应**只含状态**：不带版本、主机名、库名、迁移号、错误类别或耗时；
  探测失败的原因不进响应，也不逐次记日志（探针每 10 秒一次）。只认 GET（其他方法 405，由框架回应），响应一律 no-store。
- 探针路径不经路由监听（route_listener 只服务模型路由接口），只在主监听上。
- 学习服务的就绪探针另核对 0058 的执行权（has_function_privilege，契约审查建议 4：迁移被回滚或授权被收回时不再就绪）。
"""
# ---- 以下在 learning/learning/health.py、insight/insight/service/health.py、knowledge/knowledge/service/health.py 三处逐字相同
# ---- （各服务各带一份、不跨服务导入；deploy/tests/test_health_shared.py 核对）
from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi.responses import JSONResponse

HEALTH_PATH = "/healthz"
READY_PATH = "/readyz"
READY_TIMEOUT = 2.0
READY_CACHE = 3.0  # 秒：就绪结果的复用期（安全审查采纳建议：single-flight + 2–5 秒缓存，探针并发时不放大到库）
HEADERS = {"cache-control": "no-store", "x-content-type-options": "nosniff"}


async def check(probe: Callable[[], Awaitable[bool]], timeout: float | None = None) -> bool:
    """probe 返回真且在时限内完成才算就绪；超时（取消查询）、抛错、返回假一律 False（不区分原因，不回显异常）"""
    timeout = READY_TIMEOUT if timeout is None else timeout  # 调用时取模块常量（测试可调小）
    try:
        return await asyncio.wait_for(probe(), timeout) is True
    except Exception:  # noqa: BLE001 —— 连接断开、超时、池已关闭：都只是“没就绪”
        return False


def respond(ok: bool) -> JSONResponse:
    if ok:
        return JSONResponse({"status": "ready"}, headers=HEADERS)
    return JSONResponse({"status": "not_ready"}, status_code=503, headers=HEADERS)


async def ready(probe: Callable[[], Awaitable[bool]], timeout: float | None = None) -> JSONResponse:
    return respond(await check(probe, timeout))


class ReadyCheck:
    """single-flight + 短缓存：同一时刻只有一次检查在跑（并发探测等同一个结果），结果在 READY_CACHE 秒内复用"""

    def __init__(self, probe: Callable[[], Awaitable[bool]], clock: Callable[[], float] = time.monotonic) -> None:
        self.probe, self.clock = probe, clock
        self._task: asyncio.Task[bool] | None = None
        self._last: tuple[float, bool] | None = None

    async def _run(self) -> bool:
        try:
            ok = await check(self.probe)
            self._last = (self.clock(), ok)
            return ok
        finally:
            self._task = None

    async def __call__(self) -> bool:
        last = self._last
        if last is not None and self.clock() - last[0] < READY_CACHE:  # 调用时取模块常量（测试可调成 0）
            return last[1]
        if self._task is None:
            self._task = asyncio.ensure_future(self._run())
        return await asyncio.shield(self._task)  # 某个探测断开不取消共用的那次检查


def alive() -> JSONResponse:
    return JSONResponse({"status": "ok"}, headers=HEADERS)


async def pool_alive(pool: Any) -> bool:
    """连接池能取到连接并执行 SELECT 1"""
    return pool is not None and await pool.fetchval("SELECT 1") == 1


def register(app: Any, probe: Callable[[], Awaitable[bool]]) -> ReadyCheck:
    """在应用上注册两个端点（在其他路由之前注册，避免被挂载在 / 上的子应用截走）"""
    cached = ReadyCheck(probe)

    @app.get(HEALTH_PATH, include_in_schema=False)
    async def healthz() -> JSONResponse:
        return alive()

    @app.get(READY_PATH, include_in_schema=False)
    async def readyz() -> JSONResponse:
        return respond(await cached())

    return cached
