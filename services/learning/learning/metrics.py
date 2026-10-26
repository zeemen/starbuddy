"""学习服务自己的指标（LT-05 范围第 1 条，specs/launch-lt05-observability）：模型路由（调用、失败、熔断、成本护栏剩余、估算成本）、
写入前的计量核对（放行 / 409）、统一复核队列积压。全部经 learning/obs.py 的工厂登记（名字与标签白名单在那里核对）。

- 模型路由：调用记录（ledger.CallRecord，只有元数据）经 MetricsSink 计数——按路由结果（task、outcome）与每次供应商尝试
  （provider、task、outcome = Attempt.status）；估算成本按成功那次尝试的供应商累加（没有单价不计）。熔断状态与护栏剩余在采集时
  从当前路由对象现读（只读快照，不改状态）。
- 调用记录落库（specs/plt-model-call-log，迁移 0060，开关 LEARNING_MODEL_CALL_LOG）：DbSink 的已写入、丢弃（队列满或退出时没刷完）、
  写库失败三个无标签计数；后两个接告警（deploy/observability/alerts.yaml）。
- 复核队列积压：采集时按学科数 platform.review_task 里未结案的任务（starbuddy_learning 已有 0006 的只读策略；只取计数与最早创建时间，
  不取任何学生字段），结果缓存 BACKLOG_CACHE 秒、并发 1，查询限时，失败保留上一轮的值（失败同样缓存，避免采集时反复打库）；
  最早等待秒数按小时分档；枚举外的学科合并记 other（计数相加、等待取大）。
"""
from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Iterable
from typing import Any

from prometheus_client.core import GaugeMetricFamily

from . import obs
from .model_router.ledger import CallRecord, DbSink, LogSink, TeeSink

ROUTE_CALLS = obs.counter("starbuddy_model_route_calls_total", "模型路由调用（按路由结果：ok、unavailable、rejected、budget_exceeded、error）",
                          ("task", "outcome"))
PROVIDER_CALLS = obs.counter("starbuddy_model_provider_calls_total", "供应商尝试（含重试与切备；outcome 为尝试结果，非 ok 即失败）",
                             ("provider", "task", "outcome"))
COST = obs.counter("starbuddy_model_estimated_cost_cny_total", "估算成本（元；路由表单价 × 供应商报告的 token，只是估算，不参与计量）",
                   ("provider", "task"))
METER_CHECK = obs.counter("starbuddy_learning_meter_check_total", "写入前的计量核对（allowed 放行；sealed 即 409 IDEMPOTENCY_KEY_SEALED）",
                          ("outcome",))
# 调用记录落库（specs/plt-model-call-log 范围第 4 条，迁移 0060）：无标签，只数行
CALL_LOG_WRITTEN = obs.counter("starbuddy_model_call_log_written_total", "调用记录已写入 platform.model_call_log 的行数")
CALL_LOG_DROPPED = obs.counter("starbuddy_model_call_log_dropped_total",
                               "调用记录没写库就丢弃的行数（队列满，或退出时限内没刷完；结构化日志里仍有完整的一条）")
CALL_LOG_FAILED = obs.counter("starbuddy_model_call_log_failed_total",
                              "调用记录写库失败的行数（连接、超时、约束、权限，或字段格式不合 0060 的约束；不重试）")
BACKLOG_CACHE = 30.0
BACKLOG_TIMEOUT = 2.0
AGE_STEP = 3600
BACKLOG_SQL = ("SELECT subject::text AS subject, count(*) AS n, extract(epoch FROM now() - min(created_at)) AS oldest"
               " FROM platform.review_task WHERE status <> 'resolved' GROUP BY subject")


class MetricsSink:
    """模型路由调用记录 → 计数（只用记录里的元数据：任务、结果、供应商、尝试状态、估算成本）"""

    def write(self, record: CallRecord) -> None:
        try:
            ROUTE_CALLS.labels(task=record.task, outcome=record.outcome).inc()
            for a in record.attempts:
                PROVIDER_CALLS.labels(provider=a.vendor, task=record.task, outcome=a.status).inc()
            ok = [a for a in record.attempts if a.status == "ok"]
            if record.estimated_cost_cny is not None and record.estimated_cost_cny > 0 and ok:
                COST.labels(provider=ok[-1].vendor, task=record.task).inc(record.estimated_cost_cny)
        except Exception:  # noqa: BLE001 —— 计数出任何错都只丢这一条，不影响调用（决定记录（续）第 6 条）
            pass


def _inc(metric: obs.SafeMetric) -> Any:
    return lambda n: metric.plain().inc(n)


def db_sink(pool: Any, **kw: Any) -> DbSink:
    """调用记录落库（LEARNING_MODEL_CALL_LOG=1 时由 app.py 建）：三个计数接到已写入、丢弃、写库失败"""
    return DbSink(pool, on_written=_inc(CALL_LOG_WRITTEN), on_dropped=_inc(CALL_LOG_DROPPED), on_failed=_inc(CALL_LOG_FAILED), **kw)


def router_sink(db: DbSink | None = None) -> TeeSink:
    """调用记录同时写结构化日志、指标与（打开落库时）数据库；TeeSink 里单个落地出错不影响其他的"""
    return TeeSink(LogSink(), MetricsSink(), *([db] if db is not None else []))


def meter_checked(allowed: bool) -> None:
    """计量核对计数：出任何错都只丢这一条，核对结果（放行 / 409）不变（决定记录（续）第 6 条）"""
    try:
        METER_CHECK.labels(outcome="allowed" if allowed else "sealed").inc()
    except Exception:  # noqa: BLE001
        pass


class _LearningState:
    """采集时现读：当前模型路由的熔断与护栏、复核队列积压（上一轮刷新的值）"""

    def __init__(self) -> None:
        self.router: Callable[[], Any] = lambda: None
        self.backlog: dict[str, tuple[int, float]] = {}

    def collect(self) -> Iterable[GaugeMetricFamily]:
        breaker = GaugeMetricFamily("starbuddy_model_breaker_open", "熔断打开（1 打开或半开、0 关闭；按供应商与任务）",
                                    labels=["provider", "task"])
        ratio = GaugeMetricFamily("starbuddy_model_budget_remaining_ratio", "成本护栏当日剩余比例（次数与 token 取小者）", labels=["task"])
        calls = GaugeMetricFamily("starbuddy_model_budget_remaining_calls", "成本护栏当日剩余调用次数", labels=["task"])
        tokens = GaugeMetricFamily("starbuddy_model_budget_remaining_tokens", "成本护栏当日剩余 token", labels=["task"])
        backlog = GaugeMetricFamily("starbuddy_review_queue_backlog", "复核队列未结案任务数（按学科）", labels=["subject"])
        oldest = GaugeMetricFamily("starbuddy_review_queue_oldest_seconds", "复核队列最早未结案任务的等待秒数（按学科）", labels=["subject"])
        router = self.router()
        if router is not None:
            try:
                for (vendor, task), st in sorted(router.breaker.snapshot().items()):
                    breaker.add_metric([obs.label_value("provider", vendor), obs.label_value("task", task)], 0.0 if st == "closed" else 1.0)
                for task, (left_calls, left_tokens, limit) in sorted(router.budget.remaining().items(), key=lambda kv: kv[0].value):
                    t = obs.label_value("task", task.value)
                    calls.add_metric([t], float(left_calls))
                    tokens.add_metric([t], float(left_tokens))
                    ratio.add_metric([t], min(left_calls / max(limit.daily_calls, 1), left_tokens / max(limit.daily_tokens, 1)))
            except (AttributeError, ValueError):  # 注入的路由桩没有这些只读快照：这一轮不报
                pass
        for subject, (n, age) in sorted(self.backlog.items()):
            backlog.add_metric([subject], float(n))
            oldest.add_metric([subject], float(age))
        yield from (breaker, ratio, calls, tokens, backlog, oldest)


STATE = _LearningState()
obs.REGISTRY.register(STATE)


class BacklogRefresher:
    """复核队列积压：采集前刷新（缓存 BACKLOG_CACHE 秒、并发 1、限时；失败保留上一轮）"""

    def __init__(self, pool: Callable[[], Any], clock: Callable[[], float] = time.monotonic) -> None:
        self.pool, self.clock = pool, clock
        self._lock = asyncio.Lock()
        self._at: float | None = None

    async def __call__(self) -> None:
        async with self._lock:
            if self._at is not None and self.clock() - self._at < BACKLOG_CACHE:
                return
            p = self.pool()
            if p is None:
                return
            try:
                rows = await asyncio.wait_for(p.fetch(BACKLOG_SQL), BACKLOG_TIMEOUT)
            except Exception:  # noqa: BLE001 —— 连接断开、超时：保留上一轮；失败也记时刻（负缓存，缓存期内不再查库）
                self._at = self.clock()
                return
            fresh: dict[str, tuple[int, float]] = {}
            for r in rows:
                try:
                    s = obs.label_value("subject", r["subject"])
                    n0, age0 = fresh.get(s, (0, 0.0))
                    fresh[s] = (n0 + int(r["n"]), max(age0, coarse_age(float(r["oldest"] or 0.0))))
                except (ValueError, TypeError, KeyError):
                    continue
            STATE.backlog = fresh
            self._at = self.clock()


def coarse_age(seconds: float) -> float:
    """最早等待时长按小时分档（向下取整到整小时）：只用于积压告警（> 48 小时），不暴露精确的创建时刻"""
    return float(int(max(seconds, 0.0) // AGE_STEP) * AGE_STEP)


def install(router: Callable[[], Any], pool: Callable[[], Any]) -> BacklogRefresher:
    """学习服务应用装配时调用：路由对象与连接池都在采集时现取（lifespan 之后才有）"""
    STATE.router = router
    refresher = BacklogRefresher(pool)
    obs.on_scrape("learning.review_backlog", refresher)
    return refresher
