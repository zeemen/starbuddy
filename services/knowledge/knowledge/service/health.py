"""HTTP 健康端点（LT-03 范围第 4 条，specs/launch-lt03-cloud-staging；接口定义 services/knowledge/openapi/health.v0.yaml）。
口径与学习服务 learning/health.py 相同（各服务各带一份，不跨服务导入；代码部分逐字相同）：

- GET /healthz：存活，不访问任何依赖，200 {"status": "ok"}；
- GET /readyz：就绪。启动前置（契约、starbuddy_kge_read 角色断言、配置了页面快照时的逐页核对）在 lifespan 里已通过，
  且只读连接池能在 READY_TIMEOUT 秒内 SELECT 1：200 {"status": "ready"}；否则 503 {"status": "not_ready"}。
  并发探测共用一次检查，结果复用 READY_CACHE 秒。
- 无需鉴权；在 /mcp 挂载之前注册（不进 MCP 的令牌前置）；仍经 HostGuard 的 Host 校验；响应只含状态（不带快照版本、页数、
  缓存计数等内部信息）；探测失败不逐次记日志；no-store。
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
