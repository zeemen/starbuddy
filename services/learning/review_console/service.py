"""审核台的组合逻辑：差异视图、签字前的风险摘要、版本记录。库访问在 store.py，纯函数在 diff.py / risk.py。"""
from __future__ import annotations

import uuid
from typing import Any

from .diff import diff, touched_nodes
from .risk import classify, classify_change_type, summarize
from .snapshot import norm
from .store import KgeStore


def _issues_for(key: str, obj: str, issues: list[dict[str, Any]]) -> list[dict[str, Any]]:
    node = key.split(" ", 1)[0].split("|", 1)[0] if obj in ("node", "edge", "textbook", "region", "skill_link") else None
    return [i for i in issues if node is not None and i["object_id"] == node]


async def diff_view(kge: KgeStore, version_id: uuid.UUID) -> dict[str, Any]:
    v = await kge.version(version_id)
    base_id = await kge.published_base(v["subject"], version_id)
    draft = await kge.snapshot(version_id)
    base = await kge.snapshot(base_id) if base_id else None
    changes = diff(base, draft)
    issues = await kge.validate(version_id)
    requests = await kge.change_requests(version_id)
    summary = summarize(changes)
    # 所需签字人数与库一致：按差异判定为结构性，或申报了结构性变更单（kge.publish_map_version 第 3 步）
    if any(classify_change_type(r["change_type"]).structural and r["status"] == "approved" for r in requests):
        summary = {**summary, "structural": True, "required_signers": 2}
    items = []
    for c in changes:
        items.append({**c, "risk": classify(c).as_dict(), "issues": _issues_for(c["key"], c["object"], issues)})
    return norm({
        "version": v,
        "base_version_id": base_id,
        "digest": draft.digest,
        "summary": summary,
        "impact": await kge.impact(version_id, touched_nodes(changes)),
        "changes": items,
        "validation": issues,
        "change_requests": [{**r, "risk": classify_change_type(r["change_type"]).as_dict()} for r in requests],
        "signoffs": await kge.records(v["subject"], version_id),
    })
