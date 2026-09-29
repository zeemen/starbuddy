"""评分器自检（spec 设计要点 4）：fixtures/selftest.yaml 给每类（可按正负再分）一组“应通过”和一组“应失败”的合成回复。
对该类的每条用例：应通过的回复必须通过全部可自动判定检查点；应失败的回复至少一个检查点失败；
red 组（红线样例）必须进红线违例清单。回复按用例轮数展开（不够就重复最后一条）；字段值里的 {context.x} 按用例上下文替换。
"""
from __future__ import annotations

import copy
import re
from pathlib import Path
from typing import Any

import yaml

from .cases import load_cases
from .judge import judge_run, load_lexicon
from .report import score

_CTX = re.compile(r"^\{context\.([a-z_]+)\}$")


def _expand(replies: list[dict[str, Any]], case: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for i in range(len(case["turns"])):
        r = copy.deepcopy(replies[min(i, len(replies) - 1)])
        fields = r.get("fields") or {}
        for k, v in list(fields.items()):
            if isinstance(v, str) and _CTX.match(v):
                fields[k] = (case.get("context") or {}).get(_CTX.match(v).group(1))
        r["fields"] = fields
        out.append(r)
    return out


def fixture_for(fx: dict[str, Any], case: dict[str, Any], kind: str) -> list[dict[str, Any]] | None:
    cat = fx.get(case["category"]) or {}
    key = f"{kind}_{case['polarity']}"
    return cat.get(key) or cat.get(kind)


def run_selftest(case_dir: Path, lexicon_path: Path, fixture_path: Path) -> int:
    cases = load_cases(case_dir)
    lex = load_lexicon(lexicon_path)
    fx = yaml.safe_load(Path(fixture_path).read_text(encoding="utf-8"))
    problems: list[str] = []
    for case in cases:
        good = fixture_for(fx, case, "pass")
        bad = fixture_for(fx, case, "fail")
        if not good or not bad:
            problems.append(f"{case['case_id']}: 自检样例缺 pass 或 fail")
            continue
        vs = judge_run(case, {"case_id": case["case_id"], "turns": _expand(good, case)}, lex)
        failed = [v.check_id for v in vs if v.verdict == "fail"]
        if failed:
            problems.append(f"{case['case_id']}: 应通过的回复被判失败 {failed}")
        vs = judge_run(case, {"case_id": case["case_id"], "turns": _expand(bad, case)}, lex)
        if not any(v.verdict == "fail" for v in vs):
            problems.append(f"{case['case_id']}: 应失败的回复没有被判失败")
    # 红线：D 类禁语、首次未披露身份、情感信号未转介，每一类都必须进红线清单
    for cat, cids in (fx.get("redline") or {}).items():
        red = fx[cat]["red"]
        sel = [c for c in cases if c["case_id"] in cids]
        rep = score(sel, [{"case_id": c["case_id"], "run": 1, "turns": _expand(red, c)} for c in sel], lex)
        hit = {v["case_id"] for v in rep["redline_violations"]}
        for c in sel:
            if c["case_id"] not in hit:
                problems.append(f"{c['case_id']}: 红线样例没有进红线违例清单")
            if any(x["passed"] for x in rep["detail"] if x["case_id"] == c["case_id"]):
                problems.append(f"{c['case_id']}: 红线失败的运行却被判通过")
    for p in problems:
        print(f"  {p}")
    if problems:
        print(f"eval-persona: 评分器自检失败（{len(problems)} 处）")
        return 1
    print(f"eval-persona: 评分器自检通过（{len(cases)} 条用例 × 应通过 / 应失败两组合成回复，红线 {sum(len(v) for v in (fx.get('redline') or {}).values())} 条）")
    return 0
