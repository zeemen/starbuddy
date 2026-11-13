"""PLT-24（T49）：学习服务“路由 × 令牌”矩阵。

每条路由登记它认的那一组令牌；新增路由不登记，本文件第一条测试就失败。
- bff_write（写入令牌 LEARNING_SERVICE_TOKEN）：契约 learning-internal v1 的写入、重放、今日计划、星图；
- bff_session（会话令牌 LEARNING_SESSION_TOKEN）：诊断、错题再练、复测、今日编排四组服务自有接口；
- other：编排层、洞察服务、路由客户端的接口（两枚 BFF 令牌都打不开）；
- retired / public：v0 已下线（一律 410，不验令牌）；健康检查与指标。
被拒时的响应与“没带令牌”逐字节相同（状态码、正文），不透露令牌属于哪一组。
"""
from __future__ import annotations

import uuid

import httpx
import pytest

from conftest import CREDENTIAL, SESSION_CREDENTIAL, settings
from learning.app import create_app

ORCH = "synthetic-orchestrator-to-learning-cred-01"
INSIGHT = "synthetic-insight-to-learning-credential-1"
ROUTE = "synthetic-aya-adapter-route-client-cred-01"

GROUPS: dict[tuple[str, str], str] = {
    ("GET", "/healthz"): "public",
    ("GET", "/readyz"): "public",
    ("GET", "/metrics"): "public",
    ("POST", "/internal/v0/grade"): "retired",
    ("POST", "/internal/v0/diagnose"): "retired",
    ("POST", "/internal/v1/grade"): "bff_write",
    ("POST", "/internal/v1/diagnose"): "bff_write",
    ("GET", "/internal/v1/replay/{method}/{key}"): "bff_write",
    ("POST", "/internal/v1/plan"): "bff_write",
    ("POST", "/internal/v1/star-map/nodes"): "bff_write",
    ("POST", "/internal/v1/diagnosis/sessions"): "bff_session",
    ("GET", "/internal/v1/diagnosis/sessions/{session_id}"): "bff_session",
    ("POST", "/internal/v1/diagnosis/sessions/{session_id}/probe"): "bff_session",
    ("POST", "/internal/v1/diagnosis/sessions/{session_id}/answer"): "bff_session",
    ("POST", "/internal/v1/diagnosis/sessions/{session_id}/pause"): "bff_session",
    ("POST", "/internal/v1/diagnosis/sessions/{session_id}/abandon"): "bff_session",
    ("POST", "/internal/v1/practice/queue"): "bff_session",
    ("POST", "/internal/v1/practice/next"): "bff_session",
    ("POST", "/internal/v1/practice/answer"): "bff_session",
    ("POST", "/internal/v1/retest/queue"): "bff_session",
    ("POST", "/internal/v1/retest/next"): "bff_session",
    ("POST", "/internal/v1/retest/answer"): "bff_session",
    ("POST", "/internal/v1/retest/check"): "bff_session",
    ("POST", "/internal/v1/retest/replay"): "bff_session",
    ("POST", "/internal/v1/today"): "bff_session",
    ("POST", "/internal/v1/profile-page/ensure"): "other",
    ("POST", "/internal/v1/profile-page/read"): "other",
    ("POST", "/internal/v1/mastery/get"): "other",
    ("POST", "/internal/v1/mastery/explain"): "other",
    ("POST", "/internal/v1/verification"): "other",
    ("POST", "/internal/v1/verification/cancel"): "other",
    ("POST", "/internal/v1/model/route"): "other",
}

TOKENS = {"bff_write": CREDENTIAL, "bff_session": SESSION_CREDENTIAL}


def _app(**over):
    s = settings(orchestrator_client_credential=ORCH, insight_client_credential=INSIGHT, route_clients={"aya": ROUTE}, **over)
    return create_app(s)


def _routes(app) -> set[tuple[str, str]]:
    out: set[tuple[str, str]] = set()
    for r in app.routes:
        methods = getattr(r, "methods", None) or set()
        path = getattr(r, "path", "")
        for m in methods:
            if path == "/metrics":
                if m == "GET":
                    out.add(("GET", path))
            elif m != "HEAD":
                out.add((m, path))
    return out


def _url(path: str) -> str:
    return (path.replace("{method}", "grade").replace("{key}", str(uuid.uuid4()))
            .replace("{session_id}", str(uuid.uuid4())))


async def _call(c: httpx.AsyncClient, method: str, path: str, token: str | None) -> httpx.Response:
    headers = {} if token is None else {"authorization": f"Bearer {token}"}
    if method == "GET":
        return await c.get(_url(path), headers=headers)
    return await c.post(_url(path), json={}, headers=headers)


def test_every_route_is_registered_in_the_matrix():
    app = _app()
    routes = _routes(app)
    assert routes == set(GROUPS), f"未登记：{sorted(routes - set(GROUPS))}；多登记：{sorted(set(GROUPS) - routes)}"


@pytest.mark.parametrize("route", sorted(k for k, g in GROUPS.items() if g in TOKENS), ids=lambda r: f"{r[0]} {r[1]}")
async def test_bff_routes_only_open_with_their_own_group_token(route):
    method, path = route
    group = GROUPS[route]
    other = SESSION_CREDENTIAL if group == "bff_write" else CREDENTIAL
    app = _app()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
                                 base_url="http://learning.test") as c:
        none = await _call(c, method, path, None)
        assert none.status_code == 401, none.text
        for bad in (other, ORCH, INSIGHT, ROUTE, f"{TOKENS[group]}x"):
            r = await _call(c, method, path, bad)
            assert (r.status_code, r.content) == (none.status_code, none.content), f"{path} 收了不属于 {group} 的令牌"
        ok = await _call(c, method, path, TOKENS[group])
        assert ok.status_code != 401, f"{path}：{group} 令牌被拒"


@pytest.mark.parametrize("route", sorted(k for k, g in GROUPS.items() if g == "other"), ids=lambda r: f"{r[0]} {r[1]}")
async def test_neither_bff_token_opens_other_callers_routes(route):
    method, path = route
    app = _app()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
                                 base_url="http://learning.test") as c:
        none = await _call(c, method, path, None)
        assert none.status_code == 401, none.text
        for bad in (CREDENTIAL, SESSION_CREDENTIAL):
            r = await _call(c, method, path, bad)
            assert (r.status_code, r.content) == (none.status_code, none.content), path


@pytest.mark.parametrize("route", sorted(k for k, g in GROUPS.items() if g == "bff_session"), ids=lambda r: f"{r[0]} {r[1]}")
async def test_session_routes_reject_everything_when_session_token_unset(route):
    """会话令牌没配置（dev / test 可以不配；staging 拒绝启动）：四组接口一律 401，写入令牌也打不开"""
    method, path = route
    app = _app(session_credential=None)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
                                 base_url="http://learning.test") as c:
        none = await _call(c, method, path, None)
        assert none.status_code == 401
        for bad in (CREDENTIAL, SESSION_CREDENTIAL):
            r = await _call(c, method, path, bad)
            assert (r.status_code, r.content) == (none.status_code, none.content), path


# ---------------------------------------------------------------- 启动配置核对（learning/config.py）
from learning.config import Settings  # noqa: E402

_W, _S = "w" * 40, "q" * 40


def _env(**extra) -> dict:
    e = {"LEARNING_ENV": "test", "LEARNING_SERVICE_TOKEN": _W, "LEARNING_DATABASE_URL": "postgresql://syn@127.0.0.1/syn"}
    e.update(extra)
    return {k: v for k, v in e.items() if v is not None}


def test_session_token_read_from_env_and_kept_out_of_repr():
    s = Settings.from_env(_env(LEARNING_SESSION_TOKEN=_S))
    assert s.session_credential == _S and s.service_credential == _W
    assert _S not in repr(s)
    assert Settings.from_env(_env()).session_credential is None  # dev / test 可以不配：四组接口 401


@pytest.mark.parametrize("bad", ["q" * 31, "x"])
def test_session_token_shorter_than_32_refused(bad):
    with pytest.raises(RuntimeError, match="LEARNING_SESSION_TOKEN 少于 32"):
        Settings.from_env(_env(LEARNING_SESSION_TOKEN=bad))


@pytest.mark.parametrize("other", ["LEARNING_SERVICE_TOKEN", "LEARNING_INSIGHT_CLIENT_TOKEN", "LEARNING_ORCHESTRATOR_CLIENT_TOKEN"])
def test_session_token_must_differ_from_every_other_token(other):
    e = _env(LEARNING_SESSION_TOKEN=_S)
    e[other] = _S
    with pytest.raises(RuntimeError, match="必须各不相同"):
        Settings.from_env(e)
