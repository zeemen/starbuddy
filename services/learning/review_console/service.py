"""审核台的组合逻辑：差异视图、签字前的风险摘要、版本记录。库访问在 store.py，纯函数在 diff.py / risk.py。"""
from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from . import pages
from .content import content_summary, diff_content, diff_scheme, policy_summary
from .diff import diff, touched_nodes
from .risk import classify, classify_change_type, summarize
from .snapshot import Snapshot, norm
from .spot_check import SPOT_CHECK_BASIS, SpotCheckSettings, sample
from .store import KgeStore

# 有知识地图骨架的学科（与迁移 0039 platform.content_requires_map 相同；发布时库为准，这里只用于提前提示）
MAP_SUBJECTS = ("aya", "zhixing")


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


def version_key(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in v.split("."))


def six_segment(v: dict[str, Any]) -> str:
    """六段标识（docs/design/kge/id-and-segments.md 第 3 节）：地区 / 届别 / 年级 / 教材 / 学科 / 内容版本号。"""
    return "/".join(str(v[k]) for k in ("region", "exam_year", "grade", "textbook_version", "subject", "content_version"))


def content_base(versions: list[dict[str, Any]], cv: dict[str, Any]) -> dict[str, Any] | None:
    """差异基准：同前五段、发布过（含之后撤下的）、版本号小于本版本的最大者（spec 第 7 节）；没有时为 None。"""
    same = [v for v in versions if v["id"] != cv["id"] and v["published_at"] is not None
            and all(v[k] == cv[k] for k in ("region", "exam_year", "grade", "textbook_version", "subject"))
            and version_key(v["content_version"]) < version_key(cv["content_version"])]
    return max(same, key=lambda v: version_key(v["content_version"]), default=None)


async def content_versions_view(kge: KgeStore, subject: str) -> dict[str, Any]:
    return norm({"items": [{**v, "six_segment": six_segment(v)} for v in await kge.content_versions(subject)]})


async def content_view(kge: KgeStore, cv_id: uuid.UUID) -> dict[str, Any]:
    """内容版本差异视图（spec 第 7 节）：与上一已发布版本比（能力视图、题目登记、准入）；签字进度与发布口径一致
    （最近一条审核结论、摘要是否与当前一致）；发布前的前提（所指地图版本已发布、（地区, 届别）有已发布考情方案）逐条列出。"""
    draft = await kge.content_snapshot(cv_id)
    v = draft["version"]
    versions = await kge.content_versions(v["subject"])
    me = next(x for x in versions if str(x["id"]) == str(cv_id))
    base_row = content_base(versions, me)
    base = await kge.content_snapshot(base_row["id"]) if base_row else None
    changes = diff_content(base, draft)
    summary = content_summary(changes)
    records = [r for r in await kge.release_records(v["subject"]) if str(r["curriculum_version_id"]) == str(cv_id)]
    reviews = [r for r in records if r["line"] == "content_version" and r["action"] == "review"]
    latest = max(reviews, key=lambda r: (r["decided_at"], str(r["id"])), default=None)
    summary.update(latest_decision=latest["decision"] if latest else None,
                   latest_matches=bool(latest and latest["digest"] == draft["digest"]),
                   ready=bool(latest and latest["decision"] == "approved" and latest["digest"] == draft["digest"]))
    checks = []
    map_brief = await kge.map_version_brief(uuid.UUID(v["map_version_id"])) if v.get("map_version_id") else None
    if v.get("map_version_id") is None:
        if v["subject"] in MAP_SUBJECTS:
            checks.append({"check_name": "map_version_missing", "severity": "error", "detail": "有知识地图骨架的学科发布时必须指定已发布的地图版本"})
    elif map_brief is None or map_brief["status"] != "published":
        checks.append({"check_name": "map_version_not_published", "severity": "error",
                       "detail": f"所指地图版本当前不是已发布（{map_brief['status'] if map_brief else 'missing'}）"})
    if not await kge.has_published_scheme(v["region"], int(v["exam_year"])):
        checks.append({"check_name": "no_published_scheme", "severity": "warning",
                       "detail": f"政策库里没有 {v['region']} {v['exam_year']} 届的已发布考情方案（只记警告，不阻断）"})
    # 本版本题目的准入审核与执行（准入变化）；待执行的批准审核（最近一条、未执行）供界面一键执行
    adm = [r for r in records if r["line"] == "admission"]
    applied = {str(r["applies_review_id"]) for r in adm if r["action"] == "apply"}
    latest_by_item: dict[str, dict[str, Any]] = {}
    for r in sorted((r for r in adm if r["action"] == "review"), key=lambda r: (r["decided_at"], str(r["id"]))):
        latest_by_item[r["item_id"]] = r
    pending = sorted((r for r in latest_by_item.values() if r["decision"] == "approved" and str(r["id"]) not in applied),
                     key=lambda r: r["item_id"])
    return norm({
        "version": {**v, "six_segment": six_segment(v)},
        "base_version": ({"id": base_row["id"], "content_version": base_row["content_version"], "status": base_row["status"]}
                         if base_row else None),
        "digest": draft["digest"],
        "map_version": map_brief,
        "summary": summary,
        "changes": changes,
        "checks": checks,
        "items": draft["items"],
        "records": records,
        "pending_admissions": pending,
    })


async def policy_list_view(kge: KgeStore, city: str | None) -> dict[str, Any]:
    return norm({"schemes": await kge.schemes(city), "drafts": await kge.policy_drafts(city)})


async def policy_view(kge: KgeStore, scheme_id: uuid.UUID) -> dict[str, Any]:
    """考情方案差异视图（spec 第 8 节）：与同城市同届别的上一版本、与上一届已发布方案比较（科目、满分、计分、合场、批次）；
    policy.validate_scheme 的结果逐条列出；签字进度（当前内容的有效批准人数）与所需人数。"""
    draft = await kge.scheme_snapshot(scheme_id)
    prev_id, last_id = await kge.scheme_bases(draft["scheme"])
    prev = await kge.scheme_snapshot(prev_id) if prev_id else None
    last = await kge.scheme_snapshot(last_id) if last_id else None
    vs_prev = diff_scheme(prev, draft)
    vs_last = diff_scheme(last, draft)
    summary = policy_summary(vs_prev)
    summary.update(approvals=draft["approvals"])
    records = [q for q in await kge.policy_records() if str(q["object_id"]) == str(scheme_id)]
    return norm({
        "scheme": draft["scheme"], "digest": draft["digest"], "total": draft["total"],
        "subjects": draft["subjects"], "batches": draft["batches"], "transitions": draft["transitions"],
        "previous_version": prev["scheme"] if prev else None, "previous_cohort": last["scheme"] if last else None,
        "diff_previous_version": vs_prev, "diff_previous_cohort": vs_last,
        "validation": await kge.validate_scheme(scheme_id),
        "summary": summary, "records": records,
    })


def _load_export(settings: Any):
    from .errors import ReviewError
    try:
        return pages.parse_export(pages.load_json(settings.export_path))
    except pages.ReconcileInputError:
        raise ReviewError(503, "PAGES_EXPORT_UNAVAILABLE", "合并记录导出读不了或格式不对") from None


def page_head(settings: Any, page_id: str, page_commit: str) -> pages.MergeRequest:
    """页面签字前核对：(页面, 头提交) 必须是托管平台导出里某个合并请求的头提交；返回该合并请求（风险、状态、作者）。
    风险取合并请求上的标注（由编译器按页面改动分级，classify_page 同口径），不由签字人自报。"""
    from .errors import ReviewError
    if settings is None:
        raise ReviewError(409, "RULE_VIOLATION", "审核台没有配置页面仓库的合并记录导出，不能签页面")
    export = _load_export(settings)
    for m in export.merge_requests:
        if m.head_commit == page_commit and page_id in m.pages:
            return m
    raise ReviewError(404, "NOT_FOUND", "托管平台导出里没有这个页面的这个头提交")


def check_not_author(settings: Any, mr: pages.MergeRequest, staff: Any) -> None:
    """提交方不能自签（spec 设计要点 3）：合并请求作者经 staff 对应表映射到 staff 账号，等于签字人 → 409 SELF_SIGN。
    判不出从严：导出里没有作者、或作者不在对应表里，都判不出签字人是不是作者 → 409 AUTHOR_UNKNOWN，不让签。"""
    from .errors import ReviewError
    if mr.author is None:
        raise ReviewError(409, "AUTHOR_UNKNOWN", "合并记录导出里没有这个合并请求的作者，判不出签字人是不是提交方，不能签")
    try:
        staff_map = pages.parse_staff_map(pages.load_json(settings.staff_map_path))
    except pages.ReconcileInputError:
        raise ReviewError(503, "PAGES_EXPORT_UNAVAILABLE", "staff 对应表读不了或格式不对") from None
    author_staff = staff_map.get(mr.author)
    if author_staff is None:
        raise ReviewError(409, "AUTHOR_UNKNOWN", "合并请求作者不在 staff 对应表里，判不出签字人是不是提交方，不能签")
    if author_staff == str(staff):
        raise ReviewError(409, "SELF_SIGN", "提交方不能给自己的合并请求签字或记抽检")


async def spot_check_view(kge: KgeStore, settings: Any, spot: SpotCheckSettings | None, day: dt.date) -> dict[str, Any]:
    """低风险合并窗口的事后抽检（spec 第 2 节）：当天（北京时间）低风险合并按固定种子抽样，附已有的抽检记录。
    种子必须由配置给出（仓库里没有缺省种子）：没配置时 503 SPOT_CHECK_UNCONFIGURED。比例为 0 时照常返回，带告警 ratio_zero。"""
    from .errors import ReviewError
    if spot is None:
        raise ReviewError(503, "SPOT_CHECK_UNCONFIGURED", "没有配置抽检种子（REVIEW_CONSOLE_SPOT_CHECK_SEED），不能抽检")
    base = {"ratio": spot.ratio, "window": spot.window, "day": day.isoformat(), "basis_code": SPOT_CHECK_BASIS,
            "warnings": ["ratio_zero"] if spot.ratio == 0 else []}
    if settings is None:
        return {"configured": False, **base}
    export = _load_export(settings)
    pool, picked = sample(export, day, spot)
    rows = await kge.page_records()
    checks: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for r in rows:
        if r["basis_code"] == SPOT_CHECK_BASIS:
            checks.setdefault((r["page_id"], r["page_commit"]), []).append({"decision": r["decision"], "signer": r["signer"],
                                                                             "decided_at": r["decided_at"]})
    return norm({"configured": True, **base, "candidates": len(pool),
                 "sampled": [{"page_id": c.page_id, "mr_id": c.mr_id, "head_commit": c.head_commit,
                              "url": pages.mr_url(settings.mr_url_template, c.mr_id),
                              "checks": checks.get((c.page_id, c.head_commit), [])} for c in picked]})


def spot_check_target(settings: Any, page_id: str, page_commit: str) -> pages.MergeRequest:
    """抽检签字前核对：只能对已合并的低风险页面改动记抽检（不能借抽检给中高风险页面补签）。"""
    from .errors import ReviewError
    mr = page_head(settings, page_id, page_commit)
    if mr.risk != "low" or mr.state != "merged":
        raise ReviewError(409, "RULE_VIOLATION", "只能对已合并的低风险页面改动记抽检结果")
    return mr


RECORD_COLUMNS = ("line", "map_version_id", "version", "action", "decision", "risk_level", "basis_code", "digest", "decided_at",
                  "signer", "rubric_versions", "validation_errors", "validation_warnings", "page_id", "page_commit",
                  "policy_object", "policy_object_id", "city", "cohort", "policy_version",
                  # 冲刺 4（PR D）：内容版本与题目准入行（line = content_version / admission）
                  # 准入的学科审核等级（Q0–Q5）：列名带 admission_ 前缀，免得与风险分级的 review_level（low/medium/high）混读
                  "content_version_id", "six_segment", "content_map_version_id", "item_id", "from_admission", "to_admission",
                  "admission_review_level")


async def version_record(kge: KgeStore, subject: str) -> list[dict[str, Any]]:
    """版本记录（spec 设计要点 11）：每行一次签字、发布或撤下；附该版本涉及的评分版本与当前校验计数。
    冲刺 3 起加页面行（line = page）：页面 ID、提交号、签字人、时间（每行一次页面签字，kge.page_review_record）。
    冲刺 4 起加考情行（line = policy）：从 policy 一侧合并（spec 第 8 节“版本记录导出时从两处合并”，platform.policy_signoff_record）；
    考情按城市与届别、不按学科，每个学科的导出都带全部考情行（跨学科汇总时须按 line = policy 去重）。
    考情行的 version 一律为空（version 只给地图版本号，免得与地图版本混读），方案版本号放在末尾的 policy_version；
    cohort 是届别（招录行是年份）；risk_level 固定为 high（考情政策线一律两名不同 staff，spec 第 1、2 节），不是逐行判出来的。
    冲刺 4（PR D）起加内容版本行（line = content_version：审核、发布、撤下）与准入行（line = admission：审核、执行），带六段标识、
    所指地图版本与 rubric_version 清单（spec 第 11 节）。行按时间排序，同一时刻地图行在前、页面行次之、考情行再次、内容版本与准入行最后。
    新列只追加在末尾，已有列顺序与含义不变。"""
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
    for q in await kge.policy_records():
        out.append((q["signed_at"], 2, {
            # 考情政策线一律按高风险、两名不同 staff（spec 第 1、2 节）：risk_level 固定为 high
            "line": "policy", "map_version_id": None, "version": None, "action": q["action"], "decision": q["decision"],
            "risk_level": "high", "basis_code": None, "digest": q["digest"], "decided_at": q["signed_at"], "signer": q["signer"],
            "rubric_versions": "", "validation_errors": None, "validation_warnings": None, "page_id": None, "page_commit": None,
            "policy_object": q["object_type"], "policy_object_id": q["object_id"], "city": q["city"], "cohort": q["cohort"],
            "policy_version": q["version"]}))
    # 冲刺 4（PR D）：内容版本与题目准入行，来自 platform.release_record_export（迁移 0042）。内容版本一律高风险（spec 第 1 节）；
    # map_version_id 与 version 两列仍只给地图行（不改已有列含义），所指地图版本放在 content_map_version_id；
    # rubric_versions：内容版本行取所指地图版本的评分版本清单，准入执行行取降级时受影响的清单（0039 affected_rubric_versions）
    rv_by_map: dict[Any, str] = {}
    for c in await kge.release_records(subject):
        mv = c["map_version_id"]
        if c["line"] == "content_version":
            if mv is not None and mv not in rv_by_map:
                rv_by_map[mv] = " ".join(facts[mv]["rubric_versions"] if mv in facts else (await kge.version_facts(mv))["rubric_versions"])
            rv = rv_by_map.get(mv, "") if mv is not None else ""
        else:
            rv = " ".join(c["affected_rubric_versions"] or [])
        out.append((c["decided_at"], 3, {
            "line": c["line"], "map_version_id": None, "version": None, "action": c["action"], "decision": c["decision"],
            "risk_level": "high", "basis_code": c["basis_code"], "digest": c["digest"], "decided_at": c["decided_at"],
            "signer": c["signer"], "rubric_versions": rv, "validation_errors": None, "validation_warnings": None,
            "content_version_id": c["curriculum_version_id"], "six_segment": six_segment({**c, "subject": subject}),
            "content_map_version_id": mv, "item_id": c["item_id"], "from_admission": c["from_admission"],
            "to_admission": c["to_admission"], "admission_review_level": c["review_level"]}))
    out.sort(key=lambda x: (x[0], x[1]))
    return [norm({c: row.get(c) for c in RECORD_COLUMNS}) for _, _, row in out]

