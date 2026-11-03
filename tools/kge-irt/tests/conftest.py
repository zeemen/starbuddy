"""测试用洞察服务的 venv 运行（make kge-irt-test；本工具纯标准库，只借它的 pytest）：把本工具目录加进 sys.path。
本工具复用 tools/kge-calibrate 的守卫与输出函数（kge_irt/__init__.py 把它加进 sys.path）；变异脚本在临时目录里跑本工具的副本，
用 KGE_IRT_REPO 指回仓库（kge-calibrate 源码、学习服务的掌握配置与 bkt.py 只读核对）。"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

TOOL = Path(__file__).resolve().parents[1]
REPO = Path(os.environ.get("KGE_IRT_REPO") or TOOL.parents[1])
if str(TOOL) in sys.path:
    sys.path.remove(str(TOOL))
sys.path.insert(0, str(TOOL))
import kge_irt  # noqa: E402,F401  （先导入本包：它把 tools/kge-calibrate 加进 sys.path）


def load_synthetic(draws: int | None = None):
    from kge_irt import synth as Y
    from kge_irt.analysis import analyze
    from kge_irt.config import load_config
    from kge_irt.inputs import parse_baseline, parse_items, parse_responses

    cfg = load_config()
    inputs = Y.render_inputs(fit_fraction=cfg.synthetic_fit_fraction)
    iv = parse_items(json.loads(inputs["items.json"]))
    resp = parse_responses(inputs["responses.csv"], iv, cfg.min_fit_fraction, cfg.max_fit_fraction)
    base = parse_baseline(inputs["baseline.csv"], iv, resp)
    an = analyze(iv, resp, base, cfg, draws)
    return {"cfg": cfg, "inputs": inputs, "items": iv, "responses": resp, "baseline": base, "analysis": an}


@pytest.fixture(scope="session")
def synthetic():
    """固定种子的合成队列、全量分析（含自助法）与报表；多个测试共用"""
    from kge_irt.report import build_report, run

    d = load_synthetic()
    d["report"] = build_report(d["items"], d["responses"], d["analysis"], d["cfg"])
    d["files"] = run(d["items"], d["responses"], d["analysis"], d["cfg"], d["baseline"].pseudonyms)
    return d
