"""测试用洞察服务的 venv 运行（make kge-calibrate-test；本工具纯标准库，只借它的 pytest）：把本工具目录加进 sys.path。
变异脚本在临时目录里跑本工具的副本；仓库里其他服务的源码（学习服务门槛取值、知识服务快照白名单）与错因分类表
（docs/mapping/error-taxonomy.md）用 KGE_CALIBRATE_REPO 指回仓库，只读核对。"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

TOOL = Path(__file__).resolve().parents[1]
REPO = Path(os.environ.get("KGE_CALIBRATE_REPO") or TOOL.parents[1])
TAXONOMY = REPO / "docs/mapping/error-taxonomy.md"
if str(TOOL) in sys.path:
    sys.path.remove(str(TOOL))
sys.path.insert(0, str(TOOL))


@pytest.fixture(scope="session")
def synthetic():
    """固定种子的合成队列（第一步 + 第二步全部输入）与报表；多个测试文件共用"""
    from kge_calibrate import synth as Y
    from kge_calibrate.config import load_config
    from kge_calibrate.inputs import parse_map, parse_mastery
    from kge_calibrate.report import build_report, run
    from kge_calibrate.responses import Step2, parse_attempts, parse_errors, parse_pitfall_codes
    from kge_calibrate.taxonomy import load_taxonomy

    cfg = load_config()
    inputs = Y.render_inputs()
    mp = parse_map(json.loads(inputs["map-snapshot.json"]))
    mastery = parse_mastery(inputs["mastery.csv"], mp, cfg.mastery)
    tax = load_taxonomy(TAXONOMY)
    step2 = Step2(tax, parse_attempts(inputs["attempts.csv"], mp), parse_errors(inputs["errors.csv"], mp, tax, cfg.pitfall.min_confidence),
                  parse_pitfall_codes(json.loads(inputs["pitfall-codes.json"]), mp, tax))
    report = build_report(mp, mastery, cfg, step2)
    return {"inputs": inputs, "map": mp, "mastery": mastery, "taxonomy": tax, "step2": step2, "report": report,
            "files": run(mp, mastery, cfg, step2)}
