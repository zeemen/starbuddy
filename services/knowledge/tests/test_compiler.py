"""编译器：编入（受影响页、优先级）、渲染重建（叙述段逐字保留、哈希、风险分级、版本号）、合并窗口。"""
from __future__ import annotations

import datetime as dt

import pytest

from conftest import APPLY, LINEAR, PAGES, SQUARE, TRANSPOSE, map_doc, node_rec
from knowledge.atoms import parse_snapshot
from knowledge.compiler.jobs import RISK_WEIGHT, full_job, plan_jobs
from knowledge.compiler.output import compile_render, load_repo
from knowledge.compiler.rebuild import CST, CompileError, bump, render_rebuild
from knowledge.compiler.window import REVIEWERS, plan_window
from knowledge.pages.format import parse, serialize

NOW = dt.datetime(2026, 9, 29, 10, 0, tzinfo=CST)


def compile_with(mutate, now=NOW, repo=None):
    old = parse_snapshot(map_doc())
    doc = map_doc()
    mutate(doc)
    new = parse_snapshot(doc)
    repo = repo or load_repo(PAGES)
    jobs, results = compile_render(repo, old, new, now)
    return jobs, {r.page_id: r for r in results}, new


def narrative_of(page):
    return {s.id: s.content for s in page.sections if s.kind == "narrative"}


# ---------- 编入 ----------

def test_no_change_no_jobs():
    jobs, results, _ = compile_with(lambda d: None)
    assert jobs == [] and results == {}


def test_ingest_finds_pages_by_atom_refs_and_node():
    def mutate(d):
        node_rec(d, LINEAR)["excludes"] = ["二元一次方程组", "分式方程"]  # 被 LINEAR 页引用
        d["region_projections"].append({"id": "syn-rp-003", "node_id": SQUARE, "region": "syn-region", "exam_year": 2025,
                                        "weight": 0.02, "source_ref": "synthetic"})  # 新增原子：没有页面引用它，但挂在 SQUARE 上
    jobs, _, _ = compile_with(mutate)
    by_page = {j.page_id: j for j in jobs}
    assert set(by_page) == {LINEAR, SQUARE}
    assert [(t.key, t.change) for t in by_page[LINEAR].triggers] == [(f"node:{LINEAR}", "modified")]
    assert [(t.key, t.change) for t in by_page[SQUARE].triggers] == [("region:syn-rp-003", "added")]


def test_priority_is_risk_times_impact_and_sorted():
    def mutate(d):
        node_rec(d, LINEAR)["name"] = "一元一次方程（合成改名）"  # 高风险；LINEAR 有 1 个后续 → 影响 2
        d["region_projections"][0]["weight"] = 0.06  # APPLY 低风险；影响 1
    jobs, _, _ = compile_with(mutate)
    got = [(j.page_id, j.risk_estimate, j.impact, j.priority) for j in jobs]
    # LINEAR 改名也改变了相邻页面“前置与后续”里的名字：APPLY 的前置表述变了（高），TRANSPOSE 的后续变了（低）
    assert got == [(LINEAR, "high", 2, RISK_WEIGHT["high"] * 2), (APPLY, "high", 1, 3), (TRANSPOSE, "low", 2, 2)]
    assert all(j.status == "queued" and j.reason == "atom_changed" and j.job_id.startswith("cj-") for j in jobs)


def test_neighbor_rename_rebuilds_neighbor_pages_and_they_lint_clean(tmp_path, lexicon):
    """节点改名后，相邻页面也要重建，否则它们的渲染段与模板不一致（体检会阻断）"""
    from knowledge.lint import lint_dir
    import shutil
    _, results, new = compile_with(lambda d: node_rec(d, LINEAR).update(name="一元一次方程（合成改名）"))
    assert results[APPLY].risk == "high" and results[TRANSPOSE].risk == "low"
    work = tmp_path / "pages"
    shutil.copytree(PAGES, work)
    for r in results.values():
        (work / r.rel_path).write_text(r.new_text, encoding="utf-8")
    report = lint_dir(work, snapshot=new, baseline_dir=PAGES, lexicon=lexicon, today=NOW.date())
    assert report.issues == ()


def test_job_ids_are_deterministic():
    m = lambda d: d["region_projections"][0].update(weight=0.06)  # noqa: E731
    a, _, _ = compile_with(m)
    b, _, _ = compile_with(m)
    assert [j.job_id for j in a] == [j.job_id for j in b]


# ---------- 渲染重建 ----------

@pytest.mark.parametrize("mutate,page,risk,section", [
    (lambda d: node_rec(d, LINEAR).update(excludes=["二元一次方程组", "分式方程"]), LINEAR, "high", "definition"),
    (lambda d: d["curriculum_items"][0].update(text="合成课标条目：理解一元一次方程的概念与解。"), LINEAR, "high", "curriculum"),
    (lambda d: d["edges"].append({"from_id": SQUARE, "to_id": APPLY, "type": "prerequisite"}), APPLY, "high", "relations"),
    (lambda d: d["textbook_projections"][0].update(page_to=83), LINEAR, "high", "textbook"),
    (lambda d: d["region_projections"][0].update(weight=0.06), APPLY, "low", "region"),
    (lambda d: d["edges"].append({"from_id": APPLY, "to_id": SQUARE, "type": "prerequisite"}), APPLY, "low", "relations"),
])
def test_render_rebuild_risk_by_change_type(mutate, page, risk, section):
    _, results, new = compile_with(mutate)
    r = results[page]
    assert r.risk == risk, r.reasons
    assert section in r.changed_sections
    assert narrative_of(r.new_page) == narrative_of(r.old_page)  # 已审核叙述段逐字不变
    assert r.new_page.header["risk_level"] == risk and r.new_page.header["rebuild_kind"] == "render"
    assert r.new_page.header["page_version"] == ("1.1.0" if risk == "high" else "1.0.1")
    for ref in r.new_page.atom_refs:  # 记录所引原子的新哈希
        assert new.get(ref.type, ref.id).hash == ref.hash


def test_quote_only_change_is_high_risk_but_page_text_has_no_quote():
    _, results, _ = compile_with(lambda d: d["textbook_projections"][0].update(quote_text="合成教材原句：换了一种说法。"))
    r = results[LINEAR]
    assert r.risk == "high" and r.changed_sections == ()
    assert "换了一种说法" not in r.new_text
    assert [b.key for b in r.basis] == ["projection:syn-tp-001"]


def test_hash_only_change_is_low_risk():
    _, results, _ = compile_with(lambda d: node_rec(d, LINEAR).update(examples=["合成例子"]))
    r = results[LINEAR]
    assert r.risk == "low" and r.changed_sections == () and "只有所引原子的哈希更新" in "".join(r.reasons)
    assert [(b.key, b.change) for b in r.basis] == [(f"node:{LINEAR}", "modified")]


def test_narrative_citations_keep_hash_updated_and_removed_atoms_stay_stale():
    def mutate(d):
        d["curriculum_items"][0]["source_ref"] = "synthetic-2"  # LINEAR 的叙述段引用了它
    _, results, new = compile_with(mutate)
    ref = {x.key: x for x in results[LINEAR].new_page.atom_refs}["curriculum:2022/SYN-EQ-01"]
    assert ref.hash == new.get("curriculum", "2022/SYN-EQ-01").hash


def test_render_rebuild_is_idempotent():
    m = lambda d: node_rec(d, LINEAR).update(excludes=["二元一次方程组", "分式方程"])  # noqa: E731
    _, results, new = compile_with(m)
    r = results[LINEAR]
    again = render_rebuild(r.job, r.rel_path, r.new_page, r.new_text, new, {LINEAR, TRANSPOSE, APPLY, SQUARE}, NOW)
    assert again.unchanged and again.new_text == r.new_text


def test_render_rebuild_refuses_missing_node():
    doc = map_doc()
    doc["nodes"] = [n for n in doc["nodes"] if n["id"] != SQUARE]
    doc["curriculum_links"] = [c for c in doc["curriculum_links"] if c["node_id"] != SQUARE]
    repo = load_repo(PAGES)
    rel, page, text = repo.files[SQUARE]
    job = full_job(page, parse_snapshot(map_doc()), "test")
    with pytest.raises(CompileError, match="不在地图快照"):
        render_rebuild(job, rel, page, text, parse_snapshot(doc), repo.page_ids, NOW)


def test_new_text_is_canonical():
    _, results, _ = compile_with(lambda d: d["region_projections"][0].update(weight=0.06))
    r = results[APPLY]
    assert serialize(parse(r.new_text)) == r.new_text


def test_bump():
    assert bump("1.2.3", "low", "render") == "1.2.4"
    assert bump("1.2.3", "high", "render") == "1.3.0"
    assert bump("1.2.3", "high", "full") == "2.0.0"


# ---------- 合并窗口 ----------

def _low(page, weight, now):
    def m(d):
        if page == APPLY:
            d["region_projections"][0]["weight"] = weight
        else:
            d["region_projections"][1]["weight"] = weight
    return compile_with(m, now)[1][page]


def test_window_low_risk_daily_group_and_high_single():
    def mutate(d):
        d["region_projections"][0]["weight"] = 0.06        # APPLY 低
        d["region_projections"][1]["weight"] = 0.04        # LINEAR 低（地区权重）
        node_rec(d, SQUARE)["includes"] = ["合成改写的范围"]  # SQUARE 高
    _, results, _ = compile_with(mutate)
    mrs = plan_window(results.values())
    assert [(m.kind, m.risk, m.reviewers_required, m.page_ids) for m in mrs] == [
        ("daily_low", "low", 0, [APPLY, LINEAR]), ("single", "high", 2, [SQUARE])]
    assert mrs[0].mr_id == "mr-2026-09-29-low"


def test_window_each_page_at_most_once_per_day():
    first = _low(APPLY, 0.06, NOW)
    second = _low(APPLY, 0.07, NOW + dt.timedelta(hours=3))
    second = render_rebuild(second.job, second.rel_path, first.new_page, first.new_text, compile_with(
        lambda d: d["region_projections"][0].update(weight=0.07))[2], {LINEAR, TRANSPOSE, APPLY, SQUARE}, NOW + dt.timedelta(hours=3))
    mrs = plan_window([first, second])
    assert len(mrs) == 1 and mrs[0].page_ids == [APPLY]
    merged = mrs[0].results[0]
    assert merged.old_text == first.old_text and "0.070" in merged.new_text
    assert merged.new_page.page_version == "1.0.1"  # 同一天只发布一次，版本只升一次


def test_window_defers_page_already_published_today():
    r = _low(APPLY, 0.06, NOW)
    mrs = plan_window([r], {APPLY: [NOW.date()]})
    assert [m.mr_id for m in mrs] == ["mr-2026-09-30-low"]
    assert any("顺延" in n for n in mrs[0].notes)


def test_window_uses_beijing_date():
    late = dt.datetime(2026, 9, 29, 17, 0, tzinfo=dt.timezone.utc)  # 北京时间 30 日 01:00
    mrs = plan_window([_low(APPLY, 0.06, late)])
    assert mrs[0].day == dt.date(2026, 9, 30)


def test_window_medium_is_treated_as_high():
    assert REVIEWERS["high"] == 2 and REVIEWERS["medium"] == 2 and REVIEWERS["low"] == 0


def test_window_skips_unchanged():
    _, results, _ = compile_with(lambda d: None)
    assert plan_window(results.values()) == []
