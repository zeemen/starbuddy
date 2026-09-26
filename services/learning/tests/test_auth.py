"""服务间令牌的负向用例（冲刺 3-B 攻击面 F1、F3）：各种变形都返回 401，而且鉴权失败时不读请求体。

不需要数据库：鉴权在读请求体、调用业务之前完成，这里不进入 lifespan，也不建连接池。
"""
from __future__ import annotations

import httpx
import pytest

from learning.app import create_app
from learning.contract import internal_contract

from conftest import ADAPTER_CREDENTIAL, CREDENTIAL, settings

ROUTES = ("/internal/v0/grade", "/internal/v0/diagnose")

# 每一项都必须被拒绝：缺失、错误、前缀或后缀、大小写、多空格、别的方案、超长、混用的令牌
BAD_HEADERS: dict[str, str | None] = {
    "缺少 authorization": None,
    "空值": "",
    "只有 Bearer": "Bearer",
    "Bearer 加一个空格": "Bearer ",
    "错误令牌": "Bearer wrong",
    "令牌前缀": f"Bearer {CREDENTIAL[:-1]}",
    "令牌多一个字符": f"Bearer {CREDENTIAL}x",
    "令牌前面多字符": f"Bearer x{CREDENTIAL}",
    "令牌转大写": f"Bearer {CREDENTIAL.upper()}",
    "方案小写": f"bearer {CREDENTIAL}",
    "方案大写": f"BEARER {CREDENTIAL}",
    "没有方案": CREDENTIAL,
    "Basic 方案": f"Basic {CREDENTIAL}",
    "Bearer 后两个空格": f"Bearer  {CREDENTIAL}",
    "Bearer 后制表符": f"Bearer\t{CREDENTIAL}",
    "令牌重复两次": f"Bearer {CREDENTIAL}{CREDENTIAL}",
    "两个令牌用逗号拼接": f"Bearer {CREDENTIAL}, Bearer {CREDENTIAL}",
    "超长头（64 KiB）": "Bearer " + "A" * 65536,
    "适配器令牌打学习服务（混用）": f"Bearer {ADAPTER_CREDENTIAL}",
}


@pytest.fixture
async def bare_api():
    # 不进入 lifespan：鉴权失败的请求不应该碰到连接池、适配器客户端或契约之外的任何东西
    app = create_app(settings())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://learning.test") as client:
        yield client


def test_credentials_are_distinct():
    # F3 的前提：两个令牌不同（部署时由 db/pentest/up.sh 自检同样的性质）
    assert CREDENTIAL != ADAPTER_CREDENTIAL


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize("label", list(BAD_HEADERS))
async def test_bad_token_is_rejected_without_reading_body(bare_api, route, label):
    header = BAD_HEADERS[label]
    consumed: list[int] = []

    async def body():
        consumed.append(1)  # 被读到就记一笔
        yield b'{"context": {}}'

    headers = {"content-type": "application/json"}
    if header is not None:
        headers["authorization"] = header
    r = await bare_api.post(route, content=body(), headers=headers)
    assert r.status_code == 401, (label, r.status_code)
    problem = r.json()
    assert problem["code"] == "UNAUTHENTICATED"
    assert internal_contract().validate("Problem", problem) == []
    assert consumed == [], f"{label}：鉴权失败时仍读了请求体"
    # 响应里不回显任何令牌内容
    assert CREDENTIAL not in r.text and ADAPTER_CREDENTIAL not in r.text


async def test_body_read_detector_works_with_valid_token(bare_api):
    # 对照组：令牌正确时请求体确实会被读，证明上面的“没读请求体”断言不是空转
    consumed: list[int] = []

    async def body():
        consumed.append(1)
        yield b"{not json"

    r = await bare_api.post("/internal/v0/grade", content=body(),
                            headers={"authorization": f"Bearer {CREDENTIAL}", "content-type": "application/json"})
    assert r.status_code == 400 and consumed == [1]


async def test_unknown_routes_and_methods_do_not_expose_docs(bare_api):
    # 关掉了 /docs、/openapi.json；GET 业务路由不是可用方法
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert (await bare_api.get(path)).status_code == 404
    assert (await bare_api.get("/internal/v0/grade", headers={"authorization": f"Bearer {CREDENTIAL}"})).status_code == 405
