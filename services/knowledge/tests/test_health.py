"""LT-03 健康端点（specs/launch-lt03-cloud-staging 范围第 4 条）：/healthz 存活、/readyz 就绪。
用假只读连接池（角色断言按安全角色回答、时区上海、SELECT 1 可切换为失败或挂起），不连库、不配页面快照；全部合成值。
两个端点在 / 上挂的 MCP（令牌前置）之前注册：不带令牌也能应答，别的路径仍是 MCP 的 401。"""
from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager

import httpx
import pytest

from knowledge.service import health
from knowledge.service.app import create_app
from knowledge.service.config import Settings

CREDS = {"bff": "synthetic-bff-to-knowledge-credential-001",
         "orchestrator": "synthetic-orchestrator-to-knowledge-0001",
         "learning": "synthetic-learning-to-knowledge-cred-0001"}
BASE = "http://127.0.0.1:8790"


class FakeConn:
    def __init__(self, pool: "FakePool") -> None:
        self.pool = pool

    async def fetchrow(self, sql: str, *_a):
        if "AS cur" in sql:
            return {"cur": "starbuddy_kge_read", "rolsuper": False, "rolbypassrls": False, "rolcreaterole": False, "rolcreatedb": False,
                    "is_reader": True, "others": [], "predefined": []}
        return {"rolsuper": False, "rolbypassrls": False, "rolcreaterole": False, "rolcreatedb": False}

    async def fetchval(self, sql: str, *_a):
        return await self.pool.fetchval(sql)


class FakePool:
    def __init__(self) -> None:
        self.mode = "ok"
        self.selects = 0

    @asynccontextmanager
    async def acquire(self):
        yield FakeConn(self)

    async def fetchval(self, sql: str, *_a):
        if sql.strip() == "SELECT 1":
            self.selects += 1
            if self.mode == "fail":
                raise ConnectionError("synthetic: connection lost 10.0.0.9 starbuddy_kge_read")
            if self.mode == "hang":
                await asyncio.sleep(3)
            return 1
        if "TimeZone" in sql:
            return "Asia/Shanghai"
        return None

    async def close(self) -> None:
        return None


def app_and_pool(host: str = "127.0.0.1"):
    pool = FakePool()
    return create_app(Settings(env="test", database_url="postgresql://unused", tokens=dict(CREDS), host=host), pool=pool), pool


async def get(app, path: str, **kw):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE) as c:
        return await c.request(kw.pop("method", "GET"), path, **kw)


def test_healthz_alive_without_dependencies_and_auth():
    app, pool = app_and_pool()

    async def go():
        r = await get(app, "/healthz")
        assert r.status_code == 200 and r.json() == {"status": "ok"}
        assert r.headers["cache-control"] == "no-store" and r.headers["x-content-type-options"] == "nosniff"
        assert pool.selects == 0
    asyncio.run(go())


def test_readyz_follows_startup_and_pool(monkeypatch):
    monkeypatch.setattr(health, "READY_TIMEOUT", 0.2)
    monkeypatch.setattr(health, "READY_CACHE", 0)  # 每次都真查（缓存与 single-flight 的用例在 learning/tests/test_health.py，代码三处逐字相同）
    app, pool = app_and_pool()

    async def go():
        r = await get(app, "/readyz")
        assert r.status_code == 503 and r.json() == {"status": "not_ready"}, "启动前置没跑完不能就绪"
        async with app.router.lifespan_context(app):
            r = await get(app, "/readyz")
            assert r.status_code == 200 and r.json() == {"status": "ready"} and r.headers["cache-control"] == "no-store"
            before = pool.selects
            pool.mode = "fail"
            r = await get(app, "/readyz")
            assert r.status_code == 503 and r.text == json.dumps({"status": "not_ready"}, separators=(",", ":")), "不回显异常"
            assert pool.selects == before + 1
            pool.mode = "hang"
            assert (await get(app, "/readyz")).status_code == 503, "超时算未就绪"
            pool.mode = "ok"
            assert (await get(app, "/readyz")).status_code == 200
        assert (await get(app, "/readyz")).status_code == 503, "关闭后不再就绪"
    asyncio.run(go())


def test_health_paths_only_get_host_checked_and_mcp_unchanged():
    app, _ = app_and_pool("172.28.0.12")
    pod = {"host": "172.28.0.12:8790"}

    async def go():
        async with app.router.lifespan_context(app):
            for path in ("/healthz", "/readyz"):
                # 只认 GET：别的方法落到 / 上的 MCP 令牌前置（401），不应答状态
                assert (await get(app, path, method="POST", headers=pod)).status_code == 401
                assert (await get(app, path, headers={"host": "evil.example.test"})).status_code == 400, "仍经 Host 校验"
                assert (await get(app, path, headers=pod)).status_code == 200, "k8s 探针的 Host 是 Pod 地址"
            # 别的路径照旧进 MCP 的令牌前置
            for path in ("/health", "/ready", "/mcp"):
                assert (await get(app, path, headers=pod)).status_code == 401
    asyncio.run(go())


@pytest.mark.parametrize("path", ["/healthz", "/readyz"])
def test_health_does_not_leak(path):
    app, _ = app_and_pool()

    async def go():
        async with app.router.lifespan_context(app):
            r = await get(app, path)
            assert set(r.json()) == {"status"}
            assert not any(k in r.headers for k in ("server-timing", "x-version", "x-request-id", "mcp-session-id"))
    asyncio.run(go())
