"""LT-05 可观测性（specs/launch-lt05-observability 范围第 1、2、7 条）：洞察服务的 /metrics 与结构化日志（service/obs.py，与学习服务、
知识服务三处逐字相同；脱敏与标签规则的完整用例在 services/learning/tests/test_obs.py）。

- 业务端口上的 /metrics 一律 404；指标端口（obs.metrics_app）只服务 GET /metrics：免鉴权、no-store、Prometheus 文本格式；
  业务应用的 Host 校验照常（Host 不对 400）；
- 契约工具按路由模板计数（未鉴权 401 也计）；带 UUID 的路径不进标签；只出现白名单标签；
- 注册的指标与 deploy/observability/metrics-inventory.yaml 一致；源码里的日志调用不出现禁止字段。
用假连接池，不连库；全部合成值。"""
from __future__ import annotations

import ast
import asyncio
import json
import re
import subprocess
import sys
from pathlib import Path

import yaml

from insight.service import obs
from insight.service.contract import contract
from test_health import app_and_pool, get

SVC = Path(__file__).resolve().parents[1]
ROOT = SVC.parents[1]
INVENTORY = yaml.safe_load((ROOT / "deploy/observability/metrics-inventory.yaml").read_text(encoding="utf-8"))
UUID = "5f0e0000-0000-4000-8000-00000000abcd"


def label_names(text: str) -> set[str]:
    return {k for line in text.splitlines() if line and not line.startswith("#")
            for k in re.findall(r'([a-zA-Z_][a-zA-Z0-9_]*)="', line.split(" ")[0])}


def test_business_port_metrics_404_and_metrics_port_only_serves_metrics():
    app, _pool = app_and_pool()
    tool = next(iter(contract().tools.values())).path

    async def go():
        async with app.router.lifespan_context(app):
            assert (await get(app, tool, method="POST", content=b"{}")).status_code == 401
            assert (await get(app, f"/v0/{UUID}", method="POST")).status_code in (404, 405)
            biz = await get(app, "/metrics")
            assert biz.status_code == 404 and "starbuddy_" not in biz.text, "业务端口上的 /metrics 一律 404"
            bad = await get(app, "/metrics", headers={"host": "evil.example.invalid"})
            assert bad.status_code == 400
            # 指标端口（obs.metrics_app）：只服务 GET /metrics
            r = await get(obs.metrics_app, "/metrics")
            assert r.status_code == 200 and r.headers["cache-control"] == "no-store" and r.headers["content-type"].startswith("text/plain")
            for p in (tool, "/healthz", "/metrics/", "/metrics?x=1", "/"):
                assert (await get(obs.metrics_app, p)).status_code == 404, p
            assert (await get(obs.metrics_app, "/metrics", method="POST")).status_code == 405
            return r.text
    def unmatched() -> float:
        return sum(obs.REGISTRY.get_sample_value("starbuddy_http_requests_total", {"service": "insight", "route": "unmatched",
                                                                                   "method": "POST", "status_class": sc}) or 0.0
                   for sc in ("4xx", "5xx"))
    un0 = unmatched()
    text = asyncio.run(go())
    assert unmatched() == un0 + 1, "表外路径（带 UUID）按 unmatched 计数，不记原始路径"
    assert obs.REGISTRY.get_sample_value("starbuddy_http_requests_total", {"service": "insight", "route": tool, "method": "POST",
                                                                           "status_class": "4xx"}) >= 1
    assert UUID not in text and "starbuddy_ready" in text
    assert label_names(text) <= obs.LABELS | {"le"}


def test_registered_metrics_match_inventory():
    code = "import insight.service.app, json; from insight.service import obs; print(json.dumps(obs.catalog()))"
    out = subprocess.run([sys.executable, "-c", code], cwd=SVC, capture_output=True, text=True, check=True,
                         env={"PATH": "/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1"})
    assert json.loads(out.stdout) == sorted(INVENTORY["services"]["insight"])
    assert obs.LABELS == set(INVENTORY["labels"])


def test_scrub_and_format_basics():
    out = obs.scrub({"tool": "insight.claims", "status": 200, "phone": "13812345678", "text": "判断原文", "detail": "mail a@b.example.invalid"})
    assert out == {"tool": "insight.claims", "status": 200, "detail": f"mail {obs.REDACTED}"}
    # 顶层字段白名单（安全审查 B）：键名先驼峰归一，只留登记的元数据键；正文 UUID、带分隔的手机号替换
    lf = obs.log_fields({"studentId": UUID, "student_name": "小明", "message": "原文", "ocr_text": "x", "photo_url": "x",
                         "tool": "insight.claims", "durationMs": 3, "detail": f"{UUID} 138 1234 5678"})
    assert lf == {"tool": "insight.claims", "duration_ms": 3, "detail": f"{obs.UUID_MARK} {obs.REDACTED}"}


def test_metrics_port_config():
    from insight.service.config import Settings
    base = {"INSIGHT_ENV": "test", "INSIGHT_DATABASE_URL": "postgresql://syn@127.0.0.1/syn", "INSIGHT_TOKEN_BFF": "b" * 40}
    assert Settings.from_env(base).metrics_port is None, "dev / test 不设不起"
    assert Settings.from_env(base | {"INSIGHT_METRICS_PORT": "9999"}).metrics_port == 9999
    import pytest
    for bad in ("8791", "0", "x", "09791"):
        with pytest.raises(RuntimeError, match="INSIGHT_METRICS_PORT"):
            Settings.from_env(base | {"INSIGHT_METRICS_PORT": bad})


def test_source_log_calls_carry_no_forbidden_fields():
    bad = []
    for p in sorted((SVC / "insight").rglob("*.py")):
        for node in ast.walk(ast.parse(p.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in (
                    "debug", "info", "warning", "error", "exception", "critical", "log"):
                for d in ast.walk(node):
                    if isinstance(d, ast.Dict):
                        bad += [(p.name, k.value) for k in d.keys
                                if isinstance(k, ast.Constant) and isinstance(k.value, str) and obs.forbidden_key(k.value)]
    assert bad == [], f"日志里出现了禁止字段：{bad}"
