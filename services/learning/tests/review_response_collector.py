"""pytest 插件（specs/review-console-responses 第二节第 2 条）：记下审核台每个接口的 2xx 响应（经过了响应模型校验）。

只由 tests/test_responses_coverage_review.py 在子进程里经 `-p review_response_collector` 打开；结果写到 REVIEW_RESPONSE_COVERAGE
指定的文件（JSON：{"METHOD 路由模板": 次数}）。只记路由模板与状态，不记请求与响应内容。
"""
from __future__ import annotations

import json
import os

HITS: dict[str, int] = {}


def pytest_configure(config):
    import review_console.app as m
    orig = m.create_app

    def create_app(*a, **kw):
        app = orig(*a, **kw)

        @app.middleware("http")
        async def _record(request, call_next):
            resp = await call_next(request)
            route = request.scope.get("route")
            if route is not None and 200 <= resp.status_code < 300:
                key = f"{request.method} {route.path}"
                HITS[key] = HITS.get(key, 0) + 1
            return resp
        return app

    m.create_app = create_app


def pytest_unconfigure(config):
    out = os.environ.get("REVIEW_RESPONSE_COVERAGE")
    if out:
        with open(out, "w", encoding="utf-8") as f:
            json.dump(HITS, f, ensure_ascii=False, sort_keys=True)
