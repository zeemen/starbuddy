"""可观测性（LT-05 范围第 1、2 条，specs/launch-lt05-observability）：知识服务的 Prometheus 指标（GET /metrics 只在单独的指标端口
KNOWLEDGE_METRICS_PORT，业务端口上 404；免鉴权、只在 Pod 内网；在 / 上挂 MCP 之前注册；MCP 请求按前缀 /mcp 计）与结构化 JSON 日志
（字段白名单、集中脱敏）。说明见 services/learning/learning/obs.py 的模块说明。
"""
# ---- 以下在 learning/learning/obs.py、insight/insight/service/obs.py、knowledge/knowledge/service/obs.py 三处逐字相同
# ---- （各服务各带一份、不跨服务导入；deploy/tests/test_obs_shared.py 核对）
from __future__ import annotations

import asyncio
import contextvars
import json
import logging
import re
import sys
import time
import uuid
from collections.abc import Awaitable, Callable, Iterable, Mapping
from datetime import datetime, timezone
from typing import Any

from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, Counter, Gauge, Histogram, disable_created_metrics, generate_latest
from prometheus_client.core import GaugeMetricFamily

disable_created_metrics()

METRICS_PATH = "/metrics"
SKIP_PATHS = frozenset({METRICS_PATH, "/healthz", "/readyz"})
HEADERS = {"cache-control": "no-store", "x-content-type-options": "nosniff"}
# 标签名白名单（用户口径）：服务、路由模板、方法、状态码类别、学科、任务类型、供应商、结果
LABELS = frozenset({"service", "route", "method", "status_class", "subject", "task", "provider", "outcome"})
SERVICES = frozenset({"learning", "review-console", "insight", "knowledge", "scheduler"})
METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS", "other"})
STATUS_CLASSES = frozenset({"1xx", "2xx", "3xx", "4xx", "5xx"})
# 枚举（与 BFF src/observability/metrics.ts 的 ENUMS 一致，deploy/tests/test_obs_shared.py 核对）；枚举外的取值记 other
ENUMS: dict[str, frozenset[str]] = {
    "subject": frozenset({"aya", "zhixing", "wenli", "echo", "other"}),
    "task": frozenset({"T1", "T2", "T3", "T4", "T5", "T6", "T7", "T8", "T9", "meter-expire", "meter-renew", "erasures", "freeze", "sweep",
                       "mastery-sweep", "expire-affairs", "spot-check", "metrics", "weekly", "model-call-purge", "other"}),
    "provider": frozenset({"bailian", "ark", "fake-a", "fake-b", "fake-x", "other"}),
    "outcome": frozenset({"ok", "other", "unavailable", "rejected", "budget_exceeded", "error", "timeout", "rate_limited", "server", "auth",
                          "client", "bad_response", "skipped_breaker_open", "allowed", "sealed", "partial", "refused", "crashed",
                          "bad_output", "lock_error", "lock_lost", "skipped_locked", "skipped_running", "dry_run", "reserved", "dedup",
                          "existing", "insufficient", "daily_cap_reached", "curfew_soon", "conflict", "dedup_in_progress",
                          "no_entitlement", "retained_no_ai", "curfew", "settled", "released", "refunded", "unknown",
                          "reserve_conflict", "reserve_dedup_in_progress", "settle_conflict", "release_conflict",
                          "late_success_after_expiry", "replay_after_expiry", "sealed_after_expiry", "success_after_release",
                          "result_unknown", "learning_retry"}),
}
LATENCY_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0)
_NAME = re.compile(r"^starbuddy_[a-z][a-z0-9_]{2,80}$")
_ROUTE = re.compile(r"^(?:unmatched|/[A-Za-z0-9_./{}-]{0,120})$")
_IDISH = re.compile(r"[0-9a-fA-F]{8}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}|\d{5,}|@")

REGISTRY = CollectorRegistry(auto_describe=True)


def label_value(name: str, value: Any) -> str:
    """一个标签值能不能写：服务、方法、状态码类别只收枚举，路由只收模板形状（不合格抛 ValueError，不写入）；
    学科、任务类型、供应商、结果只收 ENUMS 里的取值，枚举外一律记 other"""
    if name not in LABELS:
        raise ValueError(f"标签 {name} 不在白名单里")
    v = value if isinstance(value, str) else ""
    if name in ENUMS:
        return v if v in ENUMS[name] else "other"
    ok = {"service": lambda: v in SERVICES, "method": lambda: v in METHODS, "status_class": lambda: v in STATUS_CLASSES,
          "route": lambda: bool(_ROUTE.fullmatch(v)) and not _IDISH.search(v)}[name]
    if not ok():
        raise ValueError(f"标签 {name} 的取值不合格（只收低基数编码）")
    return v


class SafeMetric:
    """登记过的指标：labels() 先核对标签名与取值，再交给 prometheus_client"""

    def __init__(self, metric: Any, labelnames: tuple[str, ...]) -> None:
        self._m, self.labelnames = metric, labelnames

    def labels(self, **kw: Any) -> Any:
        if set(kw) != set(self.labelnames):
            raise ValueError("标签与登记的不一致")
        return self._m.labels(**{k: label_value(k, v) for k, v in kw.items()})

    def plain(self) -> Any:
        if self.labelnames:
            raise ValueError("有标签的指标必须经 labels()")
        return self._m


def _check(name: str, labelnames: Iterable[str]) -> tuple[str, ...]:
    names = tuple(labelnames)
    if not _NAME.fullmatch(name):
        raise ValueError(f"指标名 {name} 必须以 starbuddy_ 开头、只含小写字母数字下划线")
    bad = [n for n in names if n not in LABELS]
    if bad:
        raise ValueError(f"标签 {bad} 不在白名单里")
    return names


def counter(name: str, doc: str, labelnames: Iterable[str] = ()) -> SafeMetric:
    names = _check(name, labelnames)
    return SafeMetric(Counter(name, doc, names, registry=REGISTRY), names)


def gauge(name: str, doc: str, labelnames: Iterable[str] = ()) -> SafeMetric:
    names = _check(name, labelnames)
    return SafeMetric(Gauge(name, doc, names, registry=REGISTRY), names)


def histogram(name: str, doc: str, labelnames: Iterable[str] = (), buckets: tuple[float, ...] = LATENCY_BUCKETS) -> SafeMetric:
    names = _check(name, labelnames)
    return SafeMetric(Histogram(name, doc, names, registry=REGISTRY, buckets=buckets), names)


HTTP_REQUESTS = counter("starbuddy_http_requests_total", "HTTP 请求数（路由模板、方法、状态码类别）",
                        ("service", "route", "method", "status_class"))
HTTP_DURATION = histogram("starbuddy_http_request_duration_seconds", "HTTP 请求耗时（秒）", ("service", "route", "method"))


def catalog() -> list[str]:
    """本进程注册表里的指标族（按暴露的名字：计数器带 _total；直方图是基名）。部署检查与登记清单核对用"""
    return sorted({f.name + "_total" if f.type == "counter" else f.name for f in REGISTRY.collect()})


def render() -> bytes:
    return generate_latest(REGISTRY)


# ---------------------------------------------------------------- 采集时现读的状态：连接池、就绪
_SOURCES: dict[str, dict[str, Callable[[], Any]]] = {}


def _ready_of(check: Any) -> Callable[[], bool | None]:
    """健康端点的就绪检查对象（ReadyCheck / HealthServer）最近一次结果；还没检查过为 None。不触发新的检查"""
    def read() -> bool | None:
        last = getattr(check, "_last", None)
        return None if last is None else bool(last[1])
    return read


def track(service: str, *, pools: Callable[[], Iterable[Any]] | None = None, ready: Any = None) -> None:
    """登记一个服务的状态来源（同一进程同名服务后登记的覆盖前一个）"""
    label_value("service", service)
    _SOURCES[service] = {"pools": pools or (lambda: ()), "ready": _ready_of(ready) if ready is not None else (lambda: None)}


def untrack(service: str) -> None:
    _SOURCES.pop(service, None)


class _StateCollector:
    def collect(self) -> Iterable[GaugeMetricFamily]:
        size = GaugeMetricFamily("starbuddy_db_pool_size", "连接池当前连接数", labels=["service"])
        idle = GaugeMetricFamily("starbuddy_db_pool_idle", "连接池空闲连接数", labels=["service"])
        cap = GaugeMetricFamily("starbuddy_db_pool_max", "连接池上限", labels=["service"])
        ready = GaugeMetricFamily("starbuddy_ready", "最近一次就绪检查（1 就绪、0 未就绪或还没检查）", labels=["service"])
        for svc, src in sorted(_SOURCES.items()):
            try:
                pools = [p for p in src["pools"]() if p is not None]
                if pools:
                    size.add_metric([svc], float(sum(p.get_size() for p in pools)))
                    idle.add_metric([svc], float(sum(p.get_idle_size() for p in pools)))
                    cap.add_metric([svc], float(sum(p.get_max_size() for p in pools)))
            except Exception:  # noqa: BLE001 —— 池已关闭等：这一轮不报池指标
                pass
            ready.add_metric([svc], 1.0 if src["ready"]() else 0.0)
        yield from (size, idle, cap, ready)


REGISTRY.register(_StateCollector())


# ---------------------------------------------------------------- HTTP：请求指标中间件
_REQUEST: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar("starbuddy_obs_request", default=None)


def route_template(scope: Mapping[str, Any], prefixes: Iterable[str] = ()) -> str:
    """路由模板：FastAPI 匹配到的 route.path；挂载的子应用按登记的前缀；都不是记 unmatched（不记原始路径）"""
    path = getattr(scope.get("route"), "path", None)
    if isinstance(path, str) and path.startswith("/"):
        return path
    raw = scope.get("path") or ""
    for p in prefixes:
        if raw == p or raw.startswith(p + "/"):
            return p
    return "unmatched"


def status_class(code: int) -> str:
    return f"{code // 100}xx" if 100 <= code <= 599 else "5xx"


def observe_http(service: str, route: str, method: str, status: int, seconds: float) -> None:
    """一次请求的计数与耗时；出任何错都只丢这一条（不影响请求）"""
    try:
        m = method.upper() if method.upper() in METHODS else "other"
        HTTP_REQUESTS.labels(service=service, route=route, method=m, status_class=status_class(status)).inc()
        HTTP_DURATION.labels(service=service, route=route, method=m).observe(seconds)
    except Exception:  # noqa: BLE001
        pass


class MetricsMiddleware:
    """ASGI 中间件：请求数与耗时（路由模板、方法、状态码类别）；/metrics 与健康端点不计；请求期间为日志提供 route 与 request_id"""

    def __init__(self, app: Any, service: str, prefixes: tuple[str, ...] = ()) -> None:
        self.app, self.service, self.prefixes = app, label_value("service", service), tuple(prefixes)

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http" or scope.get("path") in SKIP_PATHS:
            await self.app(scope, receive, send)
            return
        started = time.perf_counter()
        seen = {"status": 500}

        async def tracked(msg: dict[str, Any]) -> None:
            if msg.get("type") == "http.response.start":
                seen["status"] = int(msg.get("status", 500))
            await send(msg)
        token = _REQUEST.set({"scope": scope, "request_id": str(uuid.uuid4()), "prefixes": self.prefixes})
        try:
            await self.app(scope, receive, tracked)
        finally:
            _REQUEST.reset(token)
            observe_http(self.service, route_template(scope, self.prefixes), str(scope.get("method", "")), seen["status"],
                         time.perf_counter() - started)


_SCRAPE_HOOKS: dict[str, Callable[[], Awaitable[None]]] = {}
SCRAPE_HOOK_TIMEOUT = 3.0


def on_scrape(key: str, hook: Callable[[], Awaitable[None]]) -> None:
    """采集前要异步刷新的值（例如要查库的积压计数）：每次 /metrics 先等这些钩子（各自限时、出错忽略），同名后登记的覆盖"""
    _SCRAPE_HOOKS[key] = hook


async def scrape() -> bytes:
    for hook in list(_SCRAPE_HOOKS.values()):
        try:
            await asyncio.wait_for(hook(), SCRAPE_HOOK_TIMEOUT)
        except Exception:  # noqa: BLE001 —— 刷新失败只影响那几项（保留上一轮的值），不影响其余指标
            pass
    return render()


def register(app: Any, service: str, *, pools: Callable[[], Iterable[Any]] | None = None, ready: Any = None,
             prefixes: Iterable[str] = ()) -> None:
    """在业务应用上加请求指标中间件并登记状态来源；业务端口上的 /metrics 一律 404（指标只在单独的指标端口，metrics_app）。
    与健康端点一样在挂载子应用之前调用"""
    from starlette.responses import JSONResponse

    label_value("service", service)

    @app.api_route(METRICS_PATH, methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"], include_in_schema=False)
    async def no_metrics_here() -> JSONResponse:
        return JSONResponse({"status": "not_found"}, status_code=404, headers=HEADERS)

    app.add_middleware(MetricsMiddleware, service=service, prefixes=tuple(prefixes))
    track(service, pools=pools, ready=ready)


# ---------------------------------------------------------------- 单独的指标监听（决定记录（续）第 5 条）
async def _send_simple(send: Any, status: int, body: bytes, content_type: str, extra: tuple[tuple[bytes, bytes], ...] = ()) -> None:
    headers = [(b"content-type", content_type.encode()), (b"content-length", str(len(body)).encode()),
               *((k.encode(), v.encode()) for k, v in HEADERS.items()), *extra]
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": body})


async def metrics_app(scope: dict[str, Any], receive: Any, send: Any) -> None:
    """指标端口上的 ASGI 应用：只服务 GET /metrics（路径逐字相等、不收查询串），别的方法 405、别的路径 404；不记日志"""
    kind = scope.get("type")
    if kind == "lifespan":
        while True:
            msg = await receive()
            if msg["type"] == "lifespan.startup":
                await send({"type": "lifespan.startup.complete"})
            elif msg["type"] == "lifespan.shutdown":
                await send({"type": "lifespan.shutdown.complete"})
                return
    if kind == "websocket":
        await send({"type": "websocket.close", "code": 1008})
        return
    if kind != "http":
        return
    if scope.get("path") != METRICS_PATH or scope.get("query_string"):
        await _send_simple(send, 404, b'{"status":"not_found"}', "application/json")
    elif scope.get("method") != "GET":
        await _send_simple(send, 405, b'{"status":"method_not_allowed"}', "application/json", ((b"allow", b"GET"),))
    else:
        await _send_simple(send, 200, await scrape(), CONTENT_TYPE_LATEST)


def metrics_port(raw: str | None, env: str, default: int, var: str, *taken: int) -> int | None:
    """指标端口：设了就用（1–65535 的规范整数，不能与业务端口等其他端口相同）；没设时 staging 用缺省值、dev / test 不起指标监听"""
    if raw is None or raw == "":
        port = default if env == "staging" else None
    elif not raw.isdigit() or raw != str(int(raw)) or not 1 <= int(raw) <= 65535:
        raise RuntimeError(f"{var} 必须是 1–65535 的整数")
    else:
        port = int(raw)
    if port is not None and port in taken:
        raise RuntimeError(f"{var} 不能与业务端口或其他监听端口相同")
    return port


def serve(app: Any, host: str, port: int, metrics_listen_port: int | None, **kw: Any) -> None:
    """进程入口：业务监听照旧；设了指标端口时另起只服务 /metrics 的监听（同一地址）。任一监听退出就一起退出；信号只由业务监听处理"""
    import contextlib

    import uvicorn

    if metrics_listen_port is None:
        uvicorn.run(app, host=host, port=port, access_log=False, log_config=None, **kw)
        return

    class _Side(uvicorn.Server):
        @contextlib.contextmanager
        def capture_signals(self):  # type: ignore[override]  # 信号交给业务监听
            yield

    main = uvicorn.Server(uvicorn.Config(app, host=host, port=port, access_log=False, log_config=None, **kw))
    side = _Side(uvicorn.Config(metrics_app, host=host, port=metrics_listen_port, access_log=False, log_config=None, lifespan="on"))

    async def run() -> None:
        side_task = asyncio.create_task(side.serve())
        side_task.add_done_callback(lambda _t: setattr(main, "should_exit", True))
        try:
            await main.serve()
        finally:
            side.should_exit = True
            await side_task

    asyncio.run(run())


# ---------------------------------------------------------------- 日志：字段白名单、集中脱敏与 JSON 格式
REDACTED = "[redacted]"
UUID_MARK = "[uuid]"
MAX_TEXT = 300
ENVELOPE = ("ts", "level", "service", "logger", "route", "request_id", "msg", "exc_type")
# 登记的元数据键（与 BFF src/observability/log.ts 的 LOG_FIELDS 同一份；IaC 日志索引字段只能从这里选，deploy/iac/check_iac.py 读本常量）
LOG_FIELDS = frozenset(ENVELOPE + (
    "event", "method", "status", "code", "duration_ms", "replayed", "error_class", "reason", "detail", "subject", "subjects", "task",
    "data_class", "outcome", "tool", "path", "env", "usage_ref", "idempotency_ref", "source_version", "model_version", "attempts",
    "input_tokens", "output_tokens", "elapsed_ms", "model_mismatch", "reported_model", "estimated_cost_cny", "trigger", "items",
    "truncated", "unavailable", "snapshot", "sqlstate", "counts", "purpose", "verdict", "verdict_label", "pending_review",
    "evidence_level", "learner_model_updated", "response_contract_errors", "report_blocked", "cache", "cache_stats", "bytes", "entries",
    "generation", "requests", "categories", "note", "approvals", "latest_decision", "latest_matches", "required_signers", "structural",
    "ready", "job", "child_exit", "exit_code", "started_at", "finished_at", "port", "audit_event", "account_kind", "dropped_statements",
    "explain", "export_job", "export_store", "generate", "guardian_op", "human_review_required", "lexicon", "lexicon_hits",
    "monthly_plan", "policy_version", "prediction", "reconciliation", "report", "route_group", "sms", "star_nodes",
    "star_nodes_mismatch", "star_statements", "template_fingerprint", "weekly"))
# 嵌套结构里这些键整项丢掉（键名不区分大小写、先驼峰归一；带前缀的同样算，例如 guardian_phone、student_name、ocr_text、photo_url）
_FORBIDDEN_KEY = re.compile(r"(?i)^(?:.*_)?(password|passwd|secret|token|api_?key|authorization|cookie|credential|phone|mobile|email"
                            r"|id_?card|id_?number|otp|dsn|session|name|nickname|texts?|messages?|content|body|raw|answers?|prompt|output"
                            r"|stem|essay|transcript|address|ip|url|photo|image|audio|video|file|student|guardian|account|user)(?:_(?:id|ref))?$")
_HEX_OK_KEY = re.compile(r"(?i)(digest|sha256|_ref|commit|fingerprint|hash)$")
_UUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
_TEXT_RULES = [
    re.compile(r"postgres(?:ql)?://\S+", re.I),
    re.compile(r"(?i)\bbearer\s+\S+"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{4,}"),
    re.compile(r"\b(?:sk|ak|pk|rk)-[A-Za-z0-9_-]{16,}"),
    re.compile(r"LTAI[0-9A-Za-z]{12,}"),
    re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
    re.compile(r"(?<![0-9A-Za-z])\d{17}[\dXx](?![0-9A-Za-z])"),
    re.compile(r"(?<!\d)(?:\+?86[- ]?)?1[3-9]\d[ -]?\d{4}[ -]?\d{4}(?!\d)"),
]
# 密钥样式长串：40 个以上的 base64 / 十六进制字符、同时含数字与字母（只有字母和下划线的长编码名不算，例如结果编码）
_LONG_SECRET = re.compile(r"(?<![A-Za-z0-9+/=_])(?=[A-Za-z0-9+/=_]*\d)(?=[A-Za-z0-9+/=_]*[A-Za-z])[A-Za-z0-9+/=_]{40,}(?![A-Za-z0-9+/=_])")


def normalize_key(key: str) -> str:
    """studentId → student_id、StudentName → student_name：驼峰先归一为下划线小写"""
    return re.sub(r"(?<=[a-z0-9])([A-Z])", r"_\1", str(key)).replace("-", "_").lower()


def forbidden_key(key: str) -> bool:
    return bool(_FORBIDDEN_KEY.fullmatch(normalize_key(key)))


def scrub_text(s: str, key: str | None = None) -> str:
    for rule in _TEXT_RULES:
        s = rule.sub(REDACTED, s)
    s = _UUID.sub(UUID_MARK, s)
    if key is None or not _HEX_OK_KEY.search(key):
        s = _LONG_SECRET.sub(REDACTED, s)
    return s if len(s) <= MAX_TEXT else s[:MAX_TEXT] + "…[truncated]"


def scrub(value: Any, key: str | None = None, depth: int = 0) -> Any:
    """嵌套结构的集中脱敏：禁止字段丢掉，字符串按规则替换，结构限深限长"""
    if isinstance(value, str):
        return scrub_text(value, key)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if depth >= 4:
        return REDACTED
    if isinstance(value, Mapping):
        out = {}
        for k, v in list(value.items())[:64]:
            nk = normalize_key(str(k))
            if not forbidden_key(nk):
                out[nk] = scrub(v, nk, depth + 1)
        return out
    if isinstance(value, (list, tuple)):
        return [scrub(v, key, depth + 1) for v in list(value)[:50]]
    return scrub_text(str(value), key)


def log_fields(fields: Mapping[str, Any]) -> dict[str, Any]:
    """顶层字段白名单：键名驼峰归一后只留 LOG_FIELDS，其余整项丢掉；取值再脱敏（request_id 只收 UUID 原样，别的值按文本处理）"""
    out: dict[str, Any] = {}
    for k, v in list(fields.items())[:64]:
        nk = normalize_key(str(k))
        if nk not in LOG_FIELDS:
            continue
        if nk == "request_id" and isinstance(v, str) and _UUID.fullmatch(v):
            out[nk] = v
        else:
            out[nk] = scrub(v, nk, 1)
    return out


class JsonFormatter(logging.Formatter):
    def __init__(self, service: str) -> None:
        super().__init__()
        self.service = service

    def format(self, record: logging.LogRecord) -> str:
        try:
            msg = record.getMessage()
        except Exception:  # noqa: BLE001 —— 参数与格式串对不上：不回显参数
            msg = str(record.msg)
        fields: dict[str, Any] = {}
        if msg.startswith("{"):
            try:
                parsed = json.loads(msg)
                fields = parsed if isinstance(parsed, dict) else {"msg": msg}
            except ValueError:
                fields = {"msg": msg}
        else:
            fields = {"msg": msg}
        out: dict[str, Any] = {"ts": datetime.fromtimestamp(record.created, timezone.utc).isoformat(timespec="milliseconds"),
                               "level": record.levelname.lower(), "service": self.service, "logger": record.name}
        ctx = _REQUEST.get()
        if ctx is not None:
            out["route"] = route_template(ctx["scope"], ctx["prefixes"])
            fields.setdefault("request_id", ctx["request_id"])
        if record.exc_info and record.exc_info[0] is not None:
            out["exc_type"] = record.exc_info[0].__name__[:48]
        for k, v in log_fields(fields).items():
            out.setdefault(k, v)
        return json.dumps(out, ensure_ascii=False, separators=(",", ":"), default=str)


def configure_logging(service: str, stream: Any = None, level: int = logging.INFO) -> logging.Handler:
    """进程入口调用一次：根日志只留一个 JSON 处理器（uvicorn 以 log_config=None 启动，它的日志也经这里）"""
    label_value("service", service)
    handler = logging.StreamHandler(stream if stream is not None else sys.stderr)
    handler.setFormatter(JsonFormatter(service))
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)
    return handler
