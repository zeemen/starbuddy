#!/usr/bin/env python3
"""影子评审比对（specs/sie-02b-claims-first-report/spec.md 设计要点 6、待人确认 #7）：
首份画像报告的判断 vs 影子评审教师的独立判断，逐“学生 × 节点 × 认知层次”比较门槛状态，报一致率与分歧清单。

评测即代码：不联网、不连库、不调模型。只读输入；输出目录不能在 gold/ 下（金标只读，规则 14 同理：构建方不改金标）。
报告只含编码（学生 ID、节点 ID、层次、门槛状态），不含姓名、原文；不含时间与本机路径；同样输入逐字节相同。
合成输入（synthetic: true）上的报告标注“流程样例，不是评测结论”。

一致率口径：
- 可比项 = 报告与教师都给出判断的“学生 × 节点 × 层次”；一致率 = 门槛状态相同的可比项 ÷ 可比项；
- 覆盖率 = 可比项 ÷ 报告判断项（教师应独立判断报告覆盖的全部节点，#7）；
- 分歧清单三类：state_mismatch（两边状态不同）、not_judged（报告有、教师没判）、not_in_report（教师判了、报告没有）。

用法：
  python compare_shadow.py --report fixtures/report-synthetic.json --gold fixtures/gold-synthetic.yaml --out reports/sample-synthetic
  退出码：0 生成成功；2 参数或输入错误
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Any, Iterable

import yaml

REPORT_FORMAT = "sie-shadow-report/0.1"
GOLD_FORMAT = "sie-shadow-gold/0.1"
STATES = ("unseen", "understand", "execute", "stable", "transfer", "retain")  # platform.mastery_state
LEVELS = ("know", "understand", "apply", "synthesize")
UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
NODE_ID = re.compile(r"^[A-Z][A-Z0-9]{1,9}(\.[A-Z0-9][A-Z0-9_]{0,31}){1,4}$")
REVIEWER = re.compile(r"^[a-z0-9][a-z0-9\-]{0,31}$")
HERE = Path(__file__).resolve().parent
SAMPLE_BANNER = "> **流程样例，不是评测结论**：输入是合成数据（合成、非金标），只证明比对流程可用。"


class InputError(ValueError):
    pass


Key = tuple[str, str, str]  # (学生, 节点, 层次)


@dataclass(frozen=True)
class Side:
    synthetic: bool
    judgments: dict[Key, str]
    reviewers: tuple[str, ...] = ()


def _judgments(students: Any, where: str) -> dict[Key, str]:
    if not isinstance(students, list):
        raise InputError(f"{where}: students 必须是列表")
    out: dict[Key, str] = {}
    for s in students:
        if not isinstance(s, dict) or set(s) - {"student_ref", "judgments", "reviewer"} or not UUID.fullmatch(str(s.get("student_ref", ""))):
            raise InputError(f"{where}: 学生条目只收 student_ref（平台学生 UUID）、judgments（与教师评审人编码 reviewer）")
        for j in s.get("judgments") or []:
            if (not isinstance(j, dict) or set(j) != {"node_id", "level", "state"} or not NODE_ID.fullmatch(str(j["node_id"]))
                    or j["level"] not in LEVELS or j["state"] not in STATES):
                raise InputError(f"{where}: 判断只收 node_id、level、state 三个编码字段")
            k = (s["student_ref"], j["node_id"], j["level"])
            if k in out:
                raise InputError(f"{where}: 同一学生 × 节点 × 层次出现两次")
            out[k] = j["state"]
    return out


def load_report(path: Path) -> Side:
    doc = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(doc, dict) or doc.get("format") != REPORT_FORMAT or not isinstance(doc.get("synthetic"), bool):
        raise InputError(f"报告判断文件的 format 必须是 {REPORT_FORMAT}，并写明 synthetic")
    return Side(doc["synthetic"], _judgments(doc.get("students"), "报告"))


def load_gold(path: Path) -> Side:
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(doc, dict) or doc.get("format") != GOLD_FORMAT or not isinstance(doc.get("synthetic"), bool):
        raise InputError(f"教师判断文件的 format 必须是 {GOLD_FORMAT}，并写明 synthetic")
    reviewers = []
    for s in doc.get("students") or []:
        r = s.get("reviewer") if isinstance(s, dict) else None
        if not isinstance(r, str) or not REVIEWER.fullmatch(r):
            raise InputError("教师判断：每个学生要写评审人编码 reviewer（不写姓名）")
        reviewers.append(r)
    return Side(doc["synthetic"], _judgments(doc.get("students"), "教师判断"), tuple(sorted(set(reviewers))))


def _pct(x: Fraction | None) -> str:
    if x is None:
        return "不可计算"
    v = x * 1000
    tenths = v.numerator // v.denominator + (1 if (v - v.numerator // v.denominator) >= Fraction(1, 2) else 0)
    return f"{tenths // 10}.{tenths % 10}%"


def compare(report: Side, gold: Side) -> dict[str, Any]:
    both = sorted(set(report.judgments) & set(gold.judgments))
    agree = [k for k in both if report.judgments[k] == gold.judgments[k]]
    diffs: list[dict[str, Any]] = []
    for k in sorted(set(report.judgments) | set(gold.judgments)):
        r, g = report.judgments.get(k), gold.judgments.get(k)
        if r is not None and g is not None and r == g:
            continue
        kind = "state_mismatch" if r is not None and g is not None else "not_judged" if g is None else "not_in_report"
        d: dict[str, Any] = {"student_ref": k[0], "node_id": k[1], "level": k[2], "kind": kind}
        if r is not None:
            d["report_state"] = r
        if g is not None:
            d["teacher_state"] = g
        diffs.append(d)
    rate = Fraction(len(agree), len(both)) if both else None
    cover = Fraction(len(both), len(report.judgments)) if report.judgments else None
    students = sorted({k[0] for k in report.judgments} | {k[0] for k in gold.judgments})
    return {
        "format": "sie-shadow-compare/0.1",
        "sample": report.synthetic or gold.synthetic,
        "students": len(students),
        "reviewers": len(gold.reviewers),
        "report_items": len(report.judgments),
        "teacher_items": len(gold.judgments),
        "comparable": len(both),
        "agree": len(agree),
        "agreement": None if rate is None else [rate.numerator, rate.denominator],
        "agreement_pct": _pct(rate),
        "coverage": None if cover is None else [cover.numerator, cover.denominator],
        "coverage_pct": _pct(cover),
        "by_kind": {k: sum(1 for d in diffs if d["kind"] == k) for k in ("state_mismatch", "not_judged", "not_in_report")},
        "disagreements": diffs,
    }


def to_markdown(res: dict[str, Any]) -> str:
    lines = ["# 影子评审比对：首份画像报告 vs 教师判断", ""]
    if res["sample"]:
        lines += [SAMPLE_BANNER, ""]
    lines += ["| 项目 | 数值 |", "|---|---|",
              f"| 学生数 | {res['students']} |", f"| 评审教师数 | {res['reviewers']} |",
              f"| 报告判断项（学生 × 节点 × 层次） | {res['report_items']} |", f"| 教师判断项 | {res['teacher_items']} |",
              f"| 可比项 | {res['comparable']} |", f"| 一致项 | {res['agree']} |",
              f"| 一致率（一致 ÷ 可比） | {res['agreement_pct']} |", f"| 覆盖率（可比 ÷ 报告判断项） | {res['coverage_pct']} |", "",
              "## 分歧清单", "", "| 学生 | 节点 | 层次 | 类型 | 报告 | 教师 |", "|---|---|---|---|---|---|"]
    for d in res["disagreements"]:
        lines.append(f"| {d['student_ref']} | {d['node_id']} | {d['level']} | {d['kind']} | {d.get('report_state', '—')} | "
                     f"{d.get('teacher_state', '—')} |")
    if not res["disagreements"]:
        lines.append("| — | — | — | 无分歧 | — | — |")
    lines += ["", "类型：state_mismatch = 门槛状态不同；not_judged = 报告有、教师没判；not_in_report = 教师判了、报告没有。",
              "结论（是否达到验收口径）由人定；真实比对在 KGE-04 第二步、模板审核与教师标注到位后进行。", ""]
    return "\n".join(lines)


def write(res: dict[str, Any], out: Path) -> None:
    if any(p.name == "gold" for p in [out.resolve(), *out.resolve().parents]):
        raise InputError("输出目录不能在 gold/ 下（金标只读）")
    out.mkdir(parents=True, exist_ok=True)
    (out / "compare-shadow.json").write_text(json.dumps(res, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    (out / "compare-shadow.md").write_text(to_markdown(res), encoding="utf-8")


def main(argv: Iterable[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="影子评审比对（首份画像报告 vs 教师判断）")
    ap.add_argument("--report", required=True, type=Path)
    ap.add_argument("--gold", default=HERE / "gold" / "shadow-review.yaml", type=Path, help="教师判断（真实金标在 gold/，只读）")
    ap.add_argument("--out", required=True, type=Path)
    args = ap.parse_args(list(argv) if argv is not None else None)
    try:
        res = compare(load_report(args.report), load_gold(args.gold))
        write(res, args.out)
    except (InputError, OSError, ValueError, yaml.YAMLError) as e:
        print(f"compare-shadow: {type(e).__name__}: {str(e)[:200]}", file=sys.stderr)
        return 2
    print(f"compare-shadow: 一致率 {res['agreement_pct']}，覆盖率 {res['coverage_pct']}{'（流程样例）' if res['sample'] else ''}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
