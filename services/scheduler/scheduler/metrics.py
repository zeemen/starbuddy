"""调度器的指标（LT-05 范围第 1 条，specs/launch-lt05-observability）：任务成败（每轮结果）、耗时与最近一次成功的时刻。

注册表、工厂与标签白名单复用学习服务的 learning/obs.py（与 health._listen_host 复用 learning.netpolicy 同一做法：把 services/learning
加进 sys.path；调度器的 venv 与洞察服务同一组包，含 prometheus-client）。标签只有 task（= 时间表里的任务名，配置校验过的短名字）
与 outcome（运行器的结果编码：ok、partial、refused、crashed、timeout、bad_output、lock_error、lock_lost、skipped_locked、
skipped_running、dry_run），不含计数、错误摘要与任何学生信息。暴露在单独的指标监听的 GET /metrics（scheduler/health.py 的
MetricsServer；健康监听不服务 /metrics）。
"""
from __future__ import annotations

import sys
from datetime import datetime

from .config import SERVICES

_LEARNING = str(SERVICES / "learning")
if _LEARNING not in sys.path:
    sys.path.append(_LEARNING)
from learning import obs  # noqa: E402

RUNS = obs.counter("starbuddy_scheduler_job_runs_total", "调度任务每轮的结果（outcome 为运行器结果编码；alert 级别的结果即失败）",
                   ("task", "outcome"))
DURATION = obs.histogram("starbuddy_scheduler_job_duration_seconds", "调度任务一轮的耗时（秒）", ("task",),
                         buckets=(1.0, 5.0, 15.0, 60.0, 300.0, 900.0, 1800.0, 3600.0))
LAST_OK = obs.gauge("starbuddy_scheduler_job_last_success_timestamp_seconds", "调度任务最近一次 ok 的结束时刻（Unix 秒）", ("task",))


def record(result) -> None:
    """运行器每写一行结果就记一次（RunResult：job、outcome、duration_ms、finished_at）；计数出任何错都只丢这一条（不影响调度与结果行，
    决定记录（续）第 6 条）。task 与 outcome 只收 learning/obs.py 的枚举，枚举外记 other"""
    try:
        RUNS.labels(task=result.job, outcome=result.outcome).inc()
        if result.outcome not in ("skipped_locked", "skipped_running", "dry_run"):
            DURATION.labels(task=result.job).observe(max(0, result.duration_ms) / 1000)
        if result.outcome == "ok":
            LAST_OK.labels(task=result.job).set(datetime.fromisoformat(result.finished_at).timestamp())
    except Exception:  # noqa: BLE001 —— 计数出任何错都只丢这一条
        pass


def render() -> bytes:
    return obs.render()
