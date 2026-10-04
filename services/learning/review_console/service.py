"""审核台的组合逻辑：差异视图、签字前的风险摘要、版本记录。库访问在 store.py，纯函数在 diff.py / risk.py。"""
from __future__ import annotations

import uuid
from typing import Any

from . import pages
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
    # 签字进度与发布函数同一口径（契约审查 PR 3 S1）：最近一条审核结论；最近一次驳回之后、对当前摘要批准的不同签字人
    signoffs = await kge.records(v["subject"], version_id)
    reviews = [s for s in signoffs if s["action"] == "review"]
    last_reject = max((s["decided_at"] for s in reviews if s["decision"] == "rejected"), default=None)
    approvers = {s["signer"] for s in reviews if s["decision"] == "approved" and s["digest"] == draft.digest
                 and (last_reject is None or s["decided_at"] > last_reject)}
    latest = max(reviews, key=lambda s: s["decided_at"], default=None)
    summary.update(approvals=len(approvers), latest_decision=latest["decision"] if latest else None,
                   latest_matches=bool(latest and latest["digest"] == draft.digest))
    current, removed = touched_nodes(changes)
    impact = await kge.impact(version_id, current)
    if base_id and removed:
        gone = await kge.impact(base_id, removed)
        impact = {k: impact[k] + gone[k] for k in impact}
    return norm({
        "version": v,
        "base_version_id": base_id,
        "base_version": (await kge.version(base_id))["version"] if base_id else None,
        "digest": draft.digest,
        # 草稿的全部节点（改节点表单用；公共层内容）
        "nodes": [{"id": k, **v} for k, v in sorted(draft.nodes.items())],
        "summary": summary,
        "impact": impact,
        "changes": changes,
        "validation": issues,
        "unattached_issues": loose,
        "change_requests": [{**r, "risk": classify_change_type(r["change_type"]).as_dict()} for r in requests],
        "signoffs": signoffs,
    })


async def pages_view(kge: KgeStore, settings: Any) -> dict[str, Any]:
    """审核台“页面”入口（冲刺 3）：待审合并请求与已合并页面的对账状态，链接到托管平台网页（地址模板由配置给出）。
    只读：合并记录与对应表来自配置的文件（本服务不连托管平台），审核记录来自 kge.page_review_record。
    settings 为空（未配置）时只回 configured = false。文件读不了或格式不对回 503，不回显内容。"""
    if settings is None:
        return {"configured": False}
    from .errors import ReviewError
    try:
        export = pages.parse_export(pages.load_json(settings.export_path))
        staff_map = pages.parse_staff_map(pages.load_json(settings.staff_map_path))
        records = pages.records_from_rows(await kge.page_records())
    except pages.ReconcileInputError:
        raise ReviewError(503, "PAGES_EXPORT_UNAVAILABLE", "合并记录导出或 staff 对应表读不了或格式不对") from None
    report = pages.reconcile(export, records, staff_map)
    states = {m.mr_id: m.state for m in export.merge_requests}
    for e in report["pending"] + report["merged"]:
        e["url"] = pages.mr_url(settings.mr_url_template, e["mr_id"])
        e["state"] = states[e["mr_id"]]
    return {"configured": True, **report}


RECORD_COLUMNS = ("line", "map_version_id", "version", "action", "decision", "risk_level", "basis_code", "digest", "decided_at",
                  "signer", "rubric_versions", "validation_errors", "validation_warnings", "page_id", "page_commit")


async def version_record(kge: KgeStore, subject: str) -> list[dict[str, Any]]:
    """版本记录（spec 设计要点 11）：每行一次签字、发布或撤下；附该版本涉及的评分版本与当前校验计数。
    冲刺 3 起加页面行（line = page）：页面 ID、提交号、签字人、时间（每行一次页面签字，kge.page_review_record）。
    六段标识（冲刺 4）到时补列。行按时间排序，同一时刻地图行在前。"""
    rows = await kge.records(subject)
    facts: dict[Any, dict[str, Any]] = {}
    for r in rows:
        if r["map_version_id"] not in facts:
            facts[r["map_version_id"]] = await kge.version_facts(r["map_version_id"])
    out = []
    for r in rows:
        f = facts[r["map_version_id"]]
        out.append((r["decided_at"], 0, {"line": "map", **r, **f, "rubric_versions": " ".join(f["rubric_versions"])}))
    for p in await kge.page_records(subject):  # 库里按页面 ID 的学科前缀过滤（kge.subject_prefix）
        out.append((p["decided_at"], 1, {
            "line": "page", "map_version_id": None, "version": None, "action": "review", "decision": p["decision"],
            "risk_level": p["risk_level"], "basis_code": p["basis_code"], "digest": p["digest"], "decided_at": p["decided_at"],
            "signer": p["signer"], "rubric_versions": "", "validation_errors": None, "validation_warnings": None,
            "page_id": p["page_id"], "page_commit": p["page_commit"]}))
    out.sort(key=lambda x: (x[0], x[1]))
    return [norm({c: row.get(c) for c in RECORD_COLUMNS}) for _, _, row in out]

