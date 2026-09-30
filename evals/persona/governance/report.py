"""人设回归报告（spec 设计要点 7）：persona-report.json（按 schema/report.schema.json 校验）与 persona-report.md（人读摘要）。

md 第一屏是前后对比表（列与 persona-evaluator 的七项一致），可以直接贴进 PR 描述（CLAUDE.md 规则 7）。
报告只含合成用例的编号、规则 ID 与位置，不含回复原文，不含任何真实学生数据（规则 8）。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import jsonschema

from .common import CATEGORIES, EVALUATOR_CATEGORIES, EVALUATOR_LABELS, FAMILY_CHECKS, FAMILY_LABELS, HERE

SCHEMA_PATH = HERE / "schema" / "report.schema.json"


def empty_row() -> dict[str, Any]:
    return {"total": 0, "passed": 0, "failed": 0, "not_run": 0, "pass_rate": None, "baseline_pass_rate": None, "delta": None}


def finalize_rows(results: dict[str, dict[str, dict[str, Any]]], baseline: dict[str, Any] | None) -> None:
    for pid, cats in results.items():
        for cat, row in cats.items():
            ran = row["passed"] + row["failed"]
            row["pass_rate"] = round(row["passed"] / ran, 4) if ran else None
            b = ((baseline or {}).get(pid) or {}).get(cat) or {}
            row["baseline_pass_rate"] = b.get("pass_rate")
            row["delta"] = (round(row["pass_rate"] - row["baseline_pass_rate"], 4)
                            if row["pass_rate"] is not None and row["baseline_pass_rate"] is not None else None)


def validate(rep: dict[str, Any]) -> list[str]:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    return [f"{'/'.join(map(str, e.absolute_path))}: {e.message}" for e in jsonschema.Draft202012Validator(schema).iter_errors(rep)]


def _cell(row: dict[str, Any] | None) -> str:
    if not row or not row["total"]:
        return "—"
    now = "未运行" if row["pass_rate"] is None else f"{row['pass_rate']:.0%}"
    base = "—" if row["baseline_pass_rate"] is None else f"{row['baseline_pass_rate']:.0%}"
    if row["delta"] is not None and row["delta"] != 0:
        return f"{base} → {now}（{row['delta']:+.0%}）"
    return f"{base} → {now}"


def to_markdown(rep: dict[str, Any]) -> str:
    c = rep["conclusion"]
    lines = ["# 人设回归报告（冲刺 3-F 人设治理组件）", "",
             f"**结论：{'通过' if c['status'] == 'pass' else ('未运行（缺录音）' if c['status'] == 'not_run' else '不通过')}**"
             f" ｜ 触发 {rep['trigger']} ｜ 提交 `{rep['commit'][:12]}` ｜ 基线 `{(rep['base_commit'] or '无')[:12]}`", "",
             "## 前后对比（基线 → 当前；列与 persona-evaluator 的七项一致）", "",
             "| 角色 | " + " | ".join(EVALUATOR_LABELS[k] for k in EVALUATOR_CATEGORIES) + " |",
             "|---|" + "---|" * len(EVALUATOR_CATEGORIES)]
    for pid, cats in rep["results"].items():
        lines.append(f"| {pid} | " + " | ".join(_cell(cats.get(k)) for k in EVALUATOR_CATEGORIES) + " |")
    lines += ["", "“未运行”= 该角色还没有登记 Prompt、缺录音或未立项，不记为通过；“—”= 这一项不适用；百分比只统计实际运行的检查。", ""]
    if c["reasons"]:
        lines += ["## 不通过或未运行的原因", ""] + [f"- {r}" for r in c["reasons"]] + [""]
    ch = rep["changed"]
    lines += ["## 本次变更", "",
              f"- 人设变更：{'是' if ch['persona_change'] else '否'}（{ch['source']}）",
              f"- 受影响的 Prompt：{', '.join(ch['prompts']) or '无'}",
              f"- 受影响的规格书：{', '.join(ch['specs']) or '无'}",
              f"- 受影响的角色：{', '.join(ch['personas']) or '无'}", ""]
    lines += ["## 家族级检查与漂移（当前）", "",
              "| 角色 | " + " | ".join(FAMILY_LABELS[k] for k in FAMILY_CHECKS) + " | 漂移 | 泄露 |",
              "|---|" + "---|" * (len(FAMILY_CHECKS) + 2)]
    for pid, cats in rep["results"].items():
        cols = [f"family_{k}" for k in FAMILY_CHECKS] + ["drift", "leak"]
        lines.append(f"| {pid} | " + " | ".join(_now(cats.get(k)) for k in cols) + " |")
    v = rep["versions"]
    rs = rep["recordings"]
    lines += ["", "## 版本与录音", "",
              f"- 登记表 {v['registry_version']}；禁语词表 {v['lexicon_version']}（{'已核对' if v['lexicon_reviewed'] else '未核对，待人设负责人与教研签字'}）；"
              f"guard {v['guard_version']}；用例 {v['cases_version']}",
              f"- 录音：有效 {rs['valid']}，过期 {rs['outdated']}，缺失 {rs['missing']}，不合法 {rs['invalid']}",
              f"- 判定来源：{', '.join(f'{k} {n}' for k, n in rep['method_share'].items())}",
              f"- 检查器自检（合成正例 / 反例）：{rep['selftest']['passed']}/{rep['selftest']['total']} 判对", ""]
    for r in rs["items"]:
        if r["status"] != "valid":
            lines.append(f"  - {r['persona_id']}/{r['case_id']}：{r['reason']}")
    if rep["forbidden_hits"]["items"]:
        lines += ["", "## 禁语命中（录制回放；只列编号与位置）", ""]
        lines += [f"- {h['persona_id']} {h['case_id']} 第 {h['turn']} 轮：{h['class']} {h['rule_id']} {h['span']}（{h['method']}）"
                  for h in rep["forbidden_hits"]["items"]]
    if rep["spec_conflicts"]:
        lines += ["", "## 规格冲突（只标注，不算失败；需规格负责人裁决，spec 待人确认 #9）", ""]
        lines += [f"- #{x['id']} {x.get('persona_id') or ''} {x['detail']}".replace("  ", " ") for x in rep["spec_conflicts"]]
    if rep["exemptions"]:
        lines += ["", "## 未登记 Prompt 扫描的豁免", ""] + [f"- {x}" for x in rep["exemptions"]]
    lines += ["", "报告只含合成用例的编号、规则 ID 与位置；不含回复原文与任何真实学生数据。"]
    return "\n".join(lines) + "\n"


def _now(row: dict[str, Any] | None) -> str:
    if not row or not row["total"]:
        return "—"
    if row["pass_rate"] is None:
        return "未运行"
    return f"{row['pass_rate']:.0%}（{row['passed']}/{row['passed'] + row['failed']}）"


def write(rep: dict[str, Any], out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "persona-report.json").write_text(json.dumps(rep, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (out_dir / "persona-report.md").write_text(to_markdown(rep), encoding="utf-8")


__all__ = ["CATEGORIES", "empty_row", "finalize_rows", "to_markdown", "validate", "write"]
