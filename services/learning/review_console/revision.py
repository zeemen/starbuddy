"""修订发布预检（specs/kge06-weekly-revision 范围 2）：纯函数，只给建议，签字发布仍走 kge.publish_map_version 与现有流程。

依据：KGE V1.1 :246（修订按周、次版本按月、主版本按学期）、:523（候选 → 审核台 → 结构性两人签 → 影响分析 → 发布）。
口径与发布函数（迁移 0019 kge.publish_map_version）一致：
- 结构性 = kge.structural_diff（与当前已发布版本相比，有效节点或有效前置 / 从属边有增删）或有未驳回的结构性变更单
  （由 service.diff_view 算好传进来，这里不重判）；
- 版本号必须大于同学科已发布或已撤下的最大版本；含结构性变更时次版本号或主版本号必须增加；
- 结构性两名不同 staff 对当前内容批准，其余一名。
建议的版本号段：结构性 → 次版本（minor，主.次+1.0）；非结构性 → 修订号（patch，主.次.修订+1）；主版本只由人按学期定，不建议。
没有已发布或已撤下的版本（首次发布）时整张图都算新增（kge.structural_diff 为真），版本号不比较。
"""
from __future__ import annotations

import re
from typing import Any

VERSION = re.compile(r"^(\d{1,3})\.(\d{1,3})\.(\d{1,4})$")  # 与知识服务契约 MapVersion 一致
NOTE = "只给建议：是否结构性、升哪一段、要几人签以发布函数为准；签字与发布仍走现有流程"

# 问题（发布函数会拒绝的）与提示（发布函数允许，但不合修订节奏）
PROBLEMS = {
    "not_draft": "只能发布草稿版本",
    "version_format": "版本号不是 主.次.修订 三段数字",
    "not_greater": "版本号必须大于同学科已发布或已撤下的最大版本",
    "structural_needs_minor": "含结构性变更时次版本号或主版本号必须增加",
}
ADVICE = {
    "minor_not_required": "没有结构性变更：按周修订只需升修订号，升次版本号不是必须的",
    "major_bump": "主版本号增加：主版本按学期，由人决定",
    "skips_versions": "跳过了中间的版本号（比建议的版本号大）",
}


def parse_version(v: Any) -> tuple[int, int, int] | None:
    m = VERSION.fullmatch(v) if isinstance(v, str) else None
    return (int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else None


def fmt(v: tuple[int, int, int]) -> str:
    return ".".join(str(x) for x in v)


def structural_reasons(changes: list[dict[str, Any]], change_requests: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """结构性的依据：逐条差异里标了 structural 的（与 kge.structural_diff 同口径），以及未驳回的结构性变更单。只含公共层的键。"""
    out = [{"source": "diff", "object": c["object"], "key": c["key"], "op": c["op"], "reason": c["risk"]["reason"]}
           for c in changes if c.get("risk", {}).get("structural")]
    out += [{"source": "change_request", "object": "map_change", "key": str(r["id"]), "op": r["change_type"],
             "reason": r["risk"]["reason"]}
            for r in change_requests if r.get("risk", {}).get("structural") and r.get("status") != "rejected"]
    return out


def preflight(*, status: str, draft_version: str, existing_versions: list[str], structural: bool) -> dict[str, Any]:
    """status：草稿版本当前状态；existing_versions：同学科其他已发布或已撤下的版本号；structural：以库为准的结构性判定。"""
    required = 2 if structural else 1
    problems: list[str] = []
    advice: list[str] = []
    if status != "draft":
        problems.append("not_draft")
    d = parse_version(draft_version)
    parsed = [p for p in (parse_version(v) for v in existing_versions) if p is not None]
    latest = max(parsed) if parsed else None
    if latest is None:
        bump, suggested = "initial", None
    elif structural:
        bump, suggested = "minor", (latest[0], latest[1] + 1, 0)
    else:
        bump, suggested = "patch", (latest[0], latest[1], latest[2] + 1)
    if d is None:
        problems.append("version_format")
    elif latest is not None:
        if d <= latest:
            problems.append("not_greater")
        if structural and d[:2] == latest[:2]:
            problems.append("structural_needs_minor")
        if not problems:
            if d[0] > latest[0]:
                advice.append("major_bump")
            elif not structural and d[1] > latest[1]:
                advice.append("minor_not_required")
            if suggested is not None and d > suggested and d[0] == latest[0] and (structural or d[1] == latest[1]):
                advice.append("skips_versions")
    return {
        "structural": structural,
        "required_signers": required,
        "bump": bump,
        "latest_version": fmt(latest) if latest else None,
        "suggested_version": fmt(suggested) if suggested else None,
        "draft_version": draft_version,
        "version_ok": not problems,
        "problems": [{"code": p, "detail": PROBLEMS[p]} for p in problems],
        "advice": [{"code": a, "detail": ADVICE[a]} for a in advice],
        "note": NOTE,
    }
