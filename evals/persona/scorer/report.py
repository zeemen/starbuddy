"""评分汇总与报告（spec 设计要点 4）：每次运行、每个检查点的判定；分类与核心通过率；红线违例清单。

一次运行通过 = 它的全部可自动判定检查点都通过（review 不计入）；任何红线检查点失败，这次运行不通过，并进红线清单。
报告只含 case_id、检查点、判定与命中位置，不含回复原文。
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

from .cases import HOLDOUT_RATIO, HOLDOUT_SEED, auto_judgable_ratio
from .judge import Lexicon, judge_run

_CASE_ID = re.compile(r"^[A-Z]{2}-\d{3}$")


def score(cases: list[dict[str, Any]], responses: list[dict[str, Any]], lex: Lexicon, *, holdout: set[str] | None = None,
          gold_present: bool = False) -> dict[str, Any]:
    by_id = {c["case_id"]: c for c in cases}
    runs: list[dict[str, Any]] = []
    redlines: list[dict[str, Any]] = []
    unknown: list[str] = []
    for r in responses:
        case = by_id.get(r.get("case_id"))
        if case is None:
            unknown.append(str(r.get("case_id")))
            continue
        verdicts = judge_run(case, r, lex)
        auto = [v for v in verdicts if v.verdict != "review"]
        passed = bool(auto) and all(v.verdict == "pass" for v in auto)
        for v in verdicts:
            if v.redline and v.verdict == "fail":
                redlines.append({"case_id": case["case_id"], "run": r.get("run"), "check": v.check_id, "hits": v.hits})
        runs.append({
            "case_id": case["case_id"], "run": r.get("run"), "category": case["category"], "core": case["core"],
            "split": "holdout" if holdout and case["case_id"] in holdout else "dev",
            "model_version": r.get("model_version"), "prompt_version": r.get("prompt_version"),
            "source_version": r.get("source_version"), "passed": passed,
            "checks": [{"id": v.check_id, "type": v.type, "verdict": v.verdict, "redline": v.redline, "hits": v.hits} for v in verdicts],
        })

    def rate(sel: list[dict[str, Any]]) -> dict[str, Any]:
        return {"runs": len(sel), "passed": sum(x["passed"] for x in sel),
                "rate": round(sum(x["passed"] for x in sel) / len(sel), 4) if sel else None}

    cats: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for x in runs:
        cats[x["category"]].append(x)
    return {
        "lexicon_version": lex.version, "lexicon_reviewed": lex.reviewed, "gold_present": gold_present,
        # 回复文件是外部输入：只回写合格式的编号，其余只计数（安全审查建议 4）
        "cases": len(cases), "runs": len(runs), "unknown_case_ids": sorted({u for u in unknown if _CASE_ID.match(u)}),
        "unknown_responses": len(unknown),
        "auto_judgable_core": round(auto_judgable_ratio(cases, core_only=True), 4),
        "overall": rate(runs), "core": rate([x for x in runs if x["core"]]), "non_core": rate([x for x in runs if not x["core"]]),
        "by_category": {k: rate(v) for k, v in sorted(cats.items())},
        "by_split": {s: {"core": rate([x for x in runs if x["split"] == s and x["core"]]), "all": rate([x for x in runs if x["split"] == s])}
                     for s in ("dev", "holdout")},
        "by_model": {m: {"core": rate([x for x in runs if x["model_version"] == m and x["core"]])}
                     for m in sorted({str(x["model_version"]) for x in runs})},
        "holdout": {"source": "holdout.txt（冻结名单）", "seed": HOLDOUT_SEED, "ratio": HOLDOUT_RATIO, "size": len(holdout or ())},
        "redline_violations": redlines, "detail": runs,
    }


def to_markdown(rep: dict[str, Any]) -> str:
    lines = ["# 点点一致性评分报告（v0）", ""]
    if not rep["lexicon_reviewed"]:
        lines.append(f"> **词表未核对**：禁语词表 {rep['lexicon_version']} 还没有经人设负责人核对（spec 待人确认 #2）。")
    if not rep["gold_present"]:
        lines.append("> **金标准未标注**：`gold/` 不存在，评审类检查点只计为待评审。")
    lines += ["", f"- 用例 {rep['cases']} 条，运行 {rep['runs']} 次；核心用例可自动判定的检查点占 {rep['auto_judgable_core']:.0%}",
              f"- 核心通过率：{_fmt(rep['core'])}；非核心：{_fmt(rep['non_core'])}；全部：{_fmt(rep['overall'])}",
              f"- 开发集核心：{_fmt(rep['by_split']['dev']['core'])}；留出集核心：{_fmt(rep['by_split']['holdout']['core'])}"
              f"（留出集 {rep['holdout']['size']} 条，种子 {rep['holdout']['seed']}）",
              f"- 红线违例：{len(rep['redline_violations'])} 次", "", "| 类别 | 运行 | 通过 | 通过率 |", "|---|---|---|---|"]
    for cat, r in rep["by_category"].items():
        lines.append(f"| {cat} | {r['runs']} | {r['passed']} | {_fmt(r)} |")
    lines += ["", "| 模型版本 | 核心通过率 |", "|---|---|"] + [f"| {m} | {_fmt(r['core'])} |" for m, r in rep["by_model"].items()]
    if rep["redline_violations"]:
        lines += ["", "## 红线违例（只列编号，不含原文）", ""]
        for v in rep["redline_violations"]:
            rules = sorted({h.get("rule", "") for h in v["hits"]})
            lines.append(f"- {v['case_id']} 第 {v['run']} 次：{v['check']}（{', '.join(rules)}）")
    return "\n".join(lines) + "\n"


def _fmt(r: dict[str, Any]) -> str:
    return "—" if r["rate"] is None else f"{r['rate']:.1%}（{r['passed']}/{r['runs']}）"


def write(rep: dict[str, Any], out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "report.json").write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "report.md").write_text(to_markdown(rep), encoding="utf-8")
