"""审核台响应模型的覆盖核对（specs/review-console-responses 第二节第 2 条）。

每个接口都挂了 extra="forbid" 的响应模型（review_console/responses.py），但模型与真实返回对不对得上，只有真打到那个接口才知道。
这里在子进程里把审核台的测试文件（tests/test_review_*.py）带着收集插件跑一遍，断言 responses.ROUTES 里的每个接口都至少有一次 2xx
（2xx 即已通过响应模型校验），且收集到的接口都登记在 ROUTES 里。文件名不以 test_review_ 开头，免得子进程把自己也收进去。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from review_console.responses import ROUTES

HERE = Path(__file__).resolve().parent


def test_every_review_route_has_a_2xx_through_its_response_model(tmp_path):
    if os.environ.get("REVIEW_RESPONSE_COVERAGE"):
        pytest.skip("已在覆盖核对的子进程里")
    out = tmp_path / "hits.json"
    files = sorted(str(p) for p in HERE.glob("test_review_*.py"))
    env = {**os.environ, "REVIEW_RESPONSE_COVERAGE": str(out),
           "PYTHONPATH": os.pathsep.join([str(HERE), str(HERE.parent), os.environ.get("PYTHONPATH", "")])}
    r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-p", "review_response_collector", *files],
                       cwd=HERE.parent, env=env, capture_output=True, text=True, timeout=1800)
    tail = "\n".join(r.stdout.splitlines()[-15:])
    if "skipped" in tail and "passed" not in tail:
        pytest.skip("审核台测试在子进程里被跳过（没有测试库）")
    assert r.returncode == 0, f"子进程里的审核台测试没有全部通过：\n{tail}\n{r.stderr[-2000:]}"
    hits = json.loads(out.read_text(encoding="utf-8"))
    missing = sorted(set(ROUTES) - set(hits))
    assert not missing, f"这些接口在审核台测试里没有一次 2xx（响应模型没被真实返回核对过），补一条用例：{missing}"
    unknown = sorted(set(hits) - set(ROUTES))
    assert not unknown, f"收集到没在 responses.ROUTES 登记的接口：{unknown}"
