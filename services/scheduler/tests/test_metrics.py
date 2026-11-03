"""LT-05 调度器指标（specs/launch-lt05-observability 范围第 1、7 条；scheduler/metrics.py、health.py 的 GET /metrics）。

- 每轮结果按（任务名、结果编码）计数；失败（alert 级别）与 ok 分开；最近一次成功时刻只在 ok 时刷新；耗时不计跳过的轮次；
- 健康监听不服务 /metrics（404）；单独的指标监听（MetricsServer）只服务 GET /metrics：路径逐字相等、no-store、Prometheus 文本格式；
  只出现白名单标签，不出现错误摘要与计数；任务名枚举外记 other；计数抛异常不影响调度；
- 注册的指标与 deploy/observability/metrics-inventory.yaml 一致。
scheduler.metrics 在用例里才导入（标签白名单不合格时在用例里失败，而不是收集期出错）。不连库；全部合成值。"""
from __future__ import annotations

import asyncio
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

from scheduler import health
from scheduler.runner import RunResult

SVC = Path(__file__).resolve().parents[1]
ROOT = SVC.parents[1]
INVENTORY = yaml.safe_load((ROOT / "deploy/observability/metrics-inventory.yaml").read_text(encoding="utf-8"))


def _value(name: str, **labels: str) -> float:
    from scheduler import metrics
    return metrics.obs.REGISTRY.get_sample_value(name, labels) or 0.0


FINISHED = datetime(2026, 10, 23, 1, 0, 2, 500000, tzinfo=timezone.utc).timestamp()


def _result(job: str, outcome: str, reason: str = "", finished: str = "2026-10-23T01:00:02.500+00:00") -> RunResult:
    return RunResult(job, outcome, 0, "2026-10-23T01:00:00.000+00:00", finished, 2500, reason=reason)


def test_runs_counted_by_task_and_outcome():
    from scheduler import metrics
    ok0 = _value("starbuddy_scheduler_job_runs_total", task="meter-expire", outcome="ok")
    bad0 = _value("starbuddy_scheduler_job_runs_total", task="meter-expire", outcome="crashed")
    metrics.record(_result("meter-expire", "ok"))
    assert _value("starbuddy_scheduler_job_last_success_timestamp_seconds", task="meter-expire") == FINISHED
    metrics.record(_result("meter-expire", "crashed", reason="synthetic 13812345678", finished="2026-10-23T02:00:00.000+00:00"))
    metrics.record(_result("meter-expire", "skipped_running", finished="2026-10-23T03:00:00.000+00:00"))
    assert _value("starbuddy_scheduler_job_runs_total", task="meter-expire", outcome="ok") == ok0 + 1
    assert _value("starbuddy_scheduler_job_runs_total", task="meter-expire", outcome="crashed") == bad0 + 1
    assert _value("starbuddy_scheduler_job_last_success_timestamp_seconds", task="meter-expire") == FINISHED, "失败不刷新成功时刻"
    assert _value("starbuddy_scheduler_job_duration_seconds_count", task="meter-expire") >= 2
    text = metrics.render().decode()
    assert "13812345678" not in text and "synthetic" not in text


def test_runner_emit_records_metrics():
    from test_runner import runner
    r, _out, _locks = runner()
    before = _value("starbuddy_scheduler_job_runs_total", task="weekly", outcome="refused")
    r.emit(_result("weekly", "refused"))
    assert _value("starbuddy_scheduler_job_runs_total", task="weekly", outcome="refused") == before + 1
    # 枚举外的任务名记 other（安全审查 C：标签取值改为枚举集合）
    other0 = _value("starbuddy_scheduler_job_runs_total", task="other", outcome="refused")
    r.emit(_result("weekly-report", "refused"))
    assert _value("starbuddy_scheduler_job_runs_total", task="other", outcome="refused") == other0 + 1


def test_record_failure_does_not_affect_run(monkeypatch):
    """计数抛异常时调度不受影响：record 吞掉任何异常，运行器照常写结果行（决定记录（续）第 6 条）"""
    from scheduler import metrics
    from test_runner import runner

    def boom(**kw):
        raise RuntimeError("synthetic metrics failure")
    monkeypatch.setattr(metrics.RUNS, "labels", boom)
    metrics.record(_result("meter-expire", "ok"))
    r, out, _locks = runner()
    r.emit(_result("meter-expire", "ok"))
    assert '"outcome":"ok"' in out.getvalue().replace(" ", "")


def test_health_listener_does_not_serve_metrics():
    async def go():
        async def yes() -> bool:
            return True
        for line in (b"GET /metrics HTTP/1.1", b"POST /metrics HTTP/1.1"):
            raw = await health.answer(line, yes)
            assert int(raw.split(b" ", 2)[1]) == 404 and b"starbuddy_" not in raw, line
    asyncio.run(go())


def test_metrics_listener_only_serves_get_metrics():
    async def go():
        for line in (b"GET /healthz HTTP/1.1", b"GET /readyz HTTP/1.1", b"GET / HTTP/1.1", b"GET /Metrics HTTP/1.1"):
            assert int((await health.answer_metrics(line)).split(b" ", 2)[1]) == 404, line
        assert int((await health.answer_metrics(b"GET /metrics")).split(b" ", 2)[1]) == 400
    asyncio.run(go())


def test_metrics_listener_config():
    import pytest
    assert health.metrics_config({}, "staging", ("10.0.0.5", 8793)) == ("10.0.0.5", 9793)
    assert health.metrics_config({}, "dev", ("127.0.0.1", 8793)) is None
    assert health.metrics_config({}, "test", None) is None
    assert health.metrics_config({"SCHEDULER_METRICS_PORT": "9999"}, "test", None) == ("127.0.0.1", 9999)
    for bad in ("8793", "0", "65536", "09793", "x"):
        with pytest.raises(health.HealthConfigError, match="SCHEDULER_METRICS_PORT"):
            health.metrics_config({"SCHEDULER_METRICS_PORT": bad}, "staging", ("10.0.0.5", 8793))


def test_metrics_server_listens_and_answers():
    async def go():
        srv = health.MetricsServer("127.0.0.1", 0)
        await srv.start()
        try:
            async def ask(line: bytes) -> bytes:
                reader, writer = await asyncio.open_connection("127.0.0.1", srv.bound_port)
                writer.write(line + b"\r\nhost: x\r\n\r\n")
                await writer.drain()
                data = await reader.read()
                writer.close()
                return data
            assert (await ask(b"GET /metrics HTTP/1.1")).startswith(b"HTTP/1.1 200 ")
            assert (await ask(b"GET /healthz HTTP/1.1")).startswith(b"HTTP/1.1 404 ")
        finally:
            await srv.stop()
    asyncio.run(go())


def test_metrics_endpoint_on_metrics_listener():
    async def go():
        async def yes() -> bool:
            return True
        raw = await health.answer_metrics(b"GET /metrics HTTP/1.1")
        head, body = raw.split(b"\r\n\r\n", 1)
        lines = head.decode().split("\r\n")
        assert int(lines[0].split()[1]) == 200
        hdr = "\n".join(lines[1:]).lower()
        assert "content-type: text/plain; version=0.0.4" in hdr and "cache-control: no-store" in hdr and "connection: close" in hdr
        assert f"content-length: {len(body)}" in hdr
        text = body.decode()
        from scheduler import metrics
        names = {k for line in text.splitlines() if line and not line.startswith("#")
                 for k in re.findall(r'([a-zA-Z_][a-zA-Z0-9_]*)="', line.split(" ")[0])}
        assert names <= metrics.obs.LABELS | {"le"}
        assert "starbuddy_scheduler_job_runs_total" in text
        for line in (b"POST /metrics HTTP/1.1", b"HEAD /metrics HTTP/1.1"):
            assert int((await health.answer_metrics(line)).split(b" ", 2)[1]) == 405
        for line in (b"GET /metrics/ HTTP/1.1", b"GET /metrics?x=1 HTTP/1.1"):
            assert int((await health.answer_metrics(line)).split(b" ", 2)[1]) == 404
        assert await yes()
    asyncio.run(go())


def test_registered_metrics_match_inventory():
    code = "import json; from scheduler import metrics; print(json.dumps(metrics.obs.catalog()))"
    out = subprocess.run([sys.executable, "-c", code], cwd=SVC, capture_output=True, text=True, check=True,
                         env={"PATH": "/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1"})
    assert json.loads(out.stdout) == sorted(INVENTORY["services"]["scheduler"])
