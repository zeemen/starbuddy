"""LT-03 调度器健康端点（specs/launch-lt03-cloud-staging 范围第 4 条；scheduler/health.py）。

- 配置：不设 SCHEDULER_HEALTH_PORT 不起监听；端口只收 1–65535；SCHEDULER_HOST 与学习服务同口径（dev / test 只 127.0.0.1，
  staging 另收 RFC 1918 内网 IPv4）；设了端口必须有本机（staging 另收内网）的 SCHEDULER_DATABASE_URL；不合格拒绝启动（退出码 2）；
- 应答：/healthz 200 ok；/readyz 只有循环在跑且库探针为真才 200，否则 503；路径逐字相等、只认 GET；响应只含状态、no-store、close；
- 常驻循环：开始时就绪，收到停止后未就绪、监听关闭。
不连库（库探针用假的），监听只开在 127.0.0.1 的随机端口；全部合成值。"""
from __future__ import annotations

import asyncio
import io
import json
import os
import signal

import pytest

from scheduler import __main__ as cli
from scheduler import health
from scheduler.loop import Loop, serve_forever
from scheduler.runner import PreflightError
from test_runner import ENV, runner


def test_listen_config_rules():
    assert health.listen_config({}, "test") is None
    assert health.listen_config({"SCHEDULER_HEALTH_PORT": ""}, "staging") is None
    assert health.listen_config({"SCHEDULER_HEALTH_PORT": "8793"}, "test") == ("127.0.0.1", 8793)
    assert health.listen_config({"SCHEDULER_HEALTH_PORT": "8793", "SCHEDULER_HOST": "172.28.0.50"}, "staging") == ("172.28.0.50", 8793)
    for port in ("0", "65536", "-1", "abc", "08793", " 8793"):
        with pytest.raises(health.HealthConfigError, match="SCHEDULER_HEALTH_PORT"):
            health.listen_config({"SCHEDULER_HEALTH_PORT": port}, "test")
    for env in ("dev", "test"):
        with pytest.raises(health.HealthConfigError, match="只有 staging"):
            health.listen_config({"SCHEDULER_HEALTH_PORT": "8793", "SCHEDULER_HOST": "172.28.0.50"}, env)
    for host in ("0.0.0.0", "8.8.8.8", "scheduler", "::"):
        with pytest.raises(health.HealthConfigError, match="SCHEDULER_HOST"):
            health.listen_config({"SCHEDULER_HEALTH_PORT": "8793", "SCHEDULER_HOST": host}, "staging")
    with pytest.raises(health.HealthConfigError, match="SCHEDULER_HOST"):
        health.listen_config({"SCHEDULER_HOST": "127.0.0.1"}, "test")


def test_db_probe_requires_local_url(monkeypatch):
    from test_staging_env import CA, TLS
    if CA.startswith("/nonexistent"):
        pytest.skip("本机没有系统 CA 包")
    from scheduler.runner import _local_checks
    _local_checks()
    from learning import netpolicy
    monkeypatch.setattr(netpolicy, "RDS_CA_PATH", CA)
    with pytest.raises(PreflightError, match="SCHEDULER_DATABASE_URL"):
        health.db_probe_for({}, "test")
    with pytest.raises(PreflightError):
        health.db_probe_for({"SCHEDULER_DATABASE_URL": f"postgresql://u@8.8.8.8/x{TLS}"}, "staging")
    with pytest.raises(PreflightError):
        health.db_probe_for({"SCHEDULER_DATABASE_URL": "postgresql://u@172.28.0.20/x"}, "test")
    with pytest.raises(PreflightError):  # staging 没有 TLS（决定记录（续）第 6 条）
        health.db_probe_for({"SCHEDULER_DATABASE_URL": "postgresql://u@172.28.0.20/x"}, "staging")
    health.db_probe_for({"SCHEDULER_DATABASE_URL": f"postgresql://u@172.28.0.20/x{TLS}"}, "staging")
    health.db_probe_for({"SCHEDULER_DATABASE_URL": "postgresql://u@127.0.0.1/x"}, "test")


def test_answer_paths_and_methods():
    async def go():
        async def yes() -> bool:
            return True

        async def no() -> bool:
            return False

        async def boom() -> bool:
            raise ConnectionError("synthetic 10.0.0.9 scheduler_app")

        def parse(raw: bytes) -> tuple[int, dict, str]:
            head, body = raw.split(b"\r\n\r\n", 1)
            lines = head.decode().split("\r\n")
            return int(lines[0].split()[1]), json.loads(body), "\n".join(lines[1:]).lower()
        st, body, hdr = parse(await health.answer(b"GET /healthz HTTP/1.1", no))
        assert (st, body) == (200, {"status": "ok"}) and "cache-control: no-store" in hdr and "connection: close" in hdr
        assert parse(await health.answer(b"GET /readyz HTTP/1.1", yes))[:2] == (200, {"status": "ready"})
        assert parse(await health.answer(b"GET /readyz HTTP/1.1", no))[:2] == (503, {"status": "not_ready"})
        st, body, _ = parse(await health.answer(b"GET /readyz HTTP/1.1", boom))
        assert (st, body) == (503, {"status": "not_ready"}), "不回显异常"
        for line in (b"POST /healthz HTTP/1.1", b"HEAD /readyz HTTP/1.1"):
            assert parse(await health.answer(line, yes))[0] == 405
        for line in (b"GET /healthz/ HTTP/1.1", b"GET /readyz?x=1 HTTP/1.1", b"GET / HTTP/1.1", b"GET /jobs HTTP/1.1"):
            assert parse(await health.answer(line, yes))[0] == 404
        for line in (b"GET /healthz", b"GARBAGE", b"GET /healthz SPDY/3"):
            assert parse(await health.answer(line, yes))[0] == 400
    asyncio.run(go())


async def _http(port: int, raw: bytes) -> bytes:
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(raw)
    await writer.drain()
    data = await reader.read()
    writer.close()
    return data


def test_probe_concurrency_one_and_cache(monkeypatch):
    """安全审查采纳建议：库探测并发 1（同时只开一条连接），并发探针复用刚得到的结果；缓存期过后重查；停止后立即未就绪"""
    monkeypatch.setattr(health, "READY_CACHE", 3.0)
    state = {"inflight": 0, "max": 0, "calls": 0}

    async def db() -> bool:
        state["calls"] += 1
        state["inflight"] += 1
        state["max"] = max(state["max"], state["inflight"])
        await asyncio.sleep(0.05)
        state["inflight"] -= 1
        return True

    async def go():
        srv = health.HealthServer("127.0.0.1", 0, db)
        srv.running = True
        assert all(await asyncio.gather(*(srv.probe() for _ in range(5))))
        assert state["max"] == 1 and state["calls"] == 1, state
        monkeypatch.setattr(health, "READY_CACHE", 0)
        assert await srv.probe() is True and state["calls"] == 2
        srv.running = False
        assert await srv.probe() is False and state["calls"] == 2
    asyncio.run(go())


def test_server_and_loop_lifecycle(monkeypatch):
    """监听开在回环随机端口：循环开始后就绪；库探针为假时未就绪；停止后监听关闭"""
    monkeypatch.setattr(health, "READY_CACHE", 0)  # 每次都真查（缓存另有用例）
    state = {"db": True}

    async def db() -> bool:
        return state["db"]

    async def go():
        srv = health.HealthServer("127.0.0.1", 0, db)
        run, _, _ = runner()
        loop_ = Loop({}, run)
        loop_.next_due = {}
        stopped, drain = asyncio.Event(), asyncio.Event()

        async def serve(stop):  # 代替真实循环：等停止信号，再模拟“等正在跑的任务结束”
            await stop.wait()
            stopped.set()
            await drain.wait()
        loop_.serve = serve  # type: ignore[method-assign]
        task = asyncio.create_task(serve_forever(loop_, srv))
        for _ in range(100):
            if srv._server is not None and srv.running:
                break
            await asyncio.sleep(0.01)
        port = srv.bound_port
        assert b"200 OK" in await _http(port, b"GET /readyz HTTP/1.1\r\nHost: x\r\n\r\n")
        assert b'{"status":"ok"}' in await _http(port, b"GET /healthz HTTP/1.1\r\nHost: x\r\n\r\n")
        state["db"] = False
        assert b"503" in (await _http(port, b"GET /readyz HTTP/1.1\r\nHost: x\r\n\r\n")).split(b"\r\n")[0]
        state["db"] = True
        # 请求头没有结束、超长：400，不挂住
        assert b"400" in (await _http(port, b"GET /readyz HTTP/1.1\r\n" + b"x" * (9 * 1024) + b"\r\n\r\n")).split(b"\r\n")[0]
        # 收到 SIGTERM：不再启动新任务、立即未就绪（等正在跑的任务期间探针是 503），任务结束后监听关闭
        os.kill(os.getpid(), signal.SIGTERM)
        await asyncio.wait_for(stopped.wait(), 5)
        assert b"503" in (await _http(port, b"GET /readyz HTTP/1.1\r\nHost: x\r\n\r\n")).split(b"\r\n")[0]
        drain.set()
        await asyncio.wait_for(task, 5)
        with pytest.raises(OSError):
            await _http(port, b"GET /healthz HTTP/1.1\r\n\r\n")
    asyncio.run(go())


def test_metrics_server_follows_loop_lifecycle():
    """LT-05：指标监听与健康监听一同起停；健康端口上 /metrics 404，指标端口上只有 /metrics"""
    async def db() -> bool:
        return True

    async def go():
        srv = health.HealthServer("127.0.0.1", 0, db)
        msrv = health.MetricsServer("127.0.0.1", 0)
        run, _, _ = runner()
        loop_ = Loop({}, run)
        loop_.next_due = {}
        release = asyncio.Event()

        async def serve(stop):
            await release.wait()
        loop_.serve = serve  # type: ignore[method-assign]
        task = asyncio.create_task(serve_forever(loop_, srv, msrv))
        for _ in range(100):
            if msrv._server is not None and srv.running:
                break
            await asyncio.sleep(0.01)
        mport = msrv.bound_port
        assert b"200 OK" in (await _http(mport, b"GET /metrics HTTP/1.1\r\nHost: x\r\n\r\n")).split(b"\r\n")[0]
        assert b"404" in (await _http(mport, b"GET /healthz HTTP/1.1\r\nHost: x\r\n\r\n")).split(b"\r\n")[0]
        assert b"404" in (await _http(srv.bound_port, b"GET /metrics HTTP/1.1\r\nHost: x\r\n\r\n")).split(b"\r\n")[0]
        release.set()
        await asyncio.wait_for(task, 5)
        with pytest.raises(OSError):
            await _http(mport, b"GET /metrics HTTP/1.1\r\n\r\n")
    asyncio.run(go())


@pytest.mark.parametrize("extra,msg", [
    ({"SCHEDULER_HEALTH_PORT": "99999"}, "SCHEDULER_HEALTH_PORT"),
    ({"SCHEDULER_HEALTH_PORT": "8793", "SCHEDULER_HOST": "172.28.0.50"}, "只有 staging"),
    ({"SCHEDULER_HOST": "127.0.0.1"}, "SCHEDULER_HEALTH_PORT"),
])
def test_cli_refuses_bad_health_config(extra, msg, capsys):
    run, _, _ = runner()
    rc = cli.main([], {**ENV, **extra}, out=io.StringIO(), runner=run)
    err = capsys.readouterr().err
    assert rc == 2 and "健康监听配置不合法" in err and msg in err
