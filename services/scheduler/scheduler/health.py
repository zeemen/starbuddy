"""调度器的 HTTP 健康端点（LT-03 范围第 4 条，specs/launch-lt03-cloud-staging；接口定义 services/scheduler/openapi/health.v0.yaml）。

调度器本身不对外提供接口；为了让 k8s / compose 探针由“进程在不在”改为 HTTP，常驻模式下可另起一个只服务两条路径的极小监听
（标准库 asyncio，不新增依赖）：
  SCHEDULER_HEALTH_PORT  监听端口；不设 = 不起监听（行为与 LT-03 之前一致）。--once、--dry-run 一律不起。
  SCHEDULER_HOST         监听地址；不设 = 127.0.0.1；dev / test 只收 127.0.0.1；staging 另收 RFC 1918 内网 IPv4 字面量
                         （与学习服务 learning/netpolicy.listen_host 同一口径、同一个函数）。
- GET /healthz：存活。不访问任何依赖，200 {"status": "ok"}；
- GET /readyz：就绪。启动核对（时间表、各任务的变量与连接串、子进程解释器）已通过、常驻循环已开始，且能在 READY_TIMEOUT 秒内
  用 SCHEDULER_DATABASE_URL 连上库、核对服务端地址（与取锁同一个 check_server_local）并 SELECT 1：200 {"status": "ready"}；
  否则 503 {"status": "not_ready"}。收到 SIGTERM 之后（不再启动新任务）也是 503。库探测并发 1、结果复用 READY_CACHE 秒。
- 无需鉴权；响应只含状态（不带任务名、下一次运行时刻、版本、错误类别）；探测不记日志；其他路径 404、其他方法 405；
  请求头 8 KiB、读取 2 秒为限；一律 connection: close、no-store。
- 指标（LT-05 决定记录（续）第 5 条，scheduler/metrics.py）：健康监听**不**服务 /metrics（404）；常驻模式另起只服务 GET /metrics
  的指标监听 MetricsServer（同一地址，SCHEDULER_METRICS_PORT；staging 缺省 9793，dev / test 只在设了时起；不能与健康端口相同）。
  Prometheus 文本格式，免鉴权（只在 Pod 内网，NetworkPolicy 只给采集组件放行指标端口）；只含任务名（时间表里的短名字）与结果编码、
  耗时、最近一次成功时刻，以及最近一次就绪检查的结果（不另查库）。其他路径 404、其他方法 405，限制与健康监听相同。
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import sys
import time
from collections.abc import Awaitable, Callable
from typing import Any, Mapping

from .config import SERVICES

HEALTH_PATH = "/healthz"
READY_PATH = "/readyz"
METRICS_PATH = "/metrics"
METRICS_TYPE = "text/plain; version=0.0.4; charset=utf-8"
PORT_VAR = "SCHEDULER_HEALTH_PORT"
HOST_VAR = "SCHEDULER_HOST"
METRICS_PORT_VAR = "SCHEDULER_METRICS_PORT"
DEFAULT_METRICS_PORT = 9793  # LT-05：staging 的指标监听缺省端口（env-inventory 登记）
READY_TIMEOUT = 3.0
READY_CACHE = 3.0  # 秒：库探测结果的复用期
READ_TIMEOUT = 2.0
MAX_HEAD = 8 * 1024
_REASONS = {200: "OK", 404: "Not Found", 405: "Method Not Allowed", 400: "Bad Request", 503: "Service Unavailable"}


class HealthConfigError(ValueError):
    """健康监听的配置不合法：拒绝启动"""


def _listen_host(raw: str | None, env: str) -> str:
    learning = str(SERVICES / "learning")
    if learning not in sys.path:
        sys.path.append(learning)
    from learning.netpolicy import listen_host
    try:
        return listen_host(raw, env, HOST_VAR)
    except RuntimeError as e:
        raise HealthConfigError(str(e)) from None


def listen_config(environ: Mapping[str, str], env: str) -> tuple[str, int] | None:
    """(地址, 端口)；没设端口返回 None（不起监听）。端口不是 1–65535 的整数、地址不合口径一律拒绝"""
    raw_port = environ.get(PORT_VAR)
    if raw_port is None or raw_port == "":
        if environ.get(HOST_VAR):
            raise HealthConfigError(f"设了 {HOST_VAR} 却没有设 {PORT_VAR}")
        return None
    if not raw_port.isdigit() or not 1 <= int(raw_port) <= 65535 or raw_port != str(int(raw_port)):
        raise HealthConfigError(f"{PORT_VAR} 必须是 1–65535 的整数")
    return _listen_host(environ.get(HOST_VAR), env), int(raw_port)


def metrics_config(environ: Mapping[str, str], env: str, health_listen: tuple[str, int] | None) -> tuple[str, int] | None:
    """指标监听的 (地址, 端口)：SCHEDULER_METRICS_PORT；不设时 staging 用缺省 9793、dev / test 不起。地址与健康监听相同
    （没起健康监听时按 SCHEDULER_HOST 同一口径）；端口不是 1–65535 的规范整数、与健康端口相同一律拒绝"""
    raw = environ.get(METRICS_PORT_VAR)
    if raw is None or raw == "":
        if env != "staging":
            return None
        port = DEFAULT_METRICS_PORT
    elif not raw.isdigit() or not 1 <= int(raw) <= 65535 or raw != str(int(raw)):
        raise HealthConfigError(f"{METRICS_PORT_VAR} 必须是 1–65535 的整数")
    else:
        port = int(raw)
    if health_listen is not None and port == health_listen[1]:
        raise HealthConfigError(f"{METRICS_PORT_VAR} 不能与 {PORT_VAR} 相同")
    host = health_listen[0] if health_listen is not None else _listen_host(environ.get(HOST_VAR), env)
    return host, port


def response(status: int, body: dict[str, str], *, payload: bytes | None = None, content_type: str = "application/json") -> bytes:
    payload = json.dumps(body, separators=(",", ":")).encode() if payload is None else payload
    head = (f"HTTP/1.1 {status} {_REASONS[status]}\r\ncontent-type: {content_type}\r\ncache-control: no-store\r\n"
            f"x-content-type-options: nosniff\r\ncontent-length: {len(payload)}\r\nconnection: close\r\n\r\n")
    return head.encode("ascii") + payload


async def answer(request_line: bytes, probe: Callable[[], Awaitable[bool]]) -> bytes:
    """按请求行回应：路径逐字相等（不收查询串、尾斜杠、别名），只认 GET"""
    parts = request_line.decode("latin-1").rstrip("\r\n").split(" ")
    if len(parts) != 3 or not parts[2].startswith("HTTP/1."):
        return response(400, {"status": "bad_request"})
    method, path, _ = parts
    if path not in (HEALTH_PATH, READY_PATH):
        return response(404, {"status": "not_found"})
    if method != "GET":
        return response(405, {"status": "method_not_allowed"})
    if path == HEALTH_PATH:
        return response(200, {"status": "ok"})
    try:
        ok = await asyncio.wait_for(probe(), READY_TIMEOUT) is True
    except Exception:  # noqa: BLE001 —— 不区分原因、不回显异常
        ok = False
    return response(200, {"status": "ready"}) if ok else response(503, {"status": "not_ready"})


async def answer_metrics(request_line: bytes) -> bytes:
    """指标监听：只服务 GET /metrics（路径逐字相等，不收查询串、尾斜杠、别名）；别的路径 404、别的方法 405"""
    parts = request_line.decode("latin-1").rstrip("\r\n").split(" ")
    if len(parts) != 3 or not parts[2].startswith("HTTP/1."):
        return response(400, {"status": "bad_request"})
    method, path, _ = parts
    if path != METRICS_PATH:
        return response(404, {"status": "not_found"})
    if method == "GET":
        from . import metrics
        return response(200, {}, payload=metrics.render(), content_type=METRICS_TYPE)
    return response(405, {"status": "method_not_allowed"})


async def _serve_conn(reader: asyncio.StreamReader, writer: asyncio.StreamWriter, respond: Callable[[bytes], Awaitable[bytes]]) -> None:
    try:
        head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), READ_TIMEOUT)
        if len(head) > MAX_HEAD:
            raise ValueError("head too large")
        line = head.split(b"\r\n", 1)[0]
        writer.write(await respond(line))
    except (asyncio.TimeoutError, asyncio.IncompleteReadError, asyncio.LimitOverrunError, ValueError):
        writer.write(response(400, {"status": "bad_request"}))
    except Exception:  # noqa: BLE001 连接中途断开等：直接关
        pass
    finally:
        with contextlib.suppress(Exception):
            await writer.drain()
        writer.close()
        with contextlib.suppress(Exception):
            await writer.wait_closed()


class MetricsServer:
    """常驻循环旁的指标监听（LT-05）：只服务 GET /metrics；不记日志"""

    def __init__(self, host: str, port: int) -> None:
        self.host, self.port = host, port
        self._server: asyncio.AbstractServer | None = None

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await _serve_conn(reader, writer, answer_metrics)

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._handle, self.host, self.port, limit=MAX_HEAD)

    @property
    def bound_port(self) -> int:
        assert self._server is not None
        return self._server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            with contextlib.suppress(Exception):
                await self._server.wait_closed()


class HealthServer:
    """常驻循环旁的健康监听。ready_flag 由循环在开始与停止时设置；db_probe 是“能连上库”的核对（测试可注入）"""

    def __init__(self, host: str, port: int, db_probe: Callable[[], Awaitable[bool]]) -> None:
        self.host = host
        self.port = port
        self.db_probe = db_probe
        self.running = False
        self._server: asyncio.AbstractServer | None = None
        self._lock = asyncio.Lock()
        self._last: tuple[float, bool] | None = None

    async def probe(self) -> bool:
        """并发 1（安全审查采纳建议）：同一时刻只有一次库探测（每次新建连接、用完即断），并发的探针排队并复用
        READY_CACHE 秒内的结果，不会同时开多条连接；停止后立即未就绪（不看缓存）"""
        if not self.running:
            return False
        async with self._lock:
            last = self._last
            if last is not None and time.monotonic() - last[0] < READY_CACHE:  # 调用时取模块常量（测试可调成 0）
                return last[1]
            ok = await self.db_probe() is True
            self._last = (time.monotonic(), ok)
            return ok and self.running

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await _serve_conn(reader, writer, lambda line: answer(line, self.probe))

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._handle, self.host, self.port, limit=MAX_HEAD)
        from . import metrics
        metrics.obs.track("scheduler", ready=self)  # starbuddy_ready：最近一次就绪检查的结果（_last），采集时不另查库

    @property
    def bound_port(self) -> int:
        assert self._server is not None
        return self._server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        self.running = False
        if self._server is not None:
            self._server.close()
            with contextlib.suppress(Exception):
                await self._server.wait_closed()


def db_probe_for(environ: Mapping[str, str], env: str) -> Callable[[], Awaitable[bool]]:
    """用调度器身份的连接串连库：连上先核对服务端地址（与取锁同一个 check_server_local），再 SELECT 1，随即断开。
    构造时先按启动核对的口径核对连接串（本机；staging 另收内网 IPv4 或带 verify-full 的主机名），不合格抛 PreflightError（拒绝启动）"""
    from .runner import PreflightError, check_local_url, check_server_local
    url = environ.get("SCHEDULER_DATABASE_URL", "")
    if not url:
        raise PreflightError(f"设了 {PORT_VAR} 时必须有 SCHEDULER_DATABASE_URL（就绪探针用它核对库连接）")
    check_local_url("SCHEDULER_DATABASE_URL", url, env)

    async def probe() -> bool:
        import asyncpg
        conn: Any = await asyncpg.connect(url, timeout=READY_TIMEOUT,
                                          server_settings={"application_name": "starbuddy-scheduler:readyz"})
        try:
            try:
                await check_server_local(conn, env)
            except PreflightError:
                return False
            return await conn.fetchval("SELECT 1") == 1
        finally:
            with contextlib.suppress(Exception):
                await conn.close()
    return probe
