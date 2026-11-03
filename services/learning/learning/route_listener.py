"""staging 的回环路由监听（specs/launch-lt02-model-provider 决定记录（续）第 5 条）。

staging 下学习服务的主监听是 Pod 的内网地址（LEARNING_HOST），而三科适配器只认 https 或本机回环的 http（适配器不改代码，
沿用 LT-01 决定：同 Pod 回环互访）。所以 staging 另在 127.0.0.1:LEARNING_ROUTE_PORT 起一个监听，**只服务**
`/internal/v1/model/route`，其他任何路径一律 404；与主监听共用同一个应用对象与状态（同一个模型路由、同一份成本护栏计数），
不另跑 lifespan。主监听不变（仍服务全部接口，含模型路由接口）。dev、test 不起这个监听（设了 LEARNING_ROUTE_PORT 拒绝启动）。
适配器接线：ZHIXING_ROUTE_URL / WENLI_ROUTE_URL = http://127.0.0.1:<LEARNING_ROUTE_PORT>。
"""
from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import Awaitable, Callable, Iterator
from typing import Any

ROUTE_PATH = "/internal/v1/model/route"
DEFAULT_ROUTE_PORT = 8785
LOOPBACK = "127.0.0.1"

Scope = dict[str, Any]
ASGIApp = Callable[[Scope, Callable[[], Awaitable[dict]], Callable[[dict], Awaitable[None]]], Awaitable[None]]

_NOT_FOUND = json.dumps({"type": "/problems/not_found", "title": "not found", "status": 404, "code": "INVALID_REQUEST",
                         "retryable": False}).encode()


def route_only(app: ASGIApp) -> ASGIApp:
    """包一层：只把 ROUTE_PATH（路径逐字相等，不收尾斜杠或别名）转给应用；lifespan 自己应答，不触发应用的启动与关闭"""

    async def asgi(scope: Scope, receive, send) -> None:
        kind = scope.get("type")
        if kind == "lifespan":
            while True:
                msg = await receive()
                if msg["type"] == "lifespan.startup":
                    await send({"type": "lifespan.startup.complete"})
                elif msg["type"] == "lifespan.shutdown":
                    await send({"type": "lifespan.shutdown.complete"})
                    return
        if kind == "http" and scope.get("path") == ROUTE_PATH and not scope.get("root_path"):
            await app(scope, receive, send)
            return
        if kind == "http":
            await send({"type": "http.response.start", "status": 404,
                        "headers": [(b"content-type", b"application/problem+json"), (b"content-length", str(len(_NOT_FOUND)).encode())]})
            await send({"type": "http.response.body", "body": _NOT_FOUND})
            return
        if kind == "websocket":
            await send({"type": "websocket.close", "code": 1008})

    return asgi


def listeners(settings: Any, main_port: int) -> list[tuple[str, int, str]]:
    """（地址，端口，用途：main 全部接口 / route 只服务路由接口 / metrics 只服务 GET /metrics）。
    非 staging 只有主监听（设了 LEARNING_METRICS_PORT 时另加指标监听）"""
    out = [(settings.host, main_port, "main")]
    if settings.route_port is not None:
        out.append((LOOPBACK, settings.route_port, "route"))
    if getattr(settings, "metrics_port", None) is not None:
        out.append((settings.host, settings.metrics_port, "metrics"))
    return out


def serve(app: ASGIApp, settings: Any, main_port: int) -> None:
    """启动：主监听照旧；staging 另起回环路由监听与指标监听（LT-05：只服务 GET /metrics，与主监听同一地址）。
    任一监听退出（例如端口被占）就一起退出；信号只由主监听处理"""
    import uvicorn

    from .obs import metrics_app  # noqa: PLC0415

    class _Side(uvicorn.Server):
        @contextlib.contextmanager
        def capture_signals(self) -> Iterator[None]:  # 信号交给主监听，避免几个服务互相覆盖处理函数
            yield

    plan = listeners(settings, main_port)
    # log_config=None（LT-05）：uvicorn 不另配日志，它的日志也经根日志的 JSON 处理器（learning/obs.py，集中脱敏）
    if len(plan) == 1:
        uvicorn.run(app, host=settings.host, port=main_port, access_log=False, log_config=None)
        return
    main = uvicorn.Server(uvicorn.Config(app, host=settings.host, port=main_port, access_log=False, log_config=None))
    apps = {"route": route_only(app), "metrics": metrics_app}
    sides = [_Side(uvicorn.Config(apps[kind], host=host, port=port, access_log=False, lifespan="on", log_config=None))
             for host, port, kind in plan[1:]]

    async def run() -> None:
        tasks = [asyncio.create_task(s.serve()) for s in sides]
        for t in tasks:
            t.add_done_callback(lambda _t: setattr(main, "should_exit", True))
        try:
            await main.serve()
        finally:
            for s in sides:
                s.should_exit = True
            await asyncio.gather(*tasks, return_exceptions=True)

    asyncio.run(run())
