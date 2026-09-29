"""审核台的组合逻辑：差异视图、签字前的风险摘要、版本记录。库访问在 store.py，纯函数在 diff.py / risk.py。"""
from __future__ import annotations

import uuid
from typing import Any

from .diff import diff, touched_nodes
from .risk import classify, classify_change_type, summarize
from .snapshot import Snapshot, norm
from .store import KgeStore


def attach_issues(changes: list[dict[str, Any]], issues: list[dict[str, Any]], draft: Snapshot) -> list[dict[str, Any]]:
    """把校验问题挂到对应的差异条目上（审查 S6）；挂不上的（对象没有变化，或写法认不出）原样返回，界面单列。"""
    index = {(c["object"], c["key"]): c for c in changes}
    for c in changes:
        c["issues"] = []
    loose = []
    for i in issues:
        hit = [index[t] for t in draft.refs.get(i["object_id"], []) if t in index]
        for c in hit:
            c["issues"].append(i)
        if not hit:
            loose.append(i)
    return loose


async def diff_view(kge: KgeStore, version_id: uuid.UUID) -> dict[str, Any]:
    v = await kge.version(version_id)
    base_id = await kge.published_base(v["subject"], version_id)
    draft = await kge.snapshot(version_id)
    base = await kge.snapshot(base_id) if base_id else None
    changes = [{**c, "risk": classify(c).as_dict()} for c in diff(base, draft)]
    for c in changes:
        if c["object"] == "scoring_point":
            c["rubric_version"] = c["key"].split("|", 1)[0]  # 采分点按评分版本分组显示
    issues = await kge.validate(version_id)
    loose = attach_issues(changes, issues, draft)
    requests = await kge.change_requests(version_id)
    summary = summarize(changes)
    # 是否结构性、要几人签，以库为准：kge.structural_diff，或有未驳回的结构性变更单（批准后发布时库要求两人，契约审查 S1）
    structural = await kge.structural(version_id) or any(
        classify_change_type(r["change_type"]).structural and r["status"] != "rejected" for r in requests)
    summary.update(structural=structural, required_signers=2 if structural else 1)
    current, removed = touched_nodes(changes)
    impact = await kge.impact(version_id, current)
    if base_id and removed:
        gone = await kge.impact(base_id, removed)
        impact = {k: impact[k] + gone[k] for k in impact}
    return norm({
        "version": v,
        "base_version_id": base_id,
        "digest": draft.digest,
        "summary": summary,
        "impact": impact,
        "changes": changes,
        "validation": issues,
        "unattached_issues": loose,
        "change_requests": [{**r, "risk": classify_change_type(r["change_type"]).as_dict()} for r in requests],
        "signoffs": await kge.records(v["subject"], version_id),
    })


RECORD_COLUMNS = ("line", "map_version_id", "version", "action", "decision", "risk_level", "basis_code", "digest", "decided_at",
                  "signer", "rubric_versions", "validation_errors", "validation_warnings")


async def version_record(kge: KgeStore, subject: str) -> list[dict[str, Any]]:
    """版本记录（spec 设计要点 11）：每行一次签字、发布或撤下；附该版本涉及的评分版本与当前校验计数。
    六段标识（冲刺 4）与页面提交号（冲刺 3）到时补列。"""
    rows = await kge.records(subject)
    facts: dict[Any, dict[str, Any]] = {}
    for r in rows:
        if r["map_version_id"] not in facts:
            facts[r["map_version_id"]] = await kge.version_facts(r["map_version_id"])
    out = []
    for r in rows:
        f = facts[r["map_version_id"]]
        out.append(norm({"line": "map", **r, **f, "rubric_versions": " ".join(f["rubric_versions"])}))
    return out

