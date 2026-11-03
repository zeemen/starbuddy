"""学习服务内部模型路由接口（specs/adapter-zhixing-sprint2 设计要点 5）：鉴权绑定学科、严格校验、错误映射、日志不含内容、契约一致。

不需要数据库：模型路由注入 test 环境的假供应商，不进入 lifespan。全部合成数据（规则 8）。
"""
from __future__ import annotations

import json
import logging
import uuid

import httpx
import pytest

from learning.app import create_app
from learning.config import Settings
from learning.contract import internal_contract
from learning.model_router import DEFAULT_PATH, FakeProvider, MemorySink, ModelRouter, ProviderError, ProviderReply, Usage, load

from conftest import ADAPTER_CREDENTIAL, CREDENTIAL, settings

ZX = "synthetic-zhixing-route-client-token-0001"
AYA = "synthetic-aya-route-client-token-00000001"
SECRET = "合成作答-不应进日志-5d2c"


def body(**over) -> dict:
    b = {"task": "T2", "data_class": "L2", "subject": "zhixing", "prompt_template": "zhixing-m3-grade", "prompt_version": "0.1.0",
         "messages": [{"role": "system", "content": "合成系统提示"}, {"role": "user", "content": SECRET}],
         "usage_id": str(uuid.uuid4()), "idempotency_key": str(uuid.uuid4())}
    b.update(over)
    return b


def make_router(behavior=None) -> ModelRouter:
    cfg = load(DEFAULT_PATH, "test")

    async def ok(t, m, k):
        return ProviderReply(output='{"points": []}', usage=Usage(3, 4), reported_model=t.api_name)

    return ModelRouter(cfg, {v: FakeProvider(cfg.vendors[v], behavior or ok) for v in cfg.vendors}, sink=MemorySink())


@pytest.fixture
async def client():
    app = create_app(settings(route_clients={"zhixing": ZX, "aya": AYA}), model_router=make_router())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://learning.test") as c:
        yield c


def auth(tok: str) -> dict:
    return {"authorization": f"Bearer {tok}", "content-type": "application/json"}


async def test_route_returns_output_and_versions(client):
    r = await client.post("/internal/v1/model/route", json=body(), headers=auth(ZX))
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["model_version"] == "model:fake-a/fake-reasoner@2026-09-01" and out["source_version"] == "prompt:zhixing-m3-grade@0.1.0"
    assert out["model_mismatch"] is False
    c = internal_contract("1.0.0")
    assert c.validate("ModelRouteRequest", body()) == [] and c.validate("ModelRouteResponse", out) == []


@pytest.mark.parametrize("header", [None, "Bearer wrong", f"Bearer {CREDENTIAL}", f"Bearer {ADAPTER_CREDENTIAL}", f"bearer {ZX}", f"Bearer {ZX}x"])
async def test_only_route_client_tokens_are_accepted(client, header):
    h = {"content-type": "application/json"} | ({"authorization": header} if header else {})
    r = await client.post("/internal/v1/model/route", json=body(), headers=h)
    assert r.status_code == 401 and r.json()["code"] == "UNAUTHENTICATED"
    assert internal_contract("1.0.0").validate("Problem", r.json()) == []


async def test_uppercase_usage_id_is_accepted(client):
    r = await client.post("/internal/v1/model/route", json=body(usage_id=str(uuid.uuid4()).upper()), headers=auth(ZX))
    assert r.status_code == 200, r.text


async def test_token_is_bound_to_subject(client):
    r = await client.post("/internal/v1/model/route", json=body(), headers=auth(AYA))
    assert r.status_code == 403 and r.json()["code"] == "SUBJECT_MISMATCH"


@pytest.mark.parametrize("over", [
    {"task": "T10"}, {"data_class": "L4"}, {"messages": []}, {"messages": [{"role": "tool", "content": "x"}]},
    {"messages": [{"role": "user", "content": "x" * 20001}]}, {"idempotency_key": "not-a-uuid"},
    {"idempotency_key": str(uuid.uuid1())}, {"usage_id": 7}, {"deadline_ms": 0}, {"deadline_ms": True}, {"extra": 1},
])
async def test_invalid_requests_are_rejected(client, over):
    r = await client.post("/internal/v1/model/route", json=body(**over), headers=auth(ZX))
    assert r.status_code == 400 and r.json()["code"] == "INVALID_REQUEST", over


async def test_router_rejection_and_unavailable_map_to_problems():
    app = create_app(settings(route_clients={"zhixing": ZX}), model_router=make_router())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        # T5 在骨架里没有适配器 → 路由拒绝
        r = await c.post("/internal/v1/model/route", json=body(task="T5"), headers=auth(ZX))
        assert r.status_code == 400 and r.json()["code"] == "ROUTE_REJECTED"
        # 不合契约的 prompt 名 → 路由拒绝
        r = await c.post("/internal/v1/model/route", json=body(prompt_template="Bad/Name"), headers=auth(ZX))
        assert r.status_code == 400 and r.json()["code"] == "ROUTE_REJECTED"

    async def down(t, m, k):
        raise ProviderError("server")

    app = create_app(settings(route_clients={"zhixing": ZX}), model_router=make_router(down))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post("/internal/v1/model/route", json=body(), headers=auth(ZX))
        assert r.status_code == 503 and r.json()["code"] == "ROUTE_UNAVAILABLE" and r.json()["retryable"] is True


async def test_no_router_configured_is_unavailable():
    app = create_app(settings(route_clients={"zhixing": ZX}))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post("/internal/v1/model/route", json=body(), headers=auth(ZX))
        assert r.status_code == 503 and r.json()["code"] == "ROUTE_UNAVAILABLE"


async def test_body_limit(client):
    r = await client.post("/internal/v1/model/route", content=b"{" + b" " * (1024 * 1024 + 10) + b"}", headers=auth(ZX))
    assert r.status_code == 413


async def test_logs_do_not_contain_messages_or_output(client, caplog):
    with caplog.at_level(logging.INFO, logger="starbuddy.learning"):
        r = await client.post("/internal/v1/model/route", json=body(), headers=auth(ZX))
    assert r.status_code == 200
    assert SECRET not in caplog.text and '{"points": []}' not in caplog.text and ZX not in caplog.text
    rec = json.loads(next(line for line in caplog.messages if '"model_route"' in line))
    assert rec["subject"] == "zhixing" and rec["task"] == "T2" and rec["status"] == 200


def test_settings_read_route_clients_and_require_distinct_tokens():
    base = {"LEARNING_ENV": "test", "LEARNING_SERVICE_TOKEN": CREDENTIAL, "LEARNING_DATABASE_URL": "postgresql://x"}
    s = Settings.from_env(base | {"LEARNING_ROUTE_CLIENT_ZHIXING_TOKEN": ZX})
    assert s.route_clients == {"zhixing": ZX}
    with pytest.raises(RuntimeError):
        Settings.from_env(base | {"LEARNING_ROUTE_CLIENT_ZHIXING_TOKEN": "short"})
    with pytest.raises(RuntimeError):
        Settings.from_env(base | {"LEARNING_ROUTE_CLIENT_ZHIXING_TOKEN": CREDENTIAL})
