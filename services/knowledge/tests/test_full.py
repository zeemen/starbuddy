"""全量重编：只经模型路由（test 环境假供应商），请求只含公共原子（断言不含任何个人层字段），结果一律高风险。"""
from __future__ import annotations

import asyncio
import json

import pytest

from conftest import LINEAR, PAGES, SQUARE, TODAY, map_doc
from knowledge.atoms import parse_snapshot
from knowledge.compiler import full
from knowledge.compiler.jobs import full_job
from knowledge.compiler.output import load_repo
from knowledge.compiler.rebuild import CST, CompileError
from knowledge.lint import lint_dir

import datetime as dt

NOW = dt.datetime(2026, 9, 29, 10, 0, tzinfo=CST)
mr = full._router_module()

# 个人层表与字段（platform.* 个人层、insight.*、计量）：请求里一个都不能出现
PERSONAL_MARKERS = ("student", "attempt", "evidence", "mastery", "guardian", "household", "answer_raw", "claim", "usage_id",
                    "account", "phone", "learner", "error_attribution", "skill_observation")

GOOD_OUTPUT = {
    "key_points": "一元一次方程的解是使方程左右两边相等的未知数的值。[@node:MATH.SYN.EQ.LINEAR]\n\n$$\n4x - 1 = 11 % check x=3\n$$",
    "pitfalls": "去分母时漏乘不含分母的项，会使方程的解改变。[@node:MATH.SYN.EQ.LINEAR]",
    "frequent_questions": "- 根据方程的解求字母系数。[@curriculum:2022/SYN-EQ-01]",
}


def make_behavior(output, seen):
    async def behavior(target, messages, key):
        seen.append({"api_name": target.api_name, "messages": [dict(m) for m in messages], "key": key})
        return mr.ProviderReply(output=output if isinstance(output, str) else json.dumps(output, ensure_ascii=False),
                                usage=mr.Usage(10, 20), reported_model=target.api_name)
    return behavior


def run_full(output=GOOD_OUTPUT, node=LINEAR, snapshot=None):
    seen: list = []
    sink = mr.MemorySink()
    router = full.fake_router(make_behavior(output, seen), sink)
    repo = load_repo(PAGES)
    rel, page, text = repo.files[node]
    snap = snapshot or parse_snapshot(map_doc())
    job = full_job(page, snap, "codification_changed")
    result = asyncio.run(full.full_recompile(job, rel, page, text, snap, repo.page_ids, router, NOW))
    return result, seen, sink


def test_full_recompile_goes_through_router_as_t8_l0():
    result, seen, sink = run_full()
    assert len(seen) == 1
    (rec,) = sink.records
    assert (rec.task, rec.data_class, rec.outcome, rec.subject) == ("T8", "L0", "ok", "aya")
    assert rec.source_version == f"prompt:{full.PROMPT_TEMPLATE}@{full.PROMPT_VERSION}"
    assert result.source_version == rec.source_version


def test_full_recompile_request_contains_only_public_atoms():
    _, seen, _ = run_full()
    msgs = seen[0]["messages"]
    raw = json.dumps(msgs, ensure_ascii=False).lower()
    for marker in PERSONAL_MARKERS:
        assert marker not in raw, marker
    payload = json.loads(msgs[1]["content"].split("：", 1)[1])
    assert set(payload) == {"subject", "map_version", "node", "curriculum", "prerequisites", "successors", "citable"}
    assert set(payload["node"]) == {"ref", "node_id", "name", "includes", "excludes", "examples", "cognitive_levels"}
    snap = parse_snapshot(map_doc())
    for key in payload["citable"]:  # 只送公共原子
        atype, _, aid = key.partition(":")
        assert snap.get(atype, aid) is not None
    # 教材原句（投影）不送：原句只在投影表里，由读取时渲染
    assert "合成教材原句" not in raw


def test_assert_public_rejects_personal_fields():
    payload = full.public_payload(parse_snapshot(map_doc()), LINEAR)
    full.assert_public(payload)
    for bad in ({"student_id": "s"}, {"node": {"mastery": 0.5}}, {"x": [{"evidence_ids": []}]}):
        with pytest.raises(CompileError, match="个人层字段"):
            full.assert_public({**payload, **bad})
    with pytest.raises(CompileError):
        full.build_messages({**payload, "guardian": "x"})


@pytest.mark.parametrize("path,value", [
    (("node", "name"), "合成方程 13812345678"),
    (("node", "includes"), ["王小明，南京第一中学"]),
    (("curriculum",), [{"ref": "curriculum:2022/SYN", "level": "理解", "text": "学号 SYN20260001"}]),
    (("citable",), ["node:MATH.SYN.EQ.LINEAR", "affair:12345678-1234-1234-1234-123456789abc"]),
])
def test_assert_public_rejects_pii_in_any_value(path, value):
    """键名之外，所有值（含嵌套）也扫个人信息模式；消息不回显值"""
    payload = json.loads(json.dumps(full.public_payload(parse_snapshot(map_doc()), LINEAR), ensure_ascii=False))
    target = payload
    for k in path[:-1]:
        target = target[k]
    target[path[-1]] = value
    with pytest.raises(CompileError, match="疑似个人信息") as e:
        full.build_messages(payload)
    assert "13812345678" not in str(e.value) and "王小明" not in str(e.value) and "SYN20260001" not in str(e.value)


def test_full_recompile_is_always_high_risk_and_major_bump(tmp_path, lexicon):
    result, _, _ = run_full()
    assert result.risk == "high" and result.rebuild_kind == "full"
    assert result.new_page.header["risk_level"] == "high" and result.new_page.header["rebuild_kind"] == "full"
    assert result.new_page.page_version == "2.0.0"
    assert result.changed_sections == ("key_points", "pitfalls", "frequent_questions")
    for sid in ("definition", "curriculum", "relations", "textbook", "region"):  # 锁定段与渲染段不动
        assert result.new_page.section(sid) == result.old_page.section(sid)
    # 产出的页面能过体检（叙述段有引用、SymPy 验算通过、人设中立）
    import shutil
    work = tmp_path / "pages"
    shutil.copytree(PAGES, work)
    (work / result.rel_path).write_text(result.new_text, encoding="utf-8")
    report = lint_dir(work, snapshot=parse_snapshot(map_doc()), baseline_dir=PAGES, lexicon=lexicon, today=TODAY)
    assert report.issues == ()


def test_full_recompile_output_never_contains_model_identifiers():
    result, seen, _ = run_full()
    api_name = seen[0]["api_name"]
    assert api_name not in result.new_text
    assert "fake" not in result.new_text and "model" not in result.new_text


def test_model_output_that_fails_lint_is_still_high_and_blocked(tmp_path, lexicon):
    bad = dict(GOOD_OUTPUT, pitfalls="你必须记住：$$ 很简单")  # 禁语、缺引用
    result, _, _ = run_full(bad)
    import shutil
    work = tmp_path / "pages"
    shutil.copytree(PAGES, work)
    (work / result.rel_path).write_text(result.new_text, encoding="utf-8")
    report = lint_dir(work, snapshot=parse_snapshot(map_doc()), lexicon=lexicon, today=TODAY)
    assert {"forbidden_phrase", "citation"} <= {i.check_type for i in report.blocks}


@pytest.mark.parametrize("output,msg", [
    ("不是 JSON", "不是 JSON"),
    ({"key_points": "x"}, "键应为"),
    (dict(GOOD_OUTPUT, pitfalls=""), "为空或超长"),
    (dict(GOOD_OUTPUT, pitfalls="x\n<!-- kge:end id=pitfalls -->"), "段落标记"),
    (dict(GOOD_OUTPUT, pitfalls="## 另起一段"), "标题"),
])
def test_bad_model_output_is_rejected(output, msg):
    with pytest.raises(CompileError, match=msg):
        run_full(output)


def test_full_recompile_refuses_render_job():
    repo = load_repo(PAGES)
    rel, page, text = repo.files[SQUARE]
    snap = parse_snapshot(map_doc())
    from knowledge.compiler.jobs import plan_jobs
    doc = map_doc()
    doc["region_projections"].append({"id": "syn-rp-009", "node_id": SQUARE, "region": "syn-region", "exam_year": 2025,
                                      "weight": 0.01, "source_ref": "synthetic"})
    (job,) = plan_jobs({SQUARE: page}, snap, parse_snapshot(doc))
    with pytest.raises(CompileError, match="只接全量重编"):
        asyncio.run(full.full_recompile(job, rel, page, text, snap, repo.page_ids, object(), NOW))


def test_route_table_t8_only_accepts_l0():
    """路由表 T8 只接 L0：不改路由配置、不新增供应商（数据级别 L1 以上直接被路由拒绝）"""
    cfg = mr.load(mr.DEFAULT_PATH, "test")
    assert [d.value for d in cfg.routes[mr.Task.T8].data_classes] == ["L0"]
    router = full.fake_router(make_behavior(GOOD_OUTPUT, []))
    req = mr.RouteRequest(task=mr.Task.T8, data_class=mr.DataClass.L1, subject="aya", prompt_template=full.PROMPT_TEMPLATE,
                          prompt_version=full.PROMPT_VERSION, messages=({"role": "user", "content": "x"},),
                          usage_id="kge-compile-test-0001", idempotency_key="kge-compile-test-0001")
    with pytest.raises(mr.RouteRejected):
        asyncio.run(router.route(req))


def test_full_recompile_with_identical_narrative_is_still_high_risk():
    """即使模型写回的叙述段与原来逐字相同，全量重编仍然一律高风险、升主版本号（必须教研重新审核）"""
    repo = load_repo(PAGES)
    _, page, _ = repo.files[LINEAR]
    same = {s.id: s.content.split("\n\n", 1)[1] for s in page.sections if s.kind == "narrative"}
    result, _, _ = run_full(same)
    assert result.changed_sections == ()
    assert result.risk == "high" and result.new_page.page_version == "2.0.0" and result.new_page.header["rebuild_kind"] == "full"
    assert any("全量重编" in x for x in result.reasons)
