"""首轮差异报告（冲刺 2-I，specs/diandian-consistency-r1-sprint2/spec.md 设计要点 2）。

输入：每个候选各次运行的评分结果（scorer.report.score 的输出，一次运行一份）。输出：Markdown 与 JSON，确定性（同样输入逐字节相同）。
- 每个候选：总通过率、按九类通过率、核心用例通过率、红线命中（单列）、N 次之间不稳定用例比例；
- 两两：通过率差与 95% 置信区间（按用例配对的自助法，固定种子；用例级得分 = N 次通过的平均）、按类差值表、
  “只在某一家失败”的用例清单（只列用例 ID 与检查点编号）；
- 对照 3-I 口径（核心用例通过率差 ≤ 5 个百分点）只报告、不下结论。
报告只含编号、比率与计数，不含回复原文，也不含命中位置。
"""
from __future__ import annotations

import json
import math
import random
from collections import defaultdict
from itertools import combinations
from pathlib import Path
from typing import Any

from .cases import CORE

BOOTSTRAP_RESAMPLES = 1000
BOOTSTRAP_SEED = "diandian-consistency-r1"
CI_LEVEL = 0.95
CRITERION_3I_PP = 5.0  # 3-I 验收口径：三家在核心用例上通过率差 ≤ 5 个百分点（首轮只报告）
SAMPLE_BANNER = "流程样例，不是模型评测结果"


def _r(x: float | None) -> float | None:
    return None if x is None else round(x, 4) + 0.0  # + 0.0 把 -0.0 规整成 0.0，保证输出确定


def _rate(passed: int, runs: int) -> dict[str, Any]:
    return {"runs": runs, "passed": passed, "rate": _r(passed / runs) if runs else None}


def paired_bootstrap(a: list[float], b: list[float], *, resamples: int = BOOTSTRAP_RESAMPLES,
                     seed: str = BOOTSTRAP_SEED) -> dict[str, Any] | None:
    """按用例配对的自助法：每次有放回地抽 n 条用例，算 mean(a_i - b_i)；取 2.5% 与 97.5% 分位（最近秩法）。

    a、b 是同一组用例、同一顺序的用例级得分。下界取排序后第 floor(0.025·B) 个（0 起），上界取第 ceil(0.975·B) 个（1 起）。
    """
    if len(a) != len(b):
        raise ValueError("配对自助法要求两边用例一一对应")
    n = len(a)
    if n == 0:
        return None
    d = [x - y for x, y in zip(a, b)]
    rng = random.Random(seed)
    means = sorted(sum(d[rng.randrange(n)] for _ in range(n)) / n for _ in range(resamples))
    alpha = (1 - CI_LEVEL) / 2
    lo = means[int(math.floor(alpha * resamples))]
    hi = means[int(math.ceil((1 - alpha) * resamples)) - 1]
    return {"diff": _r(sum(d) / n), "ci_low": _r(lo), "ci_high": _r(hi), "cases": n}


def summarize_candidate(runs: list[dict[str, Any]]) -> dict[str, Any]:
    """一个候选 N 次运行的汇总；runs 是 scorer.report.score 的输出列表（第 k 个元素 = 第 k 次）"""
    per_case: dict[str, list[bool]] = defaultdict(list)
    meta: dict[str, tuple[str, bool]] = {}
    failed_checks: dict[str, set[str]] = defaultdict(set)
    redlines: list[dict[str, Any]] = []
    model_versions: set[str] = set()
    for k, rep in enumerate(runs, start=1):
        for x in rep["detail"]:
            per_case[x["case_id"]].append(bool(x["passed"]))
            meta[x["case_id"]] = (x["category"], bool(x["core"]))
            model_versions.add(str(x.get("model_version")))
            if not x["passed"]:
                failed_checks[x["case_id"]] |= {c["id"] for c in x["checks"] if c["verdict"] == "fail"}
        # 红线单列：只要编号、第几次、检查点，不要命中位置
        redlines += [{"case_id": v["case_id"], "run": k, "check": v["check"]} for v in rep["redline_violations"]]
    case_ids = sorted(per_case)

    def agg(ids: list[str]) -> dict[str, Any]:
        return _rate(sum(sum(per_case[c]) for c in ids), sum(len(per_case[c]) for c in ids))

    by_cat: dict[str, list[str]] = defaultdict(list)
    for c in case_ids:
        by_cat[meta[c][0]].append(c)
    unstable = [c for c in case_ids if len(set(per_case[c])) > 1]
    return {
        "runs_per_case": sorted({len(v) for v in per_case.values()}),
        "overall": agg(case_ids),
        "core": agg([c for c in case_ids if meta[c][1]]),
        "non_core": agg([c for c in case_ids if not meta[c][1]]),
        "by_category": {cat: agg(by_cat[cat]) for cat in sorted(by_cat)},
        "redline": {"count": len(redlines), "cases": len({v["case_id"] for v in redlines}),
                    "items": sorted(redlines, key=lambda v: (v["case_id"], v["run"], v["check"]))},
        "unstable": {"cases": len(case_ids), "unstable": len(unstable), "ratio": _r(len(unstable) / len(case_ids)) if case_ids else None,
                     "case_ids": unstable},
        "model_versions": sorted(model_versions),
        # 以下只供两两比较使用，写报告时去掉
        "_case_score": {c: sum(per_case[c]) / len(per_case[c]) for c in case_ids},
        "_meta": meta,
        "_failed_checks": {c: sorted(v) for c, v in failed_checks.items()},
    }


def build(meta: dict[str, Any], candidates: list[dict[str, Any]], *, resamples: int = BOOTSTRAP_RESAMPLES,
          seed: str = BOOTSTRAP_SEED) -> dict[str, Any]:
    """candidates：[{id, vendor, model, model_version, note, runs: [score 输出...]}]，按清单顺序"""
    sums = {c["id"]: summarize_candidate(c["runs"]) for c in candidates}
    ids = [c["id"] for c in candidates]
    case_sets = {cid: set(s["_case_score"]) for cid, s in sums.items()}
    common = sorted(set.intersection(*case_sets.values())) if case_sets else []
    if any(v != set(common) for v in case_sets.values()):
        raise ValueError("各候选评分的用例集合不一致，无法配对比较")
    meta_by_case = next(iter(sums.values()))["_meta"] if sums else {}
    core_ids = [c for c in common if meta_by_case[c][1]]
    cats = sorted({meta_by_case[c][0] for c in common})

    pairs = []
    for a, b in combinations(ids, 2):
        sa, sb = sums[a]["_case_score"], sums[b]["_case_score"]

        def boot(sel: list[str], scope: str) -> dict[str, Any] | None:
            return paired_bootstrap([sa[c] for c in sel], [sb[c] for c in sel], resamples=resamples, seed=f"{seed}:{a}:{b}:{scope}")

        by_cat = {}
        for cat in cats:
            sel = [c for c in common if meta_by_case[c][0] == cat]
            by_cat[cat] = _r(sum(sa[c] - sb[c] for c in sel) / len(sel))
        pairs.append({"a": a, "b": b, "overall": boot(common, "overall"), "core": boot(core_ids, "core"), "by_category": by_cat})

    only_one = []
    for c in common:
        failing = [cid for cid in ids if sums[cid]["_case_score"][c] < 1]
        if len(failing) == 1:
            cid = failing[0]
            only_one.append({"case_id": c, "candidate": cid, "category": meta_by_case[c][0],
                             "checks": sums[cid]["_failed_checks"].get(c, []), "failed_runs": _failed_runs(sums[cid], c)})

    core_diffs = [abs(p["core"]["diff"]) for p in pairs if p["core"] is not None]
    report = {
        "report": "diandian-consistency-r1", "schema_version": 1,
        "sample": bool(meta.get("sample")), "banner": SAMPLE_BANNER if meta.get("sample") else None,
        **{k: v for k, v in meta.items() if k != "sample"},
        "cases": len(common), "core_cases": len(core_ids), "categories": cats,
        "statistics": {"unit": "用例级得分 = N 次通过的平均；通过率 = 通过次数 / 运行次数", "ci": CI_LEVEL,
                       "method": "按用例配对的自助法（百分位，最近秩）", "resamples": resamples, "seed": seed},
        "candidates": [{**{k: c.get(k) for k in ("id", "vendor", "model", "model_version", "note", "fake_profile")},
                        "runs": len(c["runs"]), "calls": c.get("calls"), "call_errors": c.get("call_errors"),
                        "model_mismatch": c.get("model_mismatch"),
                        **{k: v for k, v in sums[c["id"]].items() if not k.startswith("_")}} for c in candidates],
        "pairs": pairs,
        "only_one_failed": only_one,
        "criterion_3i": {"metric": "核心用例通过率两两差的最大绝对值", "threshold_pp": CRITERION_3I_PP,
                         "max_abs_core_diff_pp": _r(max(core_diffs) * 100) if core_diffs else None,
                         "conclusion": None, "note": "首轮只报告、不下结论；是否满足由人定（Gate 0）"},
    }
    return report


def _failed_runs(summary: dict[str, Any], case_id: str) -> int:
    score = summary["_case_score"][case_id]
    n = summary["runs_per_case"][-1]
    return int(round((1 - score) * n))


def _pct(r: dict[str, Any] | None) -> str:
    if not r or r.get("rate") is None:
        return "—"
    return f"{r['rate'] * 100:.1f}%（{r['passed']}/{r['runs']}）"


def _pp(x: float | None) -> str:
    return "—" if x is None else f"{x * 100 + 0.0:+.1f}"


def _ci(b: dict[str, Any] | None) -> str:
    return "—" if b is None else f"{_pp(b['diff'])}（95% CI {_pp(b['ci_low'])} ~ {_pp(b['ci_high'])}）"


def to_markdown(rep: dict[str, Any]) -> str:
    lines = ["# 点点跨模型一致性 · 首轮差异报告（r1）", ""]
    if rep["sample"]:
        lines += [f"> **{SAMPLE_BANNER}**：候选全部是 test 环境的进程内假供应商，回复由测试夹具按设计构造；"
                  "数字只证明运行器与报告计算的流程，不代表任何模型的表现。", ""]
    if not rep.get("lexicon_reviewed", True):
        lines.append(f"> **词表未核对**：禁语词表 {rep.get('lexicon_version')} 还没有经人设负责人核对。")
    if not rep.get("gold_present", True):
        lines.append("> **金标准未标注**：评审类检查点只计为待评审，不计入通过率。")
    prompt = rep.get("prompt") or {}
    lines += [
        "",
        f"- 环境：{rep.get('env')}；每个候选 {rep.get('runs_per_candidate')} 次；用例 {rep['cases']} 条（核心 {rep['core_cases']} 条），"
        f"留出集 {rep.get('holdout_excluded')} 条未运行（规则 14）",
        f"- 人设 Prompt：{prompt.get('source')}（{prompt.get('source_version')}，sha256 {str(prompt.get('sha256'))[:12]}）；"
        f"回复格式：{rep.get('reply_format')}",
        f"- 统计：{rep['statistics']['unit']}；置信区间为{rep['statistics']['method']}，{rep['statistics']['resamples']} 次，"
        f"种子 `{rep['statistics']['seed']}`",
        "- 报告只含用例编号、检查点编号、比率与计数，不含回复原文。",
        "", "## 每个候选", "",
        "| 候选 | 模型版本 | 总通过率 | 核心通过率 | 红线命中（次 / 用例） | 不稳定用例 | 调用失败 |",
        "|---|---|---|---|---|---|---|",
    ]
    for c in rep["candidates"]:
        u = c["unstable"]
        lines.append(f"| {c['id']} | {c.get('model_version')} | {_pct(c['overall'])} | {_pct(c['core'])} | "
                     f"{c['redline']['count']} / {c['redline']['cases']} | {u['ratio'] * 100:.1f}%（{u['unstable']}/{u['cases']}） | "
                     f"{c.get('call_errors')} |")
    cats = rep["categories"]
    lines += ["", "### 按类通过率", "", "| 类别 | 核心 | " + " | ".join(c["id"] for c in rep["candidates"]) + " |",
              "|---|---|" + "---|" * len(rep["candidates"])]
    for cat in cats:
        lines.append(f"| {cat} | {'是' if cat in CORE else '否'} | " + " | ".join(_pct(c["by_category"].get(cat)) for c in rep["candidates"]) + " |")
    lines += ["", "### 红线命中（单列；只列编号，不含原文）", ""]
    any_red = False
    for c in rep["candidates"]:
        for v in c["redline"]["items"]:
            any_red = True
            lines.append(f"- {c['id']}：{v['case_id']} 第 {v['run']} 次，检查点 {v['check']}")
    if not any_red:
        lines.append("- 无")
    lines += ["", "## 两两比较", "", "| A − B | 总通过率差（百分点） | 核心通过率差（百分点） |", "|---|---|---|"]
    for p in rep["pairs"]:
        lines.append(f"| {p['a']} − {p['b']} | {_ci(p['overall'])} | {_ci(p['core'])} |")
    lines += ["", "### 按类差值（百分点，A − B）", "", "| 类别 | " + " | ".join(f"{p['a']} − {p['b']}" for p in rep["pairs"]) + " |",
              "|---|" + "---|" * len(rep["pairs"])]
    for cat in cats:
        lines.append(f"| {cat} | " + " | ".join(_pp(p["by_category"].get(cat)) for p in rep["pairs"]) + " |")
    lines += ["", "### 只在某一家失败的用例（用例 ID 与检查点编号）", ""]
    if rep["only_one_failed"]:
        lines += ["| 用例 | 类别 | 失败的候选 | 失败次数 | 失败检查点 |", "|---|---|---|---|---|"]
        for x in rep["only_one_failed"]:
            lines.append(f"| {x['case_id']} | {x['category']} | {x['candidate']} | {x['failed_runs']} | {', '.join(x['checks'])} |")
    else:
        lines.append("- 无")
    crit = rep["criterion_3i"]
    max_pp = "—" if crit["max_abs_core_diff_pp"] is None else f"{crit['max_abs_core_diff_pp']:.1f}"
    lines += ["", "## 对照 3-I 验收口径（只报告，不下结论）", "",
              f"- 口径：三家在核心用例上通过率两两差 ≤ {crit['threshold_pp']:g} 个百分点。",
              f"- 本轮核心通过率两两差的最大绝对值：{max_pp} 个百分点"
              "（置信区间见上表）。",
              f"- {crit['note']}。"]
    return "\n".join(lines) + "\n"


def write(rep: dict[str, Any], out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "report.json").write_text(json.dumps(rep, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (out_dir / "report.md").write_text(to_markdown(rep), encoding="utf-8")


__all__ = ["BOOTSTRAP_RESAMPLES", "BOOTSTRAP_SEED", "CRITERION_3I_PP", "SAMPLE_BANNER", "build", "paired_bootstrap", "summarize_candidate", "to_markdown", "write"]
