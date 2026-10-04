"""审核台冲刺 4（G 流第二部分 PR D）的纯函数单测：风险分类器补全（页面、原子、考情、内容版本）、内容版本与考情方案差异、
事后抽检的固定种子抽样、对账对抽检记录的处理。合成数据（规则 8）。"""
from __future__ import annotations

import ast
import datetime as dt
import json
import re
from decimal import Decimal
from pathlib import Path

import pytest

from review_console import pages
from review_console.content import content_summary, diff_content, diff_scheme, policy_summary
from review_console.diff import canonical_json
from review_console.risk import (ATOM_RISK, PAGE_SECTIONS, POLICY_SIGNERS, classify_atom, classify_content, classify_page,
                                 classify_policy)
from review_console.spot_check import SpotCheckSettings, candidates, sample, sample_size

ROOT = Path(__file__).resolve().parents[3]
FIX = Path(__file__).parent / "fixtures" / "pages-reconcile"


# ---------- 页面风险（KGE 第四章表；spec 第 2、10 节） ----------
C = {"source": "compiler"}  # 编译器给出的完整描述至少带来源或重建类型之一；两者都缺按高（判不出从严）


@pytest.mark.parametrize("change,level,reason", [
    ({**C, "sections": ["definition"]}, "high", "definition_curriculum"),
    ({**C, "sections": ["curriculum"]}, "high", "definition_curriculum"),
    ({**C, "sections": ["relations"], "prerequisite_changed": True}, "high", "prerequisite_statement"),
    ({**C, "sections": ["relations"]}, "high", "prerequisite_statement"),  # 不知道前置变没变：按变了
    ({**C, "sections": ["relations"], "prerequisite_changed": False}, "low", "render_segment"),
    ({**C, "sections": ["textbook"]}, "high", "textbook_quote_page"),
    ({**C, "sections": [], "projection_quote_changed": True}, "high", "textbook_quote_page"),
    ({**C, "sections": ["key_points"]}, "medium", "narrative_rewrite"),
    ({**C, "sections": ["pitfalls"]}, "medium", "narrative_rewrite"),
    ({**C, "sections": ["region"]}, "low", "exam_stats_update"),
    ({"rebuild_kind": "render", "sections": ["region"]}, "low", "exam_stats_update"),  # 只给重建类型也算给了
    ({**C, "sections": [], "atom_hash_only": True}, "low", "atom_hash_only"),
    ({**C, "sections": ["region", "key_points"]}, "medium", "narrative_rewrite"),
    ({**C, "sections": ["region", "definition", "key_points"]}, "high", "definition_curriculum"),
    ({"sections": ["region"], "rebuild_kind": "full"}, "high", "full_rebuild"),
    ({"sections": ["region"], "source": "model_draft"}, "medium", "model_draft"),
    ({"sections": ["key_points"], "source": "model_draft"}, "medium", "narrative_rewrite"),
    ({**C, "sections": ["summary"]}, "high", "unclassified"),      # 认不出的段落
    ({"sections": ["region"], "source": "llm"}, "high", "unclassified"),
    ({"sections": ["region"], "rebuild_kind": "partial"}, "high", "unclassified"),
    ({**C, "sections": []}, "high", "unclassified"),                # 什么都没变也判不出
    ({"sections": ["region"]}, "high", "unclassified"),             # 来源与重建类型都缺：不按缺省值猜成低风险
    ({"sections": [], "atom_hash_only": True}, "high", "unclassified"),
    ({}, "high", "unclassified"),
])
def test_classify_page(change, level, reason):
    r = classify_page(change)
    assert (r.level, r.reason) == (level, reason)
    assert r.as_dict()["review_level"] == ("high" if level == "medium" else level)  # 阶段 0 中风险按高审（spec 待人确认 #7）


def test_page_sections_match_knowledge_format():
    """页面段落与类型和编译器的知识点页模板一致（services/knowledge/knowledge/pages/format.py 的 KP_SECTIONS，两处各写一份，这里核对）。"""
    src = (ROOT / "services/knowledge/knowledge/pages/format.py").read_text(encoding="utf-8")
    found = dict(re.findall(r'SectionSpec\("([a-z_]+)", "(locked|render|narrative)"', src))
    assert found == PAGE_SECTIONS


def test_classify_atom():
    assert (classify_atom("scoring_point").level, classify_atom("item_family").level, classify_atom("current_affair").level) == \
        ("high", "low", "medium")
    assert classify_atom("item_tag").level == "high" and classify_atom("unknown_atom").reason == "unclassified"
    assert set(ATOM_RISK) == {"scoring_point", "item_family", "current_affair", "item_tag"}


def test_policy_signers_match_migration():
    """考情所需人数与迁移 0041 的 policy.signers_required() 常量相等（库为准）。"""
    src = (ROOT / "db/migrations/0041_policy__signoff.up.sql").read_text(encoding="utf-8")
    m = re.search(r"FUNCTION policy\.signers_required\(\).*?SELECT (\d+) \$\$", src, re.S)
    assert m and int(m.group(1)) == POLICY_SIGNERS == 2


@pytest.mark.parametrize("change,reason", [
    ({"object": "subject", "op": "added"}, "policy_subjects"),
    ({"object": "subject", "op": "removed"}, "policy_subjects"),
    ({"object": "subject", "op": "changed", "field": "full_score"}, "policy_full_score"),
    ({"object": "subject", "op": "changed", "field": "scored"}, "policy_scoring"),
    ({"object": "subject", "op": "changed", "field": "combined_group"}, "policy_combined"),
    ({"object": "batch", "op": "added"}, "policy_batches"),
    ({"object": "batch", "op": "changed", "field": "batch_order"}, "policy_batches"),
    ({"object": "total", "op": "changed", "field": "total"}, "policy_full_score"),
    ({"object": "scheme", "op": "changed", "field": "source_document_id"}, "policy_source"),
    ({"object": "subject", "op": "changed", "field": "weight"}, "unclassified"),
    ({"object": "school", "op": "added"}, "unclassified"),
])
def test_classify_policy_always_high(change, reason):
    r = classify_policy(change)
    assert r.level == "high" and r.reason == reason


@pytest.mark.parametrize("change,reason", [
    ({"object": "version", "op": "added"}, "content_six_segment"),
    ({"object": "version", "op": "changed", "field": "content_version"}, "content_six_segment"),
    ({"object": "version", "op": "changed", "field": "map_version_id"}, "content_map_version"),
    ({"object": "skill_node", "op": "added"}, "skill_view_registry"),
    ({"object": "item", "op": "added"}, "item_registry"),
    ({"object": "item", "op": "changed", "field": "admission"}, "admission_change"),
    ({"object": "item", "op": "changed", "field": "license_status"}, "item_source"),
    ({"object": "item", "op": "changed", "field": "admitted_at"}, "unclassified"),
    ({"object": "version", "op": "changed", "field": "status"}, "unclassified"),
])
def test_classify_content_always_high(change, reason):
    r = classify_content(change)
    assert r.level == "high" and r.reason == reason


# ---------- 内容版本差异 ----------
def snap(version: str, nodes: list[tuple[str, str]], items: list[tuple[str, str, str]], map_id: str | None = None) -> dict:
    return {"version": {"region": "synth-rcr", "exam_year": 2031, "grade": 8, "textbook_version": "syn-rcr", "subject": "wenli",
                        "content_version": version, "map_version_id": map_id, "status": "draft"},
            "skill_nodes": [{"local_id": n, "parent_local_id": None, "label_code": lc} for n, lc in nodes],
            "items": [{"item_id": i, "skill_local_id": s, "admission": a, "source_ref": None, "source_type": "unknown",
                       "license_status": "unverified"} for i, s, a in items]}


def test_content_diff_first_version_all_added_and_deterministic():
    draft = snap("1.0.0", [("SYN-A", "syn-a")], [("SYN-I1", "SYN-A", "pending_review")])
    out = diff_content(None, draft)
    assert [(c["object"], c["key"], c["op"]) for c in out] == [("version", "version", "added"), ("skill_node", "SYN-A", "added"),
                                                              ("item", "SYN-I1", "added")]
    assert all(c["risk"]["level"] == "high" for c in out)
    assert canonical_json(diff_content(None, draft)) == canonical_json(out)


def test_content_diff_against_base():
    base = snap("1.0.0", [("SYN-A", "syn-a")], [("SYN-I1", "SYN-A", "admitted")], map_id="m1")
    draft = snap("1.1.0", [("SYN-A", "syn-a2"), ("SYN-B", "syn-b")], [("SYN-I1", "SYN-A", "rejected"), ("SYN-I2", "SYN-B", "pending_review")],
                 map_id="m2")
    out = diff_content(base, draft)
    keyed = {(c["object"], c["key"], c["field"], c["op"]): c for c in out}
    assert keyed[("version", "version", "content_version", "changed")]["new"] == "1.1.0"
    assert keyed[("version", "version", "map_version_id", "changed")]["risk"]["reason"] == "content_map_version"
    assert keyed[("skill_node", "SYN-A", "label_code", "changed")]["old"] == "syn-a"
    assert ("skill_node", "SYN-B", None, "added") in keyed
    adm = keyed[("item", "SYN-I1", "admission", "changed")]
    assert (adm["old"], adm["new"], adm["risk"]["reason"]) == ("admitted", "rejected", "admission_change")
    s = content_summary(out)
    assert s["required_signers"] == 1 and s["level"] == "high" and s["admission_changes"] == 1
    assert s["counts"] == {"version": 2, "skill_node": 2, "item": 2}


# ---------- 考情方案差异（合成城市 synthcity） ----------
def scheme(version: str, cohort: int, subjects: list[dict], batches: list[tuple[str, int]], total) -> dict:
    return {"scheme": {"id": f"s-{version}-{cohort}", "city": "synthcity", "cohort": cohort, "version": version, "source_document_id": "d1"},
            "subjects": subjects, "batches": [{"batch_code": b, "track": "unified", "batch_order": o, "control_line_basis": "none"}
                                              for b, o in batches],
            "transitions": [], "total": total}


def subj(code: str, full, scored=True, group=None) -> dict:
    return {"subject": code, "scored": scored, "full_score": full, "proposition": "city", "exam_form": "closed_book",
            "duration_minutes": None, "combined_group": group, "taken_in_grade": 9, "exam_month": 6, "eligibility_effect": "none"}


def test_scheme_diff_subjects_scores_combined_batches():
    prev = scheme("1.0.0", 2030, [subj("chinese", Decimal("120.0")), subj("politics", Decimal("60"), group="g1"),
                                  subj("history", Decimal("60"), group="g1")], [("batch_1", 1)], Decimal("240.0"))
    draft = scheme("1.1.0", 2030, [subj("chinese", Decimal("130")), subj("politics", Decimal("60")), subj("history", Decimal("60")),
                                   subj("pe", None, scored=False)], [("batch_1", 1), ("batch_2", 2)], Decimal("250"))
    out = diff_scheme(prev, draft)
    got = {(c["object"], c["key"], c["field"], c["op"]) for c in out}
    assert ("subject", "chinese", "full_score", "changed") in got
    assert ("subject", "politics", "combined_group", "changed") in got and ("subject", "history", "combined_group", "changed") in got
    assert ("subject", "pe", None, "added") in got and ("batch", "batch_2|unified", None, "added") in got
    assert ("total", "total", "total", "changed") in got and ("scheme", "scheme", "version", "changed") in got
    full = next(c for c in out if c["field"] == "full_score")
    assert (full["old"], full["new"]) == ("120", "130")  # Decimal 统一成字符串，120.0 与 120 不算变化
    assert all(c["risk"]["level"] == "high" for c in out)
    assert policy_summary(out)["required_signers"] == 2
    # 同一输入两次输出逐字节相同；120.0 与 120 不产生假差异
    assert canonical_json(diff_scheme(prev, draft)) == canonical_json(out)
    same = scheme("1.0.0", 2030, [subj("chinese", Decimal("120"))], [], Decimal("120"))
    same2 = scheme("1.0.0", 2030, [subj("chinese", Decimal("120.0"))], [], Decimal("120.00"))
    assert diff_scheme(same, same2) == []
    assert {c["op"] for c in diff_scheme(None, draft)} == {"added"}


# ---------- 事后抽检：固定种子抽样 ----------
def export_with_lows(n: int, day: str = "2026-10-02") -> pages.MergeExport:
    mrs = [{"mr_id": str(200 + i), "state": "merged", "risk": "low", "approvers": [], "pages": [f"MATH.SYN.T.L{i:02d}"],
            "head_commit": f"{i:02x}" * 20, "merge_commit": f"{(i + 100) % 256:02x}" * 20, "merged_at": f"{day}T03:00:00+08:00"}
           for i in range(n)]
    mrs.append({"mr_id": "999", "state": "merged", "risk": "high", "approvers": ["syn-teacher-1", "syn-teacher-2"], "pages": ["MATH.SYN.T.H"],
                "head_commit": "e" * 40, "merge_commit": "f" * 40, "merged_at": f"{day}T03:00:00+08:00"})
    return pages.parse_export({"format": pages.EXPORT_FORMAT, "repository": "synthetic/kge-pages", "branch": "main",
                               "exported_at": "2026-10-03T08:00:00+08:00", "merge_requests": mrs})


def test_spot_check_sampling_fixed_seed():
    exp = export_with_lows(20)
    day = dt.date(2026, 10, 2)
    s = SpotCheckSettings(ratio=0.1, seed="syn-seed")
    pool, picked = sample(exp, day, s)
    assert len(pool) == 20 and len(picked) == 2                        # ceil(20 × 0.1)
    assert all(c.page_id != "MATH.SYN.T.H" for c in pool)              # 只抽低风险
    assert sample(exp, day, s) == (pool, picked)                        # 同样输入与配置，结果相同
    other = sample(exp, day, SpotCheckSettings(ratio=0.1, seed="syn-seed-2"))[1]
    many = {tuple(c.page_id for c in sample(exp, day, SpotCheckSettings(ratio=0.1, seed=f"s{i}"))[1]) for i in range(20)}
    assert len(many) > 1 and isinstance(other, list)                    # 换种子结果会变（不是固定取前几个）
    assert sample(exp, dt.date(2026, 10, 3), s) == ([], [])             # 按北京时间换日
    assert sample_size(0, 0.5) == 0 and sample_size(3, 0) == 0 and sample_size(3, 0.01) == 1 and sample_size(3, 1.0) == 3
    # 跨午夜：UTC 前一天 18:00 = 北京时间当天 02:00
    late = pages.parse_export({"format": pages.EXPORT_FORMAT, "repository": "synthetic/kge-pages", "branch": "main",
                               "exported_at": "2026-10-03T08:00:00+08:00", "merge_requests": [
                                   {"mr_id": "300", "state": "merged", "risk": "low", "approvers": [], "pages": ["MATH.SYN.T.N"],
                                    "head_commit": "9" * 40, "merge_commit": "8" * 40, "merged_at": "2026-10-01T18:00:00Z"}]})
    assert [c.page_id for c in candidates(late, day)] == ["MATH.SYN.T.N"]


def _reheaded(exp: pages.MergeExport, head=None, merge=None) -> pages.MergeExport:
    """同一份导出，只换低风险合并请求的头提交或合并提交（模拟提交方重推头提交、或合并后才确定的合并提交不同）。"""
    import dataclasses
    mrs = tuple(dataclasses.replace(m, head_commit=head(m) if head else m.head_commit, merge_commit=merge(m) if merge else m.merge_commit)
                if m.risk == "low" else m for m in exp.merge_requests)
    return dataclasses.replace(exp, merge_requests=mrs)


def test_spot_check_rank_uses_merge_commit_not_head_commit():
    """抽样哈希用合并提交（合并后才确定），不用头提交：提交方反复改头提交挑不出“抽不中”的组合（契约审查）。"""
    exp = export_with_lows(20)
    day = dt.date(2026, 10, 2)
    s = SpotCheckSettings(ratio=0.2, seed="syn-seed")
    picked = [c.page_id for c in sample(exp, day, s)[1]]
    for k in range(5):  # 换头提交：抽中的页面不变
        moved = _reheaded(exp, head=lambda m, k=k: f"{(int(m.mr_id) + k * 7) % 256:02x}" * 20)
        assert [c.page_id for c in sample(moved, day, s)[1]] == picked
    variants = {tuple(c.page_id for c in sample(_reheaded(exp, merge=lambda m, k=k: f"{(int(m.mr_id) * 3 + k) % 256:02x}" * 20), day, s)[1])
                for k in range(10)}
    assert len(variants) > 1  # 合并提交不同，抽中的会变
    assert all(c.merge_commit for c in sample(exp, day, s)[0])


@pytest.mark.parametrize("env,ok", [
    ({"REVIEW_CONSOLE_SPOT_CHECK_SEED": "syn-seed"}, True),
    ({"REVIEW_CONSOLE_SPOT_CHECK_SEED": "syn-seed", "REVIEW_CONSOLE_SPOT_CHECK_RATIO": "0.25"}, True),
    ({"REVIEW_CONSOLE_SPOT_CHECK_SEED": "syn-seed", "REVIEW_CONSOLE_SPOT_CHECK_RATIO": "0"}, True),
    ({"REVIEW_CONSOLE_SPOT_CHECK_SEED": "syn-seed", "REVIEW_CONSOLE_SPOT_CHECK_RATIO": "1.5"}, False),
    ({"REVIEW_CONSOLE_SPOT_CHECK_SEED": "syn-seed", "REVIEW_CONSOLE_SPOT_CHECK_RATIO": "nan"}, False),
    ({"REVIEW_CONSOLE_SPOT_CHECK_SEED": "syn-seed", "REVIEW_CONSOLE_SPOT_CHECK_RATIO": "abc"}, False),
    ({"REVIEW_CONSOLE_SPOT_CHECK_RATIO": "1.5"}, False),  # 没设种子也照样核对比例
    ({"REVIEW_CONSOLE_SPOT_CHECK_SEED": "bad seed!"}, False),
    ({"REVIEW_CONSOLE_SPOT_CHECK_SEED": "syn-seed", "REVIEW_CONSOLE_MERGE_WINDOW": "25:00"}, False),
    ({"REVIEW_CONSOLE_SPOT_CHECK_SEED": "syn-seed", "REVIEW_CONSOLE_MERGE_WINDOW": "03:30"}, True),
])
def test_spot_check_settings(env, ok):
    if ok:
        s = SpotCheckSettings.from_env(env)
        assert s is not None and 0 <= s.ratio <= 1 and s.seed == "syn-seed"
    else:
        with pytest.raises(RuntimeError):
            SpotCheckSettings.from_env(env)


def test_spot_check_seed_must_be_configured():
    """种子必须由配置给出，仓库里没有缺省种子：没设种子时配置为空（抽检接口报错）；直接构造必须给种子。"""
    import review_console.spot_check as m
    assert "待教研负责人定" in (m.__doc__ or "")
    assert not hasattr(m, "DEFAULT_SEED")
    assert SpotCheckSettings.from_env({}) is None
    assert SpotCheckSettings.from_env({"REVIEW_CONSOLE_SPOT_CHECK_RATIO": "0.3"}) is None
    with pytest.raises(TypeError):
        SpotCheckSettings()  # type: ignore[call-arg]
    with pytest.raises(RuntimeError):
        SpotCheckSettings(seed="")
    from review_console.config import ReviewSettings
    env = {"REVIEW_CONSOLE_ENV": "test", "REVIEW_CONSOLE_KGE_DATABASE_URL": "postgresql://a@/x", "REVIEW_CONSOLE_REVIEW_DATABASE_URL": "postgresql://b@/x"}
    assert ReviewSettings.from_env(env).spot_check is None
    assert ReviewSettings.from_env({**env, "REVIEW_CONSOLE_SPOT_CHECK_SEED": "syn-seed"}).spot_check.seed == "syn-seed"


async def test_spot_check_view_unconfigured_and_ratio_zero():
    """未配置种子：接口报 503 SPOT_CHECK_UNCONFIGURED；比例为 0：照常返回（一页不抽），带告警 ratio_zero。"""
    from review_console.errors import ReviewError
    from review_console.service import spot_check_view
    day = dt.date(2026, 10, 2)
    with pytest.raises(ReviewError) as e:
        await spot_check_view(None, None, None, day)  # type: ignore[arg-type]
    assert (e.value.status, e.value.code) == (503, "SPOT_CHECK_UNCONFIGURED")
    out = await spot_check_view(None, None, SpotCheckSettings(seed="syn-seed", ratio=0.0), day)  # type: ignore[arg-type]
    assert out["warnings"] == ["ratio_zero"] and out["configured"] is False
    out = await spot_check_view(None, None, SpotCheckSettings(seed="syn-seed", ratio=0.1), day)  # type: ignore[arg-type]
    assert out["warnings"] == []


# ---------- 对账：抽检记录不算事前签字；抽检驳回扣页 ----------
def _base():
    exp = pages.parse_export(pages.load_json(str(FIX / "export.json")))
    smap = pages.parse_staff_map(pages.load_json(str(FIX / "staff-map.json")))
    recs = pages.parse_records(pages.load_json(str(FIX / "records.json")))
    return exp, smap, recs


def test_reconcile_spot_check_records():
    exp, smap, recs = _base()
    assert pages.reconcile(exp, recs, smap)["ok"]
    t1 = smap["syn-teacher-1"]
    approved = pages.ReviewRecord("MATH.SYN.T.D", "a3" * 20, "low", "approved", t1, spot_check=True)
    # 低风险页 D 的抽检批准：不当作事前签字比对批准人（否则托管平台无批准人、审核台有一人会报 approver_mismatch）
    r = pages.reconcile(exp, recs + [approved], smap)
    assert r["ok"] and next(e for e in r["merged"] if e["page_id"] == "MATH.SYN.T.D")["approvals_review"] == 0
    rejected = pages.ReviewRecord("MATH.SYN.T.D", "a3" * 20, "low", "rejected", smap["syn-teacher-2"], spot_check=True)
    r = pages.reconcile(exp, recs + [approved, rejected], smap)
    assert not r["ok"] and r["held_pages"] == ["MATH.SYN.T.D"]
    assert next(e for e in r["merged"] if e["page_id"] == "MATH.SYN.T.D")["problems"] == ["spot_check_rejected"]


def test_records_basis_code_parsing():
    raw = json.loads((FIX / "records.json").read_text(encoding="utf-8"))
    assert raw["format"] == "kge-page-review-records/1" and pages.parse_records(raw)  # 旧格式 /1 照样接受
    spot = {"page_id": "MATH.SYN.T.D", "page_commit": "a3" * 20, "risk_level": "low", "decision": "approved",
            "signer": "5f0e0000-0000-4000-8000-0000000000e1", "basis_code": "spot_check"}
    # /1 不许出现 basis_code（冲刺 4 起的字段只在 /1.1 里）
    with pytest.raises(pages.ReconcileInputError):
        pages.parse_records({**raw, "records": raw["records"] + [spot]})
    raw = {**raw, "format": "kge-page-review-records/1.1"}
    assert pages.RECORDS_FORMAT == "kge-page-review-records/1.1"
    recs = pages.parse_records({**raw, "records": raw["records"] + [spot]})
    assert recs[-1].spot_check and not any(r.spot_check for r in recs[:-1])
    # /1.1 的 basis_code 只收 mr_review 与 spot_check；mr_review 即事前签字
    recs = pages.parse_records({**raw, "records": [{**r, "basis_code": "mr_review"} for r in raw["records"]]})
    assert not any(r.spot_check for r in recs)
    for bad in ("mr_approved", "expired_auto", "", None):
        with pytest.raises(pages.ReconcileInputError):
            pages.parse_records({**raw, "records": raw["records"] + [{**spot, "basis_code": bad}]})
    with pytest.raises(pages.ReconcileInputError):
        pages.parse_records({**raw, "format": "kge-page-review-records/2"})
    # 库行：非低风险写了 spot_check 也按事前签字对待（偏严）
    rows = [{"page_id": "MATH.SYN.T.A", "page_commit": "a1" * 20, "risk_level": "high", "decision": "approved",
             "signer": "5f0e0000-0000-4000-8000-0000000000e1", "basis_code": "spot_check"}]
    assert not pages.records_from_rows(rows)[0].spot_check


# ---------- 提交方不能自签：合并请求作者（可选字段 author） ----------
def test_export_author_field():
    exp, smap, recs = _base()
    assert {m.mr_id: m.author for m in exp.merge_requests} == {"101": "syn-author-1", "102": "syn-author-1", "103": "syn-author-2",
                                                               "104": "syn-author-2"}
    raw = json.loads((FIX / "export.json").read_text(encoding="utf-8"))
    del raw["merge_requests"][0]["author"]
    assert pages.parse_export(raw).merge_requests[0].author is None  # 作者可选（旧导出没有）
    for bad in ("bad name", "", 7, None):
        raw["merge_requests"][0]["author"] = bad
        with pytest.raises(pages.ReconcileInputError):
            pages.parse_export(raw)


def test_reconcile_flags_self_approval():
    exp, smap, recs = _base()
    assert pages.reconcile(exp, recs, smap)["ok"]
    import dataclasses
    # 作者本人在托管平台上批准自己的合并请求
    mrs = tuple(dataclasses.replace(m, author="syn-teacher-1") if m.mr_id == "101" else m for m in exp.merge_requests)
    r = pages.reconcile(dataclasses.replace(exp, merge_requests=mrs), recs, smap)
    a = next(e for e in r["merged"] if e["page_id"] == "MATH.SYN.T.A")
    assert a["status"] == "failed" and "self_approval" in a["problems"] and "MATH.SYN.T.A" in r["held_pages"]
    # 作者经对应表映射到的 staff 在审核记录里批准（托管平台批准人里没有作者）
    mrs = tuple(dataclasses.replace(m, author="syn-author-1") if m.mr_id == "101" else m for m in exp.merge_requests)
    own = pages.ReviewRecord("MATH.SYN.T.A", "a1" * 20, "high", "approved", smap["syn-author-1"])
    r = pages.reconcile(dataclasses.replace(exp, merge_requests=mrs), recs + [own], smap)
    assert "self_approval" in next(e for e in r["merged"] if e["page_id"] == "MATH.SYN.T.A")["problems"]
    # 作者缺失：对账判不出，不在这里扣页（审核台在线签字处拒绝，见 test_review_console）
    mrs = tuple(dataclasses.replace(m, author=None) for m in exp.merge_requests)
    assert pages.reconcile(dataclasses.replace(exp, merge_requests=mrs), recs, smap)["ok"]


def test_check_not_author(tmp_path):
    """审核台签字前核对作者：作者映射到签字人 → SELF_SIGN；作者缺失或不在对应表 → AUTHOR_UNKNOWN（判不出从严）。"""
    import dataclasses
    import uuid as _uuid
    from review_console.config import PagesSettings
    from review_console.errors import ReviewError
    from review_console.service import check_not_author
    exp, smap, _ = _base()
    settings = PagesSettings(str(FIX / "export.json"), str(FIX / "staff-map.json"), "https://git.example.cn/p/-/merge_requests/{mr_id}")
    mr = exp.merge_requests[0]
    check_not_author(settings, mr, _uuid.UUID(smap["syn-teacher-1"]))  # 别人签：放行
    cases = [(mr, smap["syn-author-1"], "SELF_SIGN"), (dataclasses.replace(mr, author=None), smap["syn-teacher-1"], "AUTHOR_UNKNOWN"),
             (dataclasses.replace(mr, author="syn-outsider"), smap["syn-teacher-1"], "AUTHOR_UNKNOWN")]
    for m, staff, code in cases:
        with pytest.raises(ReviewError) as e:
            check_not_author(settings, m, _uuid.UUID(staff))
        assert (e.value.status, e.value.code) == (409, code)
    bad = tmp_path / "staff-map.json"
    bad.write_text("{}", encoding="utf-8")
    with pytest.raises(ReviewError) as e:
        check_not_author(dataclasses.replace(settings, staff_map_path=str(bad)), mr, _uuid.UUID(smap["syn-teacher-1"]))
    assert e.value.status == 503


def test_spot_check_constant_matches_pages():
    from review_console import spot_check
    assert spot_check.SPOT_CHECK_BASIS == pages.SPOT_CHECK_BASIS == "spot_check"
    # 抽检依据合迁移 0019 的 basis_code 格式
    assert re.fullmatch(r"[a-z0-9_]{1,48}", spot_check.SPOT_CHECK_BASIS)
    tree = ast.parse((ROOT / "services/learning/review_console/app.py").read_text(encoding="utf-8"))
    assert any(isinstance(n, ast.Constant) and n.value == "/review/v0/spot-checks/sign" for n in ast.walk(tree))
