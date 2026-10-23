"""LT-05 可观测性（specs/launch-lt05-observability 范围第 1、2、7 条）：learning/obs.py 与 learning/metrics.py。

- 标签白名单：只收服务、路由模板、方法、状态码类别、学科、任务类型、供应商、结果；学科、任务类型、供应商、结果只收枚举（枚举外记 other），
  路由只收模板形状（UUID、手机号、邮箱、长数字、原始路径一律拒绝）；指标名必须 starbuddy_ 开头；写指标出错只丢这一条；
- /metrics 只在单独的指标端口（metrics_app：只服务 GET /metrics，别的路径 404、别的方法 405）；业务端口、回环路由监听上的
  /metrics 一律 404；no-store、Prometheus 文本格式；路由标签是模板；健康端点不计；审核台业务端口的 /metrics 不要会话、回 404、仍过 Host 校验；
- 计量核对计数、调用记录计数抛异常时结果不变；
- 注册的指标与 deploy/observability/metrics-inventory.yaml 逐项一致（学习服务、审核台后端两个进程分别核对）；
- 模型路由调用 / 失败 / 熔断 / 护栏剩余 / 估算成本、计量核对放行 / 409、复核队列积压；
- 日志：一行一个 JSON（ts、level、service、logger、请求内的 route 与 request_id）；顶层字段白名单（键名先驼峰归一，只留 LOG_FIELDS）；
  集中脱敏（禁止字段丢掉、手机号（含分隔）/ 邮箱 / 身份证 / 令牌 / 连接串替换、正文 UUID 替换、超长截断）；异常只记类名；
  源码里日志调用不出现禁止字段（静态扫描）。
用假连接池，不连库；全部合成值。"""
from __future__ import annotations

import ast
import asyncio
import io
import json
import logging
import re
import subprocess
import sys
from pathlib import Path

import httpx
import pytest
import yaml
from fastapi import FastAPI

from learning import meter, metrics, obs
from learning.model_router.breaker import CircuitBreaker
from learning.model_router.budget import DailyBudget
from learning.model_router.config import Budget
from learning.model_router.ledger import CallRecord
from learning.model_router.types import Attempt, Task
from learning.route_listener import route_only
from test_health import FakePool, call, learning_app, review_app

SVC = Path(__file__).resolve().parents[1]
ROOT = SVC.parents[1]
INVENTORY = yaml.safe_load((ROOT / "deploy/observability/metrics-inventory.yaml").read_text(encoding="utf-8"))
UUID = "5f0e0000-0000-4000-8000-00000000abcd"
PHONE = "13812345678"
SAMPLE = re.compile(r"^([a-zA-Z_:][a-zA-Z0-9_:]*)(\{(.*)\})? ")


def samples(text: str) -> list[tuple[str, dict[str, str]]]:
    out = []
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        m = SAMPLE.match(line)
        assert m, line
        labels = dict(re.findall(r'([a-zA-Z_][a-zA-Z0-9_]*)="((?:[^"\\]|\\.)*)"', m.group(3) or ""))
        out.append((m.group(1), labels))
    return out


def value(name: str, **labels: str) -> float:
    return obs.REGISTRY.get_sample_value(name, labels) or 0.0


# ---------------------------------------------------------------- 标签白名单
def test_label_whitelist_is_exactly_the_agreed_set():
    assert obs.LABELS == {"service", "route", "method", "status_class", "subject", "task", "provider", "outcome"}
    assert INVENTORY["labels"] == sorted(obs.LABELS, key=INVENTORY["labels"].index) and set(INVENTORY["labels"]) == obs.LABELS


@pytest.mark.parametrize("name,val", [
    ("service", "learning"), ("route", "/internal/v1/replay/{method}/{key}"), ("route", "unmatched"), ("method", "POST"),
    ("status_class", "4xx"), ("subject", "aya"), ("task", "T1"), ("provider", "bailian"), ("outcome", "skipped_breaker_open")])
def test_label_values_accept_low_cardinality_codes(name, val):
    assert obs.label_value(name, val) == val


@pytest.mark.parametrize("name,val", [
    ("student_id", "aya"), ("account", "x"), ("phone", "x"),                     # 标签名不在白名单
    ("route", f"/internal/v1/replay/grade/{UUID}"), ("route", "/bff/v0/students/123456/today"), ("route", "relative/path"),
    ("route", "/a b"), ("route", "/x/kid@example.invalid"),
    ("service", "unknown"), ("method", "BREW"), ("status_class", "404"), ("service", None)])
def test_label_values_reject_identifiers_and_raw_values(name, val):
    with pytest.raises(ValueError):
        obs.label_value(name, val)


@pytest.mark.parametrize("name,val", [
    ("subject", UUID), ("outcome", UUID.replace("-", "")), ("task", PHONE), ("provider", "a@b.example.invalid"),
    ("subject", "s" + PHONE), ("outcome", "a" + UUID), ("provider", "k" + UUID.replace("-", "")[:20]), ("task", "T10"),
    ("subject", "数学"), ("outcome", "x" * 49), ("subject", ""), ("outcome", None), ("provider", "openai"), ("task", "t1"),
    ("outcome", "OK"), ("subject", "math")])
def test_enum_labels_map_values_outside_the_enum_to_other(name, val):
    """学科、任务类型、供应商、结果只收枚举（安全审查 C）：枚举外的任何取值（含标识、手机号、自由文本）一律记 other"""
    assert obs.label_value(name, val) == "other"


def test_enum_sets_are_closed_and_low_cardinality():
    assert set(obs.ENUMS) == {"subject", "task", "provider", "outcome"}
    for name, values in obs.ENUMS.items():
        assert "other" in values and len(values) <= 64, name
        for v in values:
            assert re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,47}", v) and not obs._IDISH.search(v), (name, v)


def test_factories_refuse_bad_names_and_labels():
    with pytest.raises(ValueError):
        obs.counter("http_requests_total", "不带前缀", ("service",))
    with pytest.raises(ValueError):
        obs.counter("starbuddy_bad_label_total", "标签不在白名单", ("student_id",))
    with pytest.raises(ValueError):
        obs.gauge("starbuddy_bad_label2", "标签不在白名单", ("account_id",))
    m = obs.HTTP_REQUESTS
    with pytest.raises(ValueError):
        m.labels(service="learning", route="/x", method="GET")  # 少一个标签
    with pytest.raises(ValueError):
        m.labels(service="learning", route=f"/x/{UUID}", method="GET", status_class="2xx")


def test_observe_http_swallows_metric_errors(monkeypatch):
    """写指标出任何错只丢这一条（安全审查 C：中间件写指标 try/except）"""
    def boom(**kw):
        raise RuntimeError("synthetic")
    monkeypatch.setattr(obs.HTTP_REQUESTS, "labels", boom)
    obs.observe_http("learning", "/x", "GET", 200, 0.01)
    obs.observe_http("learning", f"/x/{UUID}", "GET", 200, 0.01)  # 路由不合格也只丢这一条


# ---------------------------------------------------------------- 中间件与 /metrics
async def scrape_metrics(path: str = "/metrics", method: str = "GET") -> httpx.Response:
    """单独的指标端口（metrics_app）"""
    return await call(obs.metrics_app, path, method, base="http://10.0.0.5:9788")
def _mini_app() -> FastAPI:
    app = FastAPI()

    @app.get("/v/{item}")
    async def item(item: str) -> dict:
        logging.getLogger("starbuddy.test").info(json.dumps({"event": "probe", "status": 200}))
        return {"ok": True}

    @app.get("/boom")
    async def boom() -> dict:
        raise RuntimeError("synthetic")

    obs.register(app, "insight")  # 用另一个服务名，避免与学习服务的计数混在一起
    return app


def test_middleware_uses_route_template_and_skips_probe_paths():
    app = _mini_app()
    before = value("starbuddy_http_requests_total", service="insight", route="/v/{item}", method="GET", status_class="2xx")
    unmatched = value("starbuddy_http_requests_total", service="insight", route="unmatched", method="GET", status_class="4xx")

    async def go():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url="http://127.0.0.1") as c:
            assert (await c.get(f"/v/{UUID}")).status_code == 200
            assert (await c.get(f"/nowhere/{PHONE}")).status_code == 404
            assert (await c.get("/boom")).status_code == 500
            biz = await c.get("/metrics")  # 业务端口上的 /metrics 一律 404，也不计数
            assert biz.status_code == 404 and biz.json() == {"status": "not_found"}
            r = await scrape_metrics()
            assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
            assert r.headers["content-type"].startswith("text/plain") and r.headers["x-content-type-options"] == "nosniff"
            return r.text
    text = asyncio.run(go())
    assert value("starbuddy_http_requests_total", service="insight", route="/v/{item}", method="GET", status_class="2xx") == before + 1
    assert value("starbuddy_http_requests_total", service="insight", route="unmatched", method="GET", status_class="4xx") == unmatched + 1
    assert value("starbuddy_http_requests_total", service="insight", route="/boom", method="GET", status_class="5xx") >= 1
    assert UUID not in text and PHONE not in text and "/nowhere" not in text
    assert 'route="/metrics"' not in text and 'route="/healthz"' not in text
    for name, labels in samples(text):
        assert name.startswith("starbuddy_"), name
        assert set(labels) <= obs.LABELS | {"le"}, (name, labels)


def test_metrics_port_serves_only_get_metrics():
    """指标端口（决定记录（续）第 5 条）：只服务 GET /metrics；别的路径（含业务接口、健康端点、带查询串、尾斜杠）404，别的方法 405"""
    async def go():
        ok = await scrape_metrics()
        assert ok.status_code == 200 and ok.headers["content-type"].startswith("text/plain")
        assert ok.headers["x-content-type-options"] == "nosniff"
        for p in ("/", "/metrics/", "/metrics?x=1", "/healthz", "/readyz", "/internal/v1/grade", "/review/v0/queue", "/Metrics"):
            r = await scrape_metrics(p)
            assert r.status_code == 404 and r.json() == {"status": "not_found"}, p
        for m in ("POST", "PUT", "DELETE"):
            r = await scrape_metrics("/metrics", m)
            assert r.status_code == 405 and r.headers["allow"] == "GET", m
    asyncio.run(go())


@pytest.mark.parametrize("raw,env,taken,want", [
    (None, "staging", (8788,), 9788), ("", "staging", (8788,), 9788), (None, "dev", (8788,), None), (None, "test", (8788,), None),
    ("9999", "test", (8788,), 9999), ("9100", "staging", (8788, 8785), 9100)])
def test_metrics_port_defaults(raw, env, taken, want):
    assert obs.metrics_port(raw, env, 9788, "X_METRICS_PORT", *taken) == want


@pytest.mark.parametrize("raw", ["0", "65536", "09788", "97x", "-1", " 9788", "8788", "8785"])
def test_metrics_port_refuses_bad_or_taken(raw):
    with pytest.raises(RuntimeError, match="X_METRICS_PORT"):
        obs.metrics_port(raw, "staging", 9788, "X_METRICS_PORT", 8788, 8785)


def test_review_console_metrics_port():
    from review_console.__main__ import metrics_listen_port
    assert metrics_listen_port("staging", 8792, {}) == 9792
    assert metrics_listen_port("dev", 8792, {}) is None
    assert metrics_listen_port("test", 8792, {"REVIEW_CONSOLE_METRICS_PORT": "9999"}) == 9999
    with pytest.raises(RuntimeError, match="REVIEW_CONSOLE_METRICS_PORT"):
        metrics_listen_port("staging", 8792, {"REVIEW_CONSOLE_METRICS_PORT": "8792"})


def test_learning_business_ports_do_not_serve_metrics():
    pool = FakePool()
    app = learning_app(pool)

    async def go():
        async with app.router.lifespan_context(app):
            for m in ("GET", "POST", "HEAD"):
                r = await call(app, "/metrics", m)
                assert r.status_code == 404 and "starbuddy_" not in r.text, m
            r = await scrape_metrics()
            names = {n for n, _ in samples(r.text)}
            assert "starbuddy_ready" in names and "starbuddy_http_requests_total" in names
            # 回环路由监听只服务模型路由接口
            assert (await call(route_only(app), "/metrics", base="http://127.0.0.1:8785")).status_code == 404
            # 未鉴权的业务接口照旧 401，并按模板计数
            assert (await call(app, "/internal/v1/grade", "POST", content=b"{}")).status_code == 401
    asyncio.run(go())
    assert value("starbuddy_http_requests_total", service="learning", route="/internal/v1/grade", method="POST", status_class="4xx") >= 1


def test_review_console_business_port_metrics_404_without_session_but_check_host():
    app, _kp, _rp = review_app()

    async def go():
        async with app.router.lifespan_context(app):
            nf = await call(app, "/metrics", base="http://127.0.0.1:8792")
            assert nf.status_code == 404 and nf.json() == {"status": "not_found"}, "不要会话，只回 404（指标只在单独的指标端口）"
            bad = await call(app, "/metrics", base="http://evil.example.invalid")
            assert bad.status_code == 421
            assert (await call(app, "/review/v0/queue", base="http://127.0.0.1:8792")).status_code == 401
    asyncio.run(go())
    assert value("starbuddy_http_requests_total", service="review-console", route="unmatched", method="GET", status_class="4xx") >= 1


def test_pool_and_ready_gauges_read_at_scrape():
    class Pool:
        def get_size(self):
            return 4

        def get_idle_size(self):
            return 1

        def get_max_size(self):
            return 10

    class Ready:
        _last = None
    ready = Ready()
    obs.track("knowledge", pools=lambda: [Pool(), None, Pool()], ready=ready)
    try:
        assert value("starbuddy_db_pool_size", service="knowledge") == 8.0
        assert value("starbuddy_db_pool_idle", service="knowledge") == 2.0
        assert value("starbuddy_db_pool_max", service="knowledge") == 20.0
        assert value("starbuddy_ready", service="knowledge") == 0.0, "还没检查过算未就绪"
        ready._last = (1.0, True)
        assert value("starbuddy_ready", service="knowledge") == 1.0
        ready._last = (2.0, False)
        assert value("starbuddy_ready", service="knowledge") == 0.0
    finally:
        obs.untrack("knowledge")
    assert value("starbuddy_db_pool_size", service="knowledge") == 0.0


# ---------------------------------------------------------------- 登记清单
@pytest.mark.parametrize("module,service", [("learning.app", "learning"), ("review_console.app", "review-console")])
def test_registered_metrics_match_inventory(module, service):
    code = f"import {module}, json; from learning import obs; print(json.dumps(obs.catalog()))"
    out = subprocess.run([sys.executable, "-c", code], cwd=SVC, capture_output=True, text=True, check=True,
                         env={"PATH": "/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1"})
    assert json.loads(out.stdout) == sorted(INVENTORY["services"][service]), "改了指标要同步 deploy/observability/metrics-inventory.yaml"


# ---------------------------------------------------------------- 模型路由、计量核对、复核积压
def test_router_sink_counts_calls_failures_and_cost():
    rec = CallRecord(env="test", task="T1", data_class="L1", subject="aya", usage_ref="0" * 16, idempotency_ref="1" * 16,
                     source_version="prompt:x@v1", outcome="ok", model_version="m-v1",
                     attempts=(Attempt("ark", "m-v1", "timeout", 5), Attempt("bailian", "m-v1", "ok", 9)),
                     input_tokens=10, output_tokens=20, elapsed_ms=14, estimated_cost_cny=0.25)
    base = (value("starbuddy_model_route_calls_total", task="T1", outcome="ok"),
            value("starbuddy_model_provider_calls_total", provider="ark", task="T1", outcome="timeout"),
            value("starbuddy_model_estimated_cost_cny_total", provider="bailian", task="T1"))
    metrics.router_sink().write(rec)
    assert value("starbuddy_model_route_calls_total", task="T1", outcome="ok") == base[0] + 1
    assert value("starbuddy_model_provider_calls_total", provider="ark", task="T1", outcome="timeout") == base[1] + 1
    assert value("starbuddy_model_estimated_cost_cny_total", provider="bailian", task="T1") == pytest.approx(base[2] + 0.25)
    assert value("starbuddy_model_estimated_cost_cny_total", provider="ark", task="T1") == 0.0  # 失败的尝试不计成本


def test_breaker_and_budget_state_are_read_at_scrape():
    t = [0.0]
    breaker = CircuitBreaker(1, 30, clock=lambda: t[0])
    tk = breaker.acquire("bailian", "T2")
    breaker.failure(tk)
    budget = DailyBudget({Task.T2: Budget(daily_calls=4, daily_tokens=1000)}, wall_clock=lambda: 1_800_000_000.0)
    r = budget.reserve(Task.T2)
    budget.settle(r, 900)

    class Router:
        pass
    router = Router()
    router.breaker, router.budget = breaker, budget
    old = metrics.STATE.router
    metrics.STATE.router = lambda: router
    try:
        assert value("starbuddy_model_breaker_open", provider="bailian", task="T2") == 1.0
        assert value("starbuddy_model_budget_remaining_calls", task="T2") == 3.0
        assert value("starbuddy_model_budget_remaining_tokens", task="T2") == 100.0
        assert value("starbuddy_model_budget_remaining_ratio", task="T2") == pytest.approx(0.1)
        t[0] = 31.0
        assert value("starbuddy_model_breaker_open", provider="bailian", task="T2") == 1.0  # 半开也算
        breaker.success(breaker.acquire("bailian", "T2"))
        assert value("starbuddy_model_breaker_open", provider="bailian", task="T2") == 0.0
        assert budget.used(Task.T2) == (1, 900), "采集只读，不改护栏计数"
    finally:
        metrics.STATE.router = old


class _MeterPool:
    def __init__(self, state: str) -> None:
        self.state = state

    def acquire(self):
        pool = self

        class _Ctx:
            async def __aenter__(self):
                return pool

            async def __aexit__(self, *a):
                return False
        return _Ctx()

    def transaction(self):
        return self.acquire()

    async def execute(self, *a):
        return None

    async def fetchval(self, *a):
        return self.state


def test_meter_check_counts_allowed_and_sealed(monkeypatch):
    async def fake_check(pool, **kw):
        return pool.state
    monkeypatch.setattr(meter.store, "meter_check", fake_check)
    a0, s0 = value("starbuddy_learning_meter_check_total", outcome="allowed"), value("starbuddy_learning_meter_check_total", outcome="sealed")
    ctx = {"usage_id": UUID, "idempotency_key": UUID, "student_ref": UUID}
    asyncio.run(meter.require(_MeterPool("reserved"), meter.DIRECT, method="grade", ctx=ctx))
    with pytest.raises(Exception) as e:
        asyncio.run(meter.require(_MeterPool("expired"), meter.DIRECT, method="grade", ctx=ctx))
    assert getattr(e.value, "status", None) == 409
    assert value("starbuddy_learning_meter_check_total", outcome="allowed") == a0 + 1
    assert value("starbuddy_learning_meter_check_total", outcome="sealed") == s0 + 1


def test_meter_check_result_unchanged_when_counting_raises(monkeypatch):
    """计数抛异常时计量核对结果不变（决定记录（续）第 6 条）：放行照旧放行、409 照旧 409"""
    async def fake_check(pool, **kw):
        return pool.state

    def boom(**kw):
        raise RuntimeError("synthetic metrics failure")
    monkeypatch.setattr(meter.store, "meter_check", fake_check)
    monkeypatch.setattr(metrics.METER_CHECK, "labels", boom)
    ctx = {"usage_id": UUID, "idempotency_key": UUID, "student_ref": UUID}
    asyncio.run(meter.require(_MeterPool("reserved"), meter.DIRECT, method="grade", ctx=ctx))
    with pytest.raises(Exception) as e:
        asyncio.run(meter.require(_MeterPool("expired"), meter.DIRECT, method="grade", ctx=ctx))
    assert getattr(e.value, "status", None) == 409


def test_router_sink_survives_metric_errors(monkeypatch):
    """调用记录计数出任何错只丢这一条：日志照写、调用不受影响"""
    def boom(**kw):
        raise RuntimeError("synthetic metrics failure")
    monkeypatch.setattr(metrics.ROUTE_CALLS, "labels", boom)
    rec = CallRecord(env="test", task="T1", data_class="L1", subject="aya", usage_ref="0" * 16, idempotency_ref="1" * 16,
                     source_version="prompt:x@v1", outcome="ok", model_version="m-v1", attempts=(Attempt("bailian", "m-v1", "ok", 9),),
                     input_tokens=1, output_tokens=1, elapsed_ms=1, estimated_cost_cny=None)
    metrics.MetricsSink().write(rec)


def test_router_sink_maps_unknown_provider_and_outcome_to_other():
    rec = CallRecord(env="test", task="T3", data_class="L1", subject="aya", usage_ref="0" * 16, idempotency_ref="1" * 16,
                     source_version="prompt:x@v1", outcome="ok", model_version="m-v1",
                     attempts=(Attempt("vendor-" + UUID, "m-v1", "weird_" + PHONE, 9),),
                     input_tokens=1, output_tokens=1, elapsed_ms=1, estimated_cost_cny=None)
    before = value("starbuddy_model_provider_calls_total", provider="other", task="T3", outcome="other")
    metrics.MetricsSink().write(rec)
    assert value("starbuddy_model_provider_calls_total", provider="other", task="T3", outcome="other") == before + 1
    assert UUID not in obs.render().decode() and PHONE not in obs.render().decode()


class _BacklogPool:
    def __init__(self) -> None:
        self.calls = 0
        self.rows = [{"subject": "aya", "n": 3, "oldest": 7300.5}, {"subject": UUID, "n": 1, "oldest": 1.0},
                     {"subject": "math", "n": 2, "oldest": 3700.0}]
        self.fail = False

    async def fetch(self, sql: str):
        self.calls += 1
        assert "student" not in sql and "count(*)" in sql and "review_task" in sql, "只取计数，不取学生字段"
        if self.fail:
            raise ConnectionError("synthetic")
        return self.rows


def test_review_backlog_refreshes_with_cache_and_drops_bad_labels():
    pool = _BacklogPool()
    now = [100.0]
    refresher = metrics.BacklogRefresher(lambda: pool, clock=lambda: now[0])
    asyncio.run(refresher())
    assert value("starbuddy_review_queue_backlog", subject="aya") == 3.0
    assert value("starbuddy_review_queue_oldest_seconds", subject="aya") == 7200.0, "最早等待按小时分档"
    assert value("starbuddy_review_queue_backlog", subject="other") == 3.0, "枚举外学科合并记 other（计数相加）"
    assert value("starbuddy_review_queue_oldest_seconds", subject="other") == 3600.0, "等待取大"
    assert UUID not in obs.render().decode()
    asyncio.run(refresher())
    assert pool.calls == 1, "缓存期内不再查库"
    now[0] += metrics.BACKLOG_CACHE + 1
    pool.fail = True
    asyncio.run(refresher())
    assert pool.calls == 2
    assert value("starbuddy_review_queue_backlog", subject="aya") == 3.0, "查询失败保留上一轮"
    asyncio.run(refresher())
    assert pool.calls == 2, "失败也缓存（负缓存）：缓存期内不再打库"
    now[0] += metrics.BACKLOG_CACHE + 1
    pool.fail = False
    asyncio.run(refresher())
    assert pool.calls == 3


@pytest.mark.parametrize("raw,want", [(0.0, 0.0), (3599.9, 0.0), (3600.0, 3600.0), (172801.0, 172800.0), (-5.0, 0.0)])
def test_backlog_oldest_is_coarsened_to_hours(raw, want):
    assert metrics.coarse_age(raw) == want


# ---------------------------------------------------------------- 日志：格式与集中脱敏
def _log(logger: str, msg: str, *args, exc: BaseException | None = None) -> dict:
    buf = io.StringIO()
    h = logging.StreamHandler(buf)
    h.setFormatter(obs.JsonFormatter("learning"))
    lg = logging.getLogger(logger)
    lg.addHandler(h)
    lg.setLevel(logging.INFO)
    lg.propagate = False
    try:
        if exc is not None:
            try:
                raise exc
            except BaseException:
                lg.exception(msg, *args)
        else:
            lg.info(msg, *args)
    finally:
        lg.removeHandler(h)
    lines = buf.getvalue().splitlines()
    assert len(lines) == 1
    return json.loads(lines[0])


def test_json_log_envelope_and_fields():
    d = _log("starbuddy.t1", json.dumps({"method": "grade", "status": 200, "duration_ms": 12, "request_id": UUID}))
    assert d["level"] == "info" and d["service"] == "learning" and d["logger"] == "starbuddy.t1"
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}\+00:00", d["ts"])
    assert d["method"] == "grade" and d["status"] == 200 and d["duration_ms"] == 12 and d["request_id"] == UUID
    p = _log("starbuddy.t2", "snapshot loaded commit=%s pages=%d", "abc123", 7)
    assert p["msg"] == "snapshot loaded commit=abc123 pages=7"
    # 经格式化器的记录一律集中脱敏：禁止字段丢掉、字符串替换
    s = _log("starbuddy.t4", json.dumps({"event": "x", "answer": "作答原文", "phone": PHONE, "detail": f"call {PHONE}"}))
    assert "answer" not in s and "phone" not in s and s["detail"] == f"call {obs.REDACTED}" and PHONE not in json.dumps(s)
    m = _log("starbuddy.t5", "login failed for %s", "kid@example.invalid")
    assert m["msg"] == f"login failed for {obs.REDACTED}"


# 安全审查 B 给出的样例：这些键不在白名单里（或是禁止字段），整项丢掉；值里的手机号（含空格 / 连字符分隔）与 UUID 也替换
REVIEW_SAMPLES = {
    "studentId": UUID, "student": "小明", "student_name": "小明", "message": "我不会做这道题", "raw_text": "原文",
    "ocr_text": "拍照识别的原文", "question_text": "题面原文", "photo_url": "https://x.example.invalid/p.jpg", "audio": "base64...",
    "guardianPhone": PHONE, "sessionToken": "s" * 40, "session": "abc", "nickname": "小星", "unknown_field": "随便什么",
}


def test_log_fields_whitelist_drops_review_samples():
    rec = dict(REVIEW_SAMPLES, event="probe", status=200, durationMs=7, detail=f"call 138 1234 5678 / 138-1234-5678 for {UUID}")
    out = obs.log_fields(rec)
    assert set(out) == {"event", "status", "duration_ms", "detail"}, out
    assert out["duration_ms"] == 7, "键名先驼峰归一"
    assert out["detail"] == f"call {obs.REDACTED} / {obs.REDACTED} for {obs.UUID_MARK}"
    text = json.dumps(out, ensure_ascii=False)
    for needle in ("小明", "不会做", "原文", "p.jpg", "base64", PHONE, "1234 5678", UUID, "小星", "随便"):
        assert needle not in text, needle


def test_log_line_through_formatter_drops_review_samples():
    d = _log("starbuddy.t6", json.dumps(dict(REVIEW_SAMPLES, event="probe", reason=f"for {UUID}", request_id=UUID), ensure_ascii=False))
    assert set(d) == {"ts", "level", "service", "logger", "event", "reason", "request_id"}, d
    assert d["request_id"] == UUID, "request_id 字段保留原样（只收 UUID）"
    assert d["reason"] == f"for {obs.UUID_MARK}", "正文里的 UUID 替换"
    bad = _log("starbuddy.t7", json.dumps({"event": "x", "request_id": f"call {PHONE}"}))
    assert bad["request_id"] == f"call {obs.REDACTED}", "不是 UUID 的 request_id 按文本脱敏"


@pytest.mark.parametrize("raw", [f"call {PHONE[:3]} {PHONE[3:7]} {PHONE[7:]}", f"call {PHONE[:3]}-{PHONE[3:7]}-{PHONE[7:]}",
                                 f"call +86-{PHONE}", f"call 86 {PHONE[:3]} {PHONE[3:7]} {PHONE[7:]}"])
def test_scrub_text_redacts_separated_phones(raw):
    out = obs.scrub_text(raw)
    assert out == f"call {obs.REDACTED}", out


def test_log_fields_are_registered_metadata_only():
    """白名单里的每个键都不是禁止字段；信封字段都在；与 IaC 日志索引字段同一来源（deploy/tests 另核对）"""
    assert set(obs.ENVELOPE) <= obs.LOG_FIELDS
    assert [k for k in obs.LOG_FIELDS if obs.forbidden_key(k)] == []
    for k in obs.LOG_FIELDS:
        assert obs.normalize_key(k) == k, k


@pytest.mark.parametrize("key,want", [("studentId", "student_id"), ("StudentName", "student_name"), ("durationMs", "duration_ms"),
                                      ("ocrText", "ocr_text"), ("x-api-key", "x_api_key"), ("status", "status")])
def test_normalize_key(key, want):
    assert obs.normalize_key(key) == want


def test_exceptions_log_class_only():
    d = _log("starbuddy.t3", "failed", exc=RuntimeError(f"secret data {PHONE} postgresql://u:p@10.0.0.1/db"))
    assert d["exc_type"] == "RuntimeError" and PHONE not in json.dumps(d) and "postgresql" not in json.dumps(d)
    assert "Traceback" not in json.dumps(d)


@pytest.mark.parametrize("raw", [
    f"call {PHONE}", f"call +86 {PHONE}", "mail kid@example.invalid", "id 11010520120101123X", "Authorization: Bearer abc.def.ghi",
    "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.c2lnbmF0dXJlLXN5bnRoZXRpYw", "key sk-AbCdEfGhIjKlMnOpQrStUv",
    "dsn postgresql://user@10.0.0.5:5432/starbuddy?sslmode=verify-full", "token " + "a1" * 32, "LTAI5tSyntheticSynthetic0"])
def test_scrub_text_redacts_patterns(raw):
    out = obs.scrub_text(raw)
    assert obs.REDACTED in out
    for needle in (PHONE, "kid@", "11010520120101123X", "abc.def.ghi", "sk-AbCd", "user@10.0.0.5", "a1a1a1a1", "LTAI5t"):
        assert needle not in out


def test_scrub_drops_forbidden_fields_and_keeps_metadata():
    rec = {"method": "grade", "status": 409, "input_tokens": 120, "output_tokens": 30, "usage_ref": "ab" * 8, "subject": "aya",
           "answer": "我的作答原文", "answer_text": "x", "text": "x", "content": "x", "messages": [{"role": "user"}], "output": "x",
           "phone": PHONE, "guardian_phone": PHONE, "email": "a@b.example.invalid", "token": "t", "x_api_key": "k", "password": "p",
           "student_id": UUID, "account_id": UUID, "login_name": "teacher.x", "authorization": "Bearer x", "id_card": "x",
           "session": "s1", "staff_session": "s2", "studentId": UUID, "ocrText": "x",
           "snapshot_digest": "c4" * 32, "note": "d4" * 32, "reason": "in_process_state_requires_single_instance",
           "detail": "一段说明 " * 200,
           "nested": {"stem": "题面原文", "ok": 1, "deeper": [{"email": "x"}, {"code": "OK"}]}}
    out = obs.scrub(rec)
    for k in ("answer", "answer_text", "text", "content", "messages", "output", "phone", "guardian_phone", "email", "token",
              "x_api_key", "password", "student_id", "account_id", "login_name", "authorization", "id_card", "session", "staff_session",
              "studentId", "ocrText", "ocr_text"):
        assert k not in out, k
    assert out["method"] == "grade" and out["status"] == 409 and out["input_tokens"] == 120 and out["output_tokens"] == 30
    assert out["usage_ref"] == "ab" * 8 and out["subject"] == "aya" and out["snapshot_digest"] == "c4" * 32
    assert out["note"] == obs.REDACTED, "不是摘要类字段的长串按密钥处理"
    assert out["reason"] == "in_process_state_requires_single_instance", "只有字母和下划线的长编码不是密钥"
    assert out["detail"].endswith("…[truncated]") and len(out["detail"]) < 400
    assert out["nested"] == {"ok": 1, "deeper": [{}, {"code": "OK"}]}


def test_request_context_adds_route_and_request_id():
    app = _mini_app()
    buf = io.StringIO()
    h = logging.StreamHandler(buf)
    h.setFormatter(obs.JsonFormatter("learning"))
    lg = logging.getLogger("starbuddy.test")
    lg.addHandler(h)
    lg.setLevel(logging.INFO)

    async def go():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1") as c:
            await c.get(f"/v/{UUID}")
    try:
        asyncio.run(go())
    finally:
        lg.removeHandler(h)
    d = json.loads(buf.getvalue().splitlines()[-1])
    assert d["route"] == "/v/{item}" and re.fullmatch(r"[0-9a-f-]{36}", d["request_id"]) and UUID not in buf.getvalue()


def test_configure_logging_replaces_root_handlers():
    root = logging.getLogger()
    saved = (list(root.handlers), root.level)
    buf = io.StringIO()
    try:
        h = obs.configure_logging("learning", stream=buf)
        handlers = list(root.handlers)  # 读一次快照（整套测试里别的用例可能留下在跑的线程）
        assert h in handlers and isinstance(h.formatter, obs.JsonFormatter)
        assert all(not isinstance(x, logging.StreamHandler) or x is h or type(x).__module__.startswith("_pytest") for x in handlers)
        # 没有自己处理器的日志器（uvicorn 以 log_config=None 启动时就是这样）都经根日志的 JSON 处理器；
        # 整套测试里别的用例可能已按 uvicorn 缺省配置过 uvicorn.error，所以这里用一个干净的日志器
        logging.getLogger("starbuddy.obs_probe.uvicorn").info("Started server process [%d]", 1)
        assert json.loads(buf.getvalue().splitlines()[-1])["logger"] == "starbuddy.obs_probe.uvicorn"
        with pytest.raises(ValueError):
            obs.configure_logging("not-a-service", stream=buf)
    finally:
        root.handlers, _ = saved[0], root.setLevel(saved[1])


# ---------------------------------------------------------------- 静态扫描：源码里的日志调用不出现禁止字段
def _logged_keys(base: Path):
    for p in sorted(base.rglob("*.py")):
        tree = ast.parse(p.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            dicts = []
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in (
                    "debug", "info", "warning", "error", "exception", "critical", "log"):
                dicts = [d for d in ast.walk(node) if isinstance(d, ast.Dict)]
            elif isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "record" for t in node.targets) \
                    and isinstance(node.value, ast.Dict):
                dicts = [node.value]
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "update" \
                    and isinstance(node.func.value, ast.Name) and node.func.value.id == "record":
                yield from ((p, kw.arg) for kw in node.keywords if kw.arg)
            for d in dicts:
                yield from ((p, k.value) for k in d.keys if isinstance(k, ast.Constant) and isinstance(k.value, str))


def test_source_log_calls_carry_no_forbidden_fields():
    bad = [(str(p.relative_to(SVC)), k) for base in ("learning", "review_console") for p, k in _logged_keys(SVC / base) if obs.forbidden_key(k)]
    assert bad == [], f"日志里出现了禁止字段：{bad}"
    assert obs.forbidden_key("answer") and obs.forbidden_key("guardian_phone") and not obs.forbidden_key("input_tokens")
