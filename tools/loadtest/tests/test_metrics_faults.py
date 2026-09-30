"""分位数（最近秩）与汇总；故障注入的判定；假供应商 HTTP 接口（经 ASGI，不起端口）。"""
from __future__ import annotations

import json
import random

import httpx
import pytest

from loadtest.faults import FaultController, FaultSpec, LatencyModel, decide
from loadtest.metrics import Recorder, by_endpoint, by_stage, percentile, summarize


def test_percentile_nearest_rank_by_hand():
    v = [float(x) for x in range(1, 101)]  # 1..100
    assert percentile(v, 0.50) == 50 and percentile(v, 0.95) == 95 and percentile(v, 0.99) == 99 and percentile(v, 1.0) == 100
    assert percentile([5.0, 1.0, 3.0], 0.5) == 3.0  # ceil(1.5) = 2 → 升序第 2 个
    assert percentile([7.0], 0.99) == 7.0 and percentile([], 0.5) is None
    with pytest.raises(ValueError):
        percentile([1.0], 0)


def test_summary_error_rate_and_throughput():
    s = summarize([10.0, 20.0, 30.0, 40.0], errors=1, total=4, seconds=2.0)
    assert s["error_rate"] == 0.25 and s["throughput_rps"] == 2.0 and s["p50_ms"] == 20.0 and s["max_ms"] == 40.0


def test_recorder_groups_by_endpoint_and_stage():
    r = Recorder()
    r.stage_windows = {0: [0.0, 10.0], 1: [10.0, 20.0]}
    for ms in (10, 20, 30):
        r.add(0, "today", ms, "ok", "200")
    r.add(1, "today", 99, "http_5xx", "503")
    r.add(1, "grade.aya", 5, "rejected", "402")
    ep = by_endpoint(r, 20.0)
    assert ep["today"]["count"] == 4 and ep["today"]["errors"] == 1 and ep["today"]["codes"] == {"200": 3, "503": 1}
    assert ep["grade.aya"]["errors"] == 0 and ep["grade.aya"]["rejected"] == 1, "402 业务拒绝不算错误，单列"
    st = by_stage(r)
    assert [s["count"] for s in st] == [3, 2] and st[1]["error_rate"] == 0.5


def test_fault_decisions():
    rng = random.Random(1)
    lat = LatencyModel()
    assert decide(FaultSpec(mode="unavailable"), lat, rng).kind == "server"
    assert decide(FaultSpec(mode="timeout", hang_s=5), lat, rng) .kind == "timeout"
    d = decide(FaultSpec(mode="rate_limited", retry_after_s=2.0), lat, rng)
    assert d.kind == "rate_limited" and d.retry_after_s == 2.0
    assert decide(FaultSpec(mode="slow", extra_latency_ms=250), lat, rng).delay_s == pytest.approx(0.25)
    kinds = [decide(FaultSpec(mode="error_ratio", error_ratio=0.3), lat, rng).kind for _ in range(4000)]
    assert kinds.count("server") / 4000 == pytest.approx(0.3, abs=0.03)
    assert decide(FaultSpec(), lat, rng).kind is None
    with pytest.raises(ValueError):
        FaultSpec(mode="explode")
    with pytest.raises(ValueError):
        FaultSpec.parse({"mode": "ok", "extra": 1})


def test_latency_model_median_and_p90():
    rng = random.Random(3)
    m = LatencyModel(200, 600)
    xs = sorted(m.sample(rng) for _ in range(20000))
    assert xs[10000] == pytest.approx(200, rel=0.05) and xs[18000] == pytest.approx(600, rel=0.07)


def test_controller_is_seeded_per_vendor():
    a, b = FaultController(seed=5), FaultController(seed=5)
    for c in (a, b):
        c.set("fake-a", FaultSpec(mode="error_ratio", error_ratio=0.5))
    assert [a.decide("fake-a").kind for _ in range(50)] == [b.decide("fake-a").kind for _ in range(50)]
    assert a.counts["fake-a"]["server"] + a.counts["fake-a"]["ok"] == 50


async def test_fake_provider_http_controls_faults_and_counts_only_categories():
    from loadtest.fake_provider import App, estimate_tokens

    app = App(FaultController(seed=1), model=type("M", (), {"reply": staticmethod(lambda text: '{"points": []}')})())
    body = {"model": "fake-reasoner", "messages": [{"role": "user", "content": "合成" * 30}]}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://fake") as c:
        r = await c.post("/fake-a/chat/completions", json=body)
        assert r.status_code == 200
        out = r.json()
        assert out["usage"] == {"prompt_tokens": estimate_tokens(60), "completion_tokens": estimate_tokens(len('{"points": []}'))}
        assert out["model"] == "fake-reasoner"
        assert (await c.post("/_control/faults", json={"vendor": "fake-a", "spec": {"mode": "unavailable"}})).status_code == 200
        assert (await c.post("/fake-a/chat/completions", json=body)).status_code == 503
        assert (await c.post("/fake-b/chat/completions", json=body)).status_code == 200, "只影响被注入的那一家"
        await c.post("/_control/faults", json={"vendor": "fake-b", "spec": {"mode": "rate_limited", "retry_after_s": 1.5}})
        r = await c.post("/fake-b/chat/completions", json=body)
        assert r.status_code == 429 and r.headers["retry-after"] == "1.5"
        assert (await c.post("/_control/faults", json={"vendor": "nope", "spec": {"mode": "ok"}})).status_code == 400
        await c.post("/_control/faults", json={"clear": True})
        assert (await c.post("/fake-a/chat/completions", json=body)).status_code == 200
        stats = (await c.get("/_control/stats")).json()
        assert stats["counts"]["fake-a"] == {"ok": 2, "server": 1}
        assert "合成" not in json.dumps(stats, ensure_ascii=False), "计数不含请求内容"
        assert (await c.post("/fake-q/chat/completions", json=body)).status_code == 404
