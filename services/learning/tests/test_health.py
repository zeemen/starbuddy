"""LT-03 健康端点（specs/launch-lt03-cloud-staging 范围第 4 条）：学习服务与审核台后端的 /healthz（存活）、/readyz（就绪）。

- 存活不访问任何依赖；就绪要求启动前置（契约、迁移 0016 / 0058 存在与执行权、审核台的两个登录角色）已通过，且连接池能 SELECT 1；
- 无需鉴权，响应只含状态（不回显异常、不带版本），no-store；只认 GET；
- 回环路由监听（route_listener，staging）不服务健康端点；
- 审核台：Host 仍校验；staging 显式配置的内网监听地址另收（前端与 k8s 探针经 Pod 地址访问），dev / test 不变。
用假连接池，不连库；全部合成值。"""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from learning import health
from learning.app import create_app
from learning.config import Settings
from learning.route_listener import route_only
from review_console import app as rc_app
from review_console.config import ReviewSettings

CRED = "s" * 40


class FakePool:
    """迁移存在性与执行权一律回真；SELECT 1 可切换为失败或挂起"""

    def __init__(self, missing_0058: bool = False) -> None:
        self.mode = "ok"
        self.selects = 0
        self.missing_0058 = missing_0058

    async def fetchval(self, sql: str, *_a):
        if sql.strip() == "SELECT 1":
            self.selects += 1
            if self.mode == "fail":
                raise ConnectionError("synthetic: connection lost 10.0.0.9 starbuddy_learning")
            if self.mode == "hang":
                await asyncio.sleep(3)
            return 1
        if self.missing_0058 and "to_regprocedure" in sql:
            return False
        return True

    async def close(self) -> None:
        return None


def learning_app(pool: FakePool):
    s = Settings.from_env({"LEARNING_ENV": "test", "LEARNING_SERVICE_TOKEN": CRED, "LEARNING_DATABASE_URL": "postgresql://syn@127.0.0.1/syn"})
    return create_app(s, pool=pool)


async def call(app, path: str, method: str = "GET", base: str = "http://127.0.0.1:8788", **kw):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=base) as c:
        return await c.request(method, path, **kw)


ONELINE = {s: json.dumps({"status": s}, separators=(",", ":")) for s in ("ok", "ready", "not_ready")}


def test_learning_healthz_alive_without_dependencies():
    pool = FakePool()
    app = learning_app(pool)

    async def go():
        r = await call(app, "/healthz")  # 没跑 lifespan：存活不依赖库
        assert r.status_code == 200 and r.json() == {"status": "ok"}
        assert r.headers["cache-control"] == "no-store" and r.headers["x-content-type-options"] == "nosniff"
        assert pool.selects == 0
    asyncio.run(go())


def test_learning_readyz_follows_startup_and_pool(monkeypatch):
    monkeypatch.setattr(health, "READY_TIMEOUT", 0.2)
    monkeypatch.setattr(health, "READY_CACHE", 0)  # 每次都真查（缓存另有用例）
    pool = FakePool()
    app = learning_app(pool)

    async def go():
        r = await call(app, "/readyz")
        assert r.status_code == 503 and r.text == ONELINE["not_ready"], "启动前置没跑完不能就绪"
        async with app.router.lifespan_context(app):
            r = await call(app, "/readyz")
            assert r.status_code == 200 and r.text == ONELINE["ready"] and r.headers["cache-control"] == "no-store"
            before = pool.selects
            pool.mode = "fail"
            r = await call(app, "/readyz")
            assert r.status_code == 503 and r.text == ONELINE["not_ready"], "不回显异常"
            assert pool.selects == before + 1, "就绪每次都真的查库"
            pool.mode = "hang"
            assert (await call(app, "/readyz")).status_code == 503, "超时算未就绪"
            pool.mode = "ok"
            assert (await call(app, "/readyz")).status_code == 200
            # 无需令牌；带错的令牌也一样（不做鉴权，也不因此泄露什么）
            assert (await call(app, "/readyz", headers={"authorization": "Bearer wrong"})).status_code == 200
        assert (await call(app, "/readyz")).status_code == 503, "关闭后不再就绪"
    asyncio.run(go())


def test_learning_readyz_requires_0058_privilege_after_startup(monkeypatch):
    """契约审查建议 4：启动之后 0058 被回滚或执行权被收回，就绪探针变 503"""
    monkeypatch.setattr(health, "READY_CACHE", 0)
    pool = FakePool()
    app = learning_app(pool)

    async def go():
        async with app.router.lifespan_context(app):
            assert (await call(app, "/readyz")).status_code == 200
            pool.missing_0058 = True
            assert (await call(app, "/readyz")).status_code == 503
            pool.missing_0058 = False
            assert (await call(app, "/readyz")).status_code == 200
    asyncio.run(go())
    from learning import app as app_mod
    sql = app_mod.READY_METER_SQL
    assert "has_function_privilege(current_user, to_regprocedure($1), 'EXECUTE')" in sql and "to_regprocedure($1) IS NULL THEN false" in sql


def test_ready_check_single_flight_and_cache(monkeypatch):
    """安全审查采纳建议：并发探测共用一次检查；结果在 READY_CACHE 秒内复用，过期重查；超时与抛错算未就绪"""
    monkeypatch.setattr(health, "READY_CACHE", 3.0)
    monkeypatch.setattr(health, "READY_TIMEOUT", 0.2)
    clock = [100.0]
    calls = [0]
    gate: dict = {}

    async def probe() -> bool:
        calls[0] += 1
        await gate["ev"].wait()
        return gate.get("ok", True)

    async def go():
        gate["ev"] = asyncio.Event()
        rc = health.ReadyCheck(probe, clock=lambda: clock[0])
        a, b = asyncio.ensure_future(rc()), asyncio.ensure_future(rc())
        for _ in range(50):
            if calls[0]:
                break
            await asyncio.sleep(0.005)
        await asyncio.sleep(0.02)
        assert calls[0] == 1, "并发只跑一次"
        gate["ev"].set()
        assert await a is True and await b is True
        clock[0] += 2.9
        assert await rc() is True and calls[0] == 1, "缓存期内不重查"
        clock[0] += 0.2
        gate["ok"] = False
        assert await rc() is False and calls[0] == 2, "过期后重查"
        clock[0] += 10
        gate["ev"] = asyncio.Event()  # 挂起：超时算未就绪
        assert await rc() is False and calls[0] == 3
        clock[0] += 10

        async def boom() -> bool:
            raise ConnectionError("synthetic")
        assert await health.ReadyCheck(boom, clock=lambda: clock[0])() is False
    asyncio.run(go())


def test_learning_startup_refused_never_ready():
    """缺 0058：启动即拒绝（lifespan 抛错），健康端点不会变成就绪"""
    app = learning_app(FakePool(missing_0058=True))

    async def go():
        with pytest.raises(RuntimeError, match="0058"):
            async with app.router.lifespan_context(app):
                pass
        assert (await call(app, "/readyz")).status_code == 503
    asyncio.run(go())


def test_learning_health_only_get_and_not_on_route_listener():
    app = learning_app(FakePool())

    async def go():
        async with app.router.lifespan_context(app):
            for path in ("/healthz", "/readyz"):
                assert (await call(app, path, method="POST")).status_code == 405
                r = await call(route_only(app), path, base="http://127.0.0.1:8785")
                assert r.status_code == 404, "回环路由监听只服务模型路由接口"
            for path in ("/health", "/ready", "/healthz/x"):
                assert (await call(app, path)).status_code == 404
    asyncio.run(go())


# ---------------------------------------------------------------- 审核台后端


class RolePool:
    def __init__(self, user: str, role: str) -> None:
        self.user, self.role = user, role
        self.mode = "ok"

    async def fetchrow(self, _sql, *_a):
        return {"u": self.user, "rolsuper": False, "rolbypassrls": False, "app_roles": [self.role]}

    async def fetchval(self, sql: str, *_a):
        if sql.strip() == "SELECT 1":
            if self.mode == "fail":
                raise ConnectionError("synthetic")
            return 1
        return None


async def _no_staff(_request):
    return None


def review_app(settings: ReviewSettings | None = None):
    kp, rp = RolePool("kge_syn", "starbuddy_kge"), RolePool("review_syn", "starbuddy_review")
    app = rc_app.create_app(settings, authenticate=_no_staff, pools=(kp, rp), pages_settings=None)
    return app, kp, rp


def test_review_console_health(monkeypatch):
    monkeypatch.setattr(health, "READY_TIMEOUT", 0.2)
    monkeypatch.setattr(health, "READY_CACHE", 0)
    app, kp, rp = review_app()

    async def go():
        assert (await call(app, "/healthz", base="http://127.0.0.1:8792")).json() == {"status": "ok"}
        assert (await call(app, "/readyz", base="http://127.0.0.1:8792")).status_code == 503
        async with app.router.lifespan_context(app):
            r = await call(app, "/readyz", base="http://127.0.0.1:8792")
            assert r.status_code == 200 and r.text == ONELINE["ready"], "不要 staff 会话"
            rp.mode = "fail"
            r = await call(app, "/readyz", base="http://127.0.0.1:8792")
            assert r.status_code == 503 and r.text == ONELINE["not_ready"], "两个连接池都要可用"
            rp.mode = "ok"
            kp.mode = "fail"
            assert (await call(app, "/readyz", base="http://127.0.0.1:8792")).status_code == 503
            kp.mode = "ok"
            # 别的接口照旧要会话；健康端点只认 GET（别的方法走正常路径：要会话）
            assert (await call(app, "/review/v0/auth/me", base="http://127.0.0.1:8792")).status_code == 401
            assert (await call(app, "/healthz", method="POST", base="http://127.0.0.1:8792")).status_code == 401
            # Host 仍校验（DNS 重绑定）
            r = await call(app, "/healthz", base="http://127.0.0.1:8792", headers={"host": "evil.example.test"})
            assert r.status_code == 421
    asyncio.run(go())


def review_settings(env: str, host: str, service_ip: str | None = None) -> ReviewSettings:
    extra = {"REVIEW_CONSOLE_HOST": host} if host != "127.0.0.1" else {}
    if service_ip is not None:
        extra["REVIEW_CONSOLE_EXTRA_HOST"] = service_ip
    return ReviewSettings.from_env({"REVIEW_CONSOLE_ENV": env, "REVIEW_CONSOLE_KGE_DATABASE_URL": "postgresql://kge_syn@127.0.0.1/syn",
                                    "REVIEW_CONSOLE_REVIEW_DATABASE_URL": "postgresql://review_syn@127.0.0.1/syn", **extra})


def test_review_console_extra_host_is_service_ip_only_in_staging():
    """契约审查阻断 2：前端经 Service clusterIP 访问审核台，Host 头是 clusterIP——staging 另收 REVIEW_CONSOLE_EXTRA_HOST 这一个地址"""
    s = review_settings("staging", "172.28.0.13", "10.96.100.13")
    assert rc_app.allowed_hosts(s) == rc_app.LOOPBACK_HOSTS | {"172.28.0.13", "10.96.100.13"}
    for bad in ("8.8.8.8", "review-console", "10.96.100.13:8792", "0.0.0.0", "010.96.100.13"):
        with pytest.raises(RuntimeError, match="REVIEW_CONSOLE_EXTRA_HOST"):
            review_settings("staging", "172.28.0.13", bad)
    for env in ("dev", "test"):
        with pytest.raises(RuntimeError, match="只有 staging"):
            review_settings(env, "127.0.0.1", "10.96.100.13")
    app, _, _ = review_app(s)

    async def go():
        async with app.router.lifespan_context(app):
            for good in ("10.96.100.13:8792", "172.28.0.13:8792"):
                assert (await call(app, "/readyz", base="http://127.0.0.1:8792", headers={"host": good})).status_code == 200
            assert (await call(app, "/readyz", base="http://127.0.0.1:8792", headers={"host": "10.96.100.14:8792"})).status_code == 421
    asyncio.run(go())


def test_review_console_hosts_staging_listen_address_only():
    s = review_settings("staging", "172.28.0.13")
    assert rc_app.allowed_hosts(s) == rc_app.LOOPBACK_HOSTS | {"172.28.0.13"}
    assert rc_app.allowed_hosts(review_settings("staging", "127.0.0.1")) == rc_app.LOOPBACK_HOSTS
    assert rc_app.allowed_hosts(review_settings("dev", "127.0.0.1")) == rc_app.LOOPBACK_HOSTS
    assert rc_app.allowed_hosts(None) == rc_app.LOOPBACK_HOSTS
    # dev / test 的 settings 即使带着别的 host（构造器直接给）也不放宽
    assert rc_app.allowed_hosts(ReviewSettings(env="test", kge_database_url="x", review_database_url="y", host="172.28.0.13")) \
        == rc_app.LOOPBACK_HOSTS
    app, _, _ = review_app(s)

    async def go():
        async with app.router.lifespan_context(app):
            ok = await call(app, "/readyz", base="http://127.0.0.1:8792", headers={"host": "172.28.0.13:8792"})
            assert ok.status_code == 200, "k8s 探针与前端经 Pod 内网地址访问"
            for bad in ("172.28.0.14:8792", "10.0.0.1", "review-console:8792", "evil.example.test"):
                r = await call(app, "/readyz", base="http://127.0.0.1:8792", headers={"host": bad})
                assert r.status_code == 421, bad
                r = await call(app, "/review/v0/auth/me", base="http://127.0.0.1:8792", headers={"host": bad})
                assert r.status_code == 421, bad
    asyncio.run(go())
