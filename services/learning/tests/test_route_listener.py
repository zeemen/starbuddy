"""staging 回环路由监听（specs/launch-lt02-model-provider 决定记录（续）第 5 条）：回环监听只暴露模型路由接口、主监听不变、
非 staging 不起；端口配置的正反例。不连库、不联网、不真的绑端口（只用 ASGI 传输与监听计划）；全部合成值。"""
from __future__ import annotations

import uuid

import httpx
import pytest

from learning.app import create_app
from learning.config import Settings
from learning.model_router import DEFAULT_PATH, FakeProvider, MemorySink, ModelRouter, ProviderReply, Usage, load
from learning.route_listener import DEFAULT_ROUTE_PORT, LOOPBACK, ROUTE_PATH, listeners, route_only

from conftest import CREDENTIAL, settings

ZX = "synthetic-zhixing-route-client-credential-0002"


def _router() -> ModelRouter:
    cfg = load(DEFAULT_PATH, "test")

    async def ok(t, m, k):
        return ProviderReply('{"points": []}', Usage(1, 1), t.api_name)

    return ModelRouter(cfg, {v: FakeProvider(cfg.vendors[v], ok) for v in cfg.vendors}, sink=MemorySink())


def _body() -> dict:
    return {"task": "T2", "data_class": "L2", "subject": "zhixing", "prompt_template": "zhixing-m3-grade", "prompt_version": "0.1.0",
            "messages": [{"role": "user", "content": "合成作答"}], "usage_id": str(uuid.uuid4()), "idempotency_key": str(uuid.uuid4())}


@pytest.fixture
def app():
    return create_app(settings(route_clients={"zhixing": ZX}), model_router=_router())


async def test_loopback_listener_serves_only_the_route_endpoint(app):
    side = route_only(app)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=side), base_url="http://127.0.0.1:8785") as c:
        r = await c.post(ROUTE_PATH, json=_body(), headers={"authorization": f"Bearer {ZX}"})
        assert r.status_code == 200 and r.json()["model_version"].startswith("model:fake-a/")
        # 其他路径一律 404：内部写入、读取、重放、健康检查、路由接口的变体
        for method, path in [("POST", "/internal/v1/grade"), ("POST", "/internal/v1/diagnose"), ("GET", "/internal/v1/replay/grade/x"),
                             ("POST", "/internal/v1/mastery/get"), ("POST", "/internal/v0/grade"), ("GET", "/"), ("GET", "/healthz"),
                             ("POST", ROUTE_PATH + "/"), ("POST", "/internal/v1/model/route/../../v1/grade"), ("POST", "/INTERNAL/V1/MODEL/ROUTE"),
                             ("POST", "/internal/v1/model"), ("GET", "/docs"), ("GET", "/openapi.json")]:
            r = await c.request(method, path, headers={"authorization": f"Bearer {CREDENTIAL}"}, json={})
            assert r.status_code == 404, (method, path, r.status_code)
            assert r.headers["content-type"] == "application/problem+json"
        # 路由接口照样要路由客户端令牌：服务间令牌不行
        r = await c.post(ROUTE_PATH, json=_body(), headers={"authorization": f"Bearer {CREDENTIAL}"})
        assert r.status_code == 401


async def test_main_listener_unchanged(app):
    """主监听仍服务全部接口（含模型路由接口），行为与之前一致"""
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post(ROUTE_PATH, json=_body(), headers={"authorization": f"Bearer {ZX}"})
        assert r.status_code == 200
        r = await c.post("/internal/v1/grade", json={}, headers={"authorization": f"Bearer {CREDENTIAL}"})
        assert r.status_code != 404, "主监听的其他接口不受影响"


async def test_loopback_wrapper_does_not_run_app_lifespan():
    """回环监听不重复跑应用的启动与关闭（共用主监听那一份状态）"""
    called = []

    async def inner(scope, receive, send):
        called.append(scope["type"])

    msgs = [{"type": "lifespan.startup"}, {"type": "lifespan.shutdown"}]
    sent = []

    async def receive():
        return msgs.pop(0)

    async def send(m):
        sent.append(m["type"])

    await route_only(inner)({"type": "lifespan"}, receive, send)
    assert called == [] and sent == ["lifespan.startup.complete", "lifespan.shutdown.complete"]


def _staging_env(**extra) -> dict[str, str]:
    e = {"LEARNING_ENV": "staging", "LEARNING_SERVICE_TOKEN": "s" * 40, "LEARNING_DATABASE_URL": "postgresql://syn@127.0.0.1/syn",
         "LEARNING_HOST": "10.0.0.5"}
    e.update(extra)
    return e


def test_staging_starts_loopback_listener_by_default_and_port_is_configurable():
    s = Settings.from_env(_staging_env())
    assert s.route_port == DEFAULT_ROUTE_PORT == 8785
    assert listeners(s, 8788) == [("10.0.0.5", 8788, False), (LOOPBACK, 8785, True)], "主监听不变，另加回环路由监听"
    s2 = Settings.from_env(_staging_env(LEARNING_ROUTE_PORT="9100"))
    assert listeners(s2, 8788)[1] == ("127.0.0.1", 9100, True)


@pytest.mark.parametrize("env", ["dev", "test"])
def test_non_staging_does_not_start_loopback_listener(env):
    base = {"LEARNING_ENV": env, "LEARNING_SERVICE_TOKEN": "s" * 40, "LEARNING_DATABASE_URL": "postgresql://syn@127.0.0.1/syn"}
    s = Settings.from_env(base)
    assert s.route_port is None and listeners(s, 8788) == [("127.0.0.1", 8788, False)]
    with pytest.raises(RuntimeError, match="只在 staging"):
        Settings.from_env(base | {"LEARNING_ROUTE_PORT": "8785"})


@pytest.mark.parametrize("port", ["8788", "80", "0", "65536", "08785", "8785x", "-1", " 8785"])
def test_bad_loopback_port_refuses_to_start(port):
    with pytest.raises(RuntimeError, match="LEARNING_ROUTE_PORT"):
        Settings.from_env(_staging_env(LEARNING_ROUTE_PORT=port))


def test_loopback_port_must_differ_from_custom_main_port():
    with pytest.raises(RuntimeError, match="主监听"):
        Settings.from_env(_staging_env(PORT="9000", LEARNING_ROUTE_PORT="9000"))
    assert Settings.from_env(_staging_env(PORT="9000")).route_port == 8785
